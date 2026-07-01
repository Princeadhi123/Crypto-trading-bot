"""
MLSignalFilter — adaptive machine-learning gate embedded in the trading loop.

At entry time the trading engine passes the full feature snapshot of a candidate
trade. The filter scores it with a gradient-boosting win-probability model
trained on the bot's own closed-trade history and blocks entries the model
expects to lose.

Adaptive loop:
    trade closes -> outcome recorded -> every N closes the model retrains in a
    background thread on ALL accumulated data (CSV exports + live DB trades)
    and hot-reloads -> future decisions use the updated model.

Fail-open design: if no model artifact exists or scoring raises, trades are
allowed through (the classical risk stack still applies) and the event is logged.
"""
import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_ML_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ml")
MODEL_PATH = os.path.join(_ML_DIR, "model.joblib")

DEFAULT_MIN_WIN_PROBABILITY = 0.55
DEFAULT_RETRAIN_EVERY_N_CLOSES = 25


class MLSignalFilter:
    def __init__(self, model_path: str = MODEL_PATH,
                 retrain_every: int = DEFAULT_RETRAIN_EVERY_N_CLOSES):
        self.model_path = model_path
        self.retrain_every = retrain_every
        self.pipeline = None
        self.metadata: dict = {}
        self._closes_since_train = 0
        self._retraining = False
        self._retrain_task: Optional[asyncio.Task] = None
        self._last_error: Optional[str] = None
        self.load()

    # ------------------------------------------------------------------ #
    # Model loading / status
    # ------------------------------------------------------------------ #
    def load(self) -> bool:
        """Load the model artifact from disk. Returns True on success."""
        try:
            if not os.path.exists(self.model_path):
                logger.info("ML filter: no model artifact at %s — filter disabled (fail-open). "
                            "Train one with: python -m ml.trainer", self.model_path)
                return False
            import joblib
            artifact = joblib.load(self.model_path)
            self.pipeline = artifact["pipeline"]
            self.metadata = artifact.get("metadata", {})
            logger.info("ML filter: model loaded (trained %s, %s samples, AUC %.3f, threshold %.2f)",
                        self.metadata.get("trained_at", "?"),
                        self.metadata.get("n_samples", "?"),
                        self.metadata.get("walk_forward_auc", 0.0),
                        self.min_win_probability)
            return True
        except Exception as exc:
            self._last_error = str(exc)
            logger.warning("ML filter: failed to load model: %s — filter disabled (fail-open)", exc)
            self.pipeline = None
            return False

    @property
    def enabled(self) -> bool:
        return self.pipeline is not None

    @property
    def min_win_probability(self) -> float:
        """Env override > trained threshold from artifact > default."""
        env_value = os.getenv("ML_MIN_WIN_PROBABILITY", "").strip()
        if env_value:
            try:
                return float(env_value)
            except ValueError:
                pass
        return float(self.metadata.get("recommended_threshold", DEFAULT_MIN_WIN_PROBABILITY))

    # ------------------------------------------------------------------ #
    # Scoring
    # ------------------------------------------------------------------ #
    def score(self, features: dict) -> Optional[float]:
        """Returns the model's win probability for a candidate entry,
        or None when no model is available (fail-open)."""
        if not self.enabled:
            return None
        try:
            from ml.features import build_feature_frame
            frame = build_feature_frame([features])
            probability = float(self.pipeline.predict_proba(frame)[0, 1])
            return probability
        except Exception as exc:
            self._last_error = str(exc)
            logger.warning("ML filter: scoring failed (%s) — allowing trade through", exc)
            return None

    # ------------------------------------------------------------------ #
    # Adaptive retraining
    # ------------------------------------------------------------------ #
    def record_trade_closed(self):
        self._closes_since_train += 1

    def retrain_due(self) -> bool:
        return (self._closes_since_train >= self.retrain_every
                and not self._retraining
                and (self._retrain_task is None or self._retrain_task.done()))

    async def retrain_async(self):
        """Retrain the model in a worker thread on all accumulated data, then hot-reload."""
        if self._retraining:
            return
        self._retraining = True
        # Keep a reference so the task is not garbage-collected mid-flight
        self._retrain_task = asyncio.current_task()
        try:
            logger.info("ML filter: background retrain starting (%d new closed trades)",
                        self._closes_since_train)
            from ml.trainer import train_and_save
            result = await asyncio.to_thread(train_and_save)
            if result.get("success"):
                self._closes_since_train = 0
                self.load()
                logger.info("ML filter: retrain complete — AUC %.3f on %d samples",
                            result.get("walk_forward_auc", 0.0), result.get("n_samples", 0))
            else:
                logger.warning("ML filter: retrain skipped/failed: %s", result.get("message"))
        except Exception as exc:
            self._last_error = str(exc)
            logger.error("ML filter: retrain crashed: %s", exc)
        finally:
            self._retraining = False

    def status(self) -> dict:
        return {
            "enabled": self.enabled,
            "model_path": self.model_path,
            "min_win_probability": self.min_win_probability,
            "retrain_every_n_closes": self.retrain_every,
            "closes_since_last_train": self._closes_since_train,
            "retraining_now": self._retraining,
            "last_error": self._last_error,
            "metadata": self.metadata,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
