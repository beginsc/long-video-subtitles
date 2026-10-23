"""Prepare real frame inputs for the current GPT session and apply its observations.

This script does not perform model inference or call a paid Vision API.
"""
import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys

from subtitle_workbench import clear_editorial, digest, load, save, structural

CONFIG = Path(__file__).resolve().parent.parent / 'assets' / 'vision-config.json'


def file_hash(path):
    checksum = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            checksum.update(block)
    return checksum.hexdigest()


def read_config(path):
    cfg = load(path)
    if cfg.get('schema_version') != 1 or cfg.get('runner') != 'current-session-gpt':
        raise ValueError('This adapter supports current-session-gpt only')
    if cfg.get('model') != 'inherit-current-session':
        raise ValueError('Change the session model in the app; this script cannot switch models')
    for name in ('frames_per_cue', 'max_cues_per_batch'):
        if type(cfg.get(name)) is not int or not 1 <= cfg[name] <= 64:
            raise ValueError(f'{name} must be an integer from 1 to 64')
    return cfg


def sample_times(start, end, count):
    if type(start) is not int or type(end) is not int or not 0 <= start < end:
        raise ValueError('Invalid media interval')
    return sorted({min(end - 1, start + (end-start) * i // (count+1)) for i in range(1, count+1)})


def check_image(path):
    path = Path(path).resolve(strict=True)
    with path.open('rb') as stream:
        head = stream.read(16)
    supported = head.startswith(b'\xff\xd8\xff') or head.startswith(b'\x89PNG\r\n\x1a\n') or (
        head[:4] == b'RIFF' and head[8:12] == b'WEBP')
    if not supported:
        raise ValueError('Expected a real JPEG, PNG, or WebP file')
    return path


def frame_record(frame, base, index, start, end):
    time_ms = frame.get('time_ms')
    if type(time_ms) is not int or not start <= time_ms < end:
        raise ValueError('Frame timestamp must lie within its source cue interval')
    path = Path(frame['path'])
    path = check_image(path if path.is_absolute() else base / path)
    return {'frame_id': f'f{index:06d}', 'time_ms': time_ms, 'path': str(path), 'sha256': file_hash(path)}


def make_packet(doc, mapping, mapping_base, cfg):
    rows = structural(doc)
    by_id = {r['id']: r for r in rows}
    selected = mapping['segments']
    if not selected or len(selected) > cfg['max_cues_per_batch']:
        raise ValueError('Empty batch or too many cues; split into smaller frame batches')
    ids = [r['id'] for r in selected]
    if len(ids) != len(set(ids)) or set(ids) - set(by_id):
        raise ValueError('Frame map contains duplicate or unknown source IDs')
    if ids != [r['id'] for r in rows if r['id'] in set(ids)]:
        raise ValueError('Frame-map cues must follow source timeline order')
    packet_rows, counter = [], 0
    for item in selected:
        original = by_id[item['id']]
        if not isinstance(item.get('frames'), list) or not item['frames']:
            raise ValueError('Each selected cue needs at least one actual image')
        if len(item['frames']) > cfg['frames_per_cue']:
            raise ValueError('Frame count exceeds configured budget')
        frames = []
        for frame in item['frames']:
            counter += 1
            frames.append(frame_record(frame, mapping_base, counter, original['start_ms'], original['end_ms']))
        if [f['time_ms'] for f in frames] != sorted({f['time_ms'] for f in frames}):
            raise ValueError('Frame timestamps must be unique and increasing per cue')
        packet_rows.append({'id': item['id'], 'source_snapshot': copy.deepcopy(original), 'frames': frames})
    packet = {'schema_version': 1, 'kind': 'gpt-vision-batch', 'config': cfg,
              'project_meta': copy.deepcopy(doc.get('meta', {})), 'media': mapping.get('media'),
              'segments': packet_rows}
    packet['batch_id'] = 'vision-' + digest(packet)[:16]
    packet['input_digest'] = digest(packet)
    return packet


def pack_frames(args):
    mapping = load(args.frames_map)
    if not isinstance(mapping.get('media'), dict) or not mapping['media'].get('identity_note'):
        raise ValueError('Existing-frame map needs media.identity_note describing the source and timeline provenance')
    packet = make_packet(load(args.source), mapping, Path(args.frames_map).resolve().parent, read_config(args.config))
    save(args.output, packet)
    print(f'Prepared {len(packet["segments"])} cues for GPT image reading; no images analyzed yet')


def executable(value):
    found = shutil.which(value)
    if found:
        return found
    path = Path(value)
    if path.is_file():
        return str(path.resolve())
    raise ValueError(f'Required executable unavailable: {value}; configure its path or use pack-frames')


def run_media(command):
    try:
        result = subprocess.run(command, capture_output=True, timeout=120, check=False)
    except subprocess.TimeoutExpired:
        raise ValueError('Media tool timed out; keep partial frames and use a smaller batch') from None
    if result.returncode:
        raise ValueError('Media tool failed; check input format, permissions and selected time range')
    return result.stdout


def extract(args):
    cfg = read_config(args.config)
    doc = load(args.source)
    all_rows = structural(doc)
    if args.start_index < 0 or args.start_index >= len(all_rows):
        raise ValueError('start-index is outside the source cue list')
    rows = all_rows[args.start_index:args.start_index+cfg['max_cues_per_batch']]
    ffmpeg, ffprobe = executable(args.ffmpeg), executable(args.ffprobe)
    media = Path(args.media).resolve(strict=True)
    # Local media only. Never let an input playlist fetch remote resources.
    protocol = 'file,pipe'
    probe = json.loads(run_media([ffprobe, '-v', 'error', '-protocol_whitelist', protocol,
        '-show_format', '-show_streams', '-of', 'json', str(media)]))
    if not any(s.get('codec_type') == 'video' for s in probe.get('streams', [])):
        raise ValueError('No video stream found')
    duration = float(probe.get('format', {}).get('duration', 0))
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('Cannot determine actual media duration')
    # Use relative media timestamps. A subclip needs the explicit original offset.
    if args.media_offset_ms < 0:
        raise ValueError('media-offset-ms must be nonnegative')
    for r in rows:
        if r['start_ms'] < args.media_offset_ms or r['end_ms'] - args.media_offset_ms > round(duration * 1000):
            raise ValueError('Source cue falls outside this media timeline; check media-offset-ms')
    out = Path(args.output_dir).resolve()
    if out.exists():
        raise ValueError('Output directory exists; choose a new batch/version to preserve work')
    out.mkdir(parents=True, exist_ok=False)
    initial_stat = (media.stat().st_size, media.stat().st_mtime_ns)
    mapping = {'media': {'path': str(media), 'sha256': file_hash(media), 'duration_ms': round(duration*1000),
                         'offset_ms': args.media_offset_ms, 'identity_note': 'Extracted from this local media; timestamps are source-timeline milliseconds.'},
               'segments': []}
    for n, r in enumerate(rows, 1):
        frames = []
        for j, time_ms in enumerate(sample_times(r['start_ms'], r['end_ms'], cfg['frames_per_cue']), 1):
            path = out / f'cue-{n:04d}-frame-{j:02d}.jpg'
            local_time = (time_ms - args.media_offset_ms) / 1000
            run_media([ffmpeg, '-v', 'error', '-n', '-protocol_whitelist', protocol,
                       '-ss', f'{local_time:.3f}', '-i', str(media), '-map', '0:v:0',
                       '-frames:v', '1', '-q:v', '2', str(path)])
            check_image(path)
            frames.append({'path': str(path), 'time_ms': time_ms})
        mapping['segments'].append({'id': r['id'], 'frames': frames})
    if (media.stat().st_size, media.stat().st_mtime_ns) != initial_stat:
        raise ValueError('Media changed during extraction; do not use this batch')
    packet = make_packet(doc, mapping, out, cfg)
    save(out / 'vision-batch.json', packet)
    print(json.dumps({'batch': str(out / 'vision-batch.json'), 'cues': len(rows),
                      'next_start_index': args.start_index+len(rows), 'total_cues': len(all_rows),
                      'analyzed': False}))


def texts(value, label):
    if not isinstance(value, list) or any(not isinstance(v, str) or not v.strip() for v in value):
        raise ValueError(f'{label} must be a list of nonempty strings')
    return value


def apply_vision(args):
    doc, packet, result = load(args.source), load(args.packet), load(args.result)
    rows = structural(doc)
    if packet.get('kind') != 'gpt-vision-batch' or digest({k: v for k, v in packet.items() if k != 'input_digest'}) != packet.get('input_digest'):
        raise ValueError('Invalid or changed vision packet')
    if any(result.get(k) != packet.get(k) for k in ('batch_id', 'input_digest')):
        raise ValueError('Vision result belongs to different or stale inputs')
    if doc.get('meta', {}) != packet['project_meta']:
        raise ValueError('Project context changed; prepare a new vision batch')
    originals = {r['id']: r for r in packet['segments']}
    by_id = {r['id']: r for r in rows}
    changes = result.get('segments', [])
    ids = [r['id'] for r in changes]
    if len(ids) != len(set(ids)) or set(ids) != set(originals):
        raise ValueError('Vision results must cover the exact batch IDs once each')
    producer = result.get('producer')
    if not isinstance(producer, dict) or producer.get('runner') != 'current-session-gpt' or not producer.get('image_tool'):
        raise ValueError('Record the actual GPT-session image tool in producer')
    for change in changes:
        original = originals[change['id']]
        r = by_id.get(change['id'])
        if r != original['source_snapshot']:
            raise ValueError('Source cue changed; prepare another vision batch')
        for frame in original['frames']:
            if file_hash(check_image(frame['path'])) != frame['sha256']:
                raise ValueError('Frame changed after preparation; prepare and read it again')
        status = change.get('vision_status')
        if status not in ('analyzed', 'no_relevant_evidence', 'unavailable'):
            raise ValueError('Invalid vision result status')
        observations = texts(change.get('observations'), 'observations')
        onscreen = texts(change.get('onscreen_text'), 'onscreen_text')
        uncertainties = texts(change.get('uncertainties'), 'uncertainties')
        note = change.get('note')
        if not isinstance(note, str) or not note.strip():
            raise ValueError('Each result needs a concrete observation or unavailability note')
        viewed = change.get('viewed_frame_ids')
        if not isinstance(viewed, list) or any(not isinstance(v, str) for v in viewed) or len(viewed) != len(set(viewed)):
            raise ValueError('viewed_frame_ids must be a unique list')
        expected = {f['frame_id'] for f in original['frames']}
        if status != 'unavailable' and set(viewed) != expected:
            raise ValueError('Analyzed/no_relevant_evidence requires actually reading every prepared frame')
        if set(viewed) - expected:
            raise ValueError('Unknown viewed frame ID')
        if status == 'analyzed' and not (observations or onscreen):
            raise ValueError('Analyzed requires at least one visual observation')
        if status in ('unavailable', 'no_relevant_evidence') and (observations or onscreen):
            raise ValueError('This status cannot carry positive visual findings')
        evidence = {'cue_id': packet['batch_id'] + ':' + r['id'],
                    'start_ms': r['start_ms'], 'end_ms': r['end_ms'],
                    'frames': original['frames'], 'observations': observations, 'onscreen_text': onscreen,
                    'uncertainties': uncertainties, 'producer': producer, 'note': note,
                    'viewed_frame_ids': viewed, 'media': packet.get('media')}
        r.update(vision_status=status, visual_cues=[] if status == 'unavailable' else [evidence])
        r['source_notes'] = list(r.get('source_notes', [])) + ['Vision: ' + note.strip()]
        r.update(target='', review_status='pending', review_note='')
        clear_editorial(r)
    structural(doc)
    save(args.output, doc)
    print(f'Applied GPT visual observations to {len(changes)} cues; downstream review reset')


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('extract')
    p.add_argument('--source', required=True)
    p.add_argument('--media', required=True)
    p.add_argument('--output-dir', required=True)
    p.add_argument('--config', default=str(CONFIG))
    p.add_argument('--start-index', type=int, default=0)
    p.add_argument('--media-offset-ms', type=int, default=0)
    p.add_argument('--ffmpeg', default='ffmpeg')
    p.add_argument('--ffprobe', default='ffprobe')
    p.set_defaults(func=extract)
    p = sub.add_parser('pack-frames')
    p.add_argument('--source', required=True)
    p.add_argument('--frames-map', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--config', default=str(CONFIG))
    p.set_defaults(func=pack_frames)
    p = sub.add_parser('apply')
    for name in ('source', 'packet', 'result', 'output'):
        p.add_argument('--' + name, required=True)
    p.set_defaults(func=apply_vision)
    args = parser.parse_args()
    try:
        args.func(args)
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print('ERROR: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
