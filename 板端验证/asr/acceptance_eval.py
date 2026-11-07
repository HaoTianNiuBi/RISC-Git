"""Score the frozen ten-case ASR dataset; this module never runs recognition.

CLI: python acceptance_eval.py --definitions definitions.json --results results.json
Exit codes: 0 passed, 2 pending, 1 failed. JSON is always written to stdout.
Audio hashes are recorded provenance, not checked against remote files here.
"""
from __future__ import annotations

import argparse
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from pathlib import Path
import re
import unicodedata


_SHA256 = re.compile(r"[0-9a-fA-F]{64}\Z")
_PENDING_STATUSES = {
    None, "pending", "not_recorded", "not_tested", "recorded", "prepared",
    "recording", "recognizing",
}


def normalize_text(text: str) -> str:
    """NFKC, lower, remove Unicode whitespace and P* punctuation, in that order."""
    if not isinstance(text, str):
        raise TypeError("ASR reference and hypothesis must be strings")
    normalized = unicodedata.normalize("NFKC", text).lower()
    return "".join(
        char for char in normalized
        if not char.isspace() and not unicodedata.category(char).startswith("P")
    )


def edit_distance(reference: str, hypothesis: str) -> int:
    """Character Levenshtein distance; no normalization or semantic substitution."""
    if not isinstance(reference, str) or not isinstance(hypothesis, str):
        raise TypeError("edit_distance inputs must be strings")
    if len(reference) < len(hypothesis):
        reference, hypothesis = hypothesis, reference
    previous = list(range(len(hypothesis) + 1))
    for row, ref_char in enumerate(reference, 1):
        current = [row]
        for col, hyp_char in enumerate(hypothesis, 1):
            current.append(min(
                previous[col] + 1,
                current[col - 1] + 1,
                previous[col - 1] + (ref_char != hyp_char),
            ))
        previous = current
    return previous[-1]


def _is_hash(value) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _issue(code: str, message: str, severity: str = "failed") -> dict:
    return {"code": code, "message": message, "severity": severity}


def evaluate_asr(
    definitions: Mapping,
    results: Mapping,
    *,
    expected_dataset_sha256: str | None = None,
) -> dict:
    """Return an assessment without mutating inputs or accessing audio files.

    expected_dataset_sha256 is the SHA256 of the ORIGINAL definitions file bytes.
    A dict cannot recover that byte-level hash; omitting it prevents a pass.
    Missing samples remain pending. Malformed evidence and changed gold fail.
    corpus_cer stays None until every required sample has valid evidence.
    """
    report = {
        "passed": False,
        "status": "failed",
        "dataset_id": None,
        "dataset_sha256": None,
        "dataset_hash_verified": False,
        "completed_cases": 0,
        "required_cases": 10,
        "corpus_cer": None,
        "corpus_cer_max": 0.2,
        "total_edit_distance": None,
        "total_reference_characters": None,
        "ignored_cases": 0,
        "ignored_case_ids": [],
        "diagnostics": [],
        "cases": {},
    }
    issues = report["diagnostics"]
    if not isinstance(definitions, Mapping) or not isinstance(results, Mapping):
        issues.append(_issue("invalid_document", "Definitions and results must be objects."))
        return report

    dataset_id = definitions.get("dataset_id")
    report["dataset_id"] = dataset_id
    report["dataset_sha256"] = results.get("dataset_sha256")
    if not isinstance(dataset_id, str) or not dataset_id:
        issues.append(_issue("invalid_dataset_id", "Definitions need a nonempty dataset_id."))
    elif results.get("dataset_id") != dataset_id:
        issues.append(_issue("dataset_id_mismatch", "Results refer to another dataset."))

    actual_hash = results.get("dataset_sha256")
    if not _is_hash(actual_hash):
        issues.append(_issue("invalid_dataset_hash", "Results need a 64-digit SHA256."))
    if expected_dataset_sha256 is None:
        issues.append(_issue(
            "dataset_hash_unverified", "Expected original definitions hash was not supplied.",
            "pending",
        ))
    elif not _is_hash(expected_dataset_sha256):
        issues.append(_issue("invalid_expected_hash", "Expected dataset SHA256 is malformed."))
    elif _is_hash(actual_hash):
        if actual_hash.lower() != expected_dataset_sha256.lower():
            issues.append(_issue("dataset_hash_mismatch", "Frozen definitions hash differs."))
        else:
            report["dataset_hash_verified"] = True

    acceptance = definitions.get("acceptance", {})
    settings = acceptance.get("asr", {}) if isinstance(acceptance, Mapping) else {}
    if not isinstance(settings, Mapping):
        settings = {}
    required = settings.get("required_case_ids")
    if (
        not isinstance(required, list) or len(required) != 10
        or not all(isinstance(cid, str) and cid for cid in required)
        or len(set(required)) != 10
    ):
        issues.append(_issue("invalid_required_cases", "Exactly ten unique required IDs are needed."))
        return report
    try:
        threshold = Decimal(str(settings["corpus_cer_max"]))
        if not threshold.is_finite() or not Decimal("0") <= threshold <= Decimal("0.2"):
            raise ValueError("CER threshold must not exceed 0.2")
    except (KeyError, InvalidOperation, ValueError):
        issues.append(_issue("invalid_cer_threshold", "CER limit must be between 0 and 0.2."))
        return report
    report["corpus_cer_max"] = float(threshold)

    gold_rows = definitions.get("asr_cases")
    if not isinstance(gold_rows, list):
        issues.append(_issue("invalid_gold", "Definitions need the asr_cases array."))
        return report
    gold = {}
    for row in gold_rows:
        if not isinstance(row, Mapping) or not isinstance(row.get("case_id"), str):
            issues.append(_issue("invalid_gold", "Every ASR definition needs a case_id."))
            return report
        if row["case_id"] in gold:
            issues.append(_issue("duplicate_gold", "Duplicate ASR definition ID."))
            return report
        gold[row["case_id"]] = row

    case_results = results.get("cases", {})
    if not isinstance(case_results, Mapping):
        issues.append(_issue("invalid_results_cases", "Results cases must be an object."))
        return report
    extras = sorted(str(cid) for cid in case_results if cid not in required)
    report["ignored_cases"] = len(extras)
    report["ignored_case_ids"] = extras
    total_edits = total_characters = 0

    for cid in required:
        item = {
            "case_id": cid, "status": "pending", "completed": False,
            "reference_text": None, "normalized_reference": None,
            "hypothesis": None, "normalized_hypothesis": None,
            "edit_distance": None, "reference_characters": None, "cer": None,
            "diagnostics": [],
        }
        report["cases"][cid] = item
        row = gold.get(cid, {})
        reference = row.get("reference_text")
        if not isinstance(reference, str) or not normalize_text(reference):
            item["status"] = "failed"
            item["diagnostics"].append(_issue("invalid_reference", "Frozen reference is missing or empty."))
            continue
        normalized = normalize_text(reference)
        item.update(
            reference_text=reference, normalized_reference=normalized,
            reference_characters=len(normalized),
        )
        record = case_results.get(cid)
        if record is None:
            item["diagnostics"].append(_issue("missing_result", "No result for this required ID.", "pending"))
            continue
        if not isinstance(record, Mapping):
            item["status"] = "failed"
            item["diagnostics"].append(_issue("invalid_record", "Result must be an object."))
            continue
        source_status = record.get("status")
        if source_status != "recognized":
            pending = isinstance(source_status, (str, type(None))) and source_status in _PENDING_STATUSES
            item["status"] = "pending" if pending else "failed"
            item["diagnostics"].append(_issue(
                "not_recognized", "Sample has not completed recognition.", item["status"],
            ))
            continue
        if record.get("reference_text") != reference:
            item["diagnostics"].append(_issue("reference_mismatch", "Recorded gold differs from frozen gold."))
        hypothesis = record.get("hypothesis")
        if not isinstance(hypothesis, str):
            item["diagnostics"].append(_issue("invalid_hypothesis", "Hypothesis must be a string; empty is allowed."))
        for prefix in ("raw_audio", "prepared_audio"):
            path = record.get(prefix + "_path")
            if not isinstance(path, str) or not path.strip():
                item["diagnostics"].append(_issue("missing_audio_path", prefix + " path is required."))
            if not _is_hash(record.get(prefix + "_sha256")):
                item["diagnostics"].append(_issue("invalid_audio_hash", prefix + " SHA256 is required."))
        wall = record.get("recognition_wall_seconds")
        if wall is not None and (
            isinstance(wall, bool) or not isinstance(wall, (int, float))
            or not math.isfinite(wall) or wall < 0
        ):
            item["diagnostics"].append(_issue("invalid_wall_time", "Wall time must be finite and nonnegative."))
        if item["diagnostics"]:
            item["status"] = "failed"
            continue
        normalized_hypothesis = normalize_text(hypothesis)
        distance = edit_distance(normalized, normalized_hypothesis)
        item.update(
            status="scored", completed=True, hypothesis=hypothesis,
            normalized_hypothesis=normalized_hypothesis,
            edit_distance=distance, cer=distance / len(normalized),
        )
        report["completed_cases"] += 1
        total_edits += distance
        total_characters += len(normalized)

    complete = report["completed_cases"] == 10
    if complete:
        report.update(
            total_edit_distance=total_edits,
            total_reference_characters=total_characters,
            corpus_cer=total_edits / total_characters,
        )
        if Decimal(total_edits) > threshold * Decimal(total_characters):
            issues.append(_issue("cer_above_threshold", "Corpus CER exceeds the frozen limit."))
    elif not any(item["status"] == "failed" for item in report["cases"].values()):
        issues.append(_issue("incomplete_dataset", "All ten required samples must finish.", "pending"))

    failed = any(issue["severity"] == "failed" for issue in issues) or any(
        item["status"] == "failed" for item in report["cases"].values()
    )
    pending = not complete or any(issue["severity"] == "pending" for issue in issues)
    report["status"] = "failed" if failed else "pending" if pending else "passed"
    report["passed"] = report["status"] == "passed"
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--definitions", required=True, type=Path)
    parser.add_argument("--results", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        definition_bytes = args.definitions.read_bytes()
        definitions = json.loads(definition_bytes.decode("utf-8-sig"))
        results = json.loads(args.results.read_bytes().decode("utf-8-sig"))
        report = evaluate_asr(
            definitions, results,
            expected_dataset_sha256=hashlib.sha256(definition_bytes).hexdigest(),
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        report = {
            "passed": False, "status": "failed", "completed_cases": 0,
            "required_cases": 10, "corpus_cer": None, "cases": {},
            "diagnostics": [_issue("input_error", str(exc))],
        }
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    return {"passed": 0, "pending": 2, "failed": 1}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
