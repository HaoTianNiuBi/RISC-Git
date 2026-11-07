"""Prepare only the current audio-device ACL, then run the collector as haotian."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', type=Path, default=Path('/home/haotian/rv-doctor-edge-validation/asr'))
    parser.add_argument('--port', type=int, default=18791)
    args = parser.parse_args()
    base = args.base.resolve()
    manifest = json.loads((base / 'deploy-manifest.json').read_text())
    for item in manifest['files']:
        path = (base / item['file']).resolve()
        if base not in path.parents:
            raise RuntimeError('Manifest path leaves the validation directory')
        data = path.read_bytes()
        if len(data) != item['bytes'] or hashlib.sha256(data).hexdigest() != item['sha256']:
            raise RuntimeError('Deployment file mismatch: ' + item['file'])
    if os.geteuid() != 0:
        raise RuntimeError('Use the existing ADB root shell to prepare this temporary audio-device access')
    text = Path('/proc/asound/cards').read_text()
    match = re.search(r'^\s*(\d+)\s+\[sndes8326\s*\]', text, re.M)
    if not match:
        raise RuntimeError('ES8326 recording device not found')
    card = match.group(1)
    devices = ['/dev/snd/pcmC%sD0c' % card, '/dev/snd/controlC%s' % card]
    for tool in ('getfacl', 'setfacl', 'runuser'):
        if not shutil.which(tool):
            raise RuntimeError('Required existing tool not found: ' + tool)
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1', args.port)) == 0:
            raise RuntimeError('Collector port is already in use; do not start a second service')
    subprocess.run(['runuser', '-u', 'haotian', '--', 'mkdir', '-p', str(base / 'results')], check=True)
    boot_id = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    backup = base / 'results' / ('audio-acl-before-' + boot_id + '.txt')
    if not backup.exists():
        backup.write_bytes(subprocess.check_output(['getfacl', '-p'] + devices))
    subprocess.run(['setfacl', '-m', 'u:haotian:rw'] + devices, check=True)
    print(json.dumps({'deployment_verified': True, 'temporary_audio_devices': devices,
                      'acl_backup': str(backup), 'service_user': 'haotian'}), flush=True)
    os.environ['PYTHONUNBUFFERED'] = '1'
    os.execvp('runuser', ['runuser', '-u', 'haotian', '--',
                         str(base / 'venv/bin/python'), str(base / 'capture_acceptance.py'),
                         '--base', str(base), '--port', str(args.port)])


if __name__ == '__main__':
    main()
