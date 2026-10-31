"""Report the closed beta's use, outcomes, feedback and spending, without personal data.

Reads the pilot store with the operator's own Azure CLI sign-in, which needs the Storage Blob
Data Reader role or higher on the account. Prints money in US dollars from the ledger, which
holds reservations at list prices (docs/BETA.md#costs), not the providers' invoices.

    uv run --frozen python -m scripts.pilot_report \\
        --store-url https://<account>.blob.core.windows.net/pilot \\
        --invites data/private/beta/invite-hashes.txt --subscription <subscription ID>

The unit for people is the invite: one code, which approximates one participant but does not
prove one person. Every count names its denominator. Feedback counts each invite's latest saved
answer; deleted answers are gone from the store and count nowhere. The output holds aggregates
only: no free text, invite code, photo, profile or plan. --per-invite adds each invite's counts
under the first six hex digits of its digest. docs/BETA.md#reading-the-pilot-report explains
how to read the result.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx2

from backend.app.pilot import BUDGET_OF, Budget, OperationKind, TryOnIssue
from backend.app.store import STORAGE_RESOURCE, AzureBlobStore, DirectoryStore, Store

# What a call's recorded outcome means for the person. backend/app/main.py writes them.
OUTCOME_CATEGORY = {
    "success": "succeeded",  # planning
    "succeeded": "succeeded",  # try-on
    "generation_failed": "failed",
    "provider_timeout": "failed",
    "provider_error": "failed",
    "error": "failed",
    "timeout": "failed",
    "unusable_result": "failed",
    "invalid_photo": "failed",
    "too_large": "failed",
    "disconnected": "cancelled",
    "unknown": "unknown",
    "interrupted": "unknown",  # its process ended during the call
}
CATEGORIES = ("succeeded", "failed", "cancelled", "unknown", "unfinished", "other")
SCORES = ("1", "2", "3", "4", "5")


def cli_token(subscription: str | None) -> Callable[[httpx2.Client], tuple[str, float]]:
    """A storage token from the Azure CLI, in the tenant of the given subscription."""

    def token(_: httpx2.Client) -> tuple[str, float]:
        command = ["az", "account", "get-access-token", "--resource", STORAGE_RESOURCE]
        command += ["--subscription", subscription] if subscription else []
        command += ["--output", "json"]
        result = subprocess.run(command, capture_output=True, check=True, text=True)
        payload = json.loads(result.stdout)
        return payload["accessToken"], float(payload["expires_on"])

    return token


def open_store(url: str, subscription: str | None = None) -> Store:
    if url.startswith("file://"):
        return DirectoryStore(Path(url.removeprefix("file://")))
    account_url, container = url.rstrip("/").rsplit("/", 1)
    return AzureBlobStore(account_url, container, token=cli_token(subscription))


def outcome_of(record: dict[str, Any]) -> str | None:
    """What happened to a call, as recorded. A record closed before the outcome field existed has
    only its status, which says "unknown" whenever the cost was unknown, so its outcome is
    unknown too; the report does not guess it."""
    if "outcome" in record:
        return record["outcome"]
    return None if record["status"] == "unknown" else record["status"]


# Outcomes of reserved calls that never reached the provider and spent nothing
NOT_CALLED = {"cancelled_before_call": "cancelled", "infeasible": "infeasible"}


def not_called(record: dict[str, Any]) -> str | None:
    """Why a recorded call never reached the provider: refused while reserving, cancelled after
    the reservation, or found infeasible before the model was asked; None if it may have."""
    if record["status"] == "refused":
        return "refused"
    return NOT_CALLED.get(outcome_of(record) or "")


def category_of(record: dict[str, Any], held: set[str]) -> str:
    """A record that says running is unfinished only while the ledger still holds its
    reservation; otherwise its write and the ledger's disagree, and what happened is unknown."""
    if record["status"] == "running":
        return "unfinished" if record["id"] in held else "unknown"
    outcome = outcome_of(record)
    return "unknown" if outcome is None else OUTCOME_CATEGORY.get(outcome, "other")


def charge_of(record: dict[str, Any], held: set[str]) -> tuple[str, int]:
    """How certain a call's cost is, and what the ledger holds for it in micro-dollars."""
    if record["status"] == "running":
        return ("running", record["reserved"]) if record["id"] in held else ("not_in_ledger", 0)
    if not_called(record):
        return "not_called", 0
    if record["cost"] is not None:
        return "reported", record["cost"]
    return "unknown", record["reserved"]


def share(count: int, of: int | None, denominator: str) -> dict[str, Any]:
    return {"count": count, "of": of, "denominator": denominator}


def usd(micro: int) -> float:
    return round(micro / 1_000_000, 6)


def report(
    store: Store,
    invite_hashes: list[str] | None,
    *,
    per_invite: bool = False,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict[str, Any]:
    """Aggregates over the given invites, or over every invite with a record when no list is
    given. The store is read one record at a time, so run it after model calls close for a
    stable final count."""
    extracted_at = now().isoformat(timespec="seconds")
    names = store.names()
    operations = [
        {**document.data, "id": name.removeprefix("operations/").removesuffix(".json")}
        for name in names
        if name.startswith("operations/") and (document := store.read(name)) is not None
    ]
    ledger_document = store.read("ledger.json")
    ledger = ledger_document.data if ledger_document else {"spent": {}, "reserved": {}}
    held = set(ledger.get("running", {}))  # calls whose reservation the ledger still holds
    if invite_hashes is None:
        seen = {
            name.split("/")[1].removesuffix(".json")
            for name in names
            if name.startswith(("invites/", "feedback/"))
        }
        invites = sorted(seen | {record["invite"] for record in operations})
        provided, denominator = None, "invites with a record (no invite list was given)"
    else:
        invites = [digest.lower()[:16] for digest in invite_hashes]
        provided, denominator = len(invites), "invites provided"
    listed = set(invites)

    records: dict[str, dict[str, Any]] = {}
    for invite in invites:
        document = store.read(f"invites/{invite}.json")
        records[invite] = document.data if document else {}

    calls = {
        kind: [r for r in operations if r["invite"] in listed and r["kind"] == kind]
        for kind in OperationKind
    }
    succeeded = {
        kind: {r["invite"] for r in calls[kind] if category_of(r, held) == "succeeded"}
        for kind in OperationKind
    }

    feedback = _feedback(store, names, records, succeeded[OperationKind.PREVIEW])

    def counted(record: dict[str, Any], key: str) -> bool:
        return record.get(key, 0) > 0  # invite records from earlier revisions lack new counters

    funnel = {
        "activated": [i for i in invites if records[i].get("age_confirmed_at")],
        "asked_for_a_plan": [
            i
            for i in invites
            if counted(records[i], "plan_requests") or counted(records[i], "infeasible_requests")
        ],
        "used_a_planning_request": [i for i in invites if counted(records[i], "plan_requests")],
        "got_a_plan": [i for i in invites if i in succeeded[OperationKind.PLAN]],
        "used_a_try_on_request": [i for i in invites if counted(records[i], "preview_requests")],
        "got_a_try_on": [i for i in invites if i in succeeded[OperationKind.PREVIEW]],
        "sent_feedback": [i for i in invites if feedback["latest"].get(i)],
    }
    infeasible_rules: Counter[str] = Counter()
    for record in records.values():
        infeasible_rules.update(record.get("infeasible_rules", {}))
    result: dict[str, Any] = {
        "extracted_at": extracted_at,
        "unit": "invite: one code, an approximation of one participant",
        "invites": {
            "provided": provided,
            **{key: share(len(found), len(invites), denominator) for key, found in funnel.items()},
            "activated_means": "confirmed age 18 or older, the first step recorded after the link",
            "used_means": "a request counted against the invite's quota, whether or not it "
            "reached the model",
        },
        "infeasible_planning_requests": {
            "means": "requests no plan could meet, answered without a model call or quota; "
            "counted only by revisions that record them",
            "requests": sum(record.get("infeasible_requests", 0) for record in records.values()),
            "invites": share(
                sum(counted(record, "infeasible_requests") for record in records.values()),
                len(invites),
                denominator,
            ),
            "requests_by_rule": dict(sorted(infeasible_rules.items())),
        },
        "model_calls": {
            "unit": "call",
            "calls_of": denominator,
            "planning": _outcomes(calls[OperationKind.PLAN], held),
            "try_on": _outcomes(calls[OperationKind.PREVIEW], held),
            "calls_of_other_invites": sum(r["invite"] not in listed for r in operations),
        },
        "feedback": feedback["summary"],
        "spending_usd": _spending(ledger, operations, held),
    }
    if per_invite:
        result["per_invite"] = [
            {
                "invite": invite[:6],
                "age_confirmed": bool(records[invite].get("age_confirmed_at")),
                "photo_consent": bool(records[invite].get("photo_consent_at")),
                "plans": records[invite].get("plan_requests", 0),
                "try_ons": records[invite].get("preview_requests", 0),
                "feedback": len(records[invite].get("feedback", [])),
            }
            for invite in invites
        ]
    return result


def _outcomes(calls: list[dict[str, Any]], held: set[str]) -> dict[str, Any]:
    """Outcome categories of the calls that may have reached the provider. A recorded call that
    never did (refused while reserving, cancelled or found infeasible before the provider was
    asked) cost nothing and is counted apart; most refusals happen earlier and leave no
    record."""
    started = [record for record in calls if not not_called(record)]
    categories = Counter(category_of(record, held) for record in started)
    return {
        "calls": len(started),
        **{category: categories[category] for category in CATEGORIES},
        "by_outcome": dict(
            sorted(Counter(outcome_of(r) or "not_recorded" for r in started).items())
        ),
        "not_called": {
            reason: sum(not_called(record) == reason for record in calls)
            for reason in ("refused", "cancelled", "infeasible")
        },
    }


def _feedback(
    store: Store,
    names: list[str],
    records: dict[str, dict[str, Any]],
    tried_on: set[str],
) -> dict[str, Any]:
    """Current answers are those an invite's record lists and the store still holds. The latest
    is the one saved last, and between equal times the one listed last."""
    saved = 0
    repeated = 0
    unlinked = 0
    latest: dict[str, dict[str, Any]] = {}
    for invite, record in records.items():
        listed = record.get("feedback", [])
        answers = []
        for position, feedback_id in enumerate(listed):
            document = store.read(f"feedback/{invite}/{feedback_id}.json")
            if document is not None:
                created = datetime.fromisoformat(document.data["created_at"])
                answers.append((created, position, document.data["answers"]))
        stored = [n for n in names if n.startswith(f"feedback/{invite}/")]
        unlinked += sum(n.rsplit("/", 1)[-1].removesuffix(".json") not in listed for n in stored)
        saved += len(answers)
        repeated += len(answers) > 1
        if answers:
            latest[invite] = max(answers, key=lambda answer: answer[:2])[2]

    answers = list(latest.values())
    scored = [a for a in answers if a["try_on"] != "cannot_assess"]
    issues = Counter(issue for a in scored for issue in a.get("try_on_issues") or ())
    diagnosed = [a for a in scored if a.get("try_on_issues")]
    without_try_on = [i for i, a in latest.items() if a.get("try_on_issues") and i not in tried_on]
    every = {"of": len(answers), "denominator": "invites with a saved answer, latest answer each"}
    with_score = {"of": len(scored), "denominator": "latest answers with a try-on score"}
    summary = {
        "answers_saved": saved,  # answers, not invites; repeats included
        "invites_with_more_than_one_answer": share(
            repeated, len(answers), "invites with a saved answer"
        ),
        "answers_not_listed_by_their_invite": unlinked,  # left out: the save failed half-way
        "latest_answer_per_invite": {
            "would_wear": {
                **every,
                "yes": sum(a["would_wear"] is True for a in answers),
                "no": sum(a["would_wear"] is False for a in answers),
            },
            "usefulness": {
                **every,
                **{s: sum(str(a["usefulness"]) == s for a in answers) for s in SCORES},
            },
            "try_on": {
                **every,
                "cannot_assess": len(answers) - len(scored),
                "scores": {
                    **with_score,
                    **{s: sum(str(a["try_on"]) == s for a in scored) for s in SCORES},
                },
            },
            "try_on_issues": {
                **with_score,
                **{issue.value: issues[issue.value] for issue in TryOnIssue},
                "answered": len(diagnosed),
                "skipped": sum("try_on_issues" in a and not a["try_on_issues"] for a in scored),
                "not_asked": sum("try_on_issues" not in a for a in scored),
                "from_invites_without_a_successful_try_on": len(without_try_on),
            },
            "catalog_problem_written": share(
                sum(bool(a.get("catalog_problem")) for a in answers),
                every["of"],
                every["denominator"],
            ),
            "comment_written": share(
                sum(bool(a.get("comment")) for a in answers), every["of"], every["denominator"]
            ),
        },
    }
    return {"summary": summary, "latest": latest}


COST_CERTAINTY = {
    "reported": "cost from the usage the provider reported",
    "unknown": "cost unknown, so the whole reservation counts as spent",
    "running": "reservation the ledger still holds for a running call",
    "not_called": "refused or cancelled before the provider was called, nothing spent",
    "not_in_ledger": (
        "the record says running but the ledger holds no reservation for it: nothing was "
        "reserved, or the ledger settled it and the record was not closed; any cost is only "
        "in the ledger's spent total"
    ),
}


def _spending(
    data: dict[str, Any], operations: list[dict[str, Any]], held: set[str]
) -> dict[str, Any]:
    """The ledger covers every call of every invite; the breakdown by cost certainty covers the
    same calls from their records, so the two can be compared."""
    breakdown = {
        certainty: {"calls": 0, **{budget.value: 0 for budget in Budget}}
        for certainty in COST_CERTAINTY
    }
    for record in operations:
        certainty, micro = charge_of(record, held)
        breakdown[certainty]["calls"] += 1
        breakdown[certainty][BUDGET_OF[OperationKind(record["kind"])].value] += micro
    return {
        "ledger": {
            "spent": {b.value: usd(data["spent"].get(b.value, 0)) for b in Budget},
            "reserved": {b.value: usd(data["reserved"].get(b.value, 0)) for b in Budget},
            "running_calls": len(held),
        },
        "by_cost_certainty": {
            certainty: {
                "means": COST_CERTAINTY[certainty],
                "calls": values["calls"],
                **{budget.value: usd(values[budget.value]) for budget in Budget},
            }
            for certainty, values in breakdown.items()
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--store-url", required=True)
    parser.add_argument("--invites", type=Path, help="the invite-hashes.txt file")
    parser.add_argument("--subscription", help="the Azure subscription that holds the store")
    parser.add_argument(
        "--per-invite", action="store_true", help="add each invite's counts, without answers"
    )
    args = parser.parse_args()
    hashes = None
    if args.invites:
        hashes = [h for h in args.invites.read_text(encoding="utf-8").strip().split(",") if h]
    store = open_store(args.store_url, args.subscription)
    print(json.dumps(report(store, hashes, per_invite=args.per_invite), indent=2))


if __name__ == "__main__":
    main()
