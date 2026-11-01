"""The operator's invite and report scripts, without Azure."""

import csv
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from backend.app.pilot import OperationKind, Pilot, PilotLimits, Refused, invite_hash
from backend.app.store import DirectoryStore, MemoryStore
from scripts.pilot_invites import create
from scripts.pilot_report import invite_digests, open_store, report

CODES = ("report-invite-one", "report-invite-two", "report-invite-three")
HASHES = [invite_hash(code) for code in CODES]
PLAN, PREVIEW = OperationKind.PLAN, OperationKind.PREVIEW
EXTRACTED = datetime(2026, 11, 7, 9, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 2, 12, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


def pilot(store: MemoryStore, clock: Clock) -> Pilot:
    return Pilot(store, PilotLimits(), [*HASHES, invite_hash("not-in-the-list")], now=clock)


def invite(p: Pilot, code: str) -> str:
    invite_id = p.invite_id(code)
    assert invite_id is not None
    p.confirm_age(invite_id)
    p.set_photo_consent(invite_id, True)
    return invite_id


def call(p: Pilot, invite_id: str, kind: OperationKind, cost: int | None, outcome: str) -> None:
    p.finish(p.begin(invite_id, kind, str(uuid.uuid4()), 30_000), cost, outcome)


def answer(try_on: int | str = 4, **fields: Any) -> dict[str, Any]:
    return {
        "would_wear": True,
        "usefulness": 4,
        "try_on": try_on,
        "try_on_issues": None,
        "catalog_problem": "",
        "comment": "",
    } | fields


def summary(store: MemoryStore, **options: Any) -> dict[str, Any]:
    return report(store, HASHES, now=lambda: EXTRACTED, **options)


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


def test_a_new_batch_never_replaces_the_digests_of_an_earlier_one(tmp_path: Path) -> None:
    invites, hashes = create(2, "https://beta.example/", tmp_path)
    earlier = hashes.read_text(encoding="utf-8")
    invites.rename(tmp_path / "invites-batch-1.csv")  # as the old message asked
    with pytest.raises(SystemExit, match="invite-hashes.txt exists"):
        create(1, "https://beta.example/", tmp_path)
    assert hashes.read_text(encoding="utf-8") == earlier
    assert not invites.exists()


def test_an_empty_store_reports_zeros_against_the_invites_provided(tmp_path: Path) -> None:
    result = report(DirectoryStore(tmp_path / "empty"), HASHES, now=lambda: EXTRACTED)
    assert result["extracted_at"] == "2026-11-07T09:00:00+00:00"
    assert result["invites"]["provided"] == 3
    assert result["invites"]["activated"] == {
        "count": 0,
        "of": 3,
        "denominator": "invites provided",
    }
    assert result["model_calls"]["planning"]["calls"] == 0
    latest = result["feedback"]["latest_answer_per_invite"]
    assert latest["would_wear"] == {
        "of": 0,
        "denominator": "invites with a saved answer, latest answer each",
        "yes": 0,
        "no": 0,
    }
    assert latest["try_on_issues"]["of"] == 0
    assert result["infeasible_planning_requests"]["requests"] == 0
    assert result["spending_usd"]["ledger"]["spent"] == {"planning": 0.0, "images": 0.0}


def test_outcomes_stay_apart_from_cost_certainty() -> None:
    store, clock = MemoryStore(), Clock()
    p = pilot(store, clock)
    one, two = invite(p, CODES[0]), invite(p, CODES[1])
    call(p, one, PLAN, 12_500, "success")
    call(p, one, PLAN, 9_000, "generation_failed")
    call(p, one, PREVIEW, 10_000, "succeeded")
    call(p, one, PREVIEW, None, "provider_error")  # failed, cost unknown
    call(p, one, PREVIEW, None, "disconnected")  # cancelled by the browser, cost unknown
    call(p, two, PLAN, None, "error")  # failed on the server, cost unknown
    p.begin(two, PREVIEW, str(uuid.uuid4()), 30_000)  # still running
    with pytest.raises(Refused):
        p.begin(two, PLAN, str(uuid.uuid4()), 30_000)  # busy, refused before the call
    other = p.invite_id("not-in-the-list") or ""
    p.confirm_age(other)
    call(p, other, PLAN, 1_000, "success")

    result = summary(store)
    plans, previews = result["model_calls"]["planning"], result["model_calls"]["try_on"]
    assert (plans["calls"], plans["succeeded"], plans["failed"], plans["unknown"]) == (3, 1, 2, 0)
    assert plans["not_called"] == {"refused": 1, "cancelled": 0, "infeasible": 0}
    assert plans["by_outcome"] == {"error": 1, "generation_failed": 1, "success": 1}
    assert {key: previews[key] for key in ("succeeded", "failed", "cancelled", "unfinished")} == {
        "succeeded": 1,
        "failed": 1,
        "cancelled": 1,
        "unfinished": 1,
    }
    assert result["model_calls"]["calls_of_other_invites"] == 1
    funnel = result["invites"]
    assert [funnel[key]["count"] for key in ("activated", "got_a_plan", "got_a_try_on")] == [
        2,
        1,
        1,
    ]
    assert funnel["used_a_try_on_request"]["count"] == 2
    assert funnel["asked_for_a_plan"]["count"] == funnel["used_a_planning_request"]["count"] == 2

    certainty = result["spending_usd"]["by_cost_certainty"]
    assert certainty["unknown"]["calls"] == 3  # each keeps its whole reservation
    assert certainty["unknown"]["images"] == 0.06 and certainty["unknown"]["planning"] == 0.03
    assert certainty["running"]["images"] == 0.03 and certainty["not_called"]["calls"] == 1
    assert certainty["reported"]["planning"] == 0.0225  # including the invite not in the list
    ledger = result["spending_usd"]["ledger"]
    assert ledger["spent"]["images"] == 0.07 and ledger["reserved"]["images"] == 0.03


def test_feedback_counts_each_invites_latest_answer_and_shows_repeats() -> None:
    store, clock = MemoryStore(), Clock()
    p = pilot(store, clock)
    one, two, three = (invite(p, code) for code in CODES)
    call(p, one, PREVIEW, 10_000, "succeeded")
    call(p, two, PREVIEW, 10_000, "succeeded")
    p.add_feedback(one, answer(2, try_on_issues=["likeness"], would_wear=False))
    clock.now += timedelta(minutes=5)
    p.add_feedback(one, answer(4, try_on_issues=["none_noticed"]))  # replaces the first
    kept = p.add_feedback(two, answer(3, try_on_issues=["background", "other"]))
    p.add_feedback(two, answer(1, usefulness=1))  # saved at the same time: listed last wins
    second = p.status(two).feedback_ids[-1]
    assert p.delete_feedback(two, second)  # deleting the latest brings back the earlier one
    p.add_feedback(three, answer("cannot_assess"))  # no try-on, so no issues

    feedback = summary(store)["feedback"]
    assert feedback["answers_saved"] == 4
    assert feedback["invites_with_more_than_one_answer"] == {
        "count": 1,
        "of": 3,
        "denominator": "invites with a saved answer",
    }
    latest = feedback["latest_answer_per_invite"]
    assert (latest["would_wear"]["of"], latest["would_wear"]["yes"]) == (3, 3)
    try_on = latest["try_on"]
    assert (try_on["of"], try_on["cannot_assess"], try_on["scores"]["of"]) == (3, 1, 2)
    assert [try_on["scores"][score] for score in "12345"] == [0, 0, 1, 1, 0]
    issues = latest["try_on_issues"]
    assert issues["of"] == 2 and issues["answered"] == 2
    assert (issues["none_noticed"], issues["background"], issues["other"]) == (1, 1, 1)
    assert issues["likeness"] == 0  # the replaced answer counts nowhere
    assert issues["from_invites_without_a_successful_try_on"] == 0
    assert kept in p.status(two).feedback_ids


def test_the_same_time_resolves_to_the_answer_listed_last() -> None:
    store, clock = MemoryStore(), Clock()
    p = pilot(store, clock)
    one = invite(p, CODES[0])
    p.add_feedback(one, answer(usefulness=2))
    p.add_feedback(one, answer(usefulness=5))
    usefulness = summary(store)["feedback"]["latest_answer_per_invite"]["usefulness"]
    assert [usefulness[score] for score in "12345"] == [0, 0, 0, 0, 1]


def test_records_from_before_the_new_fields_are_not_guessed() -> None:
    store, clock = MemoryStore(), Clock()
    p = pilot(store, clock)
    one = invite(p, CODES[0])
    old_call = {
        "invite": one,
        "kind": "preview",
        "status": "unknown",  # an old record: the cost was unknown and the outcome not kept
        "reserved": 30_000,
        "cost": None,
        "instance": "old",
        "started_at": clock.now.isoformat(),
        "finished_at": clock.now.isoformat(),
    }
    store.create(f"operations/{uuid.uuid4()}.json", old_call)
    old_answer = answer(3)
    del old_answer["try_on_issues"]
    p.add_feedback(one, old_answer)

    result = summary(store)
    previews = result["model_calls"]["try_on"]
    assert previews["unknown"] == 1 and previews["by_outcome"] == {"not_recorded": 1}
    assert result["invites"]["got_a_try_on"]["count"] == 0
    issues = result["feedback"]["latest_answer_per_invite"]["try_on_issues"]
    assert (issues["of"], issues["not_asked"], issues["skipped"]) == (1, 1, 0)


def test_issues_from_an_invite_without_a_successful_try_on_are_flagged() -> None:
    store, clock = MemoryStore(), Clock()
    p = pilot(store, clock)
    one = invite(p, CODES[0])
    call(p, one, PREVIEW, None, "timeout")
    p.add_feedback(one, answer(2, try_on_issues=["clothes_or_colors"]))
    issues = summary(store)["feedback"]["latest_answer_per_invite"]["try_on_issues"]
    assert issues["clothes_or_colors"] == 1
    assert issues["from_invites_without_a_successful_try_on"] == 1


def test_answers_are_split_by_whether_the_invite_got_a_plan() -> None:
    store, clock = MemoryStore(), Clock()
    p = pilot(store, clock)
    one, two, three = (invite(p, code) for code in CODES)
    call(p, one, PLAN, 12_500, "success")
    call(p, two, PLAN, 9_000, "generation_failed")  # asked, but got no plan
    p.add_feedback(one, answer(usefulness=5))
    p.add_feedback(two, answer(usefulness=2, would_wear=False))
    p.add_feedback(three, answer(usefulness=3))  # never asked for a plan
    by_plan = summary(store)["feedback"]["latest_answer_per_invite"]["by_plan"]
    planned, unplanned = by_plan["with_a_plan"], by_plan["without_a_plan"]
    assert (planned["of"], planned["would_wear_yes"], planned["would_wear_no"]) == (1, 1, 0)
    assert [planned["usefulness"][score] for score in "12345"] == [0, 0, 0, 0, 1]
    assert (unplanned["of"], unplanned["would_wear_yes"], unplanned["would_wear_no"]) == (2, 1, 1)
    assert [unplanned["usefulness"][score] for score in "12345"] == [0, 1, 1, 0, 0]


def test_the_report_holds_no_codes_or_free_text_unless_asked_for_counts() -> None:
    store, clock = MemoryStore(), Clock()
    p = pilot(store, clock)
    one = invite(p, CODES[0])
    call(p, one, PLAN, 12_500, "success")
    text = {"catalog_problem": "No shoes in EU 41", "comment": "Call me at my office"}
    p.add_feedback(one, answer(**text))

    result = summary(store)
    output = json.dumps(result)
    assert "per_invite" not in result and one not in output
    assert all(code not in output for code in CODES)
    assert all(value not in output for value in text.values())
    latest = result["feedback"]["latest_answer_per_invite"]
    assert latest["catalog_problem_written"]["count"] == 1
    assert latest["comment_written"] == {
        "count": 1,
        "of": 1,
        "denominator": "invites with a saved answer, latest answer each",
    }

    rows = summary(store, per_invite=True)["per_invite"]
    assert rows[0] == {
        "invite": one[:6],
        "age_confirmed": True,
        "photo_consent": True,
        "plans": 1,
        "try_ons": 0,
        "feedback": 1,
    }
    assert all(value not in json.dumps(rows) for value in text.values())


def test_without_an_invite_list_every_invite_with_a_record_counts() -> None:
    store, clock = MemoryStore(), Clock()
    p = pilot(store, clock)
    invite(p, CODES[0])
    result = report(store, None, now=lambda: EXTRACTED)
    assert result["invites"]["provided"] is None
    assert result["invites"]["activated"]["of"] == 1


def test_requests_no_plan_could_meet_count_as_asking_but_not_as_a_call() -> None:
    store, clock = MemoryStore(), Clock()
    p = pilot(store, clock)
    one, two = invite(p, CODES[0]), invite(p, CODES[1])
    first = str(uuid.uuid4())
    p.record_infeasible(one, first, ["fit", "footwear"])
    p.record_infeasible(one, first, ["fit", "footwear"])  # the same request sent again
    p.record_infeasible(one, str(uuid.uuid4()), ["fit"])
    p.record_infeasible(p.invite_id(CODES[2]) or "", str(uuid.uuid4()), ["fit"])  # no age yet
    call(p, two, PLAN, 12_500, "success")

    result = summary(store)
    funnel = result["invites"]
    assert funnel["asked_for_a_plan"]["count"] == 2
    assert funnel["used_a_planning_request"]["count"] == 1
    infeasible = result["infeasible_planning_requests"]
    assert infeasible["requests"] == 2 and infeasible["invites"]["count"] == 1
    assert infeasible["requests_by_rule"] == {"fit": 2, "footwear": 1}
    assert p.status(one).plan_requests_used == 0  # no quota


def test_calls_that_never_reached_the_provider_or_the_ledger_are_not_counted_as_started() -> None:
    store, clock = MemoryStore(), Clock()
    p = pilot(store, clock)
    one = invite(p, CODES[0])
    call(p, one, PREVIEW, 0, "cancelled_before_call")  # reserved, then the browser left
    call(p, one, PLAN, 0, "infeasible")  # found infeasible before the model was asked
    lost = {  # its reservation failed after the record was written, so the ledger holds nothing
        "invite": one,
        "kind": "preview",
        "status": "running",
        "reserved": 30_000,
        "cost": None,
        "instance": "lost",
        "started_at": clock.now.isoformat(),
        "finished_at": None,
    }
    store.create(f"operations/{uuid.uuid4()}.json", lost)

    result = summary(store)
    try_on, planning = result["model_calls"]["try_on"], result["model_calls"]["planning"]
    assert (try_on["calls"], try_on["unknown"], try_on["unfinished"]) == (1, 1, 0)
    assert try_on["not_called"] == {"refused": 0, "cancelled": 1, "infeasible": 0}
    assert planning["calls"] == 0 and planning["not_called"]["infeasible"] == 1
    certainty = result["spending_usd"]["by_cost_certainty"]
    assert (certainty["not_called"]["calls"], certainty["reported"]["calls"]) == (2, 0)
    assert (certainty["not_in_ledger"]["calls"], certainty["not_in_ledger"]["images"]) == (1, 0)
    assert certainty["running"]["calls"] == 0
    assert result["spending_usd"]["ledger"]["reserved"]["images"] == 0


def test_the_report_reads_digests_as_the_app_does_and_refuses_anything_else() -> None:
    first, second = HASHES[0], HASHES[1]
    assert invite_digests(f"{first}, {second.upper()}\n") == [first, second]
    assert invite_digests(f"{first},{second}\n{first}\n") == [first, second]  # two files joined
    with pytest.raises(SystemExit):
        invite_digests("invite,code,link,given_to\n1,abc,https://site/#invite=abc,\n")
    with pytest.raises(SystemExit):
        invite_digests("\n")


@pytest.mark.parametrize(
    "url", ["http://stfullaura.blob.core.windows.net/pilot", "https://storage.example/pilot"]
)
def test_the_report_sends_the_operator_token_only_to_an_azure_blob_account(url: str) -> None:
    with pytest.raises(SystemExit):
        open_store(url)
