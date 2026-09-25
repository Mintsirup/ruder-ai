"""LaunchCore IPC 클라이언트.

/tmp/launchcore.sock 에 라인 단위 JSON 이벤트를 보낸다.
LaunchCore가 떠 있지 않거나 소켓 연결이 끊어져도 RuderAI 본체 동작에는
전혀 영향을 주지 않도록, 모든 연결/전송 실패를 조용히 무시한다.
"""

from __future__ import annotations

import asyncio
import json


class LaunchCoreClient:
    RETRY_COOLDOWN = 5.0  # 연결 실패 시 이 시간 동안은 재연결 안 시도

    def __init__(self, socket_path: str = "/tmp/launchcore.sock"):
        self.socket_path = socket_path
        self._writer: asyncio.StreamWriter | None = None
        self._connect_lock = asyncio.Lock()
        self._unavailable_until = 0.0

    async def _ensure_connected(self) -> bool:
        if self._writer is not None:
            return True
        loop = asyncio.get_event_loop()
        now = loop.time()
        if now < self._unavailable_until:
            return False
        async with self._connect_lock:
            if self._writer is not None:
                return True
            try:
                _, writer = await asyncio.wait_for(
                    asyncio.open_unix_connection(self.socket_path), timeout=0.5,
                )
                self._writer = writer
                return True
            except Exception:
                self._unavailable_until = now + self.RETRY_COOLDOWN
                return False

    async def send(self, event: dict) -> None:
        if not await self._ensure_connected():
            return
        try:
            line = json.dumps(event, ensure_ascii=False) + "\n"
            self._writer.write(line.encode("utf-8"))
            await self._writer.drain()
        except Exception:
            self._writer = None

    async def tool_start(self, call_id: str, name: str) -> None:
        await self.send({"cmd": "tool_call", "action": "start", "id": call_id, "name": name})

    async def tool_progress(self, call_id: str, percent: int) -> None:
        await self.send({"cmd": "tool_call", "action": "progress", "id": call_id, "percent": percent})

    async def tool_end(self, call_id: str, success: bool) -> None:
        await self.send({"cmd": "tool_call", "action": "end", "id": call_id, "success": success})

    async def close(self) -> None:
        if self._writer is not None:
            try:
                self._writer.close()
            except Exception:
                pass
            self._writer = None
