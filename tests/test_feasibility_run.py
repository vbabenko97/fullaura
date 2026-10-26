"""The feasibility script rejects bad case files and counts every planned image."""

import csv
import json
from pathlib import Path

import pytest

from backend.app.meta_images import ImageRequestError, ImageResult, InputImage
from scripts.feasibility_run import CHECKS, identity_by_subject, load_cases, run, summarize, tally
from tests.test_meta_images import exif, jpeg

CASE = {
    "id": "s1-o1",
    "subject": "s1",
    "photos": ["portrait.jpg"],
    "garments": ["shirt.png"],
    "look_prompt": "Show the person wearing the shirt.",
    "edit_prompt": "Add the jacket.",
    "edit_garment": "jacket.webp",
}
FILES = ["portrait.jpg", "shirt.png", "jacket.webp"]
WEBP = b"RIFF\x10\x00\x00\x00WEBPVP8 "


def write_cases(folder: Path, cases: list[dict[str, object]], files: list[str]) -> Path:
    """Write the input files (JPEG skeletons for .jpg, rotated ones for rotated*) and cases."""
    for name in files:
        content = (
            jpeg(exif(6))
            if name.startswith("rotated")
            else jpeg()
            if name.endswith(".jpg")
            else b"image"
        )
        (folder / name).write_bytes(content)
    path = folder / "cases.json"
    path.write_text(json.dumps({"cases": cases}), encoding="utf-8")
    return path


def test_accepts_complete_and_look_only_cases(tmp_path: Path) -> None:
    assert load_cases(write_cases(tmp_path, [CASE], FILES))[0]["id"] == "s1-o1"
    look_only = {key: CASE[key] for key in ("id", "subject", "photos", "garments", "look_prompt")}
    assert load_cases(write_cases(tmp_path, [look_only], FILES[:2]))[0]["id"] == "s1-o1"


@pytest.mark.parametrize(
    ("cases", "files", "message"),
    [
        ([CASE], FILES[:2], "jacket.webp"),
        ([{**CASE, "subject": ""}], FILES, "subject"),
        ([{**CASE, "edit_prompt": ""}], FILES, "edit_garment needs an edit_prompt"),
        ([{**CASE, "photos": ["portrait.heic"]}], ["portrait.heic", *FILES[1:]], "portrait.heic"),
        ([{**CASE, "id": "s1.o1"}], FILES, "ids are unique"),
        ([{**CASE, "id": "../x"}], FILES, "ids are unique"),
        ([CASE, CASE], FILES, "ids are unique"),
        ([{**CASE, "photos": ["portrait.png"]}], ["portrait.png", *FILES[1:]], "must be JPEG"),
        ([{**CASE, "photos": ["rotated.jpg"]}], ["rotated.jpg", *FILES[1:]], "upright"),
    ],
)
def test_rejects_a_bad_case_before_any_api_call(
    tmp_path: Path, cases: list[dict[str, object]], files: list[str], message: str
) -> None:
    with pytest.raises(SystemExit, match=message):
        load_cases(write_cases(tmp_path, cases, files))


def row(subject: str, step: str, status: str, scores: str) -> dict[str, str]:
    """A scores.csv row; scores lists the four checks, separated by spaces."""
    checks = dict(zip(CHECKS, scores.split(), strict=True))
    return {
        "case": "c",
        "subject": subject,
        "sample": "1",
        "step": step,
        "status": status,
        **checks,
    }


def test_failed_skipped_and_unassessable_images_stay_in_the_denominator() -> None:
    rows = [
        row("s1", "look", "ok", "Pass pass fail n/a"),
        row("s1", "edit", "ok", "unassessable fail pass FAIL"),
        row("s2", "look", "call_failed", "- - - n/a"),
        row("s2", "edit", "skipped", "- - - -"),
    ]
    counts = tally(rows)
    expected = {
        "identity": (4, 2, 1, 0, 1),
        "proportions": (4, 2, 1, 1, 0),
        "product_details": (4, 2, 1, 1, 0),
        "edit_succeeds": (2, 1, 0, 1, 0),
    }
    keys = ("planned", "not_produced", "pass", "fail", "unassessable")
    assert {check: tuple(count[key] for key in keys) for check, count in counts.items()} == expected
    assert identity_by_subject(rows) == {"s1": (1, 2), "s2": (0, 2)}


@pytest.mark.parametrize(
    ("step", "scores"),
    [
        ("look", "pass ? pass n/a"),  # not a score
        ("look", "n/a pass pass n/a"),  # n/a on a check that applies to every image
        ("edit", "pass pass pass n/a"),  # an edit left without its edit score
    ],
)
def test_refuses_unscored_or_misapplied_scores(step: str, scores: str) -> None:
    with pytest.raises(SystemExit, match="Score these"):
        tally([row("s1", step, "ok", scores)])


class FakeClient:
    """Fails every look of subject s1 and succeeds otherwise."""

    model = "muse-image-1.0"

    def edit(self, prompt: str, images: list[InputImage]) -> ImageResult:
        if prompt == "fail":
            raise ImageRequestError("HTTP 503", 3)
        return ImageResult(WEBP, "image/webp", 1, 0.5, {}, None)


def test_a_run_records_failed_looks_and_their_skipped_edits(tmp_path: Path) -> None:
    second = {**CASE, "id": "s2-o1", "subject": "s2"}
    cases = [{**CASE, "look_prompt": "fail"}, second]
    run_dir, results = run(
        load_cases(write_cases(tmp_path, cases, FILES)), tmp_path, 1, FakeClient()
    )
    with (run_dir / "scores.csv").open(newline="") as file:
        rows = [(r["case"], r["step"], r["status"], r["file"]) for r in csv.DictReader(file)]
    assert rows == [
        ("s1-o1", "look", "call_failed", ""),
        ("s1-o1", "edit", "skipped", ""),
        ("s2-o1", "look", "ok", "s2-o1-1.webp"),
        ("s2-o1", "edit", "ok", "s2-o1-1-edit.webp"),
    ]
    assert len(results) == 2
    assert len((run_dir / "attempts.jsonl").read_text().splitlines()) == 3
    with pytest.raises(SystemExit, match="Score these"):
        summarize(run_dir)


def test_summarize_refuses_an_unfinished_run(tmp_path: Path) -> None:
    (tmp_path / "run.json").write_text(json.dumps({"planned_images": 4}))
    (tmp_path / "scores.csv").write_text(",".join(["case", "subject", "sample", "step", "status"]))
    with pytest.raises(SystemExit, match="0 of 4 planned rows"):
        summarize(tmp_path)
