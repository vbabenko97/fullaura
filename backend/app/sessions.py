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
from typing import Any

from backend.app.concepts import Concept
from backend.app.planner import Brief
from backend.app.state import State

SESSION_COOKIE = "fullaura_session"
SESSION_COOKIE_MAX_AGE_S = 30 * 24 * 60 * 60
PLAN_TTL_S = 24 * 60 * 60
PLANS_PER_INVITE = 10  # browser sessions whose plans one invite keeps at a time
MAX_PLANS = 500


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
    if not hmac.compare_digest(mac.encode(), _mac(secret, invite_id, key).encode()):
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


@dataclass(frozen=True, slots=True)
class ConceptRecord:
    """A Design My Look concept and the response that showed it, for a try-on and a repeat."""

    token: str
    concept: Concept
    payload: dict[str, Any]  # the response body, without the token
    operation_id: str


class _SessionMemory[R: (PlanRecord, ConceptRecord)]:
    """The last record of each browser session, in process memory only, bounded per invite and
    in total. A new record gets a new token, and the previous token stops working."""

    def __init__(
        self,
        *,
        ttl_s: float = PLAN_TTL_S,
        per_invite: int = PLANS_PER_INVITE,
        limit: int = MAX_PLANS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl_s = ttl_s
        self._per_invite = per_invite
        self._limit = limit
        self._clock = clock
        self._lock = threading.Lock()
        # Keyed by browser session; the invite ID lets one invite's sessions be bounded.
        self._records: dict[str, tuple[R, float, str]] = {}

    def get(self, session: Session) -> R | None:
        now = self._clock()
        with self._lock:
            self._expire(now)
            entry = self._records.get(session.key)
            if entry is None:
                return None
            self._records[session.key] = (entry[0], now + self._ttl_s, entry[2])
            return entry[0]

    def current(self, session: Session, token: str | None) -> R | None:
        """The session's record when the token names it; None for a stale or foreign token."""
        record = self.get(session)
        if record is None or token is None:
            return None
        if not hmac.compare_digest(record.token.encode(), token.encode()):
            return None
        return record

    def forget(self, session: Session) -> None:
        with self._lock:
            self._records.pop(session.key, None)

    def _put(self, session: Session, make: Callable[[R | None], R]) -> R:
        now = self._clock()
        with self._lock:
            self._expire(now)
            previous = self._records.get(session.key)
            record = make(previous[0] if previous else None)
            self._records[session.key] = (record, now + self._ttl_s, session.invite_id)
            self._bound(session.invite_id)
            return record

    def _expire(self, now: float) -> None:
        for key in [key for key, (_, expires, _) in self._records.items() if expires <= now]:
            del self._records[key]

    def _bound(self, invite_id: str) -> None:
        """Drop the records idle longest once an invite, or the process, holds too many."""
        mine = sorted(
            (expires, key)
            for key, (_, expires, owner) in self._records.items()
            if owner == invite_id
        )
        for _, key in mine[: max(0, len(mine) - self._per_invite)]:
            del self._records[key]
        everyone = sorted((expires, key) for key, (_, expires, _) in self._records.items())
        for _, key in everyone[: max(0, len(everyone) - self._limit)]:
            del self._records[key]


class PlanMemory(_SessionMemory[PlanRecord]):
    """The last checked plan of each browser session."""

    def store(
        self,
        session: Session,
        brief: Brief,
        state: State,
        catalog_id: str,
        operation_id: str | None,
    ) -> PlanRecord:
        """Keep a newly checked plan under a new token; the previous token stops working."""
        return self._put(
            session,
            lambda previous: PlanRecord(
                token=secrets.token_urlsafe(32),
                version=previous.version + 1 if previous else 1,
                brief=brief,
                state=state,
                catalog_id=catalog_id,
                operation_id=operation_id,
            ),
        )


class ConceptMemory(_SessionMemory[ConceptRecord]):
    """The last Design My Look concept of each browser session. A new concept replaces the
    previous one, so a try-on can name only the concept the page shows."""

    def store(
        self, session: Session, concept: Concept, payload: dict[str, Any], operation_id: str
    ) -> ConceptRecord:
        return self._put(
            session,
            lambda _: ConceptRecord(secrets.token_urlsafe(32), concept, payload, operation_id),
        )
