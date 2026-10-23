"""Evidence ledger validation. Declarations are checked, not model truthfulness."""
METHODS = {'audio_review', 'visual_review', 'context_review', 'source_lookup'}


def valid_resolution(value):
    return (isinstance(value, dict) and value.get('method') in METHODS
            and isinstance(value.get('note'), str) and bool(value['note'].strip())
            and isinstance(value.get('evidence'), list) and bool(value['evidence'])
            and all(isinstance(x, str) and x.strip() for x in value['evidence']))


def validate(segment, required=False):
    if required and 'source_checks' not in segment:
        raise ValueError(f"{segment['id']}: evidence-v2 requires source_checks (empty only after checking)")
    checks=segment.get('source_checks', [])
    if not isinstance(checks, list):
        raise ValueError('source_checks must be a list')
    seen=set()
    for item in checks:
        if (not isinstance(item, dict) or not isinstance(item.get('id'), str) or not item['id'].strip()
                or item['id'] in seen or item.get('kind') not in ('asr','visual','entity','meaning')
                or item.get('status') not in ('open','resolved')
                or not isinstance(item.get('reason'), str) or not item['reason'].strip()):
            raise ValueError('Invalid or duplicate source check')
        seen.add(item['id'])
        if item['status']=='resolved' and not valid_resolution(item.get('resolution')):
            raise ValueError('Resolved source check requires actual evidence/method/note; ASR consensus alone is insufficient')
    if 'uncertainty_resolution' in segment and not valid_resolution(segment['uncertainty_resolution']):
        raise ValueError('Invalid uncertainty_resolution')
    return checks


def open_checks(segment):
    return [x for x in segment.get('source_checks',[]) if x['status']=='open']


def approval_problem(segment):
    if open_checks(segment):
        return 'Open source checks require needs_review; resolve upstream evidence and proofread again'
    if segment.get('proofreading_status')=='UNCLEAR' and not valid_resolution(segment.get('uncertainty_resolution')):
        return 'UNCLEAR requires a structured evidence resolution before reviewed'
    return None
