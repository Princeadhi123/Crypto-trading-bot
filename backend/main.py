import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Set

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from api.routes import router
from models.database import init_database
from engine.trading_engine import trading_engine

load_dotenv()

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


@asynccontextmanager
async def application_lifespan(app: FastAPI):
    logger.info("Initializing database...")
    await init_database()
    trading_engine.set_broadcast_callback(broadcast_event)

    exchange_name = os.getenv("EXCHANGE_NAME", "binance")
    api_key = os.getenv("API_KEY", "")
    api_secret = os.getenv("API_SECRET", "")
    if api_key and api_secret:
        await trading_engine.initialize_exchange(exchange_name, api_key, api_secret)

    logger.info("Trading Bot API started")
    yield
    logger.info("Shutting down trading engine...")
    await trading_engine.stop()


app = FastAPI(
    title="Crypto Trading Bot API",
    description="Automated high-frequency cryptocurrency trading bot with risk management",
    version="1.0.0",
    lifespan=application_lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await connection_manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            try:
                message = json.loads(data)
                if message.get("action") == "ping":
                    await websocket.send_text(json.dumps({"event": "pong", "data": {}}))
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        connection_manager.disconnect(websocket)


@app.get("/health")
async def health_check():
    return {"status": "ok", "engine_running": trading_engine.is_running}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
