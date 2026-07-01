"""
ML trainer — builds the win-probability model used by MLSignalFilter.

Data sources (merged, deduplicated):
  1. Every CSV in  backend/past data/   (exported trade history + backtest data)
  2. Closed trades with signal_features in the live SQLite DB (trading_bot.db)

Method:
  - Gradient boosting (sklearn HistGradientBoostingClassifier) over the shared
    feature schema in ml/features.py
  - Walk-forward (time-ordered) validation — no lookahead leakage
  - Threshold selection maximizes out-of-fold FILTERED PnL, not accuracy:
    the threshold that would have made the most money is stored in the artifact

Run manually:      python -m ml.trainer
Runs automatically: every N closed trades via MLSignalFilter.retrain_async()
"""
import glob
import json
import logging
import os
import sqlite3
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from ml.features import (
    ALL_FEATURES,
    CATEGORICAL_FEATURES,
    build_feature_frame,
    normalize_record,
)

logger = logging.getLogger(__name__)

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(_BACKEND_DIR, "past data")
DB_PATH = os.path.join(_BACKEND_DIR, "trading_bot.db")
MODEL_PATH = os.path.join(_BACKEND_DIR, "ml", "model.joblib")

MIN_SAMPLES_TO_TRAIN = 100
WALK_FORWARD_FOLDS = 4
MIN_TRADES_KEPT_FRACTION = 0.25  # threshold may not discard more than 75% of trades


# --------------------------------------------------------------------------- #
# Data loading
# --------------------------------------------------------------------------- #
def load_csv_datasets(data_dir: str = DATA_DIR) -> list[dict]:
    """Load every CSV exported into 'past data/'. Returns raw record dicts
    including profit_loss and opened_at for labeling/ordering."""
    records: list[dict] = []
    for csv_path in sorted(glob.glob(os.path.join(data_dir, "*.csv"))):
        try:
            frame = pd.read_csv(csv_path)
            # Duplicate column names (e.g. two 'symbol' columns) are mangled by
            # pandas to 'symbol.1' — drop the mangled duplicates
            frame = frame.loc[:, ~frame.columns.str.contains(r"\.\d+$")]
            for row in frame.to_dict(orient="records"):
                if row.get("profit_loss") is None or pd.isna(row.get("profit_loss")):
                    continue
                records.append(row)
            logger.info("Trainer: loaded %d rows from %s", len(frame), os.path.basename(csv_path))
        except Exception as exc:
            logger.warning("Trainer: skipped %s: %s", csv_path, exc)
    return records


def load_db_trades(db_path: str = DB_PATH) -> list[dict]:
    """Load closed trades with signal_features from the live SQLite DB.
    Handles Fernet-encrypted signal_features transparently."""
    if not os.path.exists(db_path):
        return []
    records: list[dict] = []
    try:
        from utils.encryption import decrypt_value
    except ImportError:
        def decrypt_value(v):
            return v
    try:
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT symbol, side, profit_loss, opened_at, signal_features "
            "FROM trades WHERE status = 'closed' AND signal_features IS NOT NULL"
        ).fetchall()
        connection.close()
        for row in rows:
            if row["profit_loss"] is None:
                continue
            try:
                features = json.loads(decrypt_value(row["signal_features"]))
            except (TypeError, ValueError):
                continue
            record = {**features,
                      "symbol": row["symbol"],
                      "side": row["side"],
                      "profit_loss": row["profit_loss"],
                      "opened_at": row["opened_at"]}
            records.append(record)
        logger.info("Trainer: loaded %d closed trades from DB", len(records))
    except Exception as exc:
        logger.warning("Trainer: DB load failed: %s", exc)
    return records


def build_dataset() -> pd.DataFrame:
    """Merge all sources into one labeled, time-ordered dataset."""
    raw = load_csv_datasets() + load_db_trades()
    if not raw:
        return pd.DataFrame()
    frame = build_feature_frame(raw)
    frame["profit_loss"] = [float(r.get("profit_loss", 0.0)) for r in raw]
    frame["profitable"] = (frame["profit_loss"] > 0).astype(int)
    frame["opened_at"] = pd.to_datetime(
        [r.get("opened_at") for r in raw], errors="coerce", format="mixed"
    )
    frame = frame.sort_values("opened_at", na_position="first").reset_index(drop=True)
    # Deduplicate identical rows (same trade exported to CSV AND still in DB)
    frame = frame.drop_duplicates(subset=ALL_FEATURES + ["profit_loss"], keep="first")
    return frame.reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Model
# --------------------------------------------------------------------------- #
def _make_pipeline():
    from sklearn.compose import ColumnTransformer
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import OneHotEncoder

    preprocessor = ColumnTransformer(
        transformers=[(
            "categorical",
            OneHotEncoder(handle_unknown="ignore", sparse_output=False),
            CATEGORICAL_FEATURES,
        )],
        remainder="passthrough",
        sparse_threshold=0.0,
    )
    model = HistGradientBoostingClassifier(
        max_iter=300,
        learning_rate=0.06,
        max_leaf_nodes=15,
        min_samples_leaf=15,
        l2_regularization=1.0,
        early_stopping=True,
        validation_fraction=0.15,
        random_state=42,
    )
    return Pipeline([("preprocess", preprocessor), ("model", model)], memory=None)


def _walk_forward_evaluate(frame: pd.DataFrame) -> dict:
    """Time-ordered walk-forward CV. Returns AUC + out-of-fold probabilities
    so threshold selection can optimize realized PnL without lookahead."""
    from sklearn.metrics import roc_auc_score

    n = len(frame)
    oof_probability = np.full(n, np.nan)
    fold_size = n // (WALK_FORWARD_FOLDS + 1)
    features, target = frame[ALL_FEATURES], frame["profitable"].to_numpy()

    for fold in range(1, WALK_FORWARD_FOLDS + 1):
        train_end = fold * fold_size
        test_end = min(train_end + fold_size, n) if fold < WALK_FORWARD_FOLDS else n
        if train_end < MIN_SAMPLES_TO_TRAIN // 2 or train_end >= test_end:
            continue
        train_target = target[:train_end]
        if len(np.unique(train_target)) < 2:
            continue
        pipeline = _make_pipeline()
        pipeline.fit(features.iloc[:train_end], train_target)
        oof_probability[train_end:test_end] = pipeline.predict_proba(
            features.iloc[train_end:test_end])[:, 1]

    scored_mask = ~np.isnan(oof_probability)
    metrics = {"oof_probability": oof_probability, "scored_mask": scored_mask}
    if scored_mask.sum() >= 20 and len(np.unique(target[scored_mask])) == 2:
        metrics["auc"] = float(roc_auc_score(target[scored_mask], oof_probability[scored_mask]))
    else:
        metrics["auc"] = 0.5
    return metrics


def _select_threshold(frame: pd.DataFrame, oof_probability: np.ndarray,
                      scored_mask: np.ndarray) -> dict:
    """Pick the probability threshold that maximizes out-of-fold FILTERED PnL,
    keeping at least MIN_TRADES_KEPT_FRACTION of trades."""
    pnl = frame["profit_loss"].to_numpy()
    scored_pnl = pnl[scored_mask]
    scored_probability = oof_probability[scored_mask]
    baseline_pnl = float(scored_pnl.sum())

    best = {"threshold": 0.55, "filtered_pnl": baseline_pnl,
            "baseline_pnl": baseline_pnl, "trades_kept_pct": 100.0}
    if len(scored_pnl) < 20:
        return best

    for threshold in np.arange(0.40, 0.71, 0.025):
        keep = scored_probability >= threshold
        kept_fraction = keep.mean()
        if kept_fraction < MIN_TRADES_KEPT_FRACTION:
            continue
        filtered_pnl = float(scored_pnl[keep].sum())
        if filtered_pnl > best["filtered_pnl"]:
            best = {
                "threshold": round(float(threshold), 3),
                "filtered_pnl": round(filtered_pnl, 2),
                "baseline_pnl": round(baseline_pnl, 2),
                "trades_kept_pct": round(kept_fraction * 100, 1),
            }
    return best


def train_and_save(model_path: str = MODEL_PATH) -> dict:
    """Full training pipeline: load -> walk-forward evaluate -> threshold ->
    final fit on all data -> save artifact. Returns a result summary dict."""
    import joblib

    frame = build_dataset()
    n = len(frame)
    if n < MIN_SAMPLES_TO_TRAIN:
        message = f"Not enough samples to train: {n} < {MIN_SAMPLES_TO_TRAIN}"
        logger.warning("Trainer: %s", message)
        return {"success": False, "message": message, "n_samples": n}
    if frame["profitable"].nunique() < 2:
        return {"success": False, "message": "Dataset has only one class", "n_samples": n}

    evaluation = _walk_forward_evaluate(frame)
    threshold_info = _select_threshold(frame, evaluation["oof_probability"],
                                       evaluation["scored_mask"])

    final_pipeline = _make_pipeline()
    final_pipeline.fit(frame[ALL_FEATURES], frame["profitable"].to_numpy())

    metadata = {
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "n_samples": n,
        "positive_rate": round(float(frame["profitable"].mean()), 4),
        "walk_forward_auc": round(evaluation["auc"], 4),
        "recommended_threshold": threshold_info["threshold"],
        "oof_baseline_pnl": threshold_info["baseline_pnl"],
        "oof_filtered_pnl": threshold_info["filtered_pnl"],
        "oof_trades_kept_pct": threshold_info["trades_kept_pct"],
        "feature_columns": ALL_FEATURES,
    }
    os.makedirs(os.path.dirname(model_path), exist_ok=True)
    joblib.dump({"pipeline": final_pipeline, "metadata": metadata}, model_path)
    logger.info("Trainer: saved model to %s | AUC=%.3f threshold=%.2f "
                "| OOF PnL baseline=%.2f -> filtered=%.2f (kept %.0f%% of trades)",
                model_path, metadata["walk_forward_auc"], metadata["recommended_threshold"],
                metadata["oof_baseline_pnl"], metadata["oof_filtered_pnl"],
                metadata["oof_trades_kept_pct"])
    return {"success": True, **metadata}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    result = train_and_save()
    print(json.dumps(result, indent=2, default=str))
