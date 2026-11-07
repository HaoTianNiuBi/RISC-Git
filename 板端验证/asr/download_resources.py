"""Download the exact official ASR wheel and SenseVoice resources for stage 1."""
import concurrent.futures as cf
import hashlib
import json
import tarfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
DOWNLOADS = ROOT / 'downloads'
DOWNLOADS.mkdir(exist_ok=True)
WHEEL_HASH = 'c4900d66fb5cf30cc7e910a669f929fb59aa40ad0e70be7b3cafa012ac0a41f9'
JOBS = [
    ('spacemit_asr-1.0.2-cp312-cp312-linux_riscv64.whl',
     'https://git.spacemit.com/api/v4/projects/33/packages/pypi/files/' + WHEEL_HASH + '/spacemit_asr-1.0.2-cp312-cp312-linux_riscv64.whl', WHEEL_HASH),
    ('sensevoice.tar.gz', 'https://archive.spacemit.com/spacemit-ai/model_zoo/asr/sensevoice.tar.gz', None),
]

def digest_file(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()

def download(job):
    name, url, expected = job
    target = DOWNLOADS / name
    partial = target.with_name(name + '.part')
    if target.exists() and expected and digest_file(target) == expected:
        return {'name': name, 'source_url': url, 'local_path': str(target),
                'bytes': target.stat().st_size, 'sha256': expected, 'reused': True}
    req = Request(url, headers={'User-Agent': 'RV-Doctor-K1-Stage1/1.0'})
    h, total, milestone = hashlib.sha256(), 0, 0
    with urlopen(req, timeout=45) as response, partial.open('wb') as f:
        declared = response.headers.get('Content-Length')
        if declared and int(declared) > 700 * 1024 * 1024:
            raise ValueError(name + ': unexpected archive size')
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > 700 * 1024 * 1024:
                raise ValueError(name + ': download limit exceeded')
            f.write(chunk)
            h.update(chunk)
            if total // (50 * 1024 * 1024) > milestone:
                milestone = total // (50 * 1024 * 1024)
                print('{}: {:.0f} MiB'.format(name, total / 1024 / 1024), flush=True)
        if declared and total != int(declared):
            raise ValueError(name + ': incomplete download')
    actual = h.hexdigest()
    if expected and actual != expected:
        raise ValueError(name + ': SHA256 mismatch')
    partial.replace(target)
    return {'name': name, 'source_url': url, 'local_path': str(target), 'bytes': total,
            'sha256': actual, 'publisher_sha256_verified': bool(expected),
            'downloaded_at_utc': datetime.now(timezone.utc).isoformat()}

records = []
with cf.ThreadPoolExecutor(max_workers=2) as pool:
    for record in pool.map(download, JOBS):
        records.append(record)
        print('Verified download: {} ({} bytes)'.format(record['name'], record['bytes']), flush=True)
(DOWNLOADS / 'manifest.json').write_text(json.dumps(records, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

required = {'model_quant_optimized.onnx', 'tokens.txt', 'am.mvn'}
dest = ROOT / 'models' / 'sensevoice'
dest.mkdir(parents=True, exist_ok=True)
extracted = []
with tarfile.open(str(DOWNLOADS / 'sensevoice.tar.gz'), 'r:gz') as archive:
    chosen = {}
    for member in archive.getmembers():
        name = PurePosixPath(member.name).name
        if name not in required:
            continue
        if not member.isfile() or member.issym() or member.islnk() or name in chosen:
            raise ValueError('Unexpected or duplicate model member: ' + member.name)
        if member.size > 700 * 1024 * 1024:
            raise ValueError('Model member too large: ' + member.name)
        chosen[name] = member
    if set(chosen) != required:
        raise ValueError('Missing required model resources: ' + repr(required - set(chosen)))
    for name in sorted(chosen):
        member = chosen[name]
        target = dest / name
        with archive.extractfile(member) as source, target.open('wb') as output:
            for chunk in iter(lambda: source.read(1024 * 1024), b''):
                output.write(chunk)
        extracted.append({'archive_member': member.name, 'file': name,
                          'bytes': target.stat().st_size, 'sha256': digest_file(target)})
(dest / 'manifest.json').write_text(json.dumps(extracted, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'downloaded': len(records), 'model_files': extracted}, ensure_ascii=True, indent=2), flush=True)
