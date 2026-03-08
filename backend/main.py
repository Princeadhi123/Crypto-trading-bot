import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Optional, Set

import ccxt.async_support as ccxt

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from api.routes import router
from api.auth import check_ws_token, ws_connection_acquire, ws_connection_release, _limiter
from api.auth_routes import router as auth_router
from models.database import init_database
from engine.trading_engine import trading_engine

load_dotenv()

if os.name == "nt":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


class WebSocketConnectionManager:
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)
        logger.info("WebSocket connected. Total connections: %d", len(self.active_connections))

    def disconnect(self, websocket: WebSocket):
        self.active_connections.discard(websocket)
        logger.info("WebSocket disconnected. Total connections: %d", len(self.active_connections))

    async def broadcast(self, event_type: str, data: dict):
        if not self.active_connections:
            return
        message = json.dumps({"event": event_type, "data": data})
        disconnected = set()
        for connection in self.active_connections.copy():
            try:
                await connection.send_text(message)
            except Exception:
                disconnected.add(connection)
        for connection in disconnected:
            self.disconnect(connection)


connection_manager = WebSocketConnectionManager()


async def broadcast_event(event_type: str, data: dict):
    await connection_manager.broadcast(event_type, data)


PRICE_SYMBOLS = ["BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "DOGE/USDT", "XRP/USDT", "AVAX/USDT", "MATIC/USDT"]


async def _live_price_refresh_loop():
    """Background task: fetches real-time prices from Binance public API every 1s.
    Runs independently of the trading bot — prices stay current even when bot is stopped."""
    public_exchange = ccxt.binance({"options": {"defaultType": "spot"}})
    logger.info("Live price refresh loop started")
    try:
        while True:
            try:
                tickers = await public_exchange.fetch_tickers(PRICE_SYMBOLS)
                fresh = {sym: float(tickers[sym]["last"]) for sym in PRICE_SYMBOLS if sym in tickers and tickers[sym]["last"]}
                if fresh:
                    trading_engine.market_prices.update(fresh)
                    logger.debug("Prices refreshed: BTC=%.2f ETH=%.2f", fresh.get("BTC/USDT", 0), fresh.get("ETH/USDT", 0))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Live price refresh failed: %s", exc)
            await asyncio.sleep(1)  # 1 second for real-time updates
    finally:
        try:
            await public_exchange.close()
        except Exception:
            pass
        logger.info("Live price refresh loop stopped")


@asynccontextmanager
async def application_lifespan(app: FastAPI):
    logger.info("Initializing database...")
    await init_database()
    trading_engine.set_broadcast_callback(broadcast_event)
    
    # Load paper balance from DB so dashboard shows correct value before bot starts
    await trading_engine.load_initial_balance()

    exchange_name = os.getenv("EXCHANGE_NAME", "binance")
    api_key = os.getenv("API_KEY", "")
    api_secret = os.getenv("API_SECRET", "")
    if api_key and api_secret:
        await trading_engine.initialize_exchange(exchange_name, api_key, api_secret)

    price_task = asyncio.create_task(_live_price_refresh_loop())

    async def _rate_limiter_eviction_loop():
        """Purge stale rate limiter buckets every 5 minutes to prevent unbounded memory growth."""
        while True:
            await asyncio.sleep(300)
            _limiter.evict_old()

    eviction_task = asyncio.create_task(_rate_limiter_eviction_loop())
    logger.info("Trading Bot API started")
    yield
    price_task.cancel()
    eviction_task.cancel()
    logger.info("Shutting down trading engine...")
    await trading_engine.stop()


_enable_docs = os.getenv("ENABLE_DOCS", "false").strip().lower() == "true"

# Maximum request body size (bytes) — prevents memory exhaustion via large POST/PUT payloads
_MAX_BODY_BYTES = 64 * 1024  # 64 KB is ample for any settings payload


class _BodySizeLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > _MAX_BODY_BYTES:
                    return JSONResponse(status_code=413, content={"detail": "Request body too large"})
            except ValueError:
                return JSONResponse(status_code=400, content={"detail": "Invalid Content-Length header"})
        return await call_next(request)


class _SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach security headers to every HTTP response."""
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response


app = FastAPI(
    title="Crypto Trading Bot API",
    description="Automated high-frequency cryptocurrency trading bot with risk management",
    version="1.0.0",
    lifespan=application_lifespan,
    # Disable interactive docs unless explicitly enabled via ENABLE_DOCS=true
    docs_url="/docs" if _enable_docs else None,
    redoc_url="/redoc" if _enable_docs else None,
    openapi_url="/openapi.json" if _enable_docs else None,
)


@app.exception_handler(Exception)
async def _generic_error_handler(request: Request, exc: Exception):
    """Catch-all: never leak Python tracebacks or internal details to the client."""
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


app.add_middleware(_SecurityHeadersMiddleware)
app.add_middleware(_BodySizeLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173", "https://localhost:5173",
        "http://localhost:3000", "https://localhost:3000",
        "http://127.0.0.1:5173", "https://127.0.0.1:5173",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
)

app.include_router(router, prefix="/api")
app.include_router(auth_router, prefix="/api")


# Maximum size of an incoming WebSocket message (bytes) — prevents memory exhaustion
_WS_MAX_MESSAGE_BYTES = 1024  # 1 KB is ample for a ping


@app.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    token: Optional[str] = Query(default=None),
):
    client_ip = websocket.client.host if websocket.client else "unknown"

    if not check_ws_token(token):
        await websocket.close(code=4001)
        logger.warning("WebSocket rejected: invalid token from %s", client_ip)
        return

    if not ws_connection_acquire(client_ip):
        await websocket.close(code=4008)
        logger.warning("WebSocket rejected: connection limit reached for %s", client_ip)
        return

    await connection_manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            if len(data) > _WS_MAX_MESSAGE_BYTES:
                logger.warning("WebSocket message too large (%d bytes) — dropped", len(data))
                continue
            try:
                message = json.loads(data)
                if message.get("action") == "ping":
                    await websocket.send_text(json.dumps({"event": "pong", "data": {}}))
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        pass
    finally:
        connection_manager.disconnect(websocket)
        ws_connection_release(client_ip)


@app.get("/health")
async def health_check():
    # Only expose a minimal liveness signal — no internal state
    return {"status": "ok"}


if __name__ == "__main__":
    _host = os.getenv("HOST", "127.0.0.1")
    _port = int(os.getenv("PORT", "8000"))
    # reload=True is for development only — set RELOAD=false to disable
    _reload = os.getenv("RELOAD", "false").strip().lower() == "true"
    _ssl_certfile = os.getenv("SSL_CERTFILE", "").strip() or None
    _ssl_keyfile = os.getenv("SSL_KEYFILE", "").strip() or None
    _ssl_kwargs = {}
    if _ssl_certfile and _ssl_keyfile:
        _ssl_kwargs = {"ssl_certfile": _ssl_certfile, "ssl_keyfile": _ssl_keyfile}
        logger.info("SSL enabled: %s", _ssl_certfile)
    uvicorn.run("main:app", host=_host, port=_port, reload=_reload, **_ssl_kwargs)
