"""Run the T-02 feasibility test against Muse Image (see docs/FEASIBILITY.md).

Inputs stay in data/private/feasibility/, which Git ignores: formal/ for the formal
evaluation and exploratory/ for exploratory runs. Each folder has a cases.json, with paths
relative to that folder:

    {"cases": [{
        "id": "s1-o1",
        "subject": "s1",
        "photos": ["s1/portrait.jpg", "s1/full_body.jpg"],
        "garments": ["garments/blazer.jpg", "garments/shirt.jpg"],
        "look_prompt": "Show the person from the first two images wearing exactly ...",
        "edit_prompt": "Replace only the shirt with the shirt in the second image ...",
        "edit_garment": "garments/white_shirt.jpg"
    }]}

Case ids are unique and use letters, digits, "-" and "_". edit_prompt is optional, and so
is edit_garment within an edit. Each sample makes a look preview from the photos and
garments, then, when the case has an edit_prompt, one targeted edit of that preview. A run
writes the images, run.json (model, samples, planned images), attempts.jsonl (one line per
API call: model, prompt, input names, attempts, latency, usage or error) and scores.csv into
runs/<UTC time>/ beside cases.json. scores.csv has one row per planned image, including
failed calls and edits skipped because their look failed. Score each produced image by hand
as pass, fail or unassessable; --summarize then counts every planned image in each
denominator. The script never prints or logs image bytes or the API key.

    uv run --frozen --env-file .env python -m scripts.feasibility_run --dry-run
    uv run --frozen --env-file .env python -m scripts.feasibility_run --samples 2
    uv run --frozen python -m scripts.feasibility_run --summarize <run folder>
"""

from __future__ import annotations

import argparse
import csv
import json
import mimetypes
import os
import re
import statistics
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from backend.app.meta_images import (
    IMAGE_MODEL,
    ImageRequestError,
    ImageResult,
    InputImage,
    MuseImageClient,
    strip_jpeg_metadata,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "data" / "private" / "feasibility" / "formal" / "cases.json"
PRICE_PER_IMAGE_USD = 0.01  # Meta pricing page, checked 2026-10-26
REQUIRED = ("id", "subject", "photos", "garments", "look_prompt")
CASE_ID = re.compile(r"[A-Za-z0-9_-]+")
PLACEHOLDER = re.compile(r"<[^<>]+>")  # left over from the prompt template, such as <garment>
IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
CHECKS = ("identity", "proportions", "product_details", "edit_succeeds")
COLUMNS = ["case", "subject", "sample", "step", "status", "file", *CHECKS, "notes"]
SCORES = ("pass", "fail", "unassessable")


def input_path(base: Path, name: str) -> Path:
    """Resolve an input name without leaving the folder that contains cases.json."""
    requested = Path(name)
    if requested.is_absolute():
        raise ValueError("input paths must be relative")
    resolved_base = base.resolve()
    resolved = (resolved_base / requested).resolve()
    if not resolved.is_relative_to(resolved_base):
        raise ValueError("input paths must stay within the cases folder")
    return resolved


def load_cases(path: Path) -> list[dict[str, Any]]:
    """The cases, or an exit that lists every problem in every case, so that one dry run
    shows all the inputs still missing."""
    cases = json.loads(path.read_text(encoding="utf-8"))["cases"]
    problems: list[str] = []
    seen: set[str] = set()
    for number, case in enumerate(cases, 1):
        absent = [field for field in REQUIRED if not case.get(field)]
        if absent:
            problems.append(f"Case {number}: missing {absent}")
            continue
        label = f"Case {case['id']}"
        if not CASE_ID.fullmatch(case["id"]) or case["id"] in seen:
            problems.append(f"Case {number}: ids are unique and use only A-Z, a-z, 0-9, - and _")
        seen.add(case["id"])
        if case.get("edit_garment") and not case.get("edit_prompt"):
            problems.append(f"{label}: edit_garment needs an edit_prompt")
        unfilled = [
            field
            for field in ("look_prompt", "edit_prompt")
            if PLACEHOLDER.search(case.get(field, ""))
        ]
        if unfilled:
            problems.append(f"{label}: fill in the <...> placeholder in {unfilled}")
        names = [*case["photos"], *case["garments"]]
        names += [case["edit_garment"]] if case.get("edit_garment") else []
        paths: dict[str, Path] = {}
        for name in names:
            try:
                paths[name] = input_path(path.parent, name)
            except ValueError as error:
                problems.append(f"{label}: {name}: {error}")
        missing = [name for name, image_path in paths.items() if not image_path.is_file()]
        if missing:
            problems.append(f"{label}: input files not found: {missing}")
        unsupported = [name for name in names if mimetypes.guess_type(name)[0] not in IMAGE_TYPES]
        if unsupported:
            problems.append(f"{label}: use JPEG, PNG or WebP: {unsupported}")
        for name in case["photos"]:
            # The client removes metadata, such as location, from JPEGs only.
            if mimetypes.guess_type(name)[0] != "image/jpeg":
                problems.append(f"{label}: personal photos must be JPEG: {name}")
            elif name in paths and name not in missing:
                try:
                    strip_jpeg_metadata(paths[name].read_bytes())
                except ValueError as error:
                    problems.append(f"{label}: {name}: {error}")
    if problems:
        raise SystemExit("\n".join(problems))
    return cases


def planned_images(cases: list[dict[str, Any]], samples: int) -> int:
    return samples * sum(2 if case.get("edit_prompt") else 1 for case in cases)


def read_image(path: Path) -> tuple[str, InputImage]:
    return path.name, InputImage(path.read_bytes(), mimetypes.guess_type(path.name)[0] or "")


def score_row(case: dict[str, Any], sample: int, step: str, status: str, file: str = "") -> list:
    """A scores.csv row: blank checks to score for a produced image, "-" when none exists."""
    checks = dict.fromkeys(CHECKS, "" if status == "ok" else "-")
    if step == "look":
        checks["edit_succeeds"] = "n/a"
    return [case["id"], case["subject"], sample, step, status, file, *checks.values(), ""]


def applies(row: dict[str, str], check: str) -> bool:
    return check != "edit_succeeds" or row["step"] == "edit"


def score(row: dict[str, str], check: str) -> str:
    return row[check].strip().lower()


def tally(rows: list[dict[str, str]]) -> dict[str, dict[str, int]]:
    """Counts per check. Every planned image is in the denominator, so failed calls,
    skipped edits and unassessable images all count as not passing."""
    unscored = sorted(
        {
            f"{row['case']} sample {row['sample']} {row['step']} {check}"
            for row in rows
            for check in CHECKS
            if row["status"] == "ok" and applies(row, check) and score(row, check) not in SCORES
        }
    )
    if unscored:
        raise SystemExit(f"Score these as pass, fail or unassessable first: {unscored}")
    counts = {}
    for check in CHECKS:
        planned = [row for row in rows if applies(row, check)]
        counts[check] = {"planned": len(planned)}
        counts[check]["not_produced"] = sum(row["status"] != "ok" for row in planned)
        for value in SCORES:
            counts[check][value] = sum(
                row["status"] == "ok" and score(row, check) == value for row in planned
            )
    return counts


def identity_by_subject(rows: list[dict[str, str]]) -> dict[str, tuple[int, int]]:
    """Identity passes and planned images for each subject."""
    totals: dict[str, tuple[int, int]] = {}
    for row in rows:
        passed, planned = totals.get(row["subject"], (0, 0))
        passed += row["status"] == "ok" and score(row, "identity") == "pass"
        totals[row["subject"]] = (passed, planned + 1)
    return totals


def summarize(run_dir: Path) -> None:
    planned = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))["planned_images"]
    with (run_dir / "scores.csv").open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    if len(rows) != planned:
        raise SystemExit(f"The run has {len(rows)} of {planned} planned rows; it did not finish")
    for check, count in tally(rows).items():
        if not count["planned"]:
            print(f"{check}: no images planned")
            continue
        share = count["pass"] / count["planned"]
        print(
            f"{check}: {count['pass']}/{count['planned']} passed ({share:.0%}); "
            f"{count['fail']} failed the check, {count['unassessable']} unassessable, "
            f"{count['not_produced']} not produced"
        )
    by_subject = identity_by_subject(rows)
    print("identity by subject: " + ", ".join(f"{s} {p}/{n}" for s, (p, n) in by_subject.items()))


def call(
    client: MuseImageClient,
    log: TextIO,
    record: dict[str, Any],
    prompt: str,
    inputs: list[tuple[str, InputImage]],
    out_stem: Path,
) -> tuple[Path, ImageResult] | None:
    entry = {**record, "model": client.model, "prompt": prompt, "inputs": [n for n, _ in inputs]}
    try:
        result = client.edit(prompt, [image for _, image in inputs])
    except ImageRequestError as error:
        entry |= {"status": "failed", "attempts": error.attempts, "error": str(error)}
        output = None
    else:
        path = out_stem.parent / f"{out_stem.name}.{result.extension}"
        path.write_bytes(result.image)
        entry |= {
            "status": "ok",
            "attempts": result.attempts,
            "latency_s": result.latency_s,
            "usage": result.usage,
            "revised_prompt": result.revised_prompt,
            "output": path.name,
        }
        output = path, result
    log.write(json.dumps(entry) + "\n")
    log.flush()
    print(
        f"{record['case']} sample {record['sample']} {record['step']}: {entry['status']}, "
        f"{entry['attempts']} attempts, {entry.get('latency_s', '-')} s"
    )
    return output


def run(
    cases: list[dict[str, Any]], base: Path, samples: int, client: MuseImageClient
) -> tuple[Path, list[ImageResult]]:
    """Make every planned image and write the run folder; returns it and the produced images."""
    run_dir = base / "runs" / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir.mkdir(parents=True)
    plan = {"model": client.model, "samples": samples}
    plan["planned_images"] = planned_images(cases, samples)
    (run_dir / "run.json").write_text(json.dumps(plan), encoding="utf-8")
    results: list[ImageResult] = []
    with (
        (run_dir / "attempts.jsonl").open("w", encoding="utf-8") as log,
        (run_dir / "scores.csv").open("w", newline="", encoding="utf-8") as scores_file,
    ):
        scores = csv.writer(scores_file)
        scores.writerow(COLUMNS)
        for case in cases:
            look_inputs = [
                read_image(input_path(base, name)) for name in case["photos"] + case["garments"]
            ]
            edit_garment = (
                [read_image(input_path(base, case["edit_garment"]))]
                if case.get("edit_garment")
                else []
            )
            for sample in range(1, samples + 1):
                record = {"case": case["id"], "subject": case["subject"], "sample": sample}
                stem = run_dir / f"{case['id']}-{sample}"
                look_record = {**record, "step": "look"}
                look = call(client, log, look_record, case["look_prompt"], look_inputs, stem)
                if look is None:
                    scores.writerow(score_row(case, sample, "look", "call_failed"))
                    if case.get("edit_prompt"):
                        scores.writerow(score_row(case, sample, "edit", "skipped"))
                    continue
                look_path, look_result = look
                results.append(look_result)
                scores.writerow(score_row(case, sample, "look", "ok", look_path.name))
                if not case.get("edit_prompt"):
                    continue

                look_image = InputImage(look_result.image, look_result.mime_type)
                edit_inputs = [(look_path.name, look_image), *edit_garment]
                edit_record = {**record, "step": "edit"}
                edit_stem = run_dir / f"{case['id']}-{sample}-edit"
                edit = call(client, log, edit_record, case["edit_prompt"], edit_inputs, edit_stem)
                if edit is None:
                    scores.writerow(score_row(case, sample, "edit", "call_failed"))
                    continue
                results.append(edit[1])
                scores.writerow(score_row(case, sample, "edit", "ok", edit[0].name))
    return run_dir, results


def at_least_one(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--samples", type=at_least_one, default=1, help="samples per step")
    parser.add_argument("--dry-run", action="store_true", help="check inputs; call no API")
    parser.add_argument("--summarize", type=Path, metavar="RUN_DIR", help="count scored images")
    args = parser.parse_args()
    if args.summarize:
        summarize(args.summarize)
        return

    cases = load_cases(args.cases)
    planned = planned_images(cases, args.samples)
    print(f"{len(cases)} cases x {args.samples} samples: {planned} images planned")
    print(f"About ${planned * PRICE_PER_IMAGE_USD:.2f} before retries")
    if args.dry_run:
        return

    client = MuseImageClient(
        os.environ.get("MODEL_API_KEY", ""), os.environ.get("META_IMAGE_MODEL") or IMAGE_MODEL
    )
    run_dir, results = run(cases, args.cases.parent, args.samples, client)
    latencies = [result.latency_s for result in results]
    median = statistics.median(latencies) if latencies else 0
    p90 = (
        statistics.quantiles(latencies, n=10, method="inclusive")[8]
        if len(latencies) > 1
        else median
    )
    print(f"Run folder: {run_dir}")
    print(
        f"{len(results)}/{planned} images produced, "
        f"{sum(result.attempts for result in results)} attempts for them, "
        f"latency median {median:.1f} s, p90 {p90:.1f} s, "
        f"about ${len(results) * PRICE_PER_IMAGE_USD:.2f}"
    )
    print("Score each produced image in scores.csv as pass, fail or unassessable, then run")
    print(f"  uv run --frozen python -m scripts.feasibility_run --summarize {run_dir}")


if __name__ == "__main__":
    main()
