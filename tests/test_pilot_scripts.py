"""The operator's invite and report scripts, without Azure."""

import csv
import uuid
from pathlib import Path

import pytest

from backend.app.pilot import OperationKind, Pilot, PilotLimits, invite_hash
from backend.app.store import DirectoryStore
from scripts.pilot_invites import create
from scripts.pilot_report import report


def test_invites_keep_codes_local_and_publish_only_their_digests(tmp_path: Path) -> None:
    invites, hashes = create(3, "https://beta.example/", tmp_path)
    with invites.open(encoding="utf-8") as file:
        rows = list(csv.DictReader(file))
    digests = hashes.read_text(encoding="utf-8").strip().split(",")
    assert [invite_hash(row["code"]) for row in rows] == digests
    assert all(row["link"] == f"https://beta.example/#invite={row['code']}" for row in rows)
    assert all(row["code"] not in hashes.read_text(encoding="utf-8") for row in rows)
    assert invites.stat().st_mode & 0o077 == 0
    with pytest.raises(SystemExit, match="exists"):
        create(1, "https://beta.example", tmp_path)


def test_the_report_shows_money_and_counts_but_no_codes(tmp_path: Path) -> None:
    code = "report-invite-code"
    store = DirectoryStore(tmp_path)
    pilot = Pilot(store, PilotLimits(), [invite_hash(code)])
    invite = pilot.invite_id(code) or ""
    pilot.confirm_age(invite)
    pilot.finish(pilot.begin(invite, OperationKind.PLAN, str(uuid.uuid4()), 200_000), 12_500, "ok")
    summary = report(store, [invite_hash(code)])
    assert summary["spent_usd"] == {"planning": 0.0125, "images": 0.0}
    assert summary["invites"] == [
        {
            "invite": invite[:6],
            "age_confirmed": True,
            "photo_consent": False,
            "plans": 1,
            "try_ons": 0,
            "feedback": 0,
        }
    ]
    assert code not in str(summary)
