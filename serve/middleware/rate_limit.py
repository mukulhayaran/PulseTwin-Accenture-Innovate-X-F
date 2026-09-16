"""Per-role rate limiting. In-memory sliding window -- correct for the single-process
demo deployment (`--workers 1`, ADR-009); a multi-replica production deployment would move
the counters to Redis (already in the stack for the ingestion feature buffer) so limits
are shared across pods rather than per-pod. Documented, not built, per the finals brief's
own anti-pattern guidance against over-engineering pieces the demo does not exercise.

Limits are generous for interactive roles and tighter for nothing in particular -- the
real point of this middleware is a `system`-role automation loop (a monitoring script
polling /api/system/health every second) not being able to accidentally starve a human
supervisor's browser tab of connections on the same single-worker process.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware  # fastapi has no ASGI middleware base of its own

WINDOW_S = 60.0
LIMITS_PER_MINUTE = {
    "supervisor": 300,
    "manager": 300,
    "leadership": 180,
    "system": 600,   # automation (drift checks, health polling) is expected to be chattier
}
DEFAULT_LIMIT = 120


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app) -> None:
        super().__init__(app)
        self._hits: dict[str, deque] = defaultdict(deque)

    async def dispatch(self, request: Request, call_next):
        role = request.headers.get("X-PulseTwin-Role", "supervisor").strip().lower()
        client_ip = request.client.host if request.client else "unknown"
        key = f"{role}:{client_ip}"
        limit = LIMITS_PER_MINUTE.get(role, DEFAULT_LIMIT)

        now = time.time()
        hits = self._hits[key]
        while hits and now - hits[0] > WINDOW_S:
            hits.popleft()

        if len(hits) >= limit:
            return JSONResponse(
                {"detail": f"rate limit exceeded for role '{role}': {limit}/min"},
                status_code=429,
                headers={"Retry-After": str(int(WINDOW_S - (now - hits[0])))},
            )

        hits.append(now)
        return await call_next(request)
