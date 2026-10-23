#!/usr/bin/env python3
"""Local subtitle artifacts. Standard library only; no ASR or LLM calls."""
import argparse
import copy
import csv
import hashlib
import html
import io
import json
import math
import re
import sys
import unicodedata
from pathlib import Path

import terminology
import review_evidence
import subtitle_layout
import subtitle_timing
from artifact_io import save_text
from collections import Counter


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(path, value):
    save_text(path, json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def structural(doc):
    if not isinstance(doc, dict) or doc.get('schema_version') != 1:
        raise ValueError('Expected schema_version: 1')
    rows = doc.get('segments')
    if not isinstance(rows, list) or not rows:
        raise ValueError('segments must be a nonempty list')
    seen = set()
    last = -1
    for r in rows:
        sid = r.get('id')
        if not isinstance(sid, str) or not sid or sid in seen:
            raise ValueError('Missing or duplicate segment ID')
        seen.add(sid)
        start, end = r.get('start_ms'), r.get('end_ms')
        if type(start) is not int or type(end) is not int or not 0 <= start < end:
            raise ValueError(f'{sid}: require integer 0 <= start_ms < end_ms')
        if start < last:
            raise ValueError(f'{sid}: out of chronological order')
        last = start
        for key in ('source', 'draft', 'target', 'review_note'):
            if not isinstance(r.get(key, ''), str):
                raise ValueError(f'{sid}: {key} must be text')
        if not r.get('source', '').strip():
            raise ValueError(f'{sid}: empty source')
        if r.get('review_status') not in ('pending', 'needs_review', 'reviewed'):
            raise ValueError(f'{sid}: invalid review_status')
        if not isinstance(r.get('issues'), list) or any(not isinstance(x, str) for x in r['issues']):
            raise ValueError(f'{sid}: issues must be a list of strings')
        review_evidence.validate(r, doc.get('meta', {}).get('quality_contract') == 'evidence-v2')
        parents = r.get('parent_ids', [sid])
        if not isinstance(parents, list) or not parents or any(not isinstance(x, str) or not x for x in parents):
            raise ValueError(f'{sid}: parent_ids must be a nonempty string list')
    return rows


def row(sid, start, end, source):
    return dict(id=sid, start_ms=start, end_ms=end, source=source.strip(),
                draft='', target='', review_status='pending', review_note='', issues=[])


def proofreading_digest(segment, meta):
    # Evidence is an input to language work, not a comment attached after export.
    keys = ('id', 'start_ms', 'end_ms', 'source', 'draft', 'visual_cues',
            'vision_status', 'audio_cues', 'source_notes', 'parent_ids')
    keys += tuple(k for k in ('term_context', 'term_domains') if k in segment)
    keys += tuple(k for k in ('source_checks',) if k in segment)
    return digest({'segment': {k: segment.get(k) for k in keys}, 'meta': meta})


def editing_digest(segment):
    keys = ('proofreading_input_digest', 'proofreading_status', 'suggestions',
            'proofreading_note', 'target', 'review_status', 'review_note', 'issues')
    if 'term_exceptions' in segment:
        keys += ('term_exceptions',)
    if 'uncertainty_resolution' in segment:
        keys += ('uncertainty_resolution',)
    return digest({k: segment.get(k) for k in keys})


def clear_editorial(segment):
    for key in ('proofreading_status', 'suggestions', 'proofreading_note',
                'proofreading_input_digest', 'editing_digest', 'term_exceptions', 'uncertainty_resolution'):
        segment.pop(key, None)


STAMP = r'(\d{2,}):([0-5]\d):([0-5]\d)[,.](\d{3})'
TIME_LINE = re.compile(r'^' + STAMP + r'\s+-->\s+' + STAMP + r'\s*$')


def ms(parts):
    h, m, s, fraction = map(int, parts)
    return ((h * 60 + m) * 60 + s) * 1000 + fraction


def import_srt(args):
    raw = Path(args.input).read_text(encoding='utf-8-sig').replace('\r\n', '\n').replace('\r', '\n')
    rows = []
    for block in re.split(r'\n[ \t]*\n', raw.strip()):
        lines = block.splitlines()
        pos = 1 if lines and lines[0].strip().isdigit() else 0
        match = TIME_LINE.fullmatch(lines[pos].strip()) if len(lines) > pos else None
        if not match or len(lines) <= pos + 1:
            raise ValueError(f'Invalid SRT block {len(rows) + 1}; no blocks silently skipped')
        rows.append(row(f's{len(rows)+1:06d}', ms(match.groups()[:4]),
                        ms(match.groups()[4:]), '\n'.join(lines[pos+1:])))
    write_import(args, rows)


def import_asr(args):
    data = load(args.input)
    rows = []
    for item in data['segments']:
        start, end = item['start'], item['end']
        if any(type(x) not in (int, float) or not math.isfinite(x) for x in (start, end)):
            raise ValueError('ASR times must be finite seconds')
        r = row(f's{len(rows)+1:06d}', round(start * 1000) + args.offset_ms,
                round(end * 1000) + args.offset_ms, item['text'])
        for key in ('speaker', 'avg_logprob', 'no_speech_prob'):
            if key in item:
                r[key] = item[key]
        if 'words' in item:
            r['asr_words'] = copy.deepcopy(item['words'])
            r['asr_words_offset_ms'] = args.offset_ms
        rows.append(r)
    write_import(args, rows)


def write_import(args, rows):
    doc = dict(schema_version=1, meta=dict(input_name=Path(args.input).name,
               input_sha256=hashlib.sha256(Path(args.input).read_bytes()).hexdigest()), segments=rows)
    doc['meta']['timing_mode'] = getattr(args, 'timing_mode', None) or ('rebuild' if getattr(args, 'command', '') == 'import-asr' else 'fixed')
    if args.duration_ms is not None:
        if args.duration_ms <= 0:
            raise ValueError('duration-ms must be positive')
        doc['meta']['duration_ms'] = args.duration_ms
    structural(doc)
    save(args.output, doc)
    print(f'Imported {len(rows)} segments')


def chunks(args):
    doc = load(args.input)
    rows = structural(doc)
    terminology.validate_bound(doc)
    if min(args.max_chars, args.max_cues) < 1 or args.context < 0:
        raise ValueError('Invalid batch limits')
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=False)
    groups, current, count = [], [], 0
    for i, r in enumerate(rows):
        size = len(r['source'])
        if current and (len(current) >= args.max_cues or count + size > args.max_chars):
            groups.append(current)
            current, count = [], 0
        current.append(i)
        count += size
    if current:
        groups.append(current)
    for n, indexes in enumerate(groups, 1):
        first, last = indexes[0], indexes[-1]
        packet = dict(schema_version=1, batch_id=f'b{n:05d}',
                      segments=rows[first:last+1],
                      context_before=rows[max(0, first-args.context):first],
                      context_after=rows[last+1:last+1+args.context],
                      project_meta=doc.get('meta', {}))
        packet['input_digest'] = digest(packet)
        save(out / f'b{n:05d}.json', packet)
    print(f'Created {len(groups)} batches; context is read-only; oversize single cues stay intact')


def apply_result(args):
    doc, batch, result = load(args.input), load(args.batch), load(args.result)
    rows = structural(doc)
    terminology.validate_bound(doc)
    signed = {k: v for k, v in batch.items() if k != 'input_digest'}
    if digest(signed) != batch.get('input_digest'):
        raise ValueError('Batch content changed; regenerate the batch')
    if result.get('batch_id') != batch['batch_id'] or result.get('input_digest') != batch['input_digest']:
        raise ValueError('Result belongs to another batch or stale input')
    by_id = {r['id']: r for r in rows}
    for original in batch['segments']:
        if by_id.get(original['id']) != original:
            raise ValueError(f"Stale core segment {original['id']}; regenerate batch")
    if doc.get('meta', {}) != batch.get('project_meta', {}):
        raise ValueError('Project metadata changed; regenerate batch')
    # Neighbor drafts may change while serial batches progress; the original
    # source and timeline must remain identical to the supplied context.
    for original in batch['context_before'] + batch['context_after']:
        now = by_id.get(original['id'], {})
        if any(now.get(k) != original.get(k) for k in ('id', 'source', 'start_ms', 'end_ms',
               'visual_cues', 'vision_status', 'audio_cues', 'source_notes', 'term_context', 'term_domains', 'source_checks')):
            raise ValueError('Context source changed; regenerate batch')
    changes = result.get('segments', [])
    ids = [r['id'] for r in changes]
    expected = {r['id'] for r in batch['segments']}
    if len(ids) != len(set(ids)) or set(ids) != expected:
        raise ValueError('Result IDs must match the core batch exactly, without duplicates')
    for change in changes:
        r = by_id[change['id']]
        if args.stage == 'draft':
            text = change.get('draft')
            if not isinstance(text, str) or not text.strip():
                raise ValueError(f"{r['id']}: empty draft")
            r.update(draft=text.strip(), target='', review_status='pending', review_note='', issues=[])
            clear_editorial(r)
        elif args.stage == 'proofread':
            if not r.get('draft', '').strip():
                raise ValueError(f"{r['id']}: proofreading requires a draft")
            status, suggestions, note = change.get('proofreading_status'), change.get('suggestions'), change.get('proofreading_note')
            if status not in ('PASS', 'REVISE', 'UNCLEAR'):
                raise ValueError('Proofreading status must be PASS, REVISE, or UNCLEAR')
            if not isinstance(suggestions, list) or any(not isinstance(s, str) or not s.strip() for s in suggestions):
                raise ValueError('Suggestions must be a list of nonempty strings')
            if (status == 'PASS' and suggestions) or (status != 'PASS' and not suggestions):
                raise ValueError('PASS requires no suggestions; REVISE/UNCLEAR requires specific suggestions')
            if status == 'PASS' and review_evidence.open_checks(r):
                raise ValueError('Open source checks cannot receive proofreading PASS; use UNCLEAR')
            if not isinstance(note, str) or not note.strip():
                raise ValueError('Proofreading requires a recorded conclusion')
            if 'target' in change or 'draft' in change:
                raise ValueError('Proofreading produces suggestions, not replacement translations')
            clear_editorial(r)
            r.update(proofreading_status=status, suggestions=suggestions, proofreading_note=note.strip(),
                     proofreading_input_digest=proofreading_digest(r, doc.get('meta', {})),
                     target='', review_status='pending', review_note='')
        else:
            split_workflow = doc.get('meta', {}).get('workflow') == 'vidove-inspired-v1'
            if args.stage == 'review' and split_workflow:
                raise ValueError('ViDove workflow requires --stage proofread followed by --stage edit')
            if args.stage == 'edit':
                if r.get('proofreading_status') not in ('PASS', 'REVISE', 'UNCLEAR'):
                    raise ValueError(f"{r['id']}: editing requires proofreading suggestions first")
                if r.get('proofreading_input_digest') != proofreading_digest(r, doc.get('meta', {})):
                    raise ValueError(f"{r['id']}: source, draft, evidence, or project context changed; proofread again")
            if not r.get('draft', '').strip():
                raise ValueError(f"{r['id']}: review requires a draft")
            target, note = change.get('target'), change.get('review_note')
            status, issues = change.get('review_status'), change.get('issues')
            if not isinstance(target, str) or not target.strip() or not isinstance(note, str) or not note.strip():
                raise ValueError(f"{r['id']}: review needs target and review_note")
            if status not in ('reviewed', 'needs_review') or not isinstance(issues, list) or any(not isinstance(x, str) for x in issues):
                raise ValueError('Invalid review status or issues')
            if status == 'reviewed' and issues:
                raise ValueError('Unresolved issues cannot be marked reviewed')
            r.pop('uncertainty_resolution', None)
            if 'uncertainty_resolution' in change:
                if not review_evidence.valid_resolution(change['uncertainty_resolution']):
                    raise ValueError('uncertainty_resolution requires evidence, method and note')
                r['uncertainty_resolution'] = change['uncertainty_resolution']
            problem = review_evidence.approval_problem(r)
            if status == 'reviewed' and problem:
                raise ValueError(problem)
            if status == 'needs_review' and not issues:
                raise ValueError('needs_review requires a concrete unresolved issue')
            r.update(target=target.strip(), review_note=note.strip(), review_status=status, issues=issues)
            exceptions = change.get('term_exceptions', [])
            if not isinstance(exceptions, list):
                raise ValueError('term_exceptions must be a list')
            matched_keys = {key for h in r.get('term_context', {}).get('matches', []) for key in h['entry_keys']}
            seen_exceptions = set()
            for exception in exceptions:
                if (not isinstance(exception, dict) or exception.get('entry_key') not in matched_keys
                    or exception['entry_key'] in seen_exceptions or not terminology.text(exception.get('reason'))):
                    raise ValueError('Term exceptions require a unique matched entry_key and a specific reason')
                seen_exceptions.add(exception['entry_key'])
            r['term_exceptions'] = [dict(entry_key=e['entry_key'], reason=e['reason'],
                input_digest=digest({'source_digest': terminology.source_digest(r), 'target': r['target'],
                                     'term_context': r['term_context']})) for e in exceptions]
            if args.stage == 'edit':
                r['editing_digest'] = editing_digest(r)
    if args.stage in ('edit', 'review'):
        reviewed_ids = {c['id'] for c in changes if by_id[c['id']]['review_status'] == 'reviewed'}
        violations = [f for f in terminology.findings(doc) if f['id'] in reviewed_ids or f['id'] == '*']
        if violations:
            raise ValueError('Cannot mark reviewed with unresolved terminology: ' + violations[0]['message'])
    structural(doc)
    save(args.output, doc)
    print(f'Applied {args.stage} to {len(changes)} segments')


def width(text):
    return sum(0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in ('W', 'F') else 1 for c in text)


def check(doc, args):
    rows = structural(doc)
    findings = []

    def flag(level, sid, code, message):
        findings.append(dict(level=level, id=sid, code=code, message=message))

    baseline = load(args.baseline) if args.baseline else doc
    base_rows = structural(baseline)
    if baseline.get('meta', {}).get('terminology_policy') and not doc.get('meta', {}).get('terminology_policy'):
        flag('error', '*', 'terminology_removed', 'The baseline requires terminology; do not remove its policy to bypass checks')
    if baseline.get('meta', {}).get('quality_contract') == 'evidence-v2' and doc.get('meta', {}).get('quality_contract') != 'evidence-v2':
        flag('error', '*', 'quality_contract_removed', 'Do not remove the evidence contract from a new project')
    base_ids = {r['id'] for r in base_rows}
    covered = {p for r in rows for p in r.get('parent_ids', [r['id']])}
    for sid in sorted(base_ids - covered):
        flag('error', sid, 'missing_source_id', 'Original source ID has no delivery cue')
    for sid in sorted(covered - base_ids):
        flag('error', sid, 'unknown_source_id', 'Unknown parent ID')
    duration = args.duration_ms if args.duration_ms is not None else doc.get('meta', {}).get('duration_ms')
    if duration is not None and (type(duration) is not int or duration <= 0):
        raise ValueError('duration_ms must be a positive integer')
    latest_end, latest_id = -1, ''
    for r in rows:
        sid, start, end = r['id'], r['start_ms'], r['end_ms']
        if start < latest_end:
            flag('warning', sid, 'overlap', f'Overlaps {latest_id}; inspect simultaneous speech or timing')
        if end > latest_end:
            latest_end, latest_id = end, sid
        if duration is not None and end > duration:
            flag('error', sid, 'outside_media', 'Cue ends beyond measured media duration')
        if not r.get('target', '').strip():
            flag('error', sid, 'missing_translation', 'No final target text')
        if r['review_status'] != 'reviewed' or not r.get('review_note', '').strip():
            flag('error', sid, 'unreviewed', 'Review not completed or recorded')
        if r['issues']:
            flag('error', sid, 'unresolved_issues', '; '.join(r['issues']))
        problem = review_evidence.approval_problem(r)
        if problem:
            flag('error', sid, 'evidence_unresolved', problem)
        if doc.get('meta', {}).get('workflow') == 'vidove-inspired-v1':
            if r.get('proofreading_input_digest') != proofreading_digest(r, doc.get('meta', {})):
                flag('error', sid, 'proofreading_missing_or_stale', 'Proofreading is absent or its inputs changed')
            if r.get('editing_digest') != editing_digest(r):
                flag('error', sid, 'editing_missing_or_stale', 'Editor stage is absent or its output changed')
            if r.get('vision_status') not in ('analyzed', 'no_relevant_evidence', 'unavailable', 'skipped'):
                flag('error', sid, 'vision_status_missing', 'Record actual vision execution or reason for skipping')
            if r.get('vision_status') == 'analyzed' and not r.get('visual_cues'):
                flag('error', sid, 'vision_evidence_missing', 'Analyzed requires visual evidence records')
            frames = [f for c in r.get('visual_cues', []) for f in c.get('frames', [])]
            if frames and not any(start <= f.get('time_ms', -1) <= end for f in frames):
                flag('warning', sid, 'coarse_visual_only', 'No linked frame lies inside this cue; context only, not direct visual verification')
        seconds = (end - start) / 1000
        if seconds < getattr(args, 'flash_seconds', 0.5):
            flag('error', sid, 'flash_duration', f'{seconds:.3f} seconds is below the final-export readability floor; remake timing or deliver draft')
        if not args.min_seconds <= seconds <= args.max_seconds:
            flag('warning', sid, 'duration', f'{seconds:.3f} seconds')
        display = subtitle_layout.tracks(r, args)
        layout_profile = subtitle_layout.profile(args)
        tracks = [('target', display['target'], args.target_cps)]
        if args.bilingual:
            tracks.append(('source', display['source'], args.source_cps))
            estimated_lines = sum(sum(max(1, math.ceil(width(line)/layout_profile[track+'_width'])) for line in text.splitlines()) for track,text,_ in tracks)
            if estimated_lines > args.bilingual_total_lines:
                flag('warning', sid, 'bilingual_total_layout', f'Estimated {estimated_lines} combined wrapped lines > {args.bilingual_total_lines}; verify actual rendering')
        for track, text, limit in tracks:
            line_limit = args.max_lines if not args.bilingual else args.bilingual_lines
            if len(text.splitlines()) > line_limit:
                flag('warning', sid, track + '_lines', f'Exceeds {line_limit} lines per track')
            line_width = max((width(x) for x in text.splitlines()), default=0)
            track_width = layout_profile[track+'_width']
            if line_width > track_width:
                flag('warning', sid, track + '_width', f'Display width {line_width} > {track_width}')
            cps = len(re.sub(r'\s', '', text)) / seconds
            if cps > limit:
                flag('warning', sid, track + '_cps', f'{cps:.2f} > {limit} characters/sec')
            original = r['source'] if track == 'source' else r.get('target') or r.get('draft', '')
            if any(ord(c) < 32 and c not in '\n\t\r' for c in original):
                flag('error', sid, 'control_character', 'Unsupported control character')
            if re.search(r'<[^>]+>|\{[^}]+\}', text):
                flag('warning', sid, 'markup', 'Input markup is literal text; inspect before export')
            if re.search(r'\n[ \t]*\n', original.replace('\r\n', '\n')):
                flag('error', sid, 'blank_line', 'Blank line inside cue is not allowed')
    findings.extend(terminology.findings(doc))
    return dict(segments=len(rows), errors=sum(f['level'] == 'error' for f in findings),
                warnings=sum(f['level'] == 'warning' for f in findings),
                coverage_baseline='provided' if args.baseline else 'self-only', findings=findings,
                summary=dict(error_cues=len({f['id'] for f in findings if f['level']=='error'}),
                             needs_review_cues=sum(r['review_status']=='needs_review' for r in rows),
                             warning_cues=len({f['id'] for f in findings if f['level']=='warning'}),
                             evidence_coverage_warnings=sum(f['code']=='coarse_visual_only' for f in findings),
                             other_warnings=sum(f['level']=='warning' and f['code']!='coarse_visual_only' for f in findings),
                             codes=dict(Counter(f['code'] for f in findings))),
                layout_profile=subtitle_layout.profile(args),
                terminology_mode=doc.get('meta', {}).get('terminology_policy', 'not-configured'),
                note='Technical and literal terminology checks only; no semantic, auditory, or visual verification.')


def qa(args):
    report = check(load(args.input), args)
    save(args.output, report)
    print(json.dumps({k: report[k] for k in ('segments', 'errors', 'warnings', 'coverage_baseline')}))
    return 2 if report['errors'] else 0


def stamp(value, separator=',', ass=False):
    if ass:
        centis = (value + 5) // 10
        h, rem = divmod(centis, 360000)
        m, rem = divmod(rem, 6000)
        s, cs = divmod(rem, 100)
        return f'{h}:{m:02}:{s:02}.{cs:02}'
    h, rem = divmod(value, 3600000)
    m, rem = divmod(rem, 60000)
    s, fraction = divmod(rem, 1000)
    return f'{h:02}:{m:02}:{s:02}{separator}{fraction:03}'


def ass_escape(text):
    return text.replace('\\', '＼').replace('{', '｛').replace('}', '｝').replace('\r', '').replace('\n', r'\N')


def export(args):
    doc = load(args.input)
    report = check(doc, args)
    if report['errors'] and not args.draft:
        raise ValueError(f"Final export blocked by {report['errors']} errors; run qa or use --draft for review only")
    if not args.draft and not args.baseline:
        raise ValueError('Final export requires --baseline source.json for coverage checks')
    rows = structural(doc)
    for r in rows:
        if args.format == 'ass' and (r['start_ms'] + 5)//10 >= (r['end_ms'] + 5)//10:
            raise ValueError('Cue collapses after ASS centisecond rounding')
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=False)
    label = 'draft' if args.draft else 'final'
    pieces = ['WEBVTT\n\n'] if args.format == 'vtt' else []
    if args.format == 'ass':
        pieces = ['[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\nWrapStyle: 2\n\n'
                  '[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n'
                  'Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,1,2,80,80,60,1\n\n'
                  '[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n']
    table = io.StringIO(newline='')
    writer = csv.writer(table)
    keys = ['id', 'parent_ids', 'start_ms', 'end_ms', 'source', 'draft', 'target', 'review_status', 'review_note', 'issues',
            'proofreading_status', 'suggestions', 'proofreading_note', 'vision_status', 'term_context', 'term_exceptions', 'source_checks', 'uncertainty_resolution']
    writer.writerow(keys)
    for i, r in enumerate(rows, 1):
        display = subtitle_layout.tracks(r, args)
        target = display['target'] or '[待翻译]'
        parts = [target, display['source']] if args.bilingual else [target]
        if args.source_first and args.bilingual:
            parts.reverse()
        text = '\n'.join(parts).replace('\r', '')
        if args.format == 'ass':
            pieces.append(f"Dialogue: 0,{stamp(r['start_ms'], ass=True)},{stamp(r['end_ms'], ass=True)},Default,,0,0,0,,{ass_escape(text)}\n")
        else:
            sep = '.' if args.format == 'vtt' else ','
            safe = html.escape(text, quote=False)
            if args.format == 'srt':
                # SRT renderers can display &amp; literally; preserve textual ampersands.
                safe = safe.replace('&amp;', '&')
            # Keep malformed blank lines from ending a draft cue prematurely.
            safe = re.sub(r'\n\s*\n', '\n', safe)
            pieces.append(f"{i}\n{stamp(r['start_ms'], sep)} --> {stamp(r['end_ms'], sep)}\n{safe}\n\n")
        values = []
        for key in keys:
            value = r.get(key, '')
            if isinstance(value, (list, dict)):
                value = json.dumps(value, ensure_ascii=False)
            # Avoid executing subtitle text as a spreadsheet formula on review.
            if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')):
                value = "'" + value
            values.append(value)
        writer.writerow(values)
    save_text(out / f'{label}.{args.format}', ''.join(pieces))
    save_text(out / 'review.csv', '\ufeff' + table.getvalue())
    save(out / 'qa.json', report)
    save(out / 'layout.json', subtitle_layout.plan(rows, args))
    if doc.get('terminology'):
        save(out / 'glossary.snapshot.json', doc['terminology'])
    save(out / 'delivery-manifest.json', dict(input_sha256=hashlib.sha256(Path(args.input).read_bytes()).hexdigest(),
         status=label, files={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in out.iterdir() if p.is_file()},
         render_verified=False, note='Files are bound to this export QA; later manual changes require a new export and render check.'))
    print(f'Exported {label}: {len(rows)} cues, {report["warnings"]} warnings; render/playback not verified')


def quality_options(parser):
    parser.add_argument('--baseline', help='Original canonical source.json (required for final export)')
    parser.add_argument('--duration-ms', type=int)
    parser.add_argument('--bilingual', action='store_true')
    parser.add_argument('--min-seconds', type=float, default=0.8)
    parser.add_argument('--flash-seconds', type=float, default=0.5, help='Final-export minimum display duration; shorter cues remain exportable as draft')
    parser.add_argument('--max-seconds', type=float, default=7)
    parser.add_argument('--max-width', type=int, default=42)
    parser.add_argument('--wrap', action='store_true', help='Wrap display tracks without modifying reviewed source/target or timing')
    parser.add_argument('--target-width', type=int, help='Target display width; defaults to max-width')
    parser.add_argument('--source-width', type=int, help='Source display width; defaults to max-width')
    parser.add_argument('--max-lines', type=int, default=2)
    parser.add_argument('--bilingual-lines', type=int, default=1)
    parser.add_argument('--bilingual-total-lines', type=int, default=4)
    parser.add_argument('--target-cps', type=float, default=12)
    parser.add_argument('--source-cps', type=float, default=20)


def triage(args):
    doc = load(args.input)
    queue = []
    for r in structural(doc):
        reasons = [x['reason'] for x in review_evidence.open_checks(r)] + r['issues']
        if r.get('proofreading_status') == 'UNCLEAR' and not r.get('uncertainty_resolution'):
            reasons += r.get('suggestions', [])
        if reasons:
            queue.append(dict(id=r['id'], start_ms=r['start_ms'], end_ms=r['end_ms'],
                              source=r['source'], reasons=list(dict.fromkeys(reasons)), source_checks=r.get('source_checks', [])))
    save(args.output, dict(input_digest=digest(doc), unresolved_count=len(queue), items=queue,
                           note='Recorded concerns only; an empty queue does not establish ASR or semantic accuracy.'))
    print(f'{len(queue)} cues require evidence review')


def verify_export(args):
    root = Path(args.output_dir).resolve()
    manifest = load(root / 'delivery-manifest.json')
    for name, expected in manifest['files'].items():
        path = (root / name).resolve()
        if path.parent != root or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('Export changed after QA: ' + name)
    print('Export hashes match QA; this does not verify rendered layout or semantic accuracy')


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest='command', required=True)
    p = subs.add_parser('timing-plan', help='Create readability proposals without editing the input')
    p.add_argument('input'); p.add_argument('output')
    p.add_argument('--min-seconds', type=float, default=0.8)
    p.add_argument('--target-cps', type=float, default=12)
    p.add_argument('--max-extension-ms', type=int, default=500)
    p.set_defaults(func=lambda a: save(a.output, subtitle_timing.plan(load(a.input), a.min_seconds, a.target_cps, a.max_extension_ms)))
    p = subs.add_parser('triage')
    p.add_argument('input'); p.add_argument('output'); p.set_defaults(func=triage)
    p = subs.add_parser('verify-export')
    p.add_argument('output_dir'); p.set_defaults(func=verify_export)
    for name, func in [('import-srt', import_srt), ('import-asr', import_asr)]:
        p = subs.add_parser(name)
        p.add_argument('input')
        p.add_argument('output')
        p.add_argument('--duration-ms', type=int)
        p.add_argument('--timing-mode', choices=('fixed', 'rebuild'), help='Default fixed for SRT, rebuild for raw ASR; respects user fixed-axis requests')
        if name == 'import-asr':
            p.add_argument('--offset-ms', type=int, default=0)
        p.set_defaults(func=func)
    p = subs.add_parser('chunks')
    p.add_argument('input')
    p.add_argument('output_dir')
    p.add_argument('--max-chars', type=int, default=6000)
    p.add_argument('--max-cues', type=int, default=60)
    p.add_argument('--context', type=int, default=3)
    p.set_defaults(func=chunks)
    p = subs.add_parser('apply')
    for key in ('input', 'batch', 'result', 'output'):
        p.add_argument(key)
    p.add_argument('--stage', choices=['draft', 'proofread', 'edit', 'review'], required=True)
    p.set_defaults(func=apply_result)
    p = subs.add_parser('qa')
    p.add_argument('input')
    p.add_argument('output')
    quality_options(p)
    p.set_defaults(func=qa)
    p = subs.add_parser('export')
    p.add_argument('input')
    p.add_argument('output_dir')
    p.add_argument('--format', choices=['srt', 'vtt', 'ass'], default='srt')
    p.add_argument('--draft', action='store_true')
    p.add_argument('--source-first', action='store_true')
    quality_options(p)
    p.set_defaults(func=export)
    args = parser.parse_args()
    try:
        if args.command in ('qa', 'export'):
            for key in ('target_width', 'source_width'):
                if getattr(args, key) is not None and getattr(args, key) <= 0:
                    raise ValueError(key + ' must be positive')
            for key in ('flash_seconds', 'min_seconds', 'max_seconds', 'max_width', 'max_lines', 'bilingual_lines', 'bilingual_total_lines', 'target_cps', 'source_cps'):
                value = getattr(args, key)
                if not math.isfinite(value) or value <= 0:
                    raise ValueError(f'{key} must be positive and finite')
            if args.max_seconds < args.min_seconds:
                raise ValueError('max-seconds must be >= min-seconds')
            if args.flash_seconds > args.min_seconds:
                raise ValueError('flash-seconds must be <= min-seconds')
        return args.func(args) or 0
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
