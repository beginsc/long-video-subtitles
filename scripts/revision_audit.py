"""Read-only revision comparison with split/merge lineage; never judges meaning."""
import argparse
import difflib
import hashlib
import json
from pathlib import Path
import re


ATTENTION = {
    'stage_time': r'\b(?:coming up|about to|already|still|yet|soon|before|after|now|eventually)\b',
    'certainty': r'\b(?:may|might|could|should|would|probably|seems?|guess|knows?|knowing)\b',
    'polarity_scope': r"\b(?:no|not|never|only|any|all|without)\b|n't\b",
    'condition_cause': r'\b(?:if|unless|because|since|therefore|so that|as soon as)\b',
    'reaction_contrast': r'\b(?:but|though|although|even|there it is|cool|wow|actually)\b',
    'stance_reaction': r'\b(?:surpris(?:ed|ing)|(?:really\s+)?like\s+(?:this|that)|no denying|keep in mind|appreciate)\b',
    'quantity': r'\b\d+(?:[.,]\d+)?\b|\b(?:one|two|three|four|five|six|seven|eight|nine|ten|half|double|more|less)\b',
}


def compact(text):
    return re.sub(r'\s+', '', text)


def rows(doc, field):
    result = doc.get('segments')
    if not isinstance(result, list) or not result:
        raise ValueError('Expected nonempty segments')
    seen = set()
    for s in result:
        sid = s.get('id')
        if not isinstance(sid, str) or not sid or sid in seen:
            raise ValueError('Missing or duplicate cue ID')
        seen.add(sid)
        for k in ['source', field]:
            if not isinstance(s.get(k), str):
                raise ValueError(f'{sid}: missing string field {k}; choose draft explicitly if needed')
        if any(type(s.get(k)) is not int for k in ['start_ms', 'end_ms']) or not 0 <= s['start_ms'] < s['end_ms']:
            raise ValueError(f'{sid}: invalid timestamps')
        parents = s.get('parent_ids', [sid])
        if not isinstance(parents, list) or not parents or any(not isinstance(x, str) or not x for x in parents) or len(set(parents)) != len(parents):
            raise ValueError(f'{sid}: invalid parent_ids')
    return result


def compare(before, after, before_field='target', after_field='target', retention_threshold=0.85):
    if not 0 < retention_threshold <= 1:
        raise ValueError('retention threshold must be in (0,1]')
    for key in ['video_id', 'target_language']:
        a, b = before.get('meta', {}).get(key), after.get('meta', {}).get(key)
        if a and b and a != b:
            raise ValueError(f'Incompatible {key}')
    old, new = rows(before, before_field), rows(after, after_field)
    # Bipartite links: same cue ID or an explicitly shared original ancestor.
    # Connected components avoid double-counting a merge once per parent.
    nodes = [(0, i) for i in range(len(old))] + [(1, i) for i in range(len(new))]
    parent = {x:x for x in nodes}
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    def union(a, b):
        parent[find(b)] = find(a)
    old_keys = {}
    for i, s in enumerate(old):
        for key in {s['id'], *s.get('parent_ids', [])}:
            old_keys.setdefault(key, []).append(i)
    for j, s in enumerate(new):
        for key in {s['id'], *s.get('parent_ids', [])}:
            for i in old_keys.get(key, []):
                union((0,i), (1,j))
    groups = {}
    for node in nodes:
        groups.setdefault(find(node), [[], []])[node[0]].append(node[1])
    items, errors = [], []
    for oi, ni in groups.values():
        a = sorted((old[i] for i in oi), key=lambda s:s['start_ms'])
        b = sorted((new[i] for i in ni), key=lambda s:s['start_ms'])
        aid, bid = [s['id'] for s in a], [s['id'] for s in b]
        if not a or not b:
            errors.append(dict(code='unmapped_before' if a else 'unmapped_after', before_ids=aid, after_ids=bid))
            continue
        at, bt = ''.join(compact(s[before_field]) for s in a), ''.join(compact(s[after_field]) for s in b)
        for side, ss, field in [('before', a, before_field), ('after', b, after_field)]:
            for s in ss:
                if not compact(s[field]):
                    errors.append(dict(code='empty_translation', side=side, id=s['id']))
        src_a = ' '.join(s['source'] for s in a)
        src_b = ' '.join(s['source'] for s in b)
        text_changed = at != bt
        source_changed = compact(src_a) != compact(src_b)
        structure_changed = aid != bid
        timing_changed = [(s['start_ms'],s['end_ms']) for s in a] != [(s['start_ms'],s['end_ms']) for s in b]
        ratio = len(bt)/len(at) if at else None
        attention = {k:sorted(set(m.group(0) for m in re.finditer(v,src_a+' '+src_b,re.I))) for k,v in ATTENTION.items()}
        attention = {k:v for k,v in attention.items() if v}
        reasons = []
        if text_changed:
            reasons.append('translation_changed')
            if ratio is not None and ratio < retention_threshold:
                reasons.append('shortened_review_information_and_tone')
            if attention:
                reasons.append('source_features_need_comparison')
        if source_changed:
            reasons.append('source_changed_recheck_evidence')
        if structure_changed:
            reasons.append('split_merge_review_meaning_and_order')
        items.append(dict(before_ids=aid, after_ids=bid, before=a, after=b,
            text_changed=text_changed, source_changed=source_changed, structure_changed=structure_changed,
            timing_changed=timing_changed, before_chars=len(at), after_chars=len(bt), retention_ratio=ratio,
            text_similarity=difflib.SequenceMatcher(None,at,bt,autojunk=False).ratio(),
            source_attention=attention if text_changed or source_changed or structure_changed else {},
            review_reasons=reasons, semantic_review='pending' if reasons else 'not_requested_by_this_diff'))
    # Include only necessary text/time/lineage in the output, never full project metadata.
    for item in items:
        for side, field in [('before',before_field), ('after',after_field)]:
            item[side] = [{k:s[k] for k in dict.fromkeys(['id','start_ms','end_ms','source',field])} for s in item[side]]
    return dict(schema_version=1, kind='revision-audit', before_field=before_field, after_field=after_field,
        retention_threshold=retention_threshold, structural_errors=errors,
        summary=dict(before_cues=len(old), after_cues=len(new), matched_groups=len(items),
            changed_text_groups=sum(x['text_changed'] for x in items),
            shortened_groups=sum('shortened_review_information_and_tone' in x['review_reasons'] for x in items),
            review_groups=sum(bool(x['review_reasons']) for x in items)),
        limitations='Character changes and English lexical hints are triage only, not proof of loss or fidelity. No clause-order or semantic correctness detector. Same IDs do not certify meaning; use source and evidence for review. No subtitle or status is modified.', items=items)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--before', type=Path, required=True)
    p.add_argument('--after', type=Path, required=True)
    p.add_argument('--before-field', choices=['draft','target'], default='target')
    p.add_argument('--after-field', choices=['draft','target'], default='target')
    p.add_argument('--retention-threshold', type=float, default=0.85)
    p.add_argument('--output', type=Path, required=True)
    args=p.parse_args()
    raw=[args.before.read_bytes(),args.after.read_bytes()]
    result=compare(*(json.loads(x.decode('utf-8-sig')) for x in raw),args.before_field,args.after_field,args.retention_threshold)
    result['input_sha256']={k:hashlib.sha256(v).hexdigest() for k,v in zip(['before','after'],raw)}
    with args.output.open('x',encoding='utf8') as f:
        json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps(dict(**result['summary'],structural_errors=len(result['structural_errors']))))
    return 2 if result['structural_errors'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
