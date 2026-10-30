"""Invite sessions and each browser session's last checked plan.

The session cookie names an invite and a random browser-session key, signed with the server's
secret, so a restart keeps people signed in while their quotas stay in the pilot store. The
last checked plan lives only in process memory: its opaque token lets a later update, a
replacement or a try-on refer to it without the browser sending a plan, and a restart forgets
it, after which the browser restores its saved copy through a fresh check.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from backend.app.planner import Brief
from backend.app.state import State

SESSION_COOKIE = "fullaura_session"
SESSION_COOKIE_MAX_AGE_S = 30 * 24 * 60 * 60
PLAN_TTL_S = 24 * 60 * 60


@dataclass(frozen=True, slots=True)
class Session:
    invite_id: str
    key: str  # one browser session; plans are kept per key


def sign_session(secret: bytes, invite_id: str) -> str:
    key = secrets.token_urlsafe(24)
    return f"{invite_id}.{key}.{_mac(secret, invite_id, key)}"


def read_session(secret: bytes, cookie: str | None) -> Session | None:
    if not cookie or cookie.count(".") != 2:
        return None
    invite_id, key, mac = cookie.split(".")
    if not hmac.compare_digest(mac, _mac(secret, invite_id, key)):
        return None
    return Session(invite_id, key)


def _mac(secret: bytes, invite_id: str, key: str) -> str:
    return hmac.new(secret, f"{invite_id}.{key}".encode(), hashlib.sha256).hexdigest()


@dataclass(frozen=True, slots=True)
class PlanRecord:
    """A plan that passed every check, with the state and brief it was checked against."""

    token: str
    version: int
    brief: Brief
    state: State  # state.plan is the checked plan
    catalog_id: str
    operation_id: str | None  # the planning request that produced it; None for a restore


class PlanMemory:
    """The last checked plan of each browser session, in process memory only."""

    def __init__(
        self, *, ttl_s: float = PLAN_TTL_S, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._ttl_s = ttl_s
        self._clock = clock
        self._lock = threading.Lock()
        self._plans: dict[str, tuple[PlanRecord, float]] = {}

    def get(self, session: Session) -> PlanRecord | None:
        now = self._clock()
        with self._lock:
            self._expire(now)
            entry = self._plans.get(session.key)
            if entry is None:
                return None
            self._plans[session.key] = (entry[0], now + self._ttl_s)
            return entry[0]

    def current(self, session: Session, token: str | None) -> PlanRecord | None:
        """The session's plan when the token names it; None for a stale or foreign token."""
        record = self.get(session)
        if record is None or token is None or not hmac.compare_digest(record.token, token):
            return None
        return record

    def store(
        self,
        session: Session,
        brief: Brief,
        state: State,
        catalog_id: str,
        operation_id: str | None,
    ) -> PlanRecord:
        """Keep a newly checked plan under a new token; the previous token stops working."""
        now = self._clock()
        with self._lock:
            self._expire(now)
            previous = self._plans.get(session.key)
            record = PlanRecord(
                token=secrets.token_urlsafe(32),
                version=previous[0].version + 1 if previous else 1,
                brief=brief,
                state=state,
                catalog_id=catalog_id,
                operation_id=operation_id,
            )
            self._plans[session.key] = (record, now + self._ttl_s)
            return record

    def forget(self, session: Session) -> None:
        with self._lock:
            self._plans.pop(session.key, None)

    def _expire(self, now: float) -> None:
        for key in [key for key, (_, expires) in self._plans.items() if expires <= now]:
            del self._plans[key]
