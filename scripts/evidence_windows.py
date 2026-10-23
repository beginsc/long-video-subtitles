"""Plan untranscribed gaps and local visual samples; never infer missing speech."""
import argparse,hashlib,json,math
from pathlib import Path

def number(value,label):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
        raise ValueError(f'{label} must be a finite number')
    return value

def plan(doc,min_gap_ms=4000,focus=(),step_ms=500,max_frames=512,max_word_ms=2000):
    duration=number(doc.get('meta',{}).get('duration_ms'),'meta.duration_ms')
    min_gap_ms=number(min_gap_ms,'gap threshold');step_ms=number(step_ms,'sampling step')
    if isinstance(max_frames,bool) or not isinstance(max_frames,int):
        raise ValueError('max_frames must be an integer')
    if duration<=0 or min_gap_ms<=0 or step_ms<=0 or max_frames<1:
        raise ValueError('Duration, thresholds and limits must be positive')
    max_word_ms=number(max_word_ms,'word duration threshold')
    if max_word_ms<=0:raise ValueError('Word duration threshold must be positive')
    intervals=[];fallback=[];long_words=[]
    for row in doc['segments']:
        words=row.get('asr_words')
        if words:
            offset=number(row.get('asr_words_offset_ms',0),'word offset')
            aligned=[w for w in words if w.get('start') is not None and w.get('end') is not None]
            if len(aligned)!=len(words):fallback.append({'id':row['id'],'reason':'Some words lack alignment; gaps may include unaligned speech'})
            if aligned:
                for index,w in enumerate(words):
                    if w.get('start') is None or w.get('end') is None:continue
                    start=number(w['start'],'word start')*1000+offset;end=number(w['end'],'word end')*1000+offset
                    intervals.append((start,end,row['id'],'aligned_word'))
                    if end-start>max_word_ms:
                        long_words.append(dict(cue_id=row['id'],parent_ids=row.get('parent_ids',[]),word_index=index,word=w.get('word',''),start_ms=start,end_ms=end,duration_ms=end-start,asr_words_offset_ms=offset,status='unreviewed',reason='Possible stretched alignment or prolonged speech; not a confirmed error or silence; do not automatically trim'))
                continue
        fallback.append({'id':row['id'],'reason':'No usable word alignment; cue interval can hide internal gaps'})
        intervals.append((number(row['start_ms'],'cue start'),number(row['end_ms'],'cue end'),row['id'],'cue'))
    for start,end,sid,basis in intervals:
        if not 0<=start<=end<=duration+1:raise ValueError(f'Invalid {basis} interval in {sid}: {start}, {end}, media={duration}')
    merged=[]
    for start,end,sid,basis in sorted(intervals):
        end=min(end,duration)
        if merged and start<=merged[-1][1]:
            if end>merged[-1][1]:merged[-1][1]=end;merged[-1][3]=sid
        else:merged.append([start,end,sid,sid])
    gaps=[];last_end=0;left=None
    for start,end,first,last in merged+[[duration,duration,None,None]]:
        if start-last_end>=min_gap_ms:
            gaps.append(dict(id=f'gap-{len(gaps)+1:04}',start_ms=round(last_end),end_ms=round(start),duration_ms=round(start-last_end),left_id=left,right_id=first,status='unreviewed',reason='No aligned-word/cue coverage; may be silence, music, unaligned or missed speech'))
        last_end=end;left=last
    samples=set()
    for start,end in focus:
        start=number(start,'focus start');end=number(end,'focus end')
        if not 0<=start<=end<=duration:raise ValueError('Focus window outside media')
        t=start
        while t<=end:
            samples.add(round(t));t+=step_ms
            if len(samples)>max_frames:raise ValueError('Frame plan exceeds max_frames; narrow windows or explicitly increase limit')
        samples.add(round(end))
        if len(samples)>max_frames:raise ValueError('Frame plan exceeds max_frames')
    return dict(media_duration_ms=duration,media_sha256=doc.get('meta',{}).get('media_sha256'),interval_basis='aligned_words_where_available_else_cue; not independent VAD',gap_candidates=gaps,word_duration_candidates=long_words,word_duration_threshold_ms=max_word_ms,alignment_caveats=fallback,visual_sample_times_ms=sorted(samples),visual_status='planned_not_extracted_or_viewed',note='No reference text used. Gaps are candidates, not proven omissions. Dense local samples do not certify full-video OCR coverage. Do not add words without source evidence.')

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--min-gap-ms',type=int,default=4000);p.add_argument('--focus-window',action='append',default=[],help='Media milliseconds START:END; may repeat');p.add_argument('--step-ms',type=int,default=500);p.add_argument('--max-frames',type=int,default=512);p.add_argument('--max-word-ms',type=int,default=2000);a=p.parse_args()
    focus=[]
    for window in a.focus_window:
        parts=window.split(':')
        if len(parts)!=2:p.error('focus-window requires START:END milliseconds')
        focus.append(tuple(map(int,parts)))
    raw=a.source.read_bytes();out=plan(json.loads(raw),a.min_gap_ms,focus,a.step_ms,a.max_frames,a.max_word_ms);out['source_sha256']=hashlib.sha256(raw).hexdigest();out['source_path']=str(a.source.resolve());a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.open('x',encoding='utf8') as f:json.dump(out,f,ensure_ascii=False,indent=2)
    print(json.dumps({'gap_candidates':len(out['gap_candidates']),'word_duration_candidates':len(out['word_duration_candidates']),'planned_visual_frames':len(out['visual_sample_times_ms']),'actual_evidence_review':False}))

if __name__=='__main__':main()
