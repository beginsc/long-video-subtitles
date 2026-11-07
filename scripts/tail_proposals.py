"""Propose bounded display tails from supplied evidence; never approve/apply them.

Input intervals are localized source-editor evidence, not guessed from ASR gaps.
The caller/independent reviewer must establish their suitability. Numeric checks
cannot certify absence of speech, listening or dynamic viewing.
"""
import math


def binding(unit, speech):
    from unified_cues import canonical_hash
    return canonical_hash(dict(source=unit['source'], target=''.join(unit['target'].split()),
                               ranges=unit['source_word_ranges'], speech=list(speech)))


def propose(units_doc, reference, intervals, parameters=None, next_guard_ms=80):
    from unified_cues import compose, _reference, _units, _parameters, _envelope
    if type(next_guard_ms) is not int or next_guard_ms < 0:
        raise ValueError('next_guard_ms must be a nonnegative integer')
    p = _parameters(parameters)
    words, lookup, lengths = _reference(reference)
    units, spans = _units(units_doc, words, lookup, lengths)
    by_id = {u['id']: (u, span) for u, span in zip(units, spans)}
    supplied = {}
    for interval in intervals:
        if not isinstance(interval, dict) or interval.get('unit_id') not in by_id:
            raise ValueError('Evidence interval requires a known stable unit_id')
        uid = interval['unit_id']
        if uid in supplied:
            raise ValueError('Duplicate evidence interval')
        for field in ('start_ms', 'end_ms'):
            if type(interval.get(field)) is not int or interval[field] < 0:
                raise ValueError('Invalid evidence interval ' + field)
        for field in ('reason', 'evidence'):
            if not isinstance(interval.get(field), str) or not interval[field].strip():
                raise ValueError('Evidence interval requires ' + field)
        supplied[uid] = interval
    result = []
    for cue in compose(units_doc, reference, parameters)['segments']:
        required = cue['start_ms'] + max(p['min_duration_ms'], math.ceil(sum(not c.isspace() for c in cue['target']) * 1000 / p['target_cps']))
        if required <= cue['end_ms']:
            continue
        uid = cue['unit_ids'][-1]
        unit, span = by_id[uid]
        speech = _envelope(words[span[0]:span[1]])
        item = dict(unit_id=uid, cue_id=cue['id'], required_end_ms=required,
                    current_end_ms=cue['end_ms'], proposal=None)
        interval = supplied.get(uid)
        next_onset = words[span[1]]['start_ms'] if span[1] < len(words) else None
        if not interval or not interval['start_ms'] <= speech[1] < interval['end_ms']:
            item['blocked_reason'] = 'No supplied evidence interval enclosing speech end'
        elif span[1] < len(words) and next_onset is None:
            item['blocked_reason'] = 'Next word onset unknown; no interpolation'
        else:
            cap = min(interval['end_ms'], speech[1]+p['max_tail_ms'], cue['start_ms']+p['max_duration_ms'])
            if next_onset is not None:
                cap = min(cap, next_onset-next_guard_ms)
            end = min(required, cap)
            if end <= cue['end_ms']:
                item['blocked_reason'] = 'No usable evidence-bounded tail'
            else:
                item['proposal'] = dict(reviewed=False, reason=interval['reason'], evidence=interval['evidence'],
                    start_ms=interval['start_ms'], end_ms=interval['end_ms'], display_end_ms=end,
                    next_guard_ms=next_guard_ms, binding_sha256=binding(unit, speech),
                    generator='tail-proposals-1.0', review_scope='pending independent evidence review; dynamic viewing not implied')
                item['residual_shortfall_ms'] = max(0, required-end)
        result.append(item)
    return dict(schema_version=1, automatic_approval=False, proposals=result)
