"""Only the source-row contract needed by the retained S16 ASR adapter."""


def row(sid, start, end, source):
    return dict(id=sid, start_ms=start, end_ms=end, source=source.strip(),
                draft='', target='', review_status='pending', review_note='', issues=[])


def structural(doc):
    if not isinstance(doc, dict) or doc.get('schema_version') != 1:
        raise ValueError('Expected schema_version: 1')
    rows = doc.get('segments')
    if not isinstance(rows, list) or not rows:
        raise ValueError('segments must be a nonempty list')
    seen, previous = set(), -1
    for item in rows:
        sid = item.get('id')
        if not isinstance(sid, str) or not sid or sid in seen:
            raise ValueError('Missing/duplicate source ID')
        seen.add(sid)
        a, b = item.get('start_ms'), item.get('end_ms')
        if type(a) is not int or type(b) is not int or not 0 <= a < b or a < previous:
            raise ValueError('Invalid source timing/order')
        previous = a
        if not isinstance(item.get('source'), str) or not item['source'].strip():
            raise ValueError('Empty source text')
    return rows
