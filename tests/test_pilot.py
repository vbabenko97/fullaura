"""Invites, quotas, reservations and feedback of the closed beta, against an in-memory store."""

import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from backend.app.pilot import (
    LEDGER,
    WRITE_WINDOW_S,
    Budget,
    ClosingStore,
    OperationKind,
    Pilot,
    PilotLimits,
    RecordsClosed,
    Refusal,
    Refused,
    invite_hash,
    planning_cost_micro_usd,
    planning_reserve_micro_usd,
)
from backend.app.store import SEND_BY, Document, MemoryStore, StoreUnavailable

CODE = "invite-code-for-tests"
OTHER = "another-invite-for-tests"
PLAN, PREVIEW = OperationKind.PLAN, OperationKind.PREVIEW


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 2, 12, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


def pilot(store: MemoryStore | None = None, clock: Clock | None = None, **limits: Any) -> Pilot:
    return Pilot(
        store or MemoryStore(),
        PilotLimits(**limits),
        [invite_hash(CODE), invite_hash(OTHER)],
        now=clock or Clock(),
    )


def ready(p: Pilot, code: str = CODE, *, photos: bool = False) -> str:
    invite = p.invite_id(code)
    assert invite is not None
    p.confirm_age(invite)
    if photos:
        p.set_photo_consent(invite, True)
    return invite


def op() -> str:
    return str(uuid.uuid4())


def test_only_configured_invite_codes_open_an_invite() -> None:
    p = pilot()
    assert p.invite_id(CODE) is not None and p.invite_id(f" {CODE} ") == p.invite_id(CODE)
    assert p.invite_id("guessed-code") is None
    assert p.knows(p.invite_id(CODE) or "") and not p.knows("0" * 16)


def test_a_call_reserves_its_maximum_and_settles_at_its_actual_cost() -> None:
    p = pilot()
    invite = ready(p)
    operation = p.begin(invite, PLAN, op(), 200_000)
    assert p.ledger()["reserved"]["planning"] == 200_000
    assert p.status(invite).plan_requests_used == 1
    p.finish(operation, 12_345, "success")
    ledger = p.ledger()
    assert ledger["reserved"]["planning"] == 0 and ledger["spent"]["planning"] == 12_345
    assert ledger["running"] == {}


def test_an_unknown_outcome_keeps_the_whole_reservation() -> None:
    p = pilot()
    operation = p.begin(ready(p), PLAN, op(), 200_000)
    p.finish(operation, None, "error")
    assert p.ledger()["spent"]["planning"] == 200_000


def test_a_repeated_operation_id_never_starts_a_second_paid_call() -> None:
    p = pilot()
    invite = ready(p)
    operation_id = op()
    p.finish(p.begin(invite, PLAN, operation_id, 1000), 500, "success")
    with pytest.raises(Refused) as refused:
        p.begin(invite, PLAN, operation_id, 1000)
    assert refused.value.reason is Refusal.DUPLICATE
    assert p.status(invite).plan_requests_used == 1
    assert p.ledger()["spent"]["planning"] == 500


def test_an_invite_runs_one_model_call_at_a_time() -> None:
    p = pilot()
    invite = ready(p, photos=True)
    running = p.begin(invite, PLAN, op(), 1000)
    for kind in (PLAN, PREVIEW):
        with pytest.raises(Refused) as refused:
            p.begin(invite, kind, op(), 1000)
        assert refused.value.reason is Refusal.BUSY
    other = p.begin(ready(p, OTHER), PLAN, op(), 1000)  # another invite is not blocked
    p.finish(running, 100, "success")
    p.finish(other, 100, "success")
    p.finish(p.begin(invite, PREVIEW, op(), 1000), 100, "succeeded")
    # A refused call did not count against the quota.
    assert (p.status(invite).plan_requests_used, p.status(invite).preview_requests_used) == (1, 1)


def test_quotas_are_upper_limits_per_invite() -> None:
    p = pilot(plan_requests=2, preview_requests=1)
    invite = ready(p, photos=True)
    for _ in range(2):
        p.finish(p.begin(invite, PLAN, op(), 1000), 10, "success")
    with pytest.raises(Refused) as refused:
        p.begin(invite, PLAN, op(), 1000)
    assert refused.value.reason is Refusal.QUOTA
    p.finish(p.begin(invite, PREVIEW, op(), 1000), 10, "succeeded")
    with pytest.raises(Refused, match="try-on"):
        p.begin(invite, PREVIEW, op(), 1000)


def test_the_shared_budget_can_close_calls_before_any_quota() -> None:
    p = pilot(budgets_micro_usd=((Budget.PLANNING, 2500), (Budget.IMAGES, 1000)))
    first, second = ready(p), ready(p, OTHER)
    held = p.begin(first, PLAN, op(), 2000)
    with pytest.raises(Refused) as refused:  # 2000 reserved + 1000 > 2500
        p.begin(second, PLAN, op(), 1000)
    assert refused.value.reason is Refusal.BUDGET
    assert p.status(second).plan_requests_used == 0
    p.finish(held, 1000, "success")
    p.finish(p.begin(second, PLAN, op(), 1000), 1000, "success")
    assert not p.budget_open(PLAN, 1000)


def test_age_and_photo_consent_come_before_any_call() -> None:
    p = pilot()
    invite = p.invite_id(CODE) or ""
    with pytest.raises(Refused) as refused:
        p.begin(invite, PLAN, op(), 1000)
    assert refused.value.reason is Refusal.AGE_REQUIRED
    p.confirm_age(invite)
    with pytest.raises(Refused) as refused:
        p.check(invite, PREVIEW, 1000)
    assert refused.value.reason is Refusal.PHOTO_CONSENT_REQUIRED
    p.set_photo_consent(invite, True)
    p.check(invite, PREVIEW, 1000)
    p.set_photo_consent(invite, False)
    assert not p.status(invite).photo_consent
    assert p.status(invite).preview_requests_used == 0  # check() counts nothing


def test_after_a_restart_counts_and_spending_remain_and_a_running_call_is_not_retried() -> None:
    store, clock = MemoryStore(), Clock()
    before = pilot(store, clock)
    invite = ready(before)
    before.finish(before.begin(invite, PLAN, op(), 1000), 400, "success")
    lost = before.begin(invite, PLAN, op(), 1000)  # the process ends during this call

    after = pilot(store, clock)  # a new process: same store, new instance ID
    assert after.status(invite).plan_requests_used == 2
    with pytest.raises(Refused) as refused:  # it cannot know the other process has ended
        after.begin(invite, PLAN, op(), 1000)
    assert refused.value.reason is Refusal.BUSY
    clock.now += timedelta(seconds=301)
    after.finish(after.begin(invite, PLAN, op(), 1000), 300, "success")
    ledger = after.ledger()
    assert ledger["spent"]["planning"] == 400 + 1000 + 300  # the lost call keeps its reservation
    assert ledger["reserved"]["planning"] == 0
    operation = store.read(f"operations/{lost.id}.json")
    assert operation is not None and operation.data["status"] == "interrupted"
    before.finish(lost, 100, "success")  # a late finish cannot refund a held reservation
    assert after.ledger()["spent"]["planning"] == 1700


def test_two_live_instances_never_run_two_calls_for_one_invite() -> None:
    """Two revisions overlap during a deployment and share the store."""
    store, clock = MemoryStore(), Clock()
    old, new = pilot(store, clock), pilot(store, clock)
    assert old.instance_id != new.instance_id
    invite = ready(old, photos=True)
    running = old.begin(invite, PLAN, op(), 1000)
    for call in (
        lambda: new.check(invite, PREVIEW, 1000),
        lambda: new.begin(invite, PREVIEW, op(), 1000),
        lambda: new.begin(invite, PLAN, op(), 1000),
    ):
        with pytest.raises(Refused) as refused:
            call()
        assert refused.value.reason is Refusal.BUSY
    assert old.ledger()["running"].keys() == {running.id}
    assert new.status(invite).plan_requests_used == 1  # the refused calls counted nothing

    old.finish(running, 250, "success")  # the old revision's call ends normally
    new.finish(new.begin(invite, PREVIEW, op(), 1000), 10_000, "succeeded")
    ledger = new.ledger()
    assert ledger["spent"] == {"planning": 250, "images": 10_000}
    assert ledger["reserved"] == {"planning": 0, "images": 0} and ledger["running"] == {}


def test_a_dead_instances_call_is_released_only_after_the_timeout_and_keeps_its_reservation() -> (
    None
):
    store, clock = MemoryStore(), Clock()
    old, new = pilot(store, clock), pilot(store, clock)
    invite = ready(old)
    dead = old.begin(invite, PLAN, op(), 1000)  # the old revision stops during this call
    clock.now += timedelta(seconds=299)
    with pytest.raises(Refused) as refused:
        new.check(invite, PLAN, 1000)
    assert refused.value.reason is Refusal.BUSY
    clock.now += timedelta(seconds=2)
    new.check(invite, PLAN, 1000)
    current = new.begin(invite, PLAN, op(), 2000)
    ledger = new.ledger()
    assert ledger["spent"]["planning"] == 1000  # the dead call's whole reservation
    assert ledger["reserved"]["planning"] == 2000 and ledger["running"].keys() == {current.id}

    # The dead call's process reports late: no refund, and the new call stays untouched.
    old.finish(dead, None, "error")
    old.finish(dead, 1, "success")
    ledger = new.ledger()
    assert ledger["spent"]["planning"] == 1000
    assert ledger["reserved"]["planning"] == 2000 and ledger["running"].keys() == {current.id}
    with pytest.raises(Refused) as refused:  # the new call still holds the invite
        old.begin(invite, PLAN, op(), 1000)
    assert refused.value.reason is Refusal.BUSY
    new.finish(current, 500, "success")
    ledger = new.ledger()
    assert ledger["spent"]["planning"] == 1500 and ledger["reserved"]["planning"] == 0
    record = store.read(f"operations/{dead.id}.json")
    assert record is not None and record.data["status"] == "interrupted"


def test_a_call_older_than_every_deadline_stops_blocking_its_invite() -> None:
    clock = Clock()
    p = pilot(clock=clock)
    invite = ready(p)
    p.begin(invite, PLAN, op(), 1000)
    clock.now += timedelta(seconds=301)
    p.check(invite, PLAN, 1000)
    p.begin(invite, PLAN, op(), 1000)
    assert p.ledger()["spent"]["planning"] == 1000


class RacingStore(MemoryStore):
    """Another process changes the ledger between this process's read and write, once."""

    def __init__(self) -> None:
        super().__init__()
        self.raced = False

    def replace(self, name: str, data: dict[str, Any], etag: str) -> str:
        if name == LEDGER and not self.raced:
            self.raced = True
            current = self.read(name)
            assert current is not None
            other = {**current.data, "spent": {**current.data["spent"], "images": 7}}
            super().replace(name, other, current.etag)
        return super().replace(name, data, etag)


def test_a_concurrent_ledger_change_is_kept_rather_than_overwritten() -> None:
    store = RacingStore()
    p = pilot(store)
    invite = ready(p)
    p.ledger()  # nothing written yet; the first begin creates the ledger
    p.finish(p.begin(invite, PLAN, op(), 1000), 250, "success")
    ledger = p.ledger()
    assert store.raced
    assert ledger["spent"] == {"planning": 250, "images": 7}


class BrokenStore(MemoryStore):
    def read(self, name: str) -> Document | None:
        raise StoreUnavailable("storage is down")


def test_unavailable_accounting_refuses_model_calls() -> None:
    p = Pilot(BrokenStore(), PilotLimits(), [invite_hash(CODE)])
    invite = p.invite_id(CODE) or ""
    for call in (lambda: p.begin(invite, PLAN, op(), 1000), lambda: p.check(invite, PLAN, 1000)):
        with pytest.raises(Refused) as refused:
            call()
        assert refused.value.reason is Refusal.ACCOUNTING_UNAVAILABLE


def test_the_pilot_window_closes_model_calls() -> None:
    clock = Clock()
    p = pilot(clock=clock, ends_at=clock.now + timedelta(days=7))
    invite = ready(p)
    p.check(invite, PLAN, 1000)
    clock.now += timedelta(days=7)
    with pytest.raises(Refused) as refused:
        p.begin(invite, PLAN, op(), 1000)
    assert refused.value.reason is Refusal.PILOT_ENDED


def test_a_closure_check_that_took_too_long_or_failed_allows_no_write() -> None:
    clock = Clock()
    ticks = iter([0.0, WRITE_WINDOW_S + 1])  # the marker read stalled past the window
    slow = ClosingStore(MemoryStore(), None, clock, monotonic=lambda: next(ticks))
    with pytest.raises(StoreUnavailable, match="too long"):
        slow.create("invites/a.json", {})
    broken = ClosingStore(BrokenStore(), None, clock)
    for write in (
        lambda: broken.create("invites/a.json", {}),
        lambda: broken.replace("invites/a.json", {}, "etag"),
        lambda: broken.delete("invites/a.json"),
    ):
        with pytest.raises(StoreUnavailable):
            write()
    store = MemoryStore()
    closed = ClosingStore(store, clock.now, clock)  # the purge time has come, marker or not
    with pytest.raises(RecordsClosed):
        closed.create("invites/a.json", {})
    assert store.names() == []


class DeadlineStore(MemoryStore):
    """Records the send-by time that each write receives."""

    def __init__(self) -> None:
        super().__init__()
        self.send_by: list[float | None] = []

    def create(self, name: str, data: dict[str, Any]) -> str:
        self.send_by.append(SEND_BY.get())
        return super().create(name, data)


def test_an_allowed_write_must_be_sent_within_the_window_of_its_check() -> None:
    store = DeadlineStore()
    before = time.monotonic()
    ClosingStore(store, None, Clock()).create("invites/a.json", {})
    after = time.monotonic()
    [send_by] = store.send_by
    assert send_by is not None and before < send_by <= after + WRITE_WINDOW_S
    assert SEND_BY.get() is None  # only that write had the window


def test_feedback_can_be_deleted_only_by_its_invite() -> None:
    store = MemoryStore()
    p = pilot(store)
    invite, other = ready(p), ready(p, OTHER)
    answers = {"would_wear": True, "usefulness": 4, "try_on": "cannot_assess"}
    feedback_id = p.add_feedback(invite, answers)
    saved = store.read(f"feedback/{invite}/{feedback_id}.json")
    assert saved is not None and saved.data["answers"] == answers
    assert p.status(invite).feedback_ids == (feedback_id,)
    assert not p.delete_feedback(other, feedback_id)
    assert p.delete_feedback(invite, feedback_id)
    assert store.read(f"feedback/{invite}/{feedback_id}.json") is None
    assert p.status(invite).feedback_ids == ()


def test_planning_costs_follow_reported_tokens_and_hold_unknown_attempts() -> None:
    attempt = planning_reserve_micro_usd(1, 6000)
    assert planning_reserve_micro_usd(4, 6000) == 4 * attempt
    # 1,000 input tokens at $1.25 and 300 output tokens at $4.25 per million.
    assert planning_cost_micro_usd(1000, 300, 1, 1, 6000) == 1250 + 1275
    # A second HTTP attempt returned nothing, so it counts at its maximum.
    assert planning_cost_micro_usd(1000, 300, 1, 2, 6000) == 2525 + attempt
