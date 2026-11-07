"""Prepare traceable PCM16 mono inputs; keep the source recording unchanged."""
import argparse
import hashlib
import json
import wave
from pathlib import Path

import numpy as np


def downsample_mono(samples, source_rate, target_rate):
    if source_rate == target_rate:
        return samples.copy()
    if source_rate % target_rate:
        raise ValueError('Only integer downsampling ratios are supported')
    ratio = source_rate // target_rate
    if ratio < 1:
        raise ValueError('Upsampling is not supported')
    # Windowed-sinc anti-alias filter. The fixed passband is suitable for speech.
    taps = 127
    cutoff = target_rate * 0.45
    n = np.arange(taps, dtype=np.float64) - (taps - 1) / 2
    filt = 2 * cutoff / source_rate * np.sinc(2 * cutoff / source_rate * n)
    filt *= np.hamming(taps)
    filt /= filt.sum()
    return np.convolve(samples, filt, mode='same')[::ratio]


def prepare(source, output, target_rate=16000, trim_start=0.0, gain_db=0.0):
    source, output = Path(source), Path(output)
    if source.resolve() == output.resolve():
        raise ValueError('Source recording must not be overwritten')
    before_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    with wave.open(str(source), 'rb') as w:
        channels, source_rate, width = w.getnchannels(), w.getframerate(), w.getsampwidth()
        if width != 2 or w.getcomptype() != 'NONE':
            raise ValueError('Expected uncompressed signed 16-bit PCM WAV')
        frames = w.getnframes()
        samples = np.frombuffer(w.readframes(frames), dtype='<i2').reshape(-1, channels)
    trim_frames = int(round(trim_start * source_rate))
    if trim_frames < 0 or trim_frames >= frames:
        raise ValueError('Invalid trim duration')
    mono = samples[trim_frames:].astype(np.float64).mean(axis=1)
    resampled = downsample_mono(mono, source_rate, target_rate)
    amplified = resampled * 10 ** (gain_db / 20)
    clipped = int(np.count_nonzero((amplified > 32767) | (amplified < -32768)))
    if clipped:
        raise ValueError('Requested gain would clip {} samples'.format(clipped))
    pcm = np.rint(amplified).astype('<i2')
    output.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(target_rate)
        w.writeframes(pcm.tobytes())
    if hashlib.sha256(source.read_bytes()).hexdigest() != before_hash:
        raise RuntimeError('Source recording changed')
    report = {
        'source': str(source), 'source_sha256': before_hash,
        'source_rate': source_rate, 'source_channels': channels, 'source_frames': frames,
        'output': str(output), 'output_sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
        'output_rate': target_rate, 'output_channels': 1, 'output_frames': len(pcm),
        'duration_seconds': len(pcm) / target_rate, 'trim_start_seconds': trim_start,
        'gain_db': gain_db, 'clipped_samples': clipped,
        'resampling': '127-tap Hamming-windowed sinc, cutoff 0.45*target_rate, integer decimation',
        'peak': int(np.max(np.abs(pcm.astype(np.int32)))) if len(pcm) else 0,
        'purpose': 'ASR input preparation; not an ASR accuracy result',
    }
    output.with_suffix('.prepare.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--target-rate', type=int, default=16000)
    parser.add_argument('--trim-start', type=float, default=0)
    parser.add_argument('--gain-db', type=float, default=0)
    args = parser.parse_args()
    print(json.dumps(prepare(args.source, args.output, args.target_rate, args.trim_start, args.gain_db), ensure_ascii=True, indent=2))
