"""Presentation-only wrapping shared by QA and export. Never changes cue timing."""
import hashlib
import re
import unicodedata
import terminology


def width(text):
    return sum(0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in ('W', 'F') else 1 for c in text)


def compact(text):
    return re.sub(r'\s', '', text)


def wrap_line(line, limit, protected):
    line = ' '.join(line.split())
    blocked = set()
    for literal in protected:
        for start, end in terminology.spans(line, literal):
            blocked.update(range(start + 1, end))
    closing = ',.!?:;，。！？：；、）】》」』”’%％'
    opening = '（【《「『“‘'
    boundaries = [0]
    for i in range(1, len(line)):
        left, right = line[i-1], line[i]
        if i in blocked or unicodedata.combining(right):
            continue
        if right in closing or left in opening:
            continue
        if left.isspace() or right.isspace() or terminology.cjk(left) or terminology.cjk(right) or left in ',;:!?':
            boundaries.append(i)
    boundaries.append(len(line))
    # Minimize overflow, then line count, then raggedness across ALL lines.
    # Including the last line avoids a lone final number after two full lines.
    best = {len(line): ((0, 0, 0), [])}
    for start in reversed(boundaries[:-1]):
        choices = []
        for end in boundaries:
            if end <= start or end not in best:
                continue
            value = line[start:end].strip()
            if not value:
                continue
            used = width(value); tail, tail_lines = best[end]
            cost = (max(0, used-limit)+tail[0], 1+tail[1], (limit-used)**2+tail[2])
            choices.append((cost, [value, *tail_lines]))
        if choices:
            best[start] = min(choices, key=lambda x: x[0])
    lines = best.get(0, ((0, 0, 0), []))[1]
    return '\n'.join(lines)


def wrap(text, limit, protected=()):
    if type(limit) is not int or limit <= 0:
        raise ValueError('Display width must be a positive integer')
    # Preserve explicit editorial line boundaries. Blank lines remain for QA.
    result = '\n'.join(wrap_line(line, limit, protected) for line in text.replace('\r\n', '\n').replace('\r', '\n').split('\n'))
    if compact(result) != compact(text):
        raise ValueError('Wrapping changed non-whitespace text')
    return result


def profile(args):
    return dict(wrap=bool(getattr(args, 'wrap', False)),
                target_width=getattr(args, 'target_width', None) or args.max_width,
                source_width=getattr(args, 'source_width', None) or args.max_width)


def tracks(row, args):
    settings = profile(args)
    result = {'target': row.get('target') or row.get('draft', ''), 'source': row['source']}
    if settings['wrap']:
        entries = [e['entry'] for e in row.get('term_context', {}).get('entries', [])]
        for track in result:
            protected = [s for e in entries for s in
                         ([e['source'], *e.get('aliases', [])] if track == 'source' else
                          [e['preferred_target'], *e.get('allowed_targets', [])]) if s]
            result[track] = wrap(result[track], settings[track + '_width'], protected)
    return result


def plan(rows, args):
    return dict(profile=profile(args), semantic_change=False, timing_change=False,
                note='Display text only; QA and export use these same tracks. Pixel rendering still needs inspection.',
                segments=[dict(id=r['id'], start_ms=r['start_ms'], end_ms=r['end_ms'], **tracks(r, args),
                               text_sha256=hashlib.sha256((compact(r.get('target') or r.get('draft', ''))+'\0'+compact(r['source'])).encode('utf-8')).hexdigest()) for r in rows])
