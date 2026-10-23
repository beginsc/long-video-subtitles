#!/usr/bin/env python3
"""Offline DoveBench cache, hypothesis freeze and subtitle scoring. No network calls."""
import argparse
import collections
import datetime
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys

OFFICIAL_BLOB = '9b1c02892e960b7760e09698a7820f3492ee9cd0'


def read(p):
    return json.loads(Path(p).read_text(encoding='utf-8-sig'))


def write(p, value):
    with Path(p).open('x', encoding='utf-8', newline='') as f:
        f.write(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def load_cache(root):
    manifest = read(root / 'cache.json')
    if sha(root / 'data/dovebench.jsonl') != manifest['data_sha256']:
        raise ValueError('Local reference dataset changed; restore the pinned cache before scoring')
    rows = [json.loads(x) for x in (root / 'data/dovebench.jsonl').read_text(encoding='utf-8').splitlines() if x.strip()]
    groups = collections.defaultdict(list)
    for r in rows:
        if (not all(isinstance(r.get(k), str) and r[k].strip() for k in ('video_id', 'youtube_id', 'zh_text'))
                or not isinstance(r.get('en_text'), str)
                or type(r.get('idx')) is not int
                or any(type(r.get(k)) not in (float, int) or not math.isfinite(r[k]) for k in ('start', 'end'))
                or not 0 <= r['start'] < r['end']):
            raise ValueError('Malformed reference row')
        groups[r['video_id']].append(r)
    for video, refs in groups.items():
        refs.sort(key=lambda r:r['idx'])
        if [r['idx'] for r in refs] != list(range(len(refs))) or len({r['youtube_id'] for r in refs}) != 1:
            raise ValueError('Duplicate/missing index or conflicting video identity: ' + video)
        if any(b['start'] < a['start'] for a, b in zip(refs, refs[1:])):
            raise ValueError('Reference chronology error: ' + video)
    if len(rows) != manifest['rows'] or len(groups) != manifest['videos']:
        raise ValueError('Cache count mismatch')
    # Some official rows have an empty aligned English reference. Retain them:
    # Chinese subtitle scoring does not consume en_text, and dropping them loses coverage.
    return manifest, groups


def catalog_only(root):
    # list/freeze never read reference translations; metadata was generated at installation.
    manifest = read(root / 'cache.json')
    if sha(root / 'catalog.json') != manifest['catalog_sha256']:
        raise ValueError('Catalog changed')
    return manifest, read(root / 'catalog.json')


def parse_srt(path):
    raw = Path(path).read_text(encoding='utf-8-sig').replace('\r\n', '\n').replace('\r', '\n')
    pattern = re.compile(r'^(\d{2,}):([0-5]\d):([0-5]\d)[,.](\d{3})\s+-->\s+(\d{2,}):([0-5]\d):([0-5]\d)[,.](\d{3})$')
    result = []
    for block in re.split(r'\n[ \t]*\n', raw.strip()):
        lines = block.splitlines(); pos = int(bool(lines and lines[0].strip().isdigit()))
        m = pattern.fullmatch(lines[pos].strip()) if len(lines) > pos else None
        if not m or len(lines) <= pos + 1:
            raise ValueError('Invalid SRT block; nothing is silently skipped')
        nums = list(map(int,m.groups()))
        times = [((nums[i]*60+nums[i+1])*60+nums[i+2])*1000+nums[i+3] for i in (0,4)]
        text = '\n'.join(lines[pos+1:]).strip()
        if not text or times[0] >= times[1] or (result and times[0] < result[-1]['start_ms']):
            raise ValueError('Invalid SRT text/timing')
        result.append(dict(start_ms=times[0],end_ms=times[1],text=text))
    return result


def stamp(sec):
    ms=round(sec*1000); h,ms=divmod(ms,3600000); m,ms=divmod(ms,60000); s,ms=divmod(ms,1000)
    return f'{h:02}:{m:02}:{s:02},{ms:03}'


def freeze(args, root):
    manifest, catalog = catalog_only(root)
    matches = [v for v in catalog if v['video_id']==args.video_id or v['youtube_id']==args.video_id]
    if len(matches)!=1:
        raise ValueError('Unknown/ambiguous video ID; use list (metadata only)')
    source = Path(args.hypothesis).resolve(); cues = parse_srt(source)
    output = Path(args.output); output.mkdir(parents=True,exist_ok=False)
    (output/'hypothesis.srt').write_bytes(source.read_bytes())
    write(output/'freeze.json', dict(schema_version=1,frozen_at_utc=now(),hypothesis_sha256=sha(source),
        source_path=str(source),hypothesis_segments=len(cues),hypothesis_status=args.hypothesis_status,
        video=matches[0],data_sha256=manifest['data_sha256'],prior_reference_exposure=args.prior_exposure,
        exposure_note=args.exposure_note,scope='single-video end-to-end subtitle evaluation',reference_text_read_by_freeze=False))
    print(json.dumps({'frozen':str(output.resolve()),'segments':len(cues),'prior_reference_exposure':args.prior_exposure}))


def score(args, root):
    run=Path(args.run); frozen=read(run/'freeze.json'); hyp=run/'hypothesis.srt'
    if sha(hyp)!=frozen['hypothesis_sha256']:
        raise ValueError('Frozen hypothesis modified; create a new run instead')
    output=run/'scores'; output.mkdir(exist_ok=False)
    manifest,groups=load_cache(root)
    if frozen['data_sha256']!=manifest['data_sha256']:
        raise ValueError('Reference cache differs from freeze')
    refs=groups[frozen['video']['video_id']]
    reference=output/'reference.zh.srt'
    reference.write_text('\n'.join(f"{i+1}\n{stamp(r['start'])} --> {stamp(r['end'])}\n{r['zh_text'].strip()}\n" for i,r in enumerate(refs)),encoding='utf-8')
    env=os.environ.copy();env.update(PYTHONPATH=str(root/'deps'),PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1')
    cmd=[sys.executable,'-B','-X','utf8','-m','suber','-H',str(hyp.resolve()),'-R',str(reference.resolve()),'-l','zh','--metrics','SubER','t-BLEU','t-chrF','AS-BLEU','AS-chrF','--suber-statistics']
    p=subprocess.run(cmd,capture_output=True,text=True,encoding='utf-8',env=env)
    (output/'metrics.stderr.log').write_text(p.stderr,encoding='utf-8')
    if p.returncode:
        raise ValueError('Local SubER failed; inspect '+str(output/'metrics.stderr.log'))
    scores=json.loads(p.stdout)
    sys.path.insert(0,str(root/'deps'))
    from sacrebleu.metrics import BLEU,CHRF
    cues=parse_srt(hyp); h=' '.join(s['text'].replace('\n','') for s in cues); ref=' '.join(s['zh_text'] for s in refs)
    bleu=BLEU(tokenize='zh'); chrf=CHRF()
    scores['document_BLEU_zh']=bleu.corpus_score([h],[[ref]]).score
    scores['document_chrF']=chrf.corpus_score([h],[[ref]]).score
    scores['signatures']={'document_BLEU':str(bleu.get_signature()),'document_chrF':str(chrf.get_signature())}
    scores['versions']={n:importlib.metadata.version(n) for n in ('sacrebleu','subtitle-edit-rate','jiwer','numpy')}
    scores.update(scored_at_utc=now(),offline=True,hypothesis_sha256=sha(hyp),data_sha256=manifest['data_sha256'],
        reference_sha256=sha(reference),reference_segments=len(refs),hypothesis_segments=len(cues),video=frozen['video'],
        prior_reference_exposure=frozen['prior_reference_exposure'],hypothesis_status=frozen['hypothesis_status'],
        interpretation='Automatic subtitle/text agreement, not percentage translation accuracy. No semantic accuracy claim.',
        not_run=['COMET','BLEURT','SubSONAR','independent semantic judge'],command=cmd)
    if sha(hyp)!=frozen['hypothesis_sha256']:
        raise ValueError('Frozen hypothesis changed during evaluation')
    write(output/'metrics.json',scores)
    print(json.dumps({k:scores[k] for k in ('SubER','t-BLEU','t-chrF','AS-BLEU','AS-chrF','document_BLEU_zh','document_chrF')},ensure_ascii=False))


def reference_issues(args, root):
    """Record human/model diagnostic annotations only after an intact scored run."""
    run=Path(args.run);frozen=read(run/'freeze.json');metrics=read(run/'scores/metrics.json')
    if sha(run/'hypothesis.srt')!=frozen['hypothesis_sha256'] or metrics['hypothesis_sha256']!=frozen['hypothesis_sha256']:
        raise ValueError('Scored hypothesis changed')
    manifest,groups=load_cache(root)
    if metrics['data_sha256']!=manifest['data_sha256'] or frozen['data_sha256']!=manifest['data_sha256']:
        raise ValueError('Reference cache differs from scored run')
    annotations=read(args.annotations)
    if annotations.get('video_id')!=frozen['video']['video_id']:
        raise ValueError('Annotation video differs from scored run')
    rows={r['idx']:r for r in groups[annotations['video_id']]};seen=set();issues=[]
    categories={'polarity_conflict','omission','entity_conflict','timing','style_difference','other'}
    for item in annotations['issues']:
        idx=item.get('idx')
        if type(idx) is not int or idx not in rows or idx in seen:
            raise ValueError('Reference idx must be unique, zero-based and present in this video')
        if item.get('category') not in categories or item.get('status') not in ('suspected','confirmed'):
            raise ValueError('Invalid diagnostic category/status')
        if any(not isinstance(item.get(k),str) or not item[k].strip() for k in ('note','reviewer')):
            raise ValueError('Reference issue requires reviewer and specific note')
        evidence=item.get('evidence',[])
        if not isinstance(evidence,list) or any(not isinstance(x,str) or not x.strip() for x in evidence):
            raise ValueError('Evidence must be a list of nonempty locators')
        if item['status']=='confirmed' and not evidence:
            raise ValueError('Confirmed diagnosis requires actual evidence; a reference disagreement is not an audio verdict')
        seen.add(idx);r=rows[idx]
        issues.append(dict(annotation=item,reference={k:r[k] for k in ('idx','start','end','en_text','zh_text')}))
    write(Path(args.output),dict(schema_version=1,video_id=annotations['video_id'],data_sha256=manifest['data_sha256'],
          hypothesis_sha256=frozen['hypothesis_sha256'],issues=issues,created_at_utc=now(),
          effect='Diagnostic sidecar only. No reference rows excluded, no score adjusted, no translations or cache changed.'))
    print(f'Recorded {len(issues)} post-score reference diagnostics; original scores unchanged')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cache',type=Path,default=Path(os.environ.get('DOVEBENCH_HOME',str(Path.home()/'.codex/datasets/DoveBench'))))
    sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('list');sub.add_parser('verify')
    q=sub.add_parser('freeze');q.add_argument('hypothesis');q.add_argument('--video-id',required=True);q.add_argument('--output',required=True)
    q.add_argument('--prior-exposure',choices=('none','known','unknown'),default='unknown')
    q.add_argument('--exposure-note',default='');q.add_argument('--hypothesis-status',choices=('final','draft','unreviewed'),default='unreviewed')
    q=sub.add_parser('score');q.add_argument('--run',required=True)
    q=sub.add_parser('reference-issues');q.add_argument('--run',required=True);q.add_argument('--annotations',required=True);q.add_argument('--output',required=True)
    args=p.parse_args();root=args.cache.resolve()
    try:
        if args.command=='list':
            print(json.dumps(catalog_only(root)[1],ensure_ascii=False,indent=2))
        elif args.command=='verify':
            m,g=load_cache(root);catalog_only(root)
            print(json.dumps({'verified':True,'rows':m['rows'],'videos':len(g),'data_sha256':m['data_sha256']}))
        elif args.command=='freeze':freeze(args,root)
        elif args.command=='reference-issues':reference_issues(args,root)
        else:score(args,root)
        return 0
    except (ValueError,OSError,KeyError,TypeError,ImportError) as e:
        print('ERROR: '+str(e),file=sys.stderr);return 1


if __name__=='__main__':
    sys.exit(main())
