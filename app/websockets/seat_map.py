"""WS /ws/shows/{show_id}/seats?token=<JWT>"""
import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.database import SessionLocal
from app.models import Event, Show
from app.auth.dependencies import user_from_token
from app.services.seat_service import seat_map_for_viewer
from app.utils.constants import EventStatus, Role
from app.utils.errors import Unauthorized
from app.websockets.manager import manager

router = APIRouter()
logger = logging.getLogger("app.ws")

WS_UNAUTHORIZED = 4401
WS_FORBIDDEN = 4403
WS_NOT_FOUND = 4404


def _authorise(show_id: int, token: str):
    """Returns (user_id, initial_message) or (None, close_code)."""
    db = SessionLocal()
    try:
        try:
            user = user_from_token(db, token)
        except Unauthorized:
            return None, WS_UNAUTHORIZED
        show = db.get(Show, show_id)
        if show is None:
            return None, WS_NOT_FOUND
        event = db.get(Event, show.event_id)
        privileged = user.role == Role.ADMIN.value or (user.role == Role.ORGANIZER.value and event.organizer_id == user.id)
        if event.status != EventStatus.PUBLISHED.value and not privileged:
            return None, WS_FORBIDDEN
        message = {"type": "seat_map", "viewer_id": user.id, **seat_map_for_viewer(db, show, user.id, mark_yours=True)}
        return user.id, message
    finally:
        db.close()


@router.websocket("/ws/shows/{show_id}/seats")
async def seat_updates(websocket: WebSocket, show_id: int, token: str = ""):
    if not token:
        await websocket.close(code=WS_UNAUTHORIZED)
        return
    loop = asyncio.get_running_loop()
    user_id, result = await loop.run_in_executor(None, _authorise, show_id, token)
    if user_id is None:
        await websocket.close(code=result)
        return

    await manager.connect(show_id, websocket, user_id)
    try:
        await websocket.send_json(result)  # complete current seat map first
        while True:
            text = await websocket.receive_text()  # keeps the socket open; also answers pings
            if text.strip().lower() == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.debug("websocket closed with error", exc_info=True)
    finally:
        manager.disconnect(show_id, websocket)
