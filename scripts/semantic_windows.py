"""Prepare neighboring-cue review windows and validate explicit review records.

Lexical hints select work, not errors. No translation, reference lookup or review
status mutation is performed. A structurally valid note is not proof of meaning.
"""
import argparse,hashlib,json,math,re
from collections import Counter
from pathlib import Path
from revision_audit import ATTENTION,rows
from artifact_io import save_text


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def word_view(cue):
    words=cue.get('asr_words')or[];out=[];caveats=[];last=-1
    offset=cue.get('asr_words_offset_ms',0)
    if type(offset)not in (int,float)or not math.isfinite(offset)or offset<0:raise ValueError('Invalid word offset')
    for index,w in enumerate(words):
        if any(type(w.get(k))not in (int,float)or not math.isfinite(w[k])for k in ['start','end']):
            caveats.append(dict(word_index=index,reason='missing_or_invalid_alignment'));continue
        start=w['start']*1000+offset;end=w['end']*1000+offset
        if start<0 or end<start or start<last:raise ValueError('Invalid or unordered aligned words: '+cue['id'])
        last=start
        # Shared ancestral words can extend beyond a split child's interval.
        if end<=cue['start_ms']or start>=cue['end_ms']:continue
        out.append(dict(word_index=index,word=w.get('word',''),start_ms=start,end_ms=end))
    if not out:caveats.append(dict(reason='no_overlapping_aligned_words; do_not_invent_timing'))
    return out,caveats


def prepare(doc,field='target',all_cues=False):
    cues=rows(doc,field)
    if any(a['start_ms']>b['start_ms']for a,b in zip(cues,cues[1:])):raise ValueError('Cues not chronological')
    parent_counts=Counter(p for c in cues for p in c.get('parent_ids',[]))
    windows=[]
    for i,cue in enumerate(cues):
        hints={k:[m.group()for m in re.finditer(pattern,cue['source'],re.I)]for k,pattern in ATTENTION.items()}
        hints={k:v for k,v in hints.items()if v};reasons=list(hints)
        parents=cue.get('parent_ids',[])
        if len(parents)>1 or any(parent_counts[p]>1 for p in parents):reasons.append('split_merge_lineage')
        if cue.get('source_checks'):reasons.append('source_evidence')
        if cue['end_ms']-cue['start_ms']>6500:reasons.append('long_display')
        if not reasons and not all_cues:continue
        context=[]
        for s in cues[max(0,i-1):i+2]:
            ws,caveats=word_view(s)
            context.append(dict(id=s['id'],start_ms=s['start_ms'],end_ms=s['end_ms'],source=s['source'],translation=s[field],parent_ids=s.get('parent_ids',[]),source_checks=s.get('source_checks',[]),words=ws,alignment_caveats=caveats))
        windows.append(dict(cue_id=cue['id'],reasons=reasons or['explicit_all_cues'],source_hints=hints,context=context,review_status='pending'))
    return dict(schema_version=1,kind='semantic-window-review',source_digest=digest(doc),field=field,all_cues=all_cues,windows=windows,limitations='English lexical triage may miss errors. Read each cue in display order and with neighbors. Word positions are evidence candidates, not Chinese alignment. No cue is marked reviewed; no original audio has been heard by this script.')


def check(doc,packet,review):
    expected=prepare(doc,packet['field'],packet['all_cues'])
    if packet!=expected:raise ValueError('Window packet stale or changed; rebuild from exact saved source')
    if review.get('packet_digest')!=digest(packet):raise ValueError('Review belongs to a different window packet')
    if not isinstance(review.get('reviewer'),str)or not review['reviewer'].strip():raise ValueError('Actual reviewer required')
    decisions=review.get('items');windows={w['cue_id']:w for w in packet['windows']}
    if not isinstance(decisions,list):raise ValueError('Review items required')
    seen=set();counts=dict(pass_count=0,revise=0,unclear=0)
    for item in decisions:
        sid=item.get('cue_id');status=item.get('decision')
        if sid not in windows or sid in seen:raise ValueError('Duplicate or unknown review cue')
        seen.add(sid)
        if status not in ('pass','revise','unclear'):raise ValueError('Pending/invalid review decision')
        if not isinstance(item.get('note'),str)or not item['note'].strip():raise ValueError('Specific reasoning required')
        mappings=item.get('mappings',[])
        if not isinstance(mappings,list)or(status!='unclear'and not mappings):raise ValueError('Source/translation phrase mappings required')
        context={s['id']:s for s in windows[sid]['context']}
        for mapping in mappings:
            for prefix,key in [('source','source'),('target','translation')]:
                cited=context.get(mapping.get(prefix+'_cue_id'));quote=mapping.get(prefix+'_quote')
                if cited is None or not isinstance(quote,str)or not quote.strip()or quote not in cited[key]:raise ValueError('Quoted phrase not in cited saved cue')
        counts['pass_count'if status=='pass'else status]+=1
    if seen!=set(windows):raise ValueError('Missing selected review windows')
    open_ids=[s['id']for s in doc['segments']if any(x.get('status')=='open'for x in s.get('source_checks',[]))]
    return dict(kind='semantic-window-review-validation',**counts,windows=len(windows),open_source_cues=open_ids,source_digest=digest(doc),packet_digest=digest(packet),records_structurally_valid=True,requires_followup=bool(counts['revise']or counts['unclear']or open_ids),changes_to_subtitles=0,limitation='Checks completeness, version and exact quotes only. Does not certify semantic correctness, listening or independence; apply decisions through normal proofreading/editor workflow.')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['prepare','check']);p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--field',choices=['draft','target'],default='target');p.add_argument('--all-cues',action='store_true');p.add_argument('--packet',type=Path);p.add_argument('--review',type=Path);a=p.parse_args()
    read=lambda path:json.loads(path.read_text(encoding='utf-8-sig'))
    if a.command=='prepare':result=prepare(read(a.source),a.field,a.all_cues)
    else:
        if not a.packet or not a.review:p.error('check requires --packet and --review')
        result=check(read(a.source),read(a.packet),read(a.review))
    save_text(a.output,json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(command=a.command,windows=len(result['windows'])if isinstance(result['windows'],list)else result['windows'],requires_followup=result.get('requires_followup'),semantic_certification=False)))
    return 2 if result.get('requires_followup')else 0

if __name__=='__main__':raise SystemExit(main())
