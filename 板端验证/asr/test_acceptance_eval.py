"""Synthetic scoring tests only: no recordings, ASR calls, or real score writes."""
import copy
import hashlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from acceptance_eval import edit_distance, evaluate_asr, main, normalize_text


DATASET_HASH = "a" * 64
AUDIO_HASH = "b" * 64


def fixtures(references=None):
    references = references if references is not None else ["甲乙丙丁戊"] * 10
    ids = [f"ASR-{i:02d}" for i in range(1, 11)]
    definitions = {
        "dataset_id": "synthetic-scoring-tests-not-real-measurements",
        "acceptance": {"asr": {"required_case_ids": ids, "corpus_cer_max": 0.2}},
        "asr_cases": [
            {"case_id": cid, "reference_text": text}
            for cid, text in zip(ids, references)
        ],
    }
    results = {
        "dataset_id": definitions["dataset_id"],
        "dataset_sha256": DATASET_HASH,
        "cases": {
            cid: {
                "status": "recognized", "reference_text": text, "hypothesis": text,
                "raw_audio_path": f"/synthetic/raw/{cid}.wav",
                "raw_audio_sha256": AUDIO_HASH,
                "prepared_audio_path": f"/synthetic/prepared/{cid}.wav",
                "prepared_audio_sha256": AUDIO_HASH,
                "recognition_wall_seconds": 0.1,
            }
            for cid, text in zip(ids, references)
        },
    }
    return definitions, results


def score(definitions, results):
    return evaluate_asr(definitions, results, expected_dataset_sha256=DATASET_HASH)


class NormalizationTests(unittest.TestCase):
    def test_nfkc_lower_whitespace_punctuation(self):
        self.assertEqual(normalize_text(" ＡＳＲ，比较４线程！\n\t"), "asr比较4线程")

    def test_no_semantic_or_numeric_equivalence(self):
        self.assertNotEqual(normalize_text("四线程"), normalize_text("4线程"))
        self.assertNotEqual(normalize_text("體檢"), normalize_text("体检"))
        self.assertEqual(normalize_text("喂，喂，喂！"), "喂喂喂")

    def test_symbols_are_retained(self):
        self.assertEqual(normalize_text("甲＋乙"), "甲+乙")

    def test_nonstring_is_not_silently_coerced(self):
        with self.assertRaises(TypeError):
            normalize_text(None)

    def test_hand_calculated_edits(self):
        for reference, hypothesis, expected in [
            ("甲乙丙", "甲丁丙", 1),  # substitution
            ("甲乙丙", "甲乙", 1),  # deletion
            ("甲乙丙", "甲乙丁丙", 1),  # insertion
            ("甲乙丙", "甲丁", 2),
            ("", "甲乙", 2),
            ("甲乙", "", 2),
            ("", "", 0),
        ]:
            with self.subTest(reference=reference, hypothesis=hypothesis):
                self.assertEqual(edit_distance(reference, hypothesis), expected)


class EvaluationTests(unittest.TestCase):
    def test_all_ten_with_provenance_can_pass(self):
        definitions, results = fixtures()
        before = copy.deepcopy((definitions, results))
        report = score(definitions, results)
        self.assertTrue(report["passed"])
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["completed_cases"], 10)
        self.assertEqual(report["corpus_cer"], 0.0)
        self.assertEqual((definitions, results), before)

    def test_corpus_is_weighted_not_mean_case_cer(self):
        definitions, results = fixtures(["甲", "甲乙丙丁"] + ["甲"] * 8)
        results["cases"]["ASR-01"]["hypothesis"] = ""  # 1 deletion / 13 total chars
        report = score(definitions, results)
        self.assertEqual(report["total_edit_distance"], 1)
        self.assertEqual(report["total_reference_characters"], 13)
        self.assertAlmostEqual(report["corpus_cer"], 1 / 13)
        self.assertTrue(report["passed"])

    def test_exact_twenty_percent_boundary_passes(self):
        definitions, results = fixtures()
        for record in results["cases"].values():
            record["hypothesis"] = "甲乙丙丁"  # 10 deletions / 50 reference chars
        report = score(definitions, results)
        self.assertEqual(report["corpus_cer"], 0.2)
        self.assertTrue(report["passed"])

    def test_above_boundary_fails(self):
        definitions, results = fixtures()
        for record in results["cases"].values():
            record["hypothesis"] = "甲乙丙丁"
        results["cases"]["ASR-01"]["hypothesis"] = "甲乙丙"
        report = score(definitions, results)
        self.assertEqual(report["corpus_cer"], 11 / 50)
        self.assertEqual(report["status"], "failed")

    def test_missing_required_cannot_be_replaced_by_extra(self):
        definitions, results = fixtures()
        record = results["cases"].pop("ASR-10")
        results["cases"]["SMOKE-WEIWEIWEI"] = record
        report = score(definitions, results)
        self.assertEqual(report["status"], "pending")
        self.assertFalse(report["passed"])
        self.assertEqual(report["completed_cases"], 9)
        self.assertEqual(report["required_cases"], 10)
        self.assertIsNone(report["corpus_cer"])
        self.assertEqual(report["ignored_case_ids"], ["SMOKE-WEIWEIWEI"])

    def test_unrecorded_is_pending_and_recognition_error_fails(self):
        for status, expected in [("not_recorded", "pending"), ("not_tested", "pending"), ("error", "failed")]:
            definitions, results = fixtures()
            results["cases"]["ASR-01"]["status"] = status
            with self.subTest(status=status):
                report = score(definitions, results)
                self.assertEqual(report["status"], expected)
                self.assertFalse(report["passed"])

    def test_gold_rewrite_rejected_even_if_normalization_matches(self):
        definitions, results = fixtures()
        results["cases"]["ASR-01"]["reference_text"] += "。"
        report = score(definitions, results)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["cases"]["ASR-01"]["diagnostics"][0]["code"], "reference_mismatch")

    def test_empty_and_noise_are_scored_not_dropped(self):
        for hypothesis, expected_edits, expected_cer in [("", 1, 1.0), ("喂喂喂", 3, 3.0), ("， \t", 1, 1.0)]:
            definitions, results = fixtures(["甲"] * 10)
            for record in results["cases"].values():
                record["hypothesis"] = hypothesis
            with self.subTest(hypothesis=hypothesis):
                report = score(definitions, results)
                self.assertEqual(report["completed_cases"], 10)
                self.assertEqual(report["cases"]["ASR-01"]["edit_distance"], expected_edits)
                self.assertEqual(report["corpus_cer"], expected_cer)
                self.assertEqual(report["status"], "failed")

    def test_empty_frozen_reference_is_invalid(self):
        definitions, results = fixtures()
        definitions["asr_cases"][0]["reference_text"] = "， \t"
        results["cases"]["ASR-01"]["reference_text"] = "， \t"
        self.assertEqual(score(definitions, results)["status"], "failed")

    def test_audio_paths_and_hashes_are_required(self):
        for field in ("raw_audio_path", "raw_audio_sha256", "prepared_audio_path", "prepared_audio_sha256"):
            definitions, results = fixtures()
            del results["cases"]["ASR-01"][field]
            with self.subTest(field=field):
                self.assertEqual(score(definitions, results)["status"], "failed")

    def test_invalid_audio_hash_and_missing_hypothesis_fail(self):
        for field, value in [("raw_audio_sha256", "not-a-hash"), ("hypothesis", None)]:
            definitions, results = fixtures()
            results["cases"]["ASR-01"][field] = value
            with self.subTest(field=field):
                self.assertEqual(score(definitions, results)["status"], "failed")

    def test_missing_expected_dataset_hash_prevents_pass(self):
        definitions, results = fixtures()
        report = evaluate_asr(definitions, results)
        self.assertEqual(report["status"], "pending")
        self.assertFalse(report["dataset_hash_verified"])

    def test_dataset_hash_and_id_mismatch_fail(self):
        for field, value in [("dataset_sha256", "c" * 64), ("dataset_id", "other-dataset")]:
            definitions, results = fixtures()
            results[field] = value
            with self.subTest(field=field):
                self.assertEqual(score(definitions, results)["status"], "failed")

    def test_duplicate_required_id_and_relaxed_gate_fail(self):
        definitions, results = fixtures()
        definitions["acceptance"]["asr"]["required_case_ids"][-1] = "ASR-01"
        self.assertEqual(score(definitions, results)["status"], "failed")
        definitions, results = fixtures()
        definitions["acceptance"]["asr"]["corpus_cer_max"] = 0.9
        self.assertEqual(score(definitions, results)["status"], "failed")

    def test_cli_hashes_original_bytes_and_only_prints_report(self):
        definitions, results = fixtures()
        definition_bytes = json.dumps(definitions, ensure_ascii=False, indent=3).encode()
        results["dataset_sha256"] = hashlib.sha256(definition_bytes).hexdigest()
        result_bytes = json.dumps(results, ensure_ascii=False).encode()
        def read_bytes(path):
            return definition_bytes if path.name == "definitions.json" else result_bytes
        output = io.StringIO()
        with patch.object(Path, "read_bytes", read_bytes), patch("sys.stdout", output):
            exit_code = main(["--definitions", "definitions.json", "--results", "results.json"])
        self.assertEqual(exit_code, 0)
        report = json.loads(output.getvalue())
        self.assertTrue(report["passed"])
        self.assertTrue(report["dataset_hash_verified"])


if __name__ == "__main__":
    unittest.main()
