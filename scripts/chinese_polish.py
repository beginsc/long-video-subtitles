"""Optional DeepSeek suggestions. Offline prepare; hash-bound approval before any API call."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from datetime import datetime, timezone
import urllib.request

from asr_credentials import read_key
from artifact_io import save_text

ROOT = Path(__file__).resolve().parents[1]
ENDPOINT = 'https://api.deepseek.com/chat/completions'
MODEL = 'deepseek-flash'
PROFILE_KEYS = {'target_language', 'locale', 'domain', 'register', 'audience',
                'term_policy', 'unit_policy', 'culture_policy', 'line_policy'}


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save_new(path, value):
    save_text(path, json.dumps(value, ensure_ascii=False, indent=2)+'\n')


def update_reserved(path, value):
    path = Path(path)
    fd, name = tempfile.mkstemp(prefix='.'+path.name+'.',suffix='.pending',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            f.write(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
            f.flush();os.fsync(f.fileno())
        os.replace(name,path)
    finally:
        Path(name).unlink(missing_ok=True)


def prompt_path(config):
    path = (ROOT / config['prompt']).resolve()
    if not path.is_relative_to(ROOT / 'references'):
        raise ValueError('Prompt must be inside skill references')
    return path


def public_config(config):
    return {k: config[k] for k in ('provider', 'model', 'endpoint', 'thinking', 'max_tokens', 'prompt')}


def check_config(config):
    if config.get('provider') != 'deepseek' or config.get('model') != MODEL or config.get('endpoint') != ENDPOINT:
        raise ValueError('Unsupported destination or model; update and reapprove configuration first')
    if config.get('thinking') != 'disabled' or not 1 <= config.get('max_tokens', 0) <= 16000:
        raise ValueError('Unsupported generation parameters')


def prepare(source_path, field, ids, profile_path, config):
    check_config(config)
    doc = load(source_path)
    profile = load(profile_path)
    if set(profile) != PROFILE_KEYS or any(not isinstance(v, str) or not v.strip() for v in profile.values()):
        raise ValueError('Provide all documented localization profile fields as nonempty strings')
    if profile['target_language'] != 'zh-Hans' or profile['line_policy'] not in ('single', 'preserve'):
        raise ValueError('This backend supports zh-Hans with single/preserve line policy only')
    segments = doc['segments']
    all_ids = [s['id'] for s in segments]
    if len(set(all_ids)) != len(all_ids):
        raise ValueError('Duplicate source IDs')
    if not ids or len(ids) > 40 or len(set(ids)) != len(ids) or not set(ids) <= set(all_ids):
        raise ValueError('Select 1-40 unique existing IDs per request')
    selected = [i for i, s in enumerate(segments) if s['id'] in ids]
    if selected != list(range(selected[0], selected[-1] + 1)):
        raise ValueError('Select a contiguous block so readonly neighbors retain their meaning')
    def cue(s):
        if not isinstance(s.get(field), str) or not s[field].strip() or not isinstance(s.get('source'), str) or not s['source'].strip():
            raise ValueError('Missing source or current translation')
        return {'id': s['id'], 'source': s['source'], 'current': s[field],
                'start_ms': s['start_ms'], 'end_ms': s['end_ms'],
                'term_context': s.get('term_context', {}),
                'source_checks': s.get('source_checks', []), 'issues': s.get('issues', [])}
    items = [cue(segments[i]) for i in selected]
    neighbors = [i for i in range(max(0, selected[0]-2), min(len(segments), selected[-1]+3)) if i not in selected]
    context = [cue(segments[i]) for i in neighbors]
    # No whole metadata, reference subtitles, credentials, audio, or images are included.
    packet = {'schema': 'chinese-polish-v1', 'source_path': str(Path(source_path).resolve()),
              'source_sha256': file_hash(source_path), 'field': field,
              'prompt_sha256': file_hash(prompt_path(config)), 'config': public_config(config),
              'project_profile': profile, 'items': items, 'readonly_context': context}
    if len(json.dumps(packet, ensure_ascii=False)) > 100000:
        raise ValueError('Packet too large; reduce batch size')
    packet['packet_digest'] = digest(packet)
    return packet


def check_gate(packet, approval, config):
    check_config(config)
    if config.get('enabled') is not True or config.get('status') != 'approved':
        raise ValueError('Backend disabled pending explicit user prompt approval')
    content = {k:v for k,v in packet.items() if k != 'packet_digest'}
    if packet.get('schema') != 'chinese-polish-v1' or digest(content) != packet.get('packet_digest'):
        raise ValueError('Packet changed; prepare and approve again')
    if packet['config'] != public_config(config) or packet['prompt_sha256'] != file_hash(prompt_path(config)):
        raise ValueError('Prompt or configuration changed; new user approval required')
    if packet['source_sha256'] != file_hash(packet['source_path']):
        raise ValueError('Source changed; prepare new packet')
    required = {'approved_by': 'user', 'status': 'approved', 'packet_digest': packet['packet_digest'],
                'prompt_sha256': packet['prompt_sha256'], 'model': MODEL, 'endpoint': ENDPOINT}
    if any(approval.get(k) != v for k,v in required.items()):
        raise ValueError('Approval does not cover this prompt, batch, model and destination')
    if not approval.get('user_message') or not approval.get('approved_at'):
        raise ValueError('Missing actual user authorization record')


def validate_response(response, packet):
    if not isinstance(response, dict) or set(response) != {'items'} or not isinstance(response['items'], list):
        raise ValueError('Invalid response object')
    items = response['items']
    if any(not isinstance(x, dict) for x in items) or [x.get('id') for x in items] != [x['id'] for x in packet['items']]:
        raise ValueError('Response IDs missing, duplicated or reordered')
    for before, after in zip(packet['items'], items):
        if set(after) != {'id','decision','proposed','reason','protected_meaning'}:
            raise ValueError('Unexpected response fields')
        decision = after['decision']
        if decision not in ('keep', 'revise', 'unclear'):
            raise ValueError('Invalid decision')
        if any(not isinstance(after[k], str) or not after[k].strip() for k in ('proposed','reason')):
            raise ValueError('Empty suggestion or explanation')
        if not isinstance(after['protected_meaning'], list) or not after['protected_meaning'] or any(not isinstance(x,str) or not x.strip() for x in after['protected_meaning']):
            raise ValueError('Missing protected meaning')
        if decision in ('keep','unclear') and after['proposed'] != before['current']:
            raise ValueError('Keep/unclear must preserve the current translation')
        if any(c.get('status') != 'resolved' for c in before['source_checks']) and decision != 'unclear':
            raise ValueError('Open/unknown source checks cannot be cleared by polishing')
        if decision == 'revise':
            if after['proposed'] == before['current']:
                raise ValueError('Revise requires an actual change')
            limit = 1 if packet['project_profile']['line_policy'] == 'single' else len(before['current'].splitlines())
            if len(after['proposed'].splitlines()) != limit:
                raise ValueError('Suggestion violates line policy')
    return response


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Redirect refused; credential stays at approved destination')


def request_suggestions(packet, config, key, record_response=None):
    body = {'model': MODEL, 'thinking': {'type': config['thinking']},
            'max_tokens': config['max_tokens'], 'response_format': {'type':'json_object'},
            'messages': [{'role':'system', 'content':prompt_path(config).read_text(encoding='utf-8')},
                         {'role':'user', 'content':json.dumps({k:packet[k] for k in ('project_profile','items','readonly_context')},ensure_ascii=False)}]}
    request = urllib.request.Request(ENDPOINT, data=json.dumps(body,ensure_ascii=False).encode('utf-8'),
                                     headers={'Authorization':'Bearer '+key, 'Content-Type':'application/json'})
    with urllib.request.build_opener(NoRedirect).open(request, timeout=180) as result:
        response_bytes = result.read()
    # Persist the successful HTTP response before parsing/schema validation.
    # Never persist request headers, credentials or HTTP error response bodies.
    if record_response is not None:
        record_response(response_bytes)
    raw = json.loads(response_bytes)
    choice = raw['choices'][0]
    if choice.get('finish_reason') != 'stop':
        raise ValueError('Incomplete response; no suggestions accepted')
    suggestions = validate_response(json.loads(choice['message']['content']), packet)
    return {'suggestions':suggestions, 'response_id':raw.get('id'), 'actual_model':raw.get('model'),
            'system_fingerprint':raw.get('system_fingerprint'), 'usage':raw.get('usage')}


def run(packet, approval, config, output):
    check_gate(packet, approval, config)  # Must precede credential access and all network activity.
    raw_path = Path(output).with_name(Path(output).name + '.response.json')
    if raw_path.exists():
        raise FileExistsError('Response sidecar already exists; do not resubmit')
    key = read_key(config)
    started = {'status':'started', 'packet_digest':packet['packet_digest'],
               'prompt_sha256':packet['prompt_sha256'], 'approved_request':True,
               'started_at':datetime.now(timezone.utc).isoformat(),
               'note':'No automatic retries. Existing output blocks repeat submission.'}
    save_new(output, started)  # Exclusive reservation before POST; crashes retain this state.
    def record_response(body):
        with raw_path.open('xb') as stream:
            stream.write(body); stream.flush(); os.fsync(stream.fileno())
        started.update(response_received=True, response_path=str(raw_path.resolve()),
                       response_sha256=hashlib.sha256(body).hexdigest())
        update_reserved(output, started)
    try:
        result = request_suggestions(packet, config, key, record_response=record_response)
        started.update(result, status='suggestions_only', requires_gpt_review=True)
    except Exception as exc:
        # Do not print HTTP response bodies, headers or arbitrary exception text containing secrets.
        started.update(status='failed_or_uncertain', error_type=type(exc).__name__,
                       failure_phase='response_validation_or_storage' if started.get('response_received') else 'request_or_response_capture')
        update_reserved(output,started)
        raise ValueError('Request failed or result rejected; inspect the local status. Do not automatically resubmit.') from None
    update_reserved(output,started)


def diagnose(packet, response_path):
    """Read-only triage of a recorded response. No repair, retry or acceptance."""
    content={k:v for k,v in packet.items() if k!='packet_digest'}
    if packet.get('schema')!='chinese-polish-v1' or digest(content)!=packet.get('packet_digest'):
        raise ValueError('Packet digest mismatch')
    requested=packet['items'];ids=[s['id'] for s in requested]
    if not ids or len(ids)!=len(set(ids)):raise ValueError('Invalid packet IDs')
    report=dict(kind='polish-response-diagnostic',packet_digest=packet['packet_digest'],
        response_path=str(Path(response_path).resolve()),response_sha256=file_hash(response_path),
        strict_response_valid=False,requires_gpt_review=True,changes_to_subtitles=0,
        network_calls=0,protocol_errors=[],items=[],quarantined_entries=[])
    try: raw=load(response_path)
    except (json.JSONDecodeError,UnicodeDecodeError):
        report['protocol_errors'].append('Unparseable response; no automatic JSON repair')
        return report
    obj=raw
    if isinstance(raw,dict) and 'content' in raw and 'packet_digest' in raw:
        # Older project runners stored a compact provider envelope. Bind it to
        # its packet explicitly; do not confuse it with the model's own object.
        if raw['packet_digest']!=packet['packet_digest']:
            raise ValueError('Recorded response belongs to another packet')
        report['provider_metadata']={k:raw.get(k) for k in ('id','model','system_fingerprint','usage')}
        if raw.get('finish_reason')!='stop':report['protocol_errors'].append('Incomplete response')
        try:obj=json.loads(raw['content'])
        except (ValueError,TypeError):
            report['protocol_errors'].append('Invalid archived message JSON; no automatic repair')
            return report
    elif isinstance(raw,dict) and 'choices' in raw:
        report['provider_metadata']={k:raw.get(k) for k in ('id','model','system_fingerprint','usage')}
        try:
            choice=raw['choices'][0]
            if choice.get('finish_reason')!='stop':report['protocol_errors'].append('Incomplete response')
            obj=json.loads(choice['message']['content'])
        except (ValueError,KeyError,IndexError,TypeError):
            report['protocol_errors'].append('Missing or invalid message JSON; no automatic repair')
            return report
    try:validate_response(obj,packet)
    except (ValueError,KeyError,TypeError) as exc:
        report['protocol_errors'].append(str(exc) if type(exc)is ValueError else 'Invalid response structure')
    report['strict_response_valid']=not report['protocol_errors']
    entries=obj.get('items',[]) if isinstance(obj,dict) else []
    if not isinstance(entries,list):entries=[]
    by_id={sid:[] for sid in ids}
    for index,item in enumerate(entries):
        sid=item.get('id') if isinstance(item,dict) else None
        if not isinstance(sid,str) or sid not in by_id:
            report['quarantined_entries'].append(dict(response_index=index,reason='unknown_or_readonly_id',item=item))
        else:by_id[sid].append(item)
    for before in requested:
        candidates=by_id[before['id']];errors=[]
        if not candidates:errors.append('missing_item')
        elif len(candidates)!=1:errors.append('duplicate_id; do not pick one automatically')
        else:
            single=dict(packet,items=[before])
            try:validate_response({'items':candidates},single)
            except (ValueError,KeyError,TypeError) as exc:
                errors.append(str(exc) if type(exc)is ValueError else 'Invalid item structure')
        report['items'].append(dict(id=before['id'],current=before['current'],
            candidates=candidates,protocol_errors=errors,
            status='invalid_requires_manual_review' if errors else 'schema_valid_requires_gpt_review'))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default=str(ROOT/'assets/deepseek-polish.json'))
    sub = parser.add_subparsers(dest='command',required=True)
    p = sub.add_parser('prepare')
    p.add_argument('--source',required=True);p.add_argument('--field',choices=['draft','target'],default='target')
    p.add_argument('--ids',required=True);p.add_argument('--profile',required=True);p.add_argument('--output',required=True)
    p = sub.add_parser('run')
    p.add_argument('--packet',required=True);p.add_argument('--approval',required=True);p.add_argument('--output',required=True)
    p = sub.add_parser('diagnose')
    p.add_argument('--packet',required=True);p.add_argument('--response',required=True);p.add_argument('--output',required=True)
    args = parser.parse_args()
    try:
        if args.command == 'diagnose':
            save_new(args.output,diagnose(load(args.packet),args.response))
        elif args.command == 'prepare':
            config = load(args.config)
            save_new(args.output,prepare(args.source,args.field,args.ids.split(','),args.profile,config))
        else:
            config = load(args.config)
            run(load(args.packet),load(args.approval),config,args.output)
        print('Saved local artifact; no subtitle or review status was changed.')
    except (ValueError,OSError,KeyError,TypeError) as exc:
        detail = str(exc) if type(exc) is ValueError else type(exc).__name__
        print('Polish operation stopped: '+detail,file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
