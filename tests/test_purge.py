"""The scheduled deletion of the pilot records, against local stores and a scripted Blob API,
and its race with writes that another process accepted before the deletion began."""

import logging
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2
import pytest

from backend.app import purge as purge_module
from backend.app.pilot import OperationKind, Pilot, PilotLimits, Refusal, Refused, invite_hash
from backend.app.purge import purge
from backend.app.store import (
    AzureBlobStore,
    DirectoryStore,
    Document,
    MemoryStore,
    StoreUnavailable,
)

CODE = "purge-invite-code"
ENDS_AT = datetime(2026, 11, 6, 22, 59, tzinfo=UTC)
PURGE_AT = datetime(2026, 11, 9, 9, tzinfo=UTC)
MARKER = "purge-marker.json"  # the job's closure marker, which holds no participant data


def records(store: MemoryStore | DirectoryStore) -> list[str]:
    return [name for name in store.names() if name != MARKER]


def no_wait(seconds: float) -> None:
    pass


def pilot_records(store: MemoryStore | DirectoryStore, now: datetime) -> Pilot:
    """An invite with an age confirmation, a finished call and feedback, as the beta writes."""
    pilot = Pilot(
        store,
        PilotLimits(ends_at=ENDS_AT, records_purge_at=PURGE_AT),
        [invite_hash(CODE)],
        now=lambda: now,
    )
    invite = pilot.invite_id(CODE) or ""
    pilot.confirm_age(invite)
    pilot.finish(pilot.begin(invite, OperationKind.PLAN, str(uuid.uuid4()), 1000), 400, "ok")
    pilot.add_feedback(invite, {"would_wear": True, "usefulness": 4, "try_on": "cannot_assess"})
    return pilot


@pytest.fixture(params=["memory", "directory"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> MemoryStore | DirectoryStore:
    return MemoryStore() if request.param == "memory" else DirectoryStore(tmp_path / "store")


def test_nothing_is_deleted_before_the_purge_time(store: MemoryStore | DirectoryStore) -> None:
    pilot_records(store, ENDS_AT - timedelta(days=1))
    before = store.names()
    waits: list[float] = []
    result = purge(store, PURGE_AT, now=lambda: PURGE_AT - timedelta(hours=1), sleep=waits.append)
    assert (result.due, result.found, result.deleted) == (False, 4, 0)
    assert store.names() == before and waits == []


def test_a_run_that_starts_a_little_early_waits_for_the_purge_time() -> None:
    store = MemoryStore()
    pilot_records(store, ENDS_AT - timedelta(days=1))
    clock = [PURGE_AT - timedelta(seconds=40)]
    waits: list[float] = []

    def wait(seconds: float) -> None:
        waits.append(seconds)
        clock.append(PURGE_AT)

    result = purge(store, PURGE_AT, now=lambda: clock[-1], sleep=wait)
    assert waits == [40.0, purge_module.PURGE_GRACE_S] and result.due and records(store) == []

    # Six minutes ahead is too early to wait for, so the run deletes nothing.
    fresh = MemoryStore()
    pilot_records(fresh, ENDS_AT - timedelta(days=1))
    waits.clear()
    ahead = purge(fresh, PURGE_AT, now=lambda: PURGE_AT - timedelta(minutes=6), sleep=waits.append)
    assert not ahead.due and waits == [] and len(fresh.names()) == 4 and MARKER not in fresh.names()


def test_every_record_goes_once_due_and_a_repeat_is_harmless(
    store: MemoryStore | DirectoryStore,
) -> None:
    pilot = pilot_records(store, ENDS_AT - timedelta(days=1))
    waits: list[float] = []
    result = purge(store, PURGE_AT, now=lambda: PURGE_AT, sleep=waits.append)
    assert (result.due, result.found, result.deleted, result.left) == (True, 4, 4, 0)
    assert store.names() == [MARKER] and waits == [purge_module.PURGE_GRACE_S]
    marker = store.read(MARKER)
    assert marker is not None and set(marker.data) == {"closing_since", "purge_at"}
    again = purge(store, PURGE_AT, now=lambda: PURGE_AT + timedelta(days=1), sleep=no_wait)
    assert (again.found, again.left) == (0, 0) and store.read(MARKER) == marker

    # An empty ledger reopens no budget: the pilot's end still closes model calls.
    after = Pilot(store, pilot.limits, [invite_hash(CODE)], now=lambda: PURGE_AT)
    with pytest.raises(Refused) as refused:
        after.begin(after.invite_id(CODE) or "", OperationKind.PLAN, str(uuid.uuid4()), 1)
    assert refused.value.reason is Refusal.PILOT_ENDED
    assert after.purged() and records(store) == []


class FlakyStore(MemoryStore):
    """Fails the first calls of each kind, as a storage outage of a few seconds would."""

    def __init__(self, failures: int) -> None:
        super().__init__()
        self.failures = {"names": failures, "delete": failures}

    def _fail(self, kind: str) -> None:
        if self.failures[kind]:
            self.failures[kind] -= 1
            raise StoreUnavailable("storage is busy")

    def names(self) -> list[str]:
        self._fail("names")
        return super().names()

    def delete(self, name: str) -> None:
        self._fail("delete")
        super().delete(name)


def test_a_brief_outage_is_retried_and_a_long_one_fails_the_run() -> None:
    flaky = FlakyStore(failures=2)
    pilot_records(flaky, ENDS_AT - timedelta(days=1))
    waits: list[float] = []
    result = purge(flaky, PURGE_AT, now=lambda: PURGE_AT, sleep=waits.append)
    assert result.left == 0 and flaky.names() == [MARKER]
    # The grace period, then two failed listings and two failed deletions, each retried.
    assert waits == [purge_module.PURGE_GRACE_S, 1.0, 2.0, 1.0, 2.0]

    down = FlakyStore(failures=100)
    with pytest.raises(StoreUnavailable):
        purge(down, PURGE_AT, now=lambda: PURGE_AT, sleep=no_wait)


def test_the_job_checks_its_settings_and_its_exit_code_tells_the_scheduler(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(purge_module.time, "sleep", lambda seconds: None)
    root = logging.getLogger()  # as in the job's container: no handlers, level WARNING
    monkeypatch.setattr(root, "handlers", [])
    monkeypatch.setattr(root, "level", logging.WARNING)
    folder = tmp_path / "store"
    pilot_records(DirectoryStore(folder), ENDS_AT - timedelta(days=1))
    monkeypatch.setenv("FULLAURA_STORE_URL", f"file://{folder}")
    monkeypatch.setenv("FULLAURA_PILOT_ENDS_AT", ENDS_AT.isoformat())
    assert purge_module.main() == 2  # no purge time
    monkeypatch.setenv("FULLAURA_RECORDS_PURGE_AT", ENDS_AT.isoformat())
    assert purge_module.main() == 2  # not after the pilot's end
    assert len(DirectoryStore(folder).names()) == 4

    future = datetime.now(UTC) + timedelta(days=1)
    monkeypatch.setenv("FULLAURA_PILOT_ENDS_AT", (future - timedelta(hours=1)).isoformat())
    monkeypatch.setenv("FULLAURA_RECORDS_PURGE_AT", future.isoformat())
    assert purge_module.main() == 0  # not due: an access check only
    assert len(DirectoryStore(folder).names()) == 4

    past = datetime.now(UTC) - timedelta(hours=1)
    monkeypatch.setenv("FULLAURA_PILOT_ENDS_AT", (past - timedelta(days=2)).isoformat())
    monkeypatch.setenv("FULLAURA_RECORDS_PURGE_AT", past.isoformat())
    assert purge_module.main() == 0
    assert DirectoryStore(folder).names() == [MARKER]

    monkeypatch.setenv("FULLAURA_STORE_URL", "memory://")
    assert purge_module.main() == 2  # an in-memory store would delete nothing real

    # Only the job's own logger writes INFO lines; the HTTP client's would name every blob.
    assert logging.getLogger("backend.app.purge").isEnabledFor(logging.INFO)
    assert not logging.getLogger("httpx2").isEnabledFor(logging.INFO)


def test_blob_listing_follows_every_page_and_reports_errors() -> None:
    pages = {
        "": b"<?xml version='1.0'?><EnumerationResults><Blobs><Blob><Name>ledger.json</Name>"
        b"</Blob><Blob><Name>invites/a.json</Name></Blob></Blobs>"
        b"<NextMarker>page-2</NextMarker></EnumerationResults>",
        "page-2": b"<EnumerationResults><Blobs><Blob><Name>feedback/a/b.json</Name></Blob>"
        b"</Blobs><NextMarker /></EnumerationResults>",
    }
    seen: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, content=pages[request.url.params.get("marker", "")])

    store = AzureBlobStore(
        "https://stfullaura.blob.core.windows.net",
        "pilot",
        token=lambda client: ("token-1", 9e12),
        client=httpx2.Client(transport=httpx2.MockTransport(handle)),
    )
    assert store.names() == ["ledger.json", "invites/a.json", "feedback/a/b.json"]
    assert seen[0].url.path == "/pilot" and seen[0].url.params["comp"] == "list"
    assert seen[0].url.params["restype"] == "container"
    assert all(request.headers["Authorization"] == "Bearer token-1" for request in seen)
    assert all(request.url.params["timeout"] == "10" for request in seen)  # the server gives up

    for failure in (httpx2.Response(403), httpx2.Response(200, content=b"<not xml")):
        broken = AzureBlobStore(
            "https://stfullaura.blob.core.windows.net",
            "pilot",
            token=lambda client: ("token-1", 9e12),
            client=httpx2.Client(transport=httpx2.MockTransport(lambda r, f=failure: f)),
        )
        with pytest.raises(StoreUnavailable):
            broken.names()


# Races: the app's process accepted a request before the deletion began, and its store call is
# held at a gate while the purge job runs in another process.

BEFORE = PURGE_AT - timedelta(minutes=1)  # the app's clock when it accepted the request


class GatedStore(MemoryStore):
    """Holds one chosen store call until the test lets it go, as a slow request would. Calls on
    the purge job's marker are never held."""

    def __init__(self) -> None:
        super().__init__()
        self.gate: tuple[str, str] | None = None
        self.arrived = threading.Event()
        self.go = threading.Event()

    def hold(self, method: str, prefix: str) -> None:
        self.gate, self.arrived, self.go = (method, prefix), threading.Event(), threading.Event()

    def _pass(self, method: str, name: str) -> None:
        gate = self.gate
        if gate is not None and gate[0] == method and name.startswith(gate[1]):
            self.gate = None
            self.arrived.set()
            assert self.go.wait(10), "the test never let the held call go"

    def read(self, name: str) -> Document | None:
        self._pass("read", name)
        return super().read(name)

    def create(self, name: str, data: dict[str, Any]) -> str:
        self._pass("create", name)
        return super().create(name, data)

    def replace(self, name: str, data: dict[str, Any], etag: str) -> str:
        self._pass("replace", name)
        return super().replace(name, data, etag)

    def delete(self, name: str) -> None:
        self._pass("delete", name)
        super().delete(name)


def app_pilot(store: MemoryStore, ends_at: datetime = ENDS_AT) -> tuple[Pilot, str]:
    pilot = Pilot(
        store,
        PilotLimits(ends_at=ends_at, records_purge_at=PURGE_AT),
        [invite_hash(CODE)],
        now=lambda: BEFORE,
    )
    return pilot, pilot.invite_id(CODE) or ""


def race(
    store: GatedStore,
    write: Callable[[], Any],
    held: tuple[str, str],
    *,
    let_go: str = "during the grace period",
    meanwhile: Callable[[], None] = lambda: None,
) -> Any:
    """Hold the write at one store call, run the purge job, and let the call go during the
    job's grace period or only after the job has finished. Returns what the write ended with."""
    outcome: list[Any] = []

    def run() -> None:
        try:
            outcome.append(write())
        except Exception as error:  # the outcome is what the test checks
            outcome.append(error)

    store.hold(*held)
    writer = threading.Thread(target=run)
    writer.start()
    assert store.arrived.wait(10)
    meanwhile()

    def wait(seconds: float) -> None:  # the job's grace period
        if let_go == "during the grace period":
            store.go.set()
            writer.join(10)

    result = purge(store, PURGE_AT, now=lambda: PURGE_AT, sleep=wait)
    assert result.due
    store.go.set()
    writer.join(10)
    assert not writer.is_alive() and len(outcome) == 1
    return outcome[0]


def test_a_new_record_held_inside_its_write_lands_before_the_deletion_removes_it() -> None:
    store = GatedStore()
    app, invite = app_pilot(store)
    outcome = race(store, lambda: app.confirm_age(invite), ("create", "invites/"))
    assert records(store) == []
    assert outcome.age_confirmed  # the write finished, and the deletion waited for it
    with pytest.raises(StoreUnavailable):  # and the app writes nothing afterwards
        app.confirm_age(invite)
    assert records(store) == []


def test_a_write_that_resumes_after_the_deletion_is_refused() -> None:
    store = GatedStore()
    app, invite = app_pilot(store)
    outcome = race(
        store, lambda: app.confirm_age(invite), ("read", "invites/"), let_go="after the deletion"
    )
    assert records(store) == []
    assert isinstance(outcome, StoreUnavailable)


def test_an_update_held_inside_its_write_lands_before_the_deletion_removes_it() -> None:
    store = GatedStore()
    app, invite = app_pilot(store)
    app.confirm_age(invite)
    outcome = race(store, lambda: app.set_photo_consent(invite, True), ("replace", "invites/"))
    assert records(store) == []
    assert outcome.photo_consent


def test_an_etag_retry_after_the_deletion_began_is_refused() -> None:
    store = GatedStore()
    app, invite = app_pilot(store)
    app.confirm_age(invite)
    name = f"invites/{invite}.json"

    def another_writer() -> None:  # changes the record while the held write still has the old ETag
        current = MemoryStore.read(store, name)
        assert current is not None
        MemoryStore.replace(store, name, {**current.data, "plan_requests": 1}, current.etag)

    outcome = race(
        store,
        lambda: app.set_photo_consent(invite, True),
        ("replace", "invites/"),
        meanwhile=another_writer,
    )
    assert records(store) == []
    assert isinstance(outcome, StoreUnavailable)  # its retry found the deletion under way


def test_feedback_sent_during_the_deletion_does_not_survive_it() -> None:
    store = GatedStore()
    app, invite = app_pilot(store)
    app.confirm_age(invite)
    answers = {"would_wear": True, "usefulness": 4, "try_on": "cannot_assess"}
    outcome = race(store, lambda: app.add_feedback(invite, answers), ("create", "feedback/"))
    assert records(store) == []
    assert isinstance(outcome, StoreUnavailable)  # the invite could not record the feedback ID


def test_deleting_feedback_during_the_deletion_does_not_bring_the_invite_back() -> None:
    store = GatedStore()
    app, invite = app_pilot(store)
    app.confirm_age(invite)
    feedback_id = app.add_feedback(invite, {"would_wear": False, "usefulness": 2, "try_on": 1})
    outcome = race(store, lambda: app.delete_feedback(invite, feedback_id), ("delete", "feedback/"))
    assert records(store) == []
    assert isinstance(outcome, StoreUnavailable)


def test_a_model_call_accepted_just_before_the_end_leaves_no_reservation_behind() -> None:
    store = GatedStore()
    # A pilot that ends a moment before the purge time, so the call is accepted in time.
    app, invite = app_pilot(store, ends_at=BEFORE + timedelta(seconds=30))
    app.confirm_age(invite)
    outcome = race(
        store,
        lambda: app.begin(invite, OperationKind.PLAN, str(uuid.uuid4()), 1000),
        ("create", "operations/"),
    )
    assert records(store) == []  # neither the operation, the ledger nor the count survives
    assert isinstance(outcome, Refused) and outcome.reason is Refusal.ACCOUNTING_UNAVAILABLE


def test_a_call_that_reports_after_the_deletion_writes_nothing() -> None:
    store = MemoryStore()
    clock = [ENDS_AT - timedelta(hours=1)]
    other = "purge-other-invite-code"
    app = Pilot(
        store,
        PilotLimits(ends_at=ENDS_AT, records_purge_at=PURGE_AT),
        [invite_hash(CODE), invite_hash(other)],
        now=lambda: clock[0],
    )
    invite, second = app.invite_id(CODE) or "", app.invite_id(other) or ""
    for each in (invite, second):
        app.confirm_age(each)
    known = app.begin(invite, OperationKind.PLAN, str(uuid.uuid4()), 1000)
    unknown = app.begin(second, OperationKind.PLAN, str(uuid.uuid4()), 1000)
    purge(store, PURGE_AT, now=lambda: PURGE_AT, sleep=no_wait)
    clock[0] = BEFORE  # a host whose clock lags still sees the deletion under way
    app.finish(known, 400, "success")
    app.finish(unknown, None, "error")
    assert records(store) == []

    later = Pilot(store, app.limits, [invite_hash(CODE)], now=lambda: BEFORE)  # a new process
    with pytest.raises(StoreUnavailable):
        later.confirm_age(invite)
    assert records(store) == []
