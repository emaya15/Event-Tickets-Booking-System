"""WebSocket connection manager for the live seat map.

* Messages are small deltas ({seat_id, status}); the full map is sent once on connect.
* Seats locked by the connected customer are reported as "Yours".
* Sync code (routers, scheduler thread) publishes through ``publish_*`` which hops onto the
  server's event loop. A lightweight watcher also diffs Redis/DB state every few seconds so
  lock EXPIRY (which Redis does silently) and changes made by other workers reach clients.

Scaling note: connections live in the memory of one process. With several Uvicorn workers a
client only receives instant updates from its own worker, but the watcher still delivers
everything else within WATCH_INTERVAL seconds.
"""
import asyncio
import logging
from typing import Dict, List, Optional, Tuple

from fastapi import WebSocket

from app.database import SessionLocal
from app.models import Show
from app.utils.constants import SeatState

logger = logging.getLogger("app.ws")

WATCH_INTERVAL = 3.0


class ConnectionManager:
    def __init__(self) -> None:
        self.connections: Dict[int, List[Tuple[WebSocket, int]]] = {}
        self.snapshots: Dict[int, Dict[int, Tuple[str, Optional[int]]]] = {}
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._watcher: Optional[asyncio.Task] = None

    # ----------------------------------------------------------- lifecycle
    def start(self) -> None:
        self.loop = asyncio.get_running_loop()
        self._watcher = self.loop.create_task(self._watch())

    async def stop(self) -> None:
        if self._watcher:
            self._watcher.cancel()
            try:
                await self._watcher
            except (asyncio.CancelledError, Exception):
                pass
        for conns in list(self.connections.values()):
            for ws, _ in conns:
                try:
                    await ws.close()
                except Exception:
                    pass
        self.connections.clear()
        self.snapshots.clear()
        self.loop = None

    # --------------------------------------------------------- connections
    async def connect(self, show_id: int, ws: WebSocket, user_id: int) -> None:
        await ws.accept()
        self.connections.setdefault(show_id, []).append((ws, user_id))

    def disconnect(self, show_id: int, ws: WebSocket) -> None:
        conns = self.connections.get(show_id, [])
        self.connections[show_id] = [c for c in conns if c[0] is not ws]
        if not self.connections[show_id]:
            self.connections.pop(show_id, None)
            self.snapshots.pop(show_id, None)

    # ------------------------------------------------------------ sending
    @staticmethod
    def _personalise(updates: List[dict], viewer_id: int) -> List[dict]:
        out = []
        for u in updates:
            status = u["status"]
            if status == SeatState.LOCKED.value and u.get("owner_id") == viewer_id:
                status = SeatState.YOURS.value
            out.append({"seat_id": u["seat_id"], "status": status})
        return out

    async def _safe_send(self, show_id: int, ws: WebSocket, payload: dict) -> None:
        try:
            await ws.send_json(payload)
        except Exception:  # client went away mid-send: drop it quietly
            self.disconnect(show_id, ws)

    async def broadcast_updates(self, show_id: int, event: str, updates: List[dict]) -> None:
        snap = self.snapshots.setdefault(show_id, {})
        for u in updates:
            snap[u["seat_id"]] = (u["status"], u.get("owner_id"))
        for ws, user_id in list(self.connections.get(show_id, [])):
            await self._safe_send(
                show_id, ws, {"type": "seat_update", "event": event, "seats": self._personalise(updates, user_id)}
            )

    async def broadcast_json(self, show_id: int, payload: dict) -> None:
        for ws, _ in list(self.connections.get(show_id, [])):
            await self._safe_send(show_id, ws, payload)

    async def send_to_user(self, show_id: int, user_id: int, payload: dict) -> None:
        for ws, uid in list(self.connections.get(show_id, [])):
            if uid == user_id:
                await self._safe_send(show_id, ws, payload)

    async def send_to_user_anywhere(self, user_id: int, payload: dict) -> None:
        for show_id in list(self.connections):
            await self.send_to_user(show_id, user_id, payload)

    # ------------------------------------------------------ sync publishers
    def _submit(self, coro) -> None:
        loop = self.loop
        if loop is None or loop.is_closed():
            coro.close()
            return
        try:
            asyncio.run_coroutine_threadsafe(coro, loop)
        except RuntimeError:  # loop shutting down
            coro.close()

    def publish_seats(self, show_id: int, event: str, seat_ids: List[int], status: str, owner_id: Optional[int] = None):
        """Call AFTER the Redis/DB change has succeeded."""
        if not seat_ids or show_id not in self.connections:
            return
        updates = [{"seat_id": sid, "status": status, "owner_id": owner_id} for sid in seat_ids]
        self._submit(self.broadcast_updates(show_id, event, updates))

    def publish_show_cancelled(self, show_id: int, message: str):
        self._submit(self.broadcast_json(show_id, {"type": "show_cancelled", "message": message}))

    def publish_user_notification(self, user_id: int, message: str, kind: str = "info"):
        self._submit(self.send_to_user_anywhere(user_id, {"type": "notification", "kind": kind, "message": message}))

    # -------------------------------------------------------------- watcher
    def _snapshot_for(self, show_id: int) -> Optional[Dict[int, Tuple[str, Optional[int]]]]:
        from app.services.seat_service import compute_seat_states  # local import avoids a cycle

        db = SessionLocal()
        try:
            show = db.get(Show, show_id)
            if show is None:
                return None
            return {s["seat_id"]: (s["status"], s["owner_id"]) for s in compute_seat_states(db, show)}
        finally:
            db.close()

    async def _watch(self) -> None:
        while True:
            try:
                await asyncio.sleep(WATCH_INTERVAL)
                for show_id in list(self.connections):
                    fresh = await asyncio.get_running_loop().run_in_executor(None, self._snapshot_for, show_id)
                    if fresh is None:
                        continue
                    old = self.snapshots.get(show_id)
                    self.snapshots[show_id] = fresh
                    if old is None:
                        continue
                    changed = [
                        {"seat_id": sid, "status": st, "owner_id": owner}
                        for sid, (st, owner) in fresh.items()
                        if old.get(sid) != (st, owner)
                    ]
                    if changed:
                        event = "expired" if all(
                            old.get(c["seat_id"], ("", None))[0] == SeatState.LOCKED.value
                            and c["status"] == SeatState.AVAILABLE.value
                            for c in changed
                        ) else "updated"
                        for ws, user_id in list(self.connections.get(show_id, [])):
                            await self._safe_send(
                                show_id,
                                ws,
                                {"type": "seat_update", "event": event, "seats": self._personalise(changed, user_id)},
                            )
            except asyncio.CancelledError:
                raise
            except Exception:  # keep the watcher alive no matter what
                logger.exception("seat watcher iteration failed")


manager = ConnectionManager()
