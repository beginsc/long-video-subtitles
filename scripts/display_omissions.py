"""Reviewed display omissions; raw words remain exact-once provenance.

No semantic classifier or approval automation. Fingerprints bind declarations,
not the truth of evidence or a reviewer's identity.
"""
import argparse
import copy
import json
from pathlib import Path


def context(unit, reference, _stream=None):
    from unified_cues import _reference
    words, lookup, lengths = _stream or _reference(reference)
    indices = []
    for r in unit.get('source_word_ranges', []):
        pid, a, b = r.get('parent_id'), r.get('start_word_index'), r.get('end_word_index_exclusive')
        if pid not in lengths or type(a) is not int or type(b) is not int or not 0 <= a < b <= lengths[pid]:
            raise ValueError('Omission needs valid reference word ranges')
        indices.extend(lookup[(pid, n)] for n in range(a, b))
    if not indices or indices != list(range(indices[0], indices[-1] + 1)):
        raise ValueError('Omission word ranges must be contiguous and nonoverlapping')
    a, b = indices[0], indices[-1] + 1
    parents = {p['id']: p for p in reference['segments']}
    def saved(ws):
        result = copy.deepcopy(ws)
        for w in result:
            parent = parents[w['parent_id']]
            w['asr_word'] = copy.deepcopy(parent['asr_words'][w['word_index']])
            w['offset_ms'] = parent.get('asr_words_offset_ms', reference.get('meta', {}).get('offset_ms', 0))
        return result
    return dict(before=saved(words[max(0, a - 12):a]), words=saved(words[a:b]), after=saved(words[b:b + 12]))


def propose(unit, reference, reason, proposed_by, evidence):
    """Return an unapproved declaration; caller must set target='' separately."""
    return dict(category='nonsemantic_filler', reason=reason, evidence=evidence,
                proposed_by=proposed_by, source_context=context(unit, reference))


def fingerprint(unit, reference, _stream=None):
    from unified_cues import canonical_hash
    omission = unit.get('display_omission')
    if not isinstance(omission, dict):
        raise ValueError('Missing display_omission object')
    return canonical_hash(dict(version=1,
        unit={k: unit.get(k) for k in ('id', 'source', 'target', 'source_word_ranges', 'parent_ids',
              'start_ms', 'end_ms', 'speech_start_ms', 'speech_end_ms')},
        declaration={k: v for k, v in omission.items() if k != 'reviewed_sha256'},
        source_context=context(unit, reference, _stream)))


def validate(unit, reference, _stream=None):
    omission = unit.get('display_omission')
    if not isinstance(omission, dict) or omission.get('category') != 'nonsemantic_filler':
        raise ValueError('Omission requires category nonsemantic_filler')
    if unit.get('target') != '':
        raise ValueError('Approved omission requires exactly empty target')
    for field in ('reason', 'evidence', 'proposed_by', 'reviewed_by'):
        if not isinstance(omission.get(field), str) or not omission[field].strip():
            raise ValueError('Omission requires ' + field)
    if omission['proposed_by'].strip().casefold() == omission['reviewed_by'].strip().casefold():
        raise ValueError('Omission requires an independent reviewer')
    if omission.get('source_context') != context(unit, reference, _stream):
        raise ValueError('Omission source context is missing or stale')
    if omission.get('reviewed_sha256') != fingerprint(unit, reference, _stream):
        raise ValueError('Omission review binding is missing or stale')
    if unit.get('tail_evidence'):
        raise ValueError('Omitted units cannot request display tails')


def scoped_issues(doc, omitted_ids):
    """Keep resolved records; omission never disposes of an unresolved issue."""
    for issue in doc.get('issues', []) + doc.get('source_constraints', []):
        ids = issue.get('unit_ids', issue.get('cue_ids', []))
        if set(ids) & omitted_ids and issue.get('status') != 'resolved':
            raise ValueError('Omitted unit has an unresolved issue; resolve it with evidence first')


def audit(doc, reference):
    """Validate display + reviewed omissions without trusting coverage metadata."""
    units = doc.get('units', doc.get('segments', []))
    declared = [u for u in units if 'display_omission' in u]
    if not declared and not doc.get('omitted_units'):
        return []
    try:
        if reference is None or reference is doc:
            raise ValueError('Display omissions require the original reference in every check/export')
        if doc.get('timing_mode') != 'rebuild' or 'units' not in doc:
            raise ValueError('Display omissions require composed rebuild units')
        from unified_cues import _reference, _units
        words, lookup, lengths = _reference(reference)
        _units(doc, words, lookup, lengths)
        stream = (words, lookup, lengths)
        for unit in declared:
            validate(unit, reference, stream)
        if doc.get('omitted_units') != declared:
            raise ValueError('omitted_units must exactly retain all reviewed omitted units')
        omitted_ids = {u['id'] for u in declared}
        scoped_issues(doc, omitted_ids)
        positions = {u['id']: n for n, u in enumerate(units)}
        omitted_words = [context(u, reference, stream)['words'] for u in declared]
        for cue in doc.get('segments', []):
            ids = cue.get('unit_ids', [])
            if set(ids) & omitted_ids:
                raise ValueError('Omitted units cannot appear in display cues')
            ns = [positions[x] for x in ids]
            if not ns or ns != list(range(ns[0], ns[-1] + 1)):
                raise ValueError('Display cue cannot join across an omission')
            ctx = context(cue, reference, stream)['words']
            if cue['start_ms'] < ctx[0]['start_ms']:
                raise ValueError('Display cue starts before its real words')
            # No omitted speech interval may be borrowed by an adjacent display cue.
            for ow in omitted_words:
                if ctx[-1]['end_ms'] <= ow[0]['start_ms'] and cue['end_ms'] > ow[0]['start_ms']:
                    raise ValueError('Display tail extends into omitted speech')
        return []
    except (ValueError, KeyError, TypeError) as exc:
        return ['display omission: ' + str(exc)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input')
    parser.add_argument('--reference', required=True)
    parser.add_argument('--fingerprint', required=True, help='Unit ID; prints a hash without approving')
    args = parser.parse_args()
    load = lambda p: json.loads(Path(p).read_text(encoding='utf-8-sig'))
    doc = load(args.input)
    matches = [u for u in doc.get('units', doc.get('segments', [])) if u['id'] == args.fingerprint]
    if len(matches) != 1:
        parser.error('Expected one matching unit')
    print(fingerprint(matches[0], load(args.reference)))


if __name__ == '__main__':
    main()
