"""Anonymous demo sessions and their in-process planning quota."""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

DEMO_SESSION_COOKIE = "fullaura_demo_session"
SESSION_TTL_SECONDS = 24 * 60 * 60
MAX_PLAN_REQUESTS_PER_SESSION = 3


@dataclass(slots=True)
class _DemoSession:
    age_confirmed: bool
    plan_requests_used: int
    expires_at: float


@dataclass(frozen=True, slots=True)
class DemoSessionView:
    age_confirmed: bool
    plan_requests_used: int
    plan_requests_remaining: int


class DemoSessionStore:
    """Keep anonymous age confirmation and request counts in process memory."""

    def __init__(
        self,
        *,
        ttl_seconds: int = SESSION_TTL_SECONDS,
        plan_request_limit: int = MAX_PLAN_REQUESTS_PER_SESSION,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._plan_request_limit = plan_request_limit
        self._clock = clock
        self._lock = threading.Lock()
        self._sessions: dict[str, _DemoSession] = {}

    def get_or_create(self, session_id: str | None) -> tuple[str, DemoSessionView]:
        now = self._clock()
        with self._lock:
            self._remove_expired(now)
            existing = self._sessions.get(session_id) if session_id else None
            if existing is None:
                session_id = secrets.token_urlsafe(32)
                existing = _DemoSession(False, 0, now + self._ttl_seconds)
                self._sessions[session_id] = existing
            else:
                existing.expires_at = now + self._ttl_seconds
            return session_id, self._view(existing)

    def confirm_age(self, session_id: str | None) -> DemoSessionView | None:
        now = self._clock()
        with self._lock:
            self._remove_expired(now)
            session = self._sessions.get(session_id) if session_id else None
            if session is None:
                return None
            session.age_confirmed = True
            session.expires_at = now + self._ttl_seconds
            return self._view(session)

    def refresh(self, session_id: str | None) -> bool:
        """Extend a known session after any activity that carries its cookie."""
        now = self._clock()
        with self._lock:
            self._remove_expired(now)
            session = self._sessions.get(session_id) if session_id else None
            if session is None:
                return False
            session.expires_at = now + self._ttl_seconds
            return True

    def check_plan_access(self, session_id: str | None) -> tuple[str, DemoSessionView | None]:
        now = self._clock()
        with self._lock:
            self._remove_expired(now)
            session = self._sessions.get(session_id) if session_id else None
            if session is None:
                return "missing", None
            if not session.age_confirmed:
                return "age_required", self._view(session)
            if session.plan_requests_used >= self._plan_request_limit:
                return "limit", self._view(session)
            session.expires_at = now + self._ttl_seconds
            return "ok", self._view(session)

    def reserve_plan_request(self, session_id: str | None) -> tuple[str, DemoSessionView | None]:
        """Atomically check age and quota, then charge one planning submission."""
        now = self._clock()
        with self._lock:
            self._remove_expired(now)
            session = self._sessions.get(session_id) if session_id else None
            if session is None:
                return "missing", None
            if not session.age_confirmed:
                return "age_required", self._view(session)
            if session.plan_requests_used >= self._plan_request_limit:
                return "limit", self._view(session)
            session.plan_requests_used += 1
            session.expires_at = now + self._ttl_seconds
            return "ok", self._view(session)

    def _remove_expired(self, now: float) -> None:
        expired = [
            session_id
            for session_id, session in self._sessions.items()
            if session.expires_at <= now
        ]
        for session_id in expired:
            del self._sessions[session_id]

    def _view(self, session: _DemoSession) -> DemoSessionView:
        return DemoSessionView(
            age_confirmed=session.age_confirmed,
            plan_requests_used=session.plan_requests_used,
            plan_requests_remaining=max(0, self._plan_request_limit - session.plan_requests_used),
        )
