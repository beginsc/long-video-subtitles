"""Whitespace-only balanced layout. Explicit phrase protection is not tokenization."""
import math
import unicodedata


def width(text):
    return sum(0 if unicodedata.category(c) in ('Mn', 'Me', 'Cf') else
               2 if unicodedata.east_asian_width(c) in ('W', 'F') else 1 for c in text)


def phrases(value):
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(p, str) or not p.strip() for p in value):
        raise ValueError('keep_together must be a list of nonempty strings')
    return list(dict.fromkeys(' '.join(p.split()) for p in value))


def wrap(text, max_width=42, keep_together=None):
    """Minimize line count, then balance with preference for existing spaces.

    Never split ASCII tokens, combining marks or declared literal phrases.
    Oversized atomic spans stay intact and remain measurable width risks.
    Chinese fallback cuts are character boundaries, not certified word boundaries.
    """
    if type(max_width) is not int or max_width <= 0:
        raise ValueError('max_width must be positive integer')
    text = ' '.join(text.split())
    protected = set()
    for phrase in phrases(keep_together):
        start = text.find(phrase)
        while start >= 0:
            protected.update(range(start + 1, start + len(phrase)))
            start = text.find(phrase, start + 1)
    points = [0]
    closing = ',.!?:;，。！？：；、）》】」』”’'
    opening = '（《【「『“‘'
    for i in range(1, len(text)):
        left, right = text[i-1], text[i]
        if i in protected or right in closing or left in opening:
            continue
        if unicodedata.category(right) in ('Mn', 'Me', 'Cf') or left == '\u200d':
            continue
        if left.isascii() and right.isascii() and not (left.isspace() or right.isspace()):
            continue
        # Use one canonical boundary per whitespace run.
        if left.isspace():
            continue
        points.append(i)
    points.append(len(text))
    if not text:
        return text
    edges = {}
    for a in range(len(points)-1):
        possible = []
        for b in range(a+1, len(points)):
            line = text[points[a]:points[b]].strip()
            w = width(line)
            if not line:
                continue
            if w <= max_width or b == a+1:
                possible.append((b, line, w))
            if w > max_width:
                break
        edges[a] = possible
    # Fewest feasible lines. Width overflow is allowed only for atomic spans.
    remaining = [math.inf] * len(points)
    remaining[-1] = 0
    for a in range(len(points)-2, -1, -1):
        remaining[a] = min(1 + remaining[b] for b, _, _ in edges[a])
    ideal = width(text) / remaining[0]
    costs, choices = [math.inf]*len(points), {}
    costs[-1] = 0
    for a in range(len(points)-2, -1, -1):
        for b, line, w in edges[a]:
            if remaining[a] != 1 + remaining[b]:
                continue
            pos = points[b]
            natural = pos == len(text) or text[pos].isspace() or text[pos-1] in closing
            orphan = sum(not c.isspace() and unicodedata.category(c) not in ('Mn','Me','Cf') for c in line) <= 1
            cost = costs[b] + (w-ideal)**2 + (0 if natural else 200) + (10000 if orphan and remaining[0] > 1 else 0)
            if cost < costs[a]:
                costs[a], choices[a] = cost, (b, line)
    lines, a = [], 0
    while a < len(points)-1:
        a, line = choices[a]
        lines.append(line)
    return '\n'.join(lines)


def quality(text, keep_together=None):
    """Small explicit diagnostics; no inferred Chinese dictionary or pass claim."""
    result = []
    lines = text.split('\n')
    if len(lines) > 1:
        for i, line in enumerate(lines, 1):
            visible = [c for c in line if not c.isspace() and unicodedata.category(c) not in ('Mn','Me','Cf')]
            if len(visible) == 1:
                result.append(dict(code='orphan_line', line=i, text=line))
    compact, breaks, offset = '', set(), 0
    for line in lines:
        part = ''.join(line.split())
        compact += part
        offset += len(part)
        breaks.add(offset)
    breaks.discard(len(compact))
    for phrase in phrases(keep_together):
        needle = ''.join(phrase.split())
        start = compact.find(needle)
        while start >= 0:
            if any(start < b < start+len(needle) for b in breaks):
                result.append(dict(code='split_phrase', phrase=phrase))
            start = compact.find(needle, start+1)
    return result
