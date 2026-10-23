"""Readability proposals only. Never changes speech timestamps or editorial state."""
import math
import re


def plan(doc, min_seconds=0.8, target_cps=12, max_extension_ms=500):
    from subtitle_workbench import structural, digest
    rows = structural(doc)
    if any(not math.isfinite(v) or v <= 0 for v in (min_seconds, target_cps)):
        raise ValueError('Readability limits must be positive and finite')
    if type(max_extension_ms) is not int or not 0 <= max_extension_ms <= 1000:
        raise ValueError('Extension must be between 0 and 1000 ms')
    mode = doc.get('meta', {}).get('timing_mode', 'fixed')
    if mode not in ('fixed', 'rebuild'):
        raise ValueError('timing_mode must be fixed or rebuild')
    duration = doc.get('meta', {}).get('duration_ms')
    if duration is not None and (type(duration) is not int or duration <= 0):
        raise ValueError('duration_ms must be measured positive integer milliseconds')
    boundaries = doc.get('meta', {}).get('scene_boundaries_ms', [])
    if not isinstance(boundaries, list) or any(type(t) is not int or t < 0 for t in boundaries):
        raise ValueError('scene_boundaries_ms must contain nonnegative integer times')
    items = []
    for i, r in enumerate(rows):
        start, end = r['start_ms'], r['end_ms']
        text = r.get('target') or r.get('draft') or ''
        needed = max(round(min_seconds * 1000), math.ceil(len(re.sub(r'\s', '', text)) / target_cps * 1000))
        if end - start >= needed:
            continue
        item = dict(id=r['id'], start_ms=start, end_ms=end, required_duration_ms=needed,
                    reasons=['short_duration'] if end - start < min_seconds * 1000 else [])
        if text and end - start < needed and needed > min_seconds * 1000:
            item['reasons'].append('reading_speed')
        if mode == 'fixed':
            item['action'] = 'fixed_axis_review'
            item['note'] = 'Preserve supplied times; revise wording without losing facts, or retain a documented draft limitation.'
        else:
            cap = min(end + max_extension_ms, duration if duration is not None else end)
            if i + 1 < len(rows):
                cap = min(cap, rows[i + 1]['start_ms'])
            cap = min([cap] + [t for t in boundaries if end <= t])
            if cap >= start + needed:
                item.update(action='consider_end_extension', proposed_end_ms=start + needed,
                            note='Display hold only; inspect real pause/scene before accepting. Never change ASR word times.')
            else:
                neighbors = []
                for j in (i - 1, i + 1):
                    if not 0 <= j < len(rows):
                        continue
                    other = rows[j]
                    left, right = (other, r) if j < i else (r, other)
                    same_speaker = r.get('speaker') == other.get('speaker')
                    crosses_scene = any(left['start_ms'] < t < right['end_ms'] for t in boundaries)
                    if same_speaker and 0 <= right['start_ms'] - left['end_ms'] <= 250 and not crosses_scene:
                        neighbors.append(other['id'])
                item.update(action='semantic_resegment_review', candidate_neighbor_ids=neighbors,
                            note='Inspect speaker, turn and meaning before merging. Missing speaker labels are not proof of one speaker; no automatic merge.')
        items.append(item)
    return dict(input_digest=digest(doc), timing_mode=mode, items=items, count=len(items),
                changes_applied=False, note='Proposals, not verified timing. Apply to a new version, preserve parent_ids, rebind terms and repeat proofreading/editing.')
