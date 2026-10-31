"""Delete every pilot record of the closed beta once the pilot is over.

A scheduled Container Apps job runs this with the app's image: python -m backend.app.purge
(docs/DEPLOYMENT.md#deleting-the-pilot-records). It deletes nothing before
FULLAURA_RECORDS_PURGE_AT, which the settings require to come after FULLAURA_PILOT_ENDS_AT, so it
cannot remove the quotas or the ledger of a pilot that is still running. A run that starts up to
five minutes early waits for that moment; an earlier one only checks that it may list and delete
records.

Once due, a run first writes PURGE_MARKER, after which the app refuses every write
(backend/app/pilot.py, ClosingStore). It then waits PURGE_GRACE_S, longer than any write that
passed the app's check before the marker existed can take, deletes every record except the
marker, and checks that none is left. So a write that raced the deletion either was refused or
landed before it, and nothing written by the app can appear afterwards; the pilot's end keeps
model calls closed whatever the ledger holds. A repeated run is harmless, and a failed one exits
non-zero for the job to retry. The log holds counts only, never a record's name or contents:
only this module's logger writes INFO lines, so the HTTP client's request log, which names each
blob, stays off.
"""

from __future__ import annotations

import logging
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from backend.app.config import Settings
from backend.app.pilot import PURGE_MARKER, WRITE_WINDOW_S
from backend.app.store import (
    BLOB_SERVER_TIMEOUT_S,
    STORE_TIMEOUT_S,
    Store,
    StoreUnavailable,
    VersionConflict,
    open_store,
)

ATTEMPTS = 4  # per store call, after waits of 1, 2 and 4 seconds
EARLY_START_S = 300.0  # a run this close to the purge time waits for it; within replicaTimeout
# A write that found no marker leaves the app within WRITE_WINDOW_S of its check, which
# AzureBlobStore enforces after any token refresh; its pool, connect, write and read phases each
# end within STORE_TIMEOUT_S, and the Blob service abandons it after its own timeout. The rest is
# room for DNS and network delay, which the client cannot bound.
PURGE_GRACE_S = 120.0
assert PURGE_GRACE_S > WRITE_WINDOW_S + 4 * STORE_TIMEOUT_S + BLOB_SERVER_TIMEOUT_S + 30

logger = logging.getLogger("backend.app.purge")  # also when run with python -m


@dataclass(frozen=True, slots=True)
class Purge:
    due: bool
    found: int
    deleted: int
    left: int


def purge(
    store: Store,
    purge_at: datetime,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], None] = lambda seconds: time.sleep(seconds),
) -> Purge:
    """Close and delete every record once purge_at has passed; before that, delete nothing.
    The counts leave out the marker, which holds no participant data."""

    def attempt[T](call: Callable[[], T]) -> T:
        for number in range(ATTEMPTS):
            try:
                return call()
            except StoreUnavailable:
                if number == ATTEMPTS - 1:
                    raise
                sleep(2.0**number)
        raise AssertionError("unreachable")

    def records() -> list[str]:
        return [name for name in attempt(store.names) if name != PURGE_MARKER]

    def close() -> None:
        marker = {"closing_since": now().isoformat(), "purge_at": purge_at.isoformat()}
        try:
            store.create(PURGE_MARKER, marker)
        except VersionConflict:
            pass  # an earlier run closed the records already

    early = (purge_at - now()).total_seconds()
    if 0 < early <= EARLY_START_S:  # the scheduler started this run a little early
        sleep(early)
    if now() < purge_at:
        found = len(records())
        # Deleting a name that cannot exist proves the right to delete without deleting.
        attempt(lambda: store.delete(f"purge-check/{uuid.uuid4().hex}.json"))
        return Purge(due=False, found=found, deleted=0, left=found)
    attempt(close)
    sleep(PURGE_GRACE_S)  # every write that passed the app's check before the marker ends
    names = records()
    for name in names:
        attempt(lambda name=name: store.delete(name))
    return Purge(due=True, found=len(names), deleted=len(names), left=len(records()))


def main() -> int:
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s: %(name)s: %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        settings = Settings.from_env()
        if settings.records_purge_at is None or settings.store_url == "memory://":
            raise ValueError("Set FULLAURA_RECORDS_PURGE_AT and a persistent FULLAURA_STORE_URL")
        result = purge(open_store(settings.store_url), settings.records_purge_at)
    except ValueError as error:
        logger.error("pilot records not deleted: %s", error)
        return 2
    except StoreUnavailable as error:
        logger.error("pilot records not deleted: %s", error)
        return 1
    if not result.due:
        logger.info(
            "pilot records: %d found; access checked; none deleted before %s",
            result.found,
            settings.records_purge_at.isoformat(),
        )
        return 0
    logger.info(
        "pilot records: %d found, %d deleted, %d left", result.found, result.deleted, result.left
    )
    return 0 if result.left == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
