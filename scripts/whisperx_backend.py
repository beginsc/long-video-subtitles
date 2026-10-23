"""WhisperX job API over authenticated SSH. No password in config or logs."""
import argparse
import contextlib
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from asr_credentials import read_key
from subtitle_workbench import row, structural, save

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent/'assets/asr-backends.json'
def load(path): return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def file_hash(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''): h.update(chunk)
    return h.hexdigest()
def checkpoint(path,data):
    path=Path(path)
    with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=path.parent,delete=False) as stream:
        json.dump(data,stream,ensure_ascii=False,indent=2)
        temporary=stream.name
    os.replace(temporary,path)
def validate(config):
    allowed={'adapter','ssh_host','ssh_port','ssh_user','ssh_key_file','ssh_executable','remote_port',
        'api_key_env','credential_name','model','compute_type','batch_size','vad_method','align',
        'language','request_timeout_seconds','upload_timeout_seconds'}
    if set(config)-allowed or config.get('adapter')!='whisperx': raise ValueError('Invalid WhisperX configuration fields')
    for field in ('ssh_host','ssh_user'):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*',config[field]): raise ValueError('Invalid SSH host/user')
    for field in ('ssh_port','remote_port'):
        if type(config[field]) is not int or not 1<=config[field]<=65535: raise ValueError('Invalid port')
    if not re.fullmatch(r'[A-Za-z0-9._-]{1,80}',config['model']): raise ValueError('Invalid model ID; use large-v3')
    if config['model']=='v3-large': raise ValueError('The model ID is large-v3, not v3-large')
    if config['compute_type'] not in ('float16','float32','int8'): raise ValueError('Invalid compute_type')
    if type(config['batch_size']) is not int or not 1<=config['batch_size']<=32: raise ValueError('Invalid batch_size')
    if config['vad_method'] not in ('pyannote','silero') or type(config['align']) is not bool: raise ValueError('Invalid VAD/alignment')
    if config.get('language') and not re.fullmatch(r'[a-z]{2,3}',config['language']): raise ValueError('Use a language code')
    for field in ('request_timeout_seconds','upload_timeout_seconds'):
        if type(config[field]) is not int or not 1<=config[field]<=600: raise ValueError('Invalid request timeout')
    return config

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): return None

class Client:
    def __init__(self,config):
        self.config=validate(config)
        self.key=read_key(config)
        self.process=None
    def __enter__(self):
        cfg=self.config
        identity=Path(cfg['ssh_key_file']).expanduser().resolve(strict=True)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
        args=[cfg.get('ssh_executable','ssh'),'-N','-T','-o','BatchMode=yes','-o','IdentitiesOnly=yes',
            '-o','StrictHostKeyChecking=yes','-o','ExitOnForwardFailure=yes','-o','ConnectTimeout=15',
            '-o','ServerAliveInterval=15','-o','ServerAliveCountMax=2','-p',str(cfg['ssh_port']),
            '-i',str(identity),'-L',f'127.0.0.1:{port}:127.0.0.1:{cfg["remote_port"]}',
            f'{cfg["ssh_user"]}@{cfg["ssh_host"]}']
        self.process=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        self.base=f'http://127.0.0.1:{port}'
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
        try:
            deadline=time.monotonic()+20
            while time.monotonic()<deadline:
                if self.process.poll() is not None: raise ValueError('SSH tunnel failed; check key, host key and login')
                try:
                    with socket.create_connection(('127.0.0.1',port),timeout=.3): return self
                except OSError: time.sleep(.2)
            raise ValueError('SSH tunnel startup timed out')
        except Exception:
            self.__exit__(None,None,None)
            raise
    def __exit__(self,*args):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try: self.process.wait(timeout=5)
            except subprocess.TimeoutExpired: self.process.kill(); self.process.wait()
    def request(self,route,data=None,headers=None,timeout=None):
        if not re.fullmatch(r'/v1/(capabilities|jobs(?:/[a-f0-9]{32}(?:/result)?)?)',route): raise ValueError('Invalid API route')
        request=urllib.request.Request(self.base+route,data=data,headers={'X-API-Key':self.key,**(headers or {})})
        try:
            with self.opener.open(request,timeout=timeout or self.config['request_timeout_seconds']) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            code=exc.code; exc.close()
            raise ValueError(f'WhisperX HTTP {code}; response body and credentials not logged') from None
        except (OSError,urllib.error.URLError,TimeoutError):
            raise ValueError('WhisperX connection failed; resume the saved job ID, do not resubmit automatically') from None

@contextlib.contextmanager
def multipart(path,fields):
    boundary='wx'+uuid.uuid4().hex
    with tempfile.TemporaryFile() as stream:
        for key,value in fields.items():
            stream.write(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
        stream.write(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="audio.bin"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode())
        with Path(path).open('rb') as source:
            for block in iter(lambda:source.read(1024*1024),b''): stream.write(block)
        stream.write(f'\r\n--{boundary}--\r\n'.encode())
        length=stream.tell(); stream.seek(0)
        yield stream,{'Content-Type':f'multipart/form-data; boundary={boundary}','Content-Length':str(length)}

def normalize(raw,job):
    offset=job['offset_ms']
    if type(offset) is not int or offset<0: raise ValueError('Invalid offset_ms')
    if raw.get('input_sha256')!=job['source']['sha256']: raise ValueError('Result media hash mismatch')
    meta=raw.get('meta',{})
    cfg=job['config']
    if meta.get('model')!=cfg['model'] or meta.get('vad_method')!=cfg['vad_method'] or meta.get('vad_status')!='completed':
        raise ValueError('Actual model/VAD does not match requested configuration')
    if cfg['align'] and raw.get('segments') and meta.get('alignment_status')!='completed': raise ValueError('Requested alignment did not complete')
    duration=meta.get('audio_duration_seconds')
    if type(duration) not in (int,float) or not math.isfinite(duration) or duration<=0: raise ValueError('Invalid audio duration')
    if not isinstance(raw.get('segments'),list): raise ValueError('Missing segments list')
    rows=[]
    for segment in raw['segments']:
        start,end=segment.get('start'),segment.get('end')
        if any(type(v) not in (int,float) or not math.isfinite(v) for v in (start,end)) or not 0<=start<end<=duration+.1:
            raise ValueError('Invalid segment timing; inspect raw result')
        text=segment.get('text')
        if not isinstance(text,str) or not text.strip(): raise ValueError('Empty segment; no silent deletion')
        item=row(f's{len(rows)+1:06d}',round(start*1000)+offset,round(end*1000)+offset,text)
        words=copy.deepcopy(segment.get('words',[]))
        if not isinstance(words,list) or any(not isinstance(w,dict) for w in words): raise ValueError('Invalid words list')
        missing=0
        for word in words:
            a,b=word.get('start'),word.get('end')
            if a is None or b is None:
                missing+=1; continue
            if any(type(v) not in (int,float) or not math.isfinite(v) for v in (a,b)) or not 0<=a<=b<=duration+.1:
                raise ValueError('Invalid word timestamp')
        item.update(asr_words=words,asr_words_offset_ms=offset,asr_untimed_word_count=missing)
        for key in ('speaker','avg_logprob'):
            if key in segment: item[key]=segment[key]
        if missing: item['issues'].append(f'{missing} word(s) lack alignment timestamps; do not invent timings')
        rows.append(item)
    document=dict(schema_version=1,meta=dict(asr_backend=job['backend'],asr_transcript_id=job['transcript_id'],
        asr_model_used=meta['model'],asr_details=meta,source_language=raw.get('language'),
        input_sha256=raw['input_sha256'],offset_ms=offset),segments=rows)
    if rows: structural(document)
    return document

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    for command in ('check','submit'):
        p=sub.add_parser(command)
        p.add_argument('--config',default=str(DEFAULT_CONFIG)); p.add_argument('--backend')
        if command=='submit':
            p.add_argument('--file',required=True); p.add_argument('--job',required=True)
            p.add_argument('--model'); p.add_argument('--language'); p.add_argument('--vad-method',choices=['pyannote','silero'])
            p.add_argument('--batch-size',type=int); p.add_argument('--offset-ms',type=int,default=0)
    p=sub.add_parser('status'); p.add_argument('--job',required=True)
    p.add_argument('--wait-seconds',type=int,default=0,choices=range(46)); p.add_argument('--output-dir')
    args=parser.parse_args(argv)
    try:
        if args.command in ('check','submit'):
            registry=load(args.config)
            if registry.get('schema_version')!=1: raise ValueError('Unsupported backend registry version')
            args.backend=args.backend or registry.get('default_backend') or 'whisperx'
            config=copy.deepcopy(registry['backends'][args.backend])
            if args.command=='submit':
                for arg,key in [('model','model'),('language','language'),('batch_size','batch_size'),('vad_method','vad_method')]:
                    if getattr(args,arg) is not None: config[key]=getattr(args,arg)
            validate(config)
        else:
            job=load(args.job); config=job['config']
        if args.command=='submit':
            path=Path(args.file).resolve(strict=True)
            if not path.is_file() or not 0<path.stat().st_size<=64*1024*1024: raise ValueError('Use nonempty audio chunks up to 64 MiB')
            if args.offset_ms<0: raise ValueError('offset_ms must be nonnegative')
            job=dict(schema_version=1,backend=args.backend,config=config,source=dict(file_name=path.name,sha256=file_hash(path)),
                transcript_id=uuid.uuid4().hex,offset_ms=args.offset_ms,phase='prepared')
            save(args.job,job)
        with Client(config) as client:
            if args.command=='check':
                result=client.request('/v1/capabilities')
                if result.get('api_version')!='1.1.0' or not result.get('durable_jobs') or not result.get('real_vad'):
                    raise ValueError('Server lacks required VAD/job protocol; upgrade wrapper before submitting')
                print(json.dumps(result)); return 0
            if args.command=='submit':
                fields={k:config[k] for k in ('model','batch_size','compute_type','vad_method')}
                fields.update(job_id=job['transcript_id'],align=str(config['align']).lower(),language=config.get('language') or '')
                job['phase']='submitting'; checkpoint(args.job,job)
                try:
                    with multipart(path,fields) as (data,headers): response=client.request('/v1/jobs',data,headers,config['upload_timeout_seconds'])
                    if response.get('id')!=job['transcript_id'] or response.get('input_sha256')!=job['source']['sha256']: raise ValueError('Submission identity mismatch')
                    job['phase']=response['status']
                except Exception:
                    job['phase']='submission_uncertain'; checkpoint(args.job,job); raise
                checkpoint(args.job,job)
            else:
                ident=job['transcript_id']
                deadline=time.monotonic()+args.wait_seconds
                while True:
                    response=client.request('/v1/jobs/'+ident)
                    if response.get('id')!=ident: raise ValueError('Job identity mismatch')
                    job['phase']=response['status']; checkpoint(args.job,job)
                    if job['phase'] not in ('uploading','queued','processing') or time.monotonic()>=deadline: break
                    time.sleep(min(3,max(0,deadline-time.monotonic())))
                if job['phase'] in ('error','interrupted'): raise ValueError('Remote job '+job['phase']+': '+response.get('error_code','unknown'))
                if job['phase']=='completed' and args.output_dir:
                    raw=client.request('/v1/jobs/'+ident+'/result')
                    if raw.get('input_sha256')!=job['source']['sha256']: raise ValueError('Result media hash mismatch')
                    out=Path(args.output_dir); out.mkdir(parents=True,exist_ok=False)
                    save(out/'whisperx.result.json',raw)
                    document=normalize(raw,job)
                    if document['segments']:
                        save(out/'source.json',document)
                    else:
                        save(out/'no-speech.json',{'status':'no_speech','meta':document['meta'],'note':'No source cues created; inspect audio and VAD before accepting'})
            print(json.dumps({'backend':job['backend'],'phase':job['phase'],'transcript_id':job['transcript_id']}))
        return 0
    except (ValueError,KeyError,TypeError,OSError,subprocess.SubprocessError) as exc:
        message=str(exc) if type(exc) is ValueError else 'Invalid configuration, local file, or remote response'
        print('ERROR: '+message,file=sys.stderr); return 1

def maybe_dispatch(argv):
    parser=argparse.ArgumentParser(add_help=False)
    parser.add_argument('command',nargs='?'); parser.add_argument('--backend'); parser.add_argument('--config',default=str(DEFAULT_CONFIG)); parser.add_argument('--job')
    args,_=parser.parse_known_args(argv)
    try:
        if args.command=='status' and args.job:
            selected=load(args.job).get('config',{}).get('adapter')=='whisperx'
        else:
            registry=load(args.config); name=args.backend or registry.get('default_backend')
            selected=registry.get('backends',{}).get(name,{}).get('adapter')=='whisperx'
        if selected: return main(argv)
    except (OSError,ValueError,KeyError,TypeError): pass
    return None

if __name__=='__main__': sys.exit(main())
