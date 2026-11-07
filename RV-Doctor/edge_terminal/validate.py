"""Run the frozen Stage-2 cases against the live K1 modules and save evidence."""

import argparse
import json
from pathlib import Path
import time
import uuid

from audio import AudioPipeline, digest
from intent import DEFAULT_URL, IntentClient
from tasks import TaskDispatcher
from terminal import atomic_json, read_definitions, route


def audio_sources(base, source_results, definitions, rows):
    source = json.loads(source_results.read_text(encoding='utf-8'))
    if source['dataset_sha256'] != digest(base / 'acceptance-cases.json'):
        raise ValueError('阶段 1 录音使用了不同的验收定义。')
    references = {case['case_id']: case['reference_text'] for case in definitions['asr_cases']}
    paths = {}
    missing = []
    for row in rows:
        cid = row['case_id']
        source_id = row['source_asr_case_id']
        if source_id is None:
            fixture = base / 'fixtures' / (cid + '.json')
            if not fixture.is_file():
                missing.append(cid)
                continue
            recording = json.loads(fixture.read_text(encoding='utf-8'))
            source_root = base / 'captures'
            if recording['case_id'] != cid or recording['reference_text'] != row['reference_text']:
                raise ValueError(f'{cid} 的补录样本与固定定义不符。')
        else:
            recording = source['cases'][source_id]
            source_root = source_results.parent
            if recording['reference_text'] != references[source_id] or references[source_id] != row['reference_text']:
                raise ValueError(f'{cid} 的阶段 1 音频来源与参考文本不符。')
        path = Path(recording['raw_audio_path']).resolve(strict=True)
        if not path.is_relative_to(source_root.resolve()) or digest(path) != recording['raw_audio_sha256']:
            raise ValueError(f'{cid} 的音频路径或哈希不匹配。')
        paths[cid] = path
    if missing:
        raise ValueError(f'已就绪 {len(paths)}/{len(rows)} 条，待补录 {", ".join(missing)}。')
    return paths


def summarize(report, rows, acceptance):
    breakdown = {intent: {'correct': 0, 'required': 0} for intent in
                 dict.fromkeys(row['intent_gold'] for row in rows)}
    for row in rows:
        counts = breakdown[row['intent_gold']]
        counts['required'] += 1
        counts['correct'] += int(report['cases'].get(row['case_id'], {}).get('intent_correct', False))
    observations = report['cases'].values()
    valid = sum(case['json_valid'] for case in observations)
    correct = sum(counts['correct'] for counts in breakdown.values())
    unknown_dispatches = [case['case_id'] for case in observations
                          if case['case_id'] in acceptance['unknown_case_ids']
                          and case['observed_dispatch_intent'] is not None]
    task_failures = [case['case_id'] for case in observations if case['observed_dispatch_intent']
                     and not (case['task_result'] or {}).get('ok')]
    denominator = acceptance['denominator']
    complete = len(report['cases']) == denominator
    passed = (complete and valid == denominator and correct >= acceptance['minimum_correct_cases']
              and not unknown_dispatches and not (report['test_mode'] == 'audio_e2e' and task_failures))
    return {
        'status': 'passed' if passed else 'failed' if complete else 'pending',
        'passed': passed, 'completed': len(report['cases']), 'required': denominator,
        'json_valid': valid, 'intent_correct': correct, 'per_intent': breakdown,
        'unknown_dispatches': unknown_dispatches,
        'task_failures': task_failures,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--mode', choices=('text_intent', 'audio_e2e'), required=True)
    parser.add_argument('--source-results', type=Path)
    parser.add_argument('--model-dir', type=Path)
    parser.add_argument('--llm-url', default=DEFAULT_URL)
    args = parser.parse_args()
    base = args.base.resolve()
    definitions = read_definitions(base)
    acceptance = definitions['acceptance']['intent']
    by_id = {row['case_id']: row for row in definitions['intent_cases']}
    rows = [by_id[cid] for cid in acceptance['required_case_ids']]
    sources = None
    audio = None
    if args.mode == 'audio_e2e':
        from acceptance_eval import edit_distance, normalize_text

        if args.source_results is None or args.model_dir is None:
            parser.error('audio_e2e requires --source-results and --model-dir')
        sources = audio_sources(base, args.source_results.resolve(), definitions, rows)
        audio = AudioPipeline(args.model_dir)
    client = IntentClient(definitions['intent_output_schema'], url=args.llm_url)
    dispatcher = TaskDispatcher(base)
    run_id = args.mode + '-' + uuid.uuid4().hex[:12]
    directory = base / 'validation' / run_id
    directory.mkdir(parents=True)
    report_path = directory / 'report.json'
    report = {
        'run_id': run_id, 'test_mode': args.mode, 'dataset_sha256': digest(base / 'acceptance-cases.json'),
        'llm': client.metadata, 'audio': audio.metadata if audio else None,
        'source_results_sha256': digest(args.source_results) if sources else None,
        'cases': {},
    }
    try:
        for row in rows:
            cid = row['case_id']
            started = time.perf_counter()
            evidence = {}
            try:
                text = row['reference_text']
                if audio is not None:
                    output_dir = directory / cid
                    output_dir.mkdir()
                    evidence = audio.transcribe(sources[cid], output_dir)
                    text = evidence['raw_asr_text']
                    reference = normalize_text(row['reference_text'])
                    hypothesis = normalize_text(text)
                    evidence.update(normalized_reference=reference, normalized_hypothesis=hypothesis,
                                    edit_distance=edit_distance(reference, hypothesis),
                                    reference_characters=len(reference))
                result = route(text, client, dispatcher)
            except Exception as exc:
                text = evidence.get('raw_asr_text') if audio is not None else row['reference_text']
                result = {'raw_intent_output': None, 'json_valid': False, 'intent': None,
                          'first_content_latency_s': None, 'total_latency_s': None,
                          'observed_dispatch_intent': None, 'task_result': None, 'failure_reason': str(exc)}
            observation = {
                **evidence, **result, 'case_id': cid, 'reference_text': row['reference_text'],
                'audio_file_or_text_input': str(sources[cid]) if sources else text,
                'intent_gold': row['intent_gold'],
                'intent_correct': result['json_valid'] and result['intent'] == row['intent_gold'],
                'cold_or_warm': 'model_loaded', 'case_wall_seconds': time.perf_counter() - started,
            }
            report['cases'][cid] = observation
            report['summary'] = summarize(report, rows, acceptance)
            atomic_json(report_path, report)
            print(json.dumps({'case_id': cid, 'json_valid': observation['json_valid'],
                              'intent': observation['intent'], 'correct': observation['intent_correct']},
                             ensure_ascii=False), flush=True)
    finally:
        if audio is not None:
            audio.close()
    print(json.dumps({'report': str(report_path), **report['summary']}, ensure_ascii=False), flush=True)
    return 0 if report['summary']['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
