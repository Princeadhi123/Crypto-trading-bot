"""
Security module: JWT login authentication and in-memory rate limiter.

- Set ADMIN_PASSWORD_HASH + JWT_SECRET for full JWT login system.
- Leave ADMIN_PASSWORD_HASH empty to run in dev mode with auth disabled (localhost only).
- Rate limiting is always enforced for mutating/action endpoints.
"""
import hmac
import os
import secrets
import time
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from dotenv import load_dotenv
from fastapi import HTTPException, Request, Header
import bcrypt

# Load .env so tokens are always available regardless of import order
load_dotenv()

# ---------------------------------------------------------------------------
# JWT auth
# ---------------------------------------------------------------------------
_ADMIN_USERNAME: str = os.getenv("ADMIN_USERNAME", "admin").strip()
_ADMIN_PASSWORD_HASH: str = os.getenv("ADMIN_PASSWORD_HASH", "").strip()
# JWT_SECRET: if not set, generate one per-process (users must re-login on restart)
_JWT_SECRET: str = os.getenv("JWT_SECRET", "").strip() or secrets.token_hex(32)
_JWT_ALGORITHM = "HS256"
_JWT_EXPIRE_HOURS = 24

try:
    from jose import jwt, JWTError
    import bcrypt
    _JWT_AVAILABLE = True
except ImportError:
    _JWT_AVAILABLE = False


def verify_password(plain: str, hashed: str) -> bool:
    if not _JWT_AVAILABLE:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def create_access_token(data: dict) -> str:
    if not _JWT_AVAILABLE:
        raise RuntimeError("python-jose not installed")
    payload = data.copy()
    payload["exp"] = datetime.now(timezone.utc) + timedelta(hours=_JWT_EXPIRE_HOURS)
    payload["iat"] = datetime.now(timezone.utc)
    from jose import jwt as _jwt
    return _jwt.encode(payload, _JWT_SECRET, algorithm=_JWT_ALGORITHM)


def get_admin_credentials() -> tuple[str, str]:
    return (_ADMIN_USERNAME, _ADMIN_PASSWORD_HASH)


def _verify_jwt(token: str) -> bool:
    if not _JWT_AVAILABLE:
        return False
    try:
        from jose import jwt as _jwt, JWTError as _JWTError
        _jwt.decode(token, _JWT_SECRET, algorithms=[_JWT_ALGORITHM])
        return True
    except Exception:
        return False


def require_auth(authorization: Optional[str] = Header(default=None)) -> None:
    """
    FastAPI dependency — enforces Bearer token authentication.

    Accepts a valid JWT when the login system is configured via ADMIN_PASSWORD_HASH.
    If ADMIN_PASSWORD_HASH is not configured the check is skipped (dev/local mode).
    """
    if not _ADMIN_PASSWORD_HASH:
        return  # auth disabled — login not configured

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid Authorization header. Expected: Bearer <token>",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization[len("Bearer "):].strip()

    # Try JWT first (login system)
    if _ADMIN_PASSWORD_HASH and _verify_jwt(token):
        return

    raise HTTPException(status_code=403, detail="Invalid or expired token")


# Maximum WebSocket connections allowed from a single IP
_WS_MAX_CONNECTIONS_PER_IP = 5
_ws_connections_by_ip: dict[str, int] = defaultdict(int)


def check_ws_token(token: Optional[str]) -> bool:
    """
    Validate a WebSocket connection token (passed as ?token= query param).
    Returns True if the connection is allowed.
    """
    if not _ADMIN_PASSWORD_HASH:
        return True  # auth disabled
    if not token:
        return False
    return _verify_jwt(token)


def ws_connection_acquire(ip: str) -> bool:
    """Track a new WS connection for ip. Returns False if limit exceeded."""
    if _ws_connections_by_ip[ip] >= _WS_MAX_CONNECTIONS_PER_IP:
        return False
    _ws_connections_by_ip[ip] += 1
    return True


def ws_connection_release(ip: str) -> None:
    """Release a WS connection slot for ip."""
    if _ws_connections_by_ip[ip] > 0:
        _ws_connections_by_ip[ip] -= 1


# ---------------------------------------------------------------------------
# Input validation helpers
# ---------------------------------------------------------------------------
_SYMBOL_RE = re.compile(r"^[A-Z0-9]{1,10}/[A-Z0-9]{1,10}$")
_VALID_STATUSES = frozenset({"open", "closed", "cancelled"})
_VALID_STRATEGY_IDS = frozenset({"rsi", "macd", "bollinger", "scalping", "pairs"})


def validate_symbol(symbol: str) -> str:
    """Ensure symbol matches pattern like BTC/USDT, ETH/USDT, etc."""
    if not _SYMBOL_RE.match(symbol.upper()):
        raise HTTPException(
            status_code=422,
            detail=f"Invalid symbol format '{symbol}'. Expected format: BASE/QUOTE (e.g. BTC/USDT)",
        )
    return symbol.upper()


def validate_status(status: Optional[str]) -> Optional[str]:
    """Ensure status is one of the allowed trade status values."""
    if status is None:
        return None
    if status not in _VALID_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid status '{status}'. Must be one of: {sorted(_VALID_STATUSES)}",
        )
    return status


def validate_strategy_id(strategy_id: str) -> str:
    """Ensure strategy_id is a known strategy key."""
    if strategy_id not in _VALID_STRATEGY_IDS:
        raise HTTPException(
            status_code=404,
            detail=f"Strategy '{strategy_id}' not found",
        )
    return strategy_id


# ---------------------------------------------------------------------------
# In-memory rate limiter
# ---------------------------------------------------------------------------
class _RateLimiter:
    """
    Simple sliding-window in-memory rate limiter keyed by (client_ip, path).
    Thread-safe enough for single-process asyncio use.
    """

    def __init__(self):
        self._buckets: dict[str, list[float]] = defaultdict(list)

    def check(self, key: str, max_calls: int, window_secs: int) -> None:
        now = time.monotonic()
        bucket = self._buckets[key]
        # Evict timestamps outside the window
        self._buckets[key] = [t for t in bucket if now - t < window_secs]
        if len(self._buckets[key]) >= max_calls:
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded: max {max_calls} requests per {window_secs}s. Slow down.",
            )
        self._buckets[key].append(now)

    def evict_old(self, window_secs: int = 300) -> None:
        """Periodically purge expired buckets to prevent unbounded memory growth."""
        now = time.monotonic()
        stale = [k for k, v in self._buckets.items() if not any(now - t < window_secs for t in v)]
        for k in stale:
            del self._buckets[k]


_limiter = _RateLimiter()


def rate_limit(request: Request, max_calls: int = 10, window_secs: int = 60) -> None:
    """
    FastAPI dependency factory: apply rate limiting per (client_ip, route).
    Use via: Depends(lambda req: rate_limit(req, max_calls=5, window_secs=60))
    """
    client_ip = (request.client.host if request.client else "unknown")
    key = f"{client_ip}:{request.url.path}"
    _limiter.check(key, max_calls, window_secs)
