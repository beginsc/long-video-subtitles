#!/usr/bin/env python3
"""DoveBench media preflight using numeric English fingerprints, never translation input."""
import argparse
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def grams(text):
    aliases = {'raynor': 'reynor', 'zerk': 'zerg', 'zork': 'zerg'}
    words = [aliases.get(w, w) for w in re.findall(r'[a-z0-9]+', text.casefold())]
    return set(zip(words, words[1:], words[2:]))


def compare(source, reference, duration, window=90):
    if not math.isfinite(duration) or duration <= 0 or not 0 < window <= duration:
        raise ValueError('Invalid media duration/window')
    points = sorted({0, max(0, duration / 2 - window / 2), duration - window})
    samples = []
    for lo in points:
        hi = lo + window
        a = grams(' '.join(s['text'] for s in source if s['start'] < hi and s['end'] > lo))
        b = grams(' '.join(s['text'] for s in reference if s['start'] < hi and s['end'] > lo))
        common = len(a & b)
        samples.append(dict(start_seconds=lo, end_seconds=hi, source_3grams=len(a),
                            reference_3grams=len(b), shared_3grams=common,
                            reference_coverage=common / max(1, len(b)),
                            jaccard=common / max(1, len(a | b))))
    enough = all(s['source_3grams'] >= 15 and s['reference_3grams'] >= 15 for s in samples)
    consistent = enough and all(s['reference_coverage'] >= .20 and s['shared_3grams'] >= 15 for s in samples)
    status = 'consistent_fingerprint' if consistent else ('requires_identity_review' if enough else 'insufficient_content')
    return dict(status=status, samples=samples, thresholds=dict(reference_coverage=.20, shared_3grams=15),
                interpretation='Heuristic content consistency, not certified identity or ASR accuracy. No reference tokens disclosed.')


def english_reference(cache, video_id):
    manifest = read(cache / 'cache.json')
    path = cache / 'data/dovebench.jsonl'
    if sha(path) != manifest['data_sha256']:
        raise ValueError('Pinned reference cache hash mismatch')
    result = []
    youtube_ids = set()
    with path.open(encoding='utf-8') as f:
        for line in f:
            row = json.loads(line)
            if row['video_id'] != video_id:
                continue
            # zh_text is deliberately not retained or consumed by the comparison.
            if not isinstance(row.get('en_text'), str):
                raise ValueError('Invalid English reference type')
            start, end = row['start'], row['end']
            if not all(type(x) in (int, float) and math.isfinite(x) for x in [start, end]) or not 0 <= start < end:
                raise ValueError('Invalid reference interval')
            result.append(dict(start=start, end=end, text=row['en_text']))
            youtube_ids.add(row['youtube_id'])
    if not result or len(youtube_ids) != 1:
        raise ValueError('Unknown/ambiguous reference video')
    return manifest['data_sha256'], next(iter(youtube_ids)), result


def sources(paths, duration):
    result, manifests = [], []
    for path in paths:
        doc = read(path)
        for row in doc['segments']:
            start, end = row['start_ms'], row['end_ms']
            if not all(type(x) in (int, float) and math.isfinite(x) for x in [start, end]) or not 0 <= start < end <= (duration + 5) * 1000:
                raise ValueError('Source needs absolute media times within duration')
            if not isinstance(row.get('source'), str):
                raise ValueError('Invalid source text')
            result.append(dict(start=start / 1000, end=end / 1000, text=row['source']))
        manifests.append(dict(path=str(Path(path).resolve()), sha256=sha(path)))
    return result, manifests


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--video-id', required=True)
    p.add_argument('--media', required=True)
    p.add_argument('--source', action='append', required=True, help='Source JSON with absolute media milliseconds; repeat for blocks')
    p.add_argument('--ffprobe', default='ffprobe')
    p.add_argument('--cache', type=Path, default=Path(os.environ.get('DOVEBENCH_HOME', str(Path.home() / '.codex/datasets/DoveBench'))))
    p.add_argument('--output', required=True)
    args = p.parse_args()
    output = Path(args.output)
    if output.exists():
        raise ValueError('Never overwrite an identity record; use a new version')
    media = Path(args.media).resolve()
    probe = subprocess.run([args.ffprobe, '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(media)],
                           check=True, capture_output=True, timeout=45)
    info = json.loads(probe.stdout)
    duration = float(info['format']['duration'])
    if not any(s['codec_type'] == 'audio' for s in info['streams']):
        raise ValueError('No audio stream in media; cannot verify end-to-end source')
    cache_sha, catalog_id, ref = english_reference(args.cache, args.video_id)
    src, inputs = sources(args.source, duration)
    result = compare(src, ref, duration, min(90, duration))
    result.update(schema_version=1, created_at_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  video_id=args.video_id, media_path=str(media), media_sha256=sha(media),
                  duration_seconds=duration, catalog_youtube_id=catalog_id, source_files=inputs,
                  reference_data_sha256=cache_sha, reference_content_disclosed_to_translation_model=False,
                  zh_reference_used=False, exposure_note='Automated English-reference comparison occurred. Record separately; does not certify blind testing.')
    with output.open('x', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(json.dumps(dict(video_id=args.video_id, status=result['status'],
                          coverage=[round(s['reference_coverage'], 3) for s in result['samples']]), ensure_ascii=False))


if __name__ == '__main__':
    main()
