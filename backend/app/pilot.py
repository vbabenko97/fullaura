"""The closed beta's invites, per-invite quotas, shared spending ledger and feedback.

A Store keeps them, so counts and money survive a restart. Before each model call, begin()
checks the invite, reserves the call's conservative maximum cost, counts the call against the
invite's quota and marks it running; finish() replaces the reservation with the actual cost, or
keeps the whole reservation when the outcome is unknown. Money and running operations share one
ledger document, so a reservation, its release and the one-running-operation rule change in one
conditional write. A running operation blocks its invite until it finishes or is older than any
call can last, whichever process started it, because during a deployment the old revision keeps
running for a while. An operation that outlived that limit is interrupted: it is never retried,
and its reservation counts as spent.

Every write goes through ClosingStore, which coordinates with the purge job (backend/app/purge.py)
across processes. Right before each create, replace or delete it refuses once the purge time has
passed or the job's PURGE_MARKER exists, and it trusts a marker check for WRITE_WINDOW_S only.
The job writes the marker, waits longer than any write that passed the check can take, and only
then deletes the records, so a write either is refused or lands before the deletion.

Nothing here holds a photo, preview, full plan, provider response or anything a person typed
into the plan form; feedback holds only the answers to the feedback form.
"""

from __future__ import annotations

import copy
import hashlib
import logging
import math
import secrets
import threading
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from backend.app.store import Document, Store, StoreUnavailable, VersionConflict

LEDGER = "ledger.json"
MAX_WRITE_ATTEMPTS = 8
# Written by the purge job before it deletes anything, and never deleted by it; it holds only
# when the deletion began and the configured purge time.
PURGE_MARKER = "purge-marker.json"
WRITE_WINDOW_S = 5.0  # how long a check that found no marker allows a write to start

# List prices, checked 2026-10-30 (docs/BETA.md#costs). Costs are in micro-dollars.
PLANNING_INPUT_MICRO_USD_PER_TOKEN = 1.25  # Baseten, Muse Spark Open: $1.25 per million
PLANNING_OUTPUT_MICRO_USD_PER_TOKEN = 4.25  # $4.25 per million
PLANNING_PROMPT_TOKEN_CEILING = 24_000  # per HTTP attempt, including a repair's history
IMAGE_MICRO_USD = 10_000  # Meta, Muse Image: $0.01 per generated image

logger = logging.getLogger(__name__)


class OperationKind(StrEnum):
    PLAN = "plan"
    PREVIEW = "preview"


class Budget(StrEnum):
    PLANNING = "planning"
    IMAGES = "images"


BUDGET_OF = {OperationKind.PLAN: Budget.PLANNING, OperationKind.PREVIEW: Budget.IMAGES}


class Refusal(StrEnum):
    AGE_REQUIRED = "age_required"
    PHOTO_CONSENT_REQUIRED = "photo_consent_required"
    QUOTA = "quota"
    BUSY = "busy"
    BUDGET = "budget"
    DUPLICATE = "duplicate"
    PILOT_ENDED = "pilot_ended"
    ACCOUNTING_UNAVAILABLE = "accounting_unavailable"


class Refused(Exception):
    def __init__(self, reason: Refusal, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


def planning_reserve_micro_usd(http_attempts: int, max_tokens: int) -> int:
    """The most a planning request can cost: every HTTP attempt at the prompt ceiling and the
    completion cap."""
    return http_attempts * _planning_attempt_micro_usd(max_tokens)


def planning_cost_micro_usd(
    prompt_tokens: int, completion_tokens: int, calls: int, http_attempts: int, max_tokens: int
) -> int:
    """Reported tokens at list price, plus the maximum for each HTTP attempt that returned no
    usage, because the provider may still have billed it."""
    tokens = (
        prompt_tokens * PLANNING_INPUT_MICRO_USD_PER_TOKEN
        + completion_tokens * PLANNING_OUTPUT_MICRO_USD_PER_TOKEN
    )
    unknown = max(0, http_attempts - calls) * _planning_attempt_micro_usd(max_tokens)
    return math.ceil(tokens) + unknown


def _planning_attempt_micro_usd(max_tokens: int) -> int:
    return math.ceil(
        PLANNING_PROMPT_TOKEN_CEILING * PLANNING_INPUT_MICRO_USD_PER_TOKEN
        + max_tokens * PLANNING_OUTPUT_MICRO_USD_PER_TOKEN
    )


@dataclass(frozen=True, slots=True)
class PilotLimits:
    plan_requests: int = 8  # per invite
    preview_requests: int = 12  # per invite
    budgets_micro_usd: tuple[tuple[Budget, int], ...] = (
        (Budget.PLANNING, 8_000_000),
        (Budget.IMAGES, 3_000_000),
    )
    ends_at: datetime | None = None
    records_purge_at: datetime | None = None  # after ends_at (backend/app/config.py)
    running_timeout_s: float = 300.0  # longer than the planning and preview deadlines

    def budget(self, budget: Budget) -> int:
        return dict(self.budgets_micro_usd)[budget]


@dataclass(frozen=True, slots=True)
class InviteStatus:
    age_confirmed: bool
    photo_consent: bool
    plan_requests_used: int
    preview_requests_used: int
    feedback_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Operation:
    id: str
    invite_id: str
    kind: OperationKind
    reserved_micro_usd: int


def invite_hash(code: str) -> str:
    return hashlib.sha256(code.strip().encode()).hexdigest()


def new_invite_code() -> str:
    return secrets.token_urlsafe(18)


class RecordsClosed(StoreUnavailable):
    """The pilot records are deleted or about to be, so nothing may be written. A subclass of
    StoreUnavailable, so every caller already refuses safely."""


class ClosingStore:
    """The pilot's store, with every write refused once the records are closed for deletion.

    Reads and listings pass through. A write first checks the purge time and the purge job's
    marker, then starts only if that check took less than WRITE_WINDOW_S; the job waits longer
    than that window plus the store's timeouts before it deletes (backend/app/purge.py)."""

    def __init__(
        self,
        store: Store,
        purge_at: datetime | None,
        now: Callable[[], datetime],
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = store
        self._purge_at = purge_at
        self._now = now
        self._monotonic = monotonic

    def read(self, name: str) -> Document | None:
        return self._store.read(name)

    def names(self) -> list[str]:
        return self._store.names()

    def create(self, name: str, data: dict[str, Any]) -> str:
        self._check_open()
        return self._store.create(name, data)

    def replace(self, name: str, data: dict[str, Any], etag: str) -> str:
        self._check_open()
        return self._store.replace(name, data, etag)

    def delete(self, name: str) -> None:
        self._check_open()
        self._store.delete(name)

    def _check_open(self) -> None:
        started = self._monotonic()
        if self._purge_at is not None and self._now() >= self._purge_at:
            raise RecordsClosed("The pilot records are closed for deletion")
        if self._store.read(PURGE_MARKER) is not None:
            raise RecordsClosed("The pilot records are closed for deletion")
        if self._monotonic() - started > WRITE_WINDOW_S:
            raise StoreUnavailable("The check for a running deletion took too long to trust")


def _empty_ledger() -> dict[str, Any]:
    return {
        "spent": {budget.value: 0 for budget in Budget},
        "reserved": {budget.value: 0 for budget in Budget},
        "running": {},
    }


def _empty_invite() -> dict[str, Any]:
    return {
        "age_confirmed_at": None,
        "photo_consent_at": None,
        "plan_requests": 0,
        "preview_requests": 0,
        "feedback": [],
    }


class Pilot:
    def __init__(
        self,
        store: Store,
        limits: PilotLimits,
        invite_hashes: Iterable[str],
        *,
        instance_id: str | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        # Every write below, including ETag retries and late finishes, passes this check.
        self._store = ClosingStore(store, limits.records_purge_at, now)
        self.limits = limits
        self._invites = {digest.lower(): digest.lower()[:16] for digest in invite_hashes}
        self.instance_id = instance_id or uuid.uuid4().hex
        self._now = now
        # Serializes this process's writes; ETags still guard against any other writer.
        self._write_lock = threading.Lock()

    def invite_id(self, code: str) -> str | None:
        """The invite a code opens, or None. Only hashes of the codes are configured."""
        return self._invites.get(invite_hash(code))

    def knows(self, invite_id: str) -> bool:
        return invite_id in self._invites.values()

    def ended(self) -> bool:
        return self.limits.ends_at is not None and self._now() >= self.limits.ends_at

    def purged(self) -> bool:
        """The records are deleted, or about to be (backend/app/purge.py), so nothing new may
        be written."""
        purge_at = self.limits.records_purge_at
        return purge_at is not None and self._now() >= purge_at

    # Invite records

    def status(self, invite_id: str) -> InviteStatus:
        document = self._store.read(self._invite_name(invite_id))
        data = document.data if document else _empty_invite()
        return InviteStatus(
            age_confirmed=data["age_confirmed_at"] is not None,
            photo_consent=data["photo_consent_at"] is not None,
            plan_requests_used=data["plan_requests"],
            preview_requests_used=data["preview_requests"],
            feedback_ids=tuple(data["feedback"]),
        )

    def confirm_age(self, invite_id: str) -> InviteStatus:
        def confirm(data: dict[str, Any]) -> None:
            data["age_confirmed_at"] = data["age_confirmed_at"] or self._stamp()

        self._update(self._invite_name(invite_id), confirm, _empty_invite)
        return self.status(invite_id)

    def set_photo_consent(self, invite_id: str, consent: bool) -> InviteStatus:
        def record(data: dict[str, Any]) -> None:
            given = data["photo_consent_at"] or self._stamp()
            data["photo_consent_at"] = given if consent else None

        self._update(self._invite_name(invite_id), record, _empty_invite)
        return self.status(invite_id)

    def budget_open(self, kind: OperationKind, reserve_micro_usd: int) -> bool:
        """Whether the shared budget still covers one more call of this kind at its maximum."""
        ledger = self.ledger()
        budget = BUDGET_OF[kind]
        used = ledger["spent"][budget] + ledger["reserved"][budget]
        return used + reserve_micro_usd <= self.limits.budget(budget)

    def ledger(self) -> dict[str, Any]:
        document = self._store.read(LEDGER)
        return document.data if document else _empty_ledger()

    # Model calls

    def check(self, invite_id: str, kind: OperationKind, reserve_micro_usd: int) -> None:
        """Raise the refusal begin() would raise now, without reserving or counting anything,
        so a request can be refused before its body is read."""
        if self.ended():
            raise Refused(Refusal.PILOT_ENDED, "The pilot has ended; model calls are closed")
        try:
            status = self.status(invite_id)
            ledger = self.ledger()
        except StoreUnavailable as error:
            logger.warning("pilot accounting unavailable during check: %s", error)
            raise Refused(
                Refusal.ACCOUNTING_UNAVAILABLE,
                "Spending cannot be recorded right now, so model calls are paused",
            ) from None
        self._check_invite(kind, status)
        cutoff = self._stale_cutoff()
        if any(
            entry["invite"] == invite_id and not self._stale(entry, cutoff)
            for entry in ledger["running"].values()
        ):
            raise Refused(Refusal.BUSY, "Another request for this invite is still running")
        budget = BUDGET_OF[kind]
        used = ledger["spent"][budget] + ledger["reserved"][budget]
        if used + reserve_micro_usd > self.limits.budget(budget):
            raise Refused(Refusal.BUDGET, f"The pilot's {budget} budget is used up")

    def begin(
        self, invite_id: str, kind: OperationKind, operation_id: str, reserve_micro_usd: int
    ) -> Operation:
        """Reserve one model call, or raise Refused. The operation ID makes a repeated request
        a duplicate rather than a second paid call."""
        if self.ended():
            raise Refused(Refusal.PILOT_ENDED, "The pilot has ended; model calls are closed")
        try:
            return self._begin(invite_id, kind, operation_id, reserve_micro_usd)
        except StoreUnavailable as error:
            logger.warning("pilot accounting unavailable during begin: %s", error)
            raise Refused(
                Refusal.ACCOUNTING_UNAVAILABLE,
                "Spending cannot be recorded right now, so model calls are paused",
            ) from None

    def _begin(
        self, invite_id: str, kind: OperationKind, operation_id: str, reserve: int
    ) -> Operation:
        self._check_invite(kind, self.status(invite_id))
        started_at = self._stamp()
        try:
            self._store.create(
                self._operation_name(operation_id),
                {
                    "invite": invite_id,
                    "kind": kind.value,
                    "status": "running",
                    "reserved": reserve,
                    "cost": None,
                    "instance": self.instance_id,
                    "started_at": started_at,
                    "finished_at": None,
                },
            )
        except VersionConflict:
            raise Refused(
                Refusal.DUPLICATE, "This request was already received; start a new one"
            ) from None

        budget = BUDGET_OF[kind]

        def reserve_call(ledger: dict[str, Any]) -> list[str]:
            interrupted = self._interrupt_stale(ledger)
            if any(entry["invite"] == invite_id for entry in ledger["running"].values()):
                raise Refused(Refusal.BUSY, "Another request for this invite is still running")
            used = ledger["spent"][budget] + ledger["reserved"][budget]
            if used + reserve > self.limits.budget(budget):
                raise Refused(Refusal.BUDGET, f"The pilot's {budget} budget is used up")
            ledger["reserved"][budget] += reserve
            ledger["running"][operation_id] = {
                "invite": invite_id,
                "kind": kind.value,
                "reserved": reserve,
                "instance": self.instance_id,
                "started_at": started_at,
            }
            return interrupted

        try:
            interrupted = self._update(LEDGER, reserve_call, _empty_ledger)
        except Refused as refusal:
            self._close(operation_id, "refused", None)
            raise refusal from None
        for stale_id in interrupted:
            self._close(stale_id, "interrupted", None)

        def count(data: dict[str, Any]) -> None:
            self._check_quota(kind, data["plan_requests"], data["preview_requests"])
            data["plan_requests" if kind is OperationKind.PLAN else "preview_requests"] += 1

        operation = Operation(operation_id, invite_id, kind, reserve)
        try:
            self._update(self._invite_name(invite_id), count, _empty_invite)
        except (Refused, StoreUnavailable):
            self._release(operation)
            raise
        return operation

    def finish(self, operation: Operation, cost_micro_usd: int | None, outcome: str) -> None:
        """Replace the reservation with the actual cost; None keeps the whole reservation."""
        budget = BUDGET_OF[operation.kind]

        def settle(ledger: dict[str, Any]) -> None:
            entry = ledger["running"].pop(operation.id, None)
            if entry is None:  # already interrupted, so its reservation counts as spent
                return
            ledger["reserved"][budget] -= entry["reserved"]
            known = cost_micro_usd is not None
            ledger["spent"][budget] += cost_micro_usd if known else entry["reserved"]

        try:
            self._update(LEDGER, settle, _empty_ledger)
            status = "unknown" if cost_micro_usd is None else outcome
            self._close(operation.id, status, cost_micro_usd)
        except StoreUnavailable as error:
            # The ledger still marks the call running; the stale rule will keep its reservation.
            logger.warning("pilot accounting unavailable during finish: %s", error)

    def _release(self, operation: Operation) -> None:
        """Undo a reservation for a call that never started."""
        budget = BUDGET_OF[operation.kind]

        def release(ledger: dict[str, Any]) -> None:
            entry = ledger["running"].pop(operation.id, None)
            if entry is not None:
                ledger["reserved"][budget] -= entry["reserved"]

        try:
            self._update(LEDGER, release, _empty_ledger)
            self._close(operation.id, "refused", None)
        except StoreUnavailable as error:
            logger.warning("pilot accounting unavailable during release: %s", error)

    def _check_invite(self, kind: OperationKind, status: InviteStatus) -> None:
        if not status.age_confirmed:
            raise Refused(Refusal.AGE_REQUIRED, "Confirm that you are 18 or older first")
        if kind is OperationKind.PREVIEW and not status.photo_consent:
            raise Refused(
                Refusal.PHOTO_CONSENT_REQUIRED, "Agree to the photo processing notice first"
            )
        self._check_quota(kind, status.plan_requests_used, status.preview_requests_used)

    def _check_quota(self, kind: OperationKind, plans: int, previews: int) -> None:
        if kind is OperationKind.PLAN and plans >= self.limits.plan_requests:
            raise Refused(Refusal.QUOTA, "This invite has used all its planning requests")
        if kind is OperationKind.PREVIEW and previews >= self.limits.preview_requests:
            raise Refused(Refusal.QUOTA, "This invite has used all its try-on requests")

    def _stale_cutoff(self) -> datetime:
        return self._now() - timedelta(seconds=self.limits.running_timeout_s)

    @staticmethod
    def _stale(entry: dict[str, Any], cutoff: datetime) -> bool:
        """It has outlived every deadline. Another instance ID alone proves nothing: that
        process may still be running the call."""
        return datetime.fromisoformat(entry["started_at"]) < cutoff

    def _interrupt_stale(self, ledger: dict[str, Any]) -> list[str]:
        cutoff = self._stale_cutoff()
        stale = [
            operation_id
            for operation_id, entry in ledger["running"].items()
            if self._stale(entry, cutoff)
        ]
        for operation_id in stale:
            entry = ledger["running"].pop(operation_id)
            budget = BUDGET_OF[OperationKind(entry["kind"])]
            ledger["reserved"][budget] -= entry["reserved"]
            ledger["spent"][budget] += entry["reserved"]
        return stale

    def _close(self, operation_id: str, outcome: str, cost: int | None) -> None:
        def close(data: dict[str, Any]) -> None:
            if data.get("status") == "running":
                data.update(status=outcome, cost=cost, finished_at=self._stamp())

        try:
            self._update(self._operation_name(operation_id), close, dict, create=False)
        except StoreUnavailable as error:
            logger.warning("pilot operation record not closed: %s", error)

    # Feedback

    def add_feedback(self, invite_id: str, answers: dict[str, Any]) -> str:
        feedback_id = uuid.uuid4().hex
        self._store.create(
            self._feedback_name(invite_id, feedback_id),
            {"answers": answers, "created_at": self._stamp()},
        )

        def remember(data: dict[str, Any]) -> None:
            data["feedback"].append(feedback_id)

        self._update(self._invite_name(invite_id), remember, _empty_invite)
        return feedback_id

    def delete_feedback(self, invite_id: str, feedback_id: str) -> bool:
        if feedback_id not in self.status(invite_id).feedback_ids:
            return False
        self._store.delete(self._feedback_name(invite_id, feedback_id))

        def forget(data: dict[str, Any]) -> None:
            data["feedback"] = [entry for entry in data["feedback"] if entry != feedback_id]

        self._update(self._invite_name(invite_id), forget, _empty_invite)
        return True

    # Storage helpers

    def _update[T](
        self,
        name: str,
        mutate: Callable[[dict[str, Any]], T],
        initial: Callable[[], dict[str, Any]],
        *,
        create: bool = True,
    ) -> T:
        """Apply mutate to the latest version and write it only if no one changed it meanwhile."""
        with self._write_lock:
            return self._update_unlocked(name, mutate, initial, create=create)

    def _update_unlocked[T](
        self,
        name: str,
        mutate: Callable[[dict[str, Any]], T],
        initial: Callable[[], dict[str, Any]],
        *,
        create: bool,
    ) -> T:
        for _ in range(MAX_WRITE_ATTEMPTS):
            document = self._store.read(name)
            if document is None and not create:
                raise StoreUnavailable(f"{name} does not exist")
            data = copy.deepcopy(document.data) if document else initial()
            result = mutate(data)
            try:
                if document is None:
                    self._store.create(name, data)
                else:
                    self._store.replace(name, data, document.etag)
            except VersionConflict:
                continue
            return result
        raise StoreUnavailable(f"{name} kept changing during {MAX_WRITE_ATTEMPTS} attempts")

    def _stamp(self) -> str:
        return self._now().isoformat()

    @staticmethod
    def _invite_name(invite_id: str) -> str:
        return f"invites/{invite_id}.json"

    @staticmethod
    def _operation_name(operation_id: str) -> str:
        return f"operations/{uuid.UUID(operation_id)}.json"

    @staticmethod
    def _feedback_name(invite_id: str, feedback_id: str) -> str:
        return f"feedback/{invite_id}/{uuid.UUID(feedback_id).hex}.json"
