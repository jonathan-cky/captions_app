"""
ws_server.py — WebSocket server that broadcasts transcription results
               to the Electron overlay window in real time.

Protocol (JSON over WebSocket):
    Server → Client:
        { "type": "caption",  "text": "...", "language": "en", "confidence": 0.95 }
        { "type": "status",   "state": "listening" | "processing" | "idle" }
        { "type": "error",    "message": "..." }

    Client → Server:
        { "type": "ping" }
        { "type": "set_model", "model": "small" | "medium" | "large-v3" }
"""

import asyncio
import json
import logging
import websockets
from websockets.server import WebSocketServerProtocol
from typing import Set

logger = logging.getLogger(__name__)

WS_HOST = "127.0.0.1"
WS_PORT = 8765


class CaptionServer:
    """
    Manages connected WebSocket clients and broadcasts caption events.
    Designed to run in the asyncio event loop alongside the main pipeline.
    """

    def __init__(self):
        self._clients:  Set[WebSocketServerProtocol] = set()
        self._server    = None
        self._loop:     asyncio.AbstractEventLoop | None = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self):
        self._loop   = asyncio.get_running_loop()
        self._server = await websockets.serve(
            self._handler,
            WS_HOST,
            WS_PORT,
            ping_interval=20,
            ping_timeout=10,
        )
        logger.info(f"WebSocket server listening on ws://{WS_HOST}:{WS_PORT}")

    async def stop(self):
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    # ── Client handler ────────────────────────────────────────────────────────

    async def _handler(self, ws: WebSocketServerProtocol):
        self._clients.add(ws)
        logger.info(f"Client connected ({len(self._clients)} total)")
        await self.send_status("listening", ws)

        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                    await self._handle_client_message(msg, ws)
                except json.JSONDecodeError:
                    pass
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self._clients.discard(ws)
            logger.info(f"Client disconnected ({len(self._clients)} remaining)")

    async def _handle_client_message(self, msg: dict, ws: WebSocketServerProtocol):
        t = msg.get("type")
        if t == "ping":
            await ws.send(json.dumps({"type": "pong"}))

    # ── Broadcast helpers ─────────────────────────────────────────────────────

    def broadcast_caption(self, text: str, language: str, confidence: float):
        """Thread-safe — can be called from synchronous transcription thread."""
        if not self._clients or not self._loop:
            return
        payload = json.dumps({
            "type":       "caption",
            "text":       text,
            "language":   language,
            "confidence": round(confidence, 3),
        })
        asyncio.run_coroutine_threadsafe(self._broadcast(payload), self._loop)

    def broadcast_status(self, state: str):
        """Broadcast pipeline state: listening | processing | idle."""
        if not self._clients or not self._loop:
            return
        payload = json.dumps({"type": "status", "state": state})
        asyncio.run_coroutine_threadsafe(self._broadcast(payload), self._loop)

    async def send_status(self, state: str, ws: WebSocketServerProtocol):
        await ws.send(json.dumps({"type": "status", "state": state}))

    async def _broadcast(self, payload: str):
        if not self._clients:
            return
        dead = set()
        for ws in self._clients:
            try:
                await ws.send(payload)
            except websockets.exceptions.ConnectionClosed:
                dead.add(ws)
        self._clients -= dead
