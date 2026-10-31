"""The scheduled deletion of the pilot records, against local stores and a scripted Blob API."""

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx2
import pytest

from backend.app import purge as purge_module
from backend.app.pilot import OperationKind, Pilot, PilotLimits, Refusal, Refused, invite_hash
from backend.app.purge import purge
from backend.app.store import AzureBlobStore, DirectoryStore, MemoryStore, StoreUnavailable

CODE = "purge-invite-code"
ENDS_AT = datetime(2026, 11, 6, 22, 59, tzinfo=UTC)
PURGE_AT = datetime(2026, 11, 9, 9, tzinfo=UTC)


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
    result = purge(store, PURGE_AT, now=lambda: PURGE_AT - timedelta(seconds=1))
    assert (result.due, result.found, result.deleted) == (False, 4, 0)
    assert store.names() == before


def test_every_record_goes_once_due_and_a_repeat_is_harmless(
    store: MemoryStore | DirectoryStore,
) -> None:
    pilot = pilot_records(store, ENDS_AT - timedelta(days=1))
    result = purge(store, PURGE_AT, now=lambda: PURGE_AT)
    assert (result.due, result.found, result.deleted, result.left) == (True, 4, 4, 0)
    assert store.names() == []
    assert purge(store, PURGE_AT, now=lambda: PURGE_AT + timedelta(days=1)).found == 0

    # An empty ledger reopens no budget: the pilot's end still closes model calls.
    after = Pilot(store, pilot.limits, [invite_hash(CODE)], now=lambda: PURGE_AT)
    with pytest.raises(Refused) as refused:
        after.begin(after.invite_id(CODE) or "", OperationKind.PLAN, str(uuid.uuid4()), 1)
    assert refused.value.reason is Refusal.PILOT_ENDED
    assert after.purged() and store.names() == []


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
    assert result.left == 0 and flaky.names() == []
    assert waits == [1.0, 2.0, 1.0, 2.0]  # two failed listings, then two failed deletions

    down = FlakyStore(failures=100)
    with pytest.raises(StoreUnavailable):
        purge(down, PURGE_AT, now=lambda: PURGE_AT, sleep=lambda seconds: None)


def test_the_job_checks_its_settings_and_its_exit_code_tells_the_scheduler(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(purge_module.time, "sleep", lambda seconds: None)
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
    assert DirectoryStore(folder).names() == []

    monkeypatch.setenv("FULLAURA_STORE_URL", "memory://")
    assert purge_module.main() == 2  # an in-memory store would delete nothing real


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

    for failure in (httpx2.Response(403), httpx2.Response(200, content=b"<not xml")):
        broken = AzureBlobStore(
            "https://stfullaura.blob.core.windows.net",
            "pilot",
            token=lambda client: ("token-1", 9e12),
            client=httpx2.Client(transport=httpx2.MockTransport(lambda r, f=failure: f)),
        )
        with pytest.raises(StoreUnavailable):
            broken.names()
