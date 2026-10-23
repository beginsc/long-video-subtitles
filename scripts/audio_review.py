"""Prepare local, time-aligned audio evidence for unresolved cues; no ASR/inference."""
import argparse
import json
import math
from pathlib import Path
import sys
from subtitle_workbench import digest, load, save, structural
from vision_workbench import executable, run_media, file_hash
from audio_timeline import decode, slice_pcm


def clip_plan(doc, queue, duration_ms, offset_ms=0, before_ms=2500, after_ms=2500):
    rows = structural(doc)
    if queue.get('input_digest') != digest(doc):
        raise ValueError('Review queue is stale; run triage on this exact source version')
    if any(type(x) is not int or x < 0 for x in (offset_ms, before_ms, after_ms)) or type(duration_ms) is not int or duration_ms <= 0:
        raise ValueError('Invalid media interval or context padding')
    by_id = {r['id']: (i,r) for i,r in enumerate(rows)}
    seen, result = set(), []
    for item in queue['items']:
        sid = item['id']
        if sid in seen or sid not in by_id:
            raise ValueError('Duplicate or unknown review cue')
        seen.add(sid);i,r = by_id[sid]
        if (item['start_ms'], item['end_ms'], item['source']) != (r['start_ms'], r['end_ms'], r['source']):
            raise ValueError('Review queue content does not match source')
        if not offset_ms <= r['start_ms'] < r['end_ms'] <= offset_ms + duration_ms:
            raise ValueError('Cue outside media; check explicit media offset')
        start = max(offset_ms, r['start_ms']-before_ms)
        end = min(offset_ms+duration_ms, r['end_ms']+after_ms)
        if end-start > 90000:
            raise ValueError('Review clip exceeds 90 seconds; split the source cue or reduce context')
        result.append(dict(id=sid, start_ms=r['start_ms'], end_ms=r['end_ms'], source=r['source'],
                           clip_start_ms=start, clip_end_ms=end, local_start_ms=start-offset_ms,
                           focus_in_clip_ms=[r['start_ms']-start, r['end_ms']-start], reasons=item['reasons'],
                           context=[dict(id=x['id'], source=x['source'], start_ms=x['start_ms'], end_ms=x['end_ms']) for x in rows[max(0,i-1):i+2]]))
    if not result:
        raise ValueError('Queue contains no recorded unresolved cues; no audio extracted')
    return result


def prepare(args):
    doc, queue = load(args.source), load(args.queue)
    media = Path(args.media).resolve(strict=True)
    if args.audio_stream < 0:
        raise ValueError('audio-stream must be nonnegative')
    ffmpeg, ffprobe = executable(args.ffmpeg), executable(args.ffprobe)
    probe = json.loads(run_media([ffprobe,'-v','error','-protocol_whitelist','file,pipe','-show_format','-show_streams','-of','json',str(media)]))
    streams = [s for s in probe.get('streams',[]) if s.get('codec_type')=='audio']
    if args.audio_stream >= len(streams):
        raise ValueError('Requested audio stream not found')
    duration = float(probe.get('format',{}).get('duration',0))
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('Cannot determine media duration')
    plan = clip_plan(doc,queue,round(duration*1000),args.media_offset_ms,args.before_ms,args.after_ms)
    out = Path(args.output_dir).resolve();out.mkdir(parents=True,exist_ok=False)
    fingerprint = (media.stat().st_size, media.stat().st_mtime_ns)
    packet = dict(schema_version=1, kind='audio-review-packet', source_digest=digest(doc),
                  media=dict(path=str(media),sha256=file_hash(media),duration_ms=round(duration*1000),offset_ms=args.media_offset_ms,audio_stream=args.audio_stream),
                  status='prepared_not_listened', items=plan,
                  note='No model/audio review performed. Use an actual audio-input tool or a human; ASR agreement is auxiliary only.')
    timeline=decode(media,out/'timeline.wav',ffmpeg,ffprobe,args.audio_stream,channels=min(2,streams[args.audio_stream].get('channels',1)))
    packet['canonical_audio']=timeline
    for n,item in enumerate(plan,1):
        path=out/f'clip-{n:04d}.wav'
        local_end=item['clip_end_ms']-args.media_offset_ms
        cut=slice_pcm(timeline['pcm_path'],path,item['local_start_ms'],local_end,allow_rounded_end=True)
        item.update(clip=path.name,clip_sha256=cut['sha256'],sample_interval=[cut['start_sample'],cut['end_sample']],requested_local_interval_ms=cut['requested_interval_ms'],actual_local_interval_ms=cut['actual_interval_ms'],end_rounding_clamped_samples=cut['end_rounding_clamped_samples'],review_status='pending')
    if fingerprint != (media.stat().st_size,media.stat().st_mtime_ns):
        raise ValueError('Media changed during extraction; keep partial files and use a new output directory')
    packet['packet_digest']=digest(packet)
    save(out/'packet.json',packet)
    save(out/'review-template.json',dict(packet_digest=packet['packet_digest'],reviewer='',audio_input_tool='',
         items=[dict(id=x['id'],clip_sha256=x['clip_sha256'],status='pending',heard_text='',speech_origin='unknown',
                     alternatives=[],decision='',note='') for x in plan]))
    print(f'Prepared {len(plan)} local audio clips; no listening performed and no review status changed')


def verify(args):
    path=Path(args.packet).resolve();packet=load(path);expected=packet.pop('packet_digest')
    if digest(packet)!=expected:
        raise ValueError('Audio packet changed')
    for item in packet['items']:
        clip=(path.parent/item['clip']).resolve()
        if clip.parent!=path.parent or file_hash(clip)!=item['clip_sha256']:
            raise ValueError('Review audio changed: '+item['id'])
    print('Audio packet and clip hashes match; this does not certify listening')


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    q=sub.add_parser('prepare')
    for key in ('source','queue','media','output-dir'):q.add_argument('--'+key,required=True)
    q.add_argument('--ffmpeg',default='ffmpeg');q.add_argument('--ffprobe',default='ffprobe')
    q.add_argument('--audio-stream',type=int,default=0);q.add_argument('--media-offset-ms',type=int,default=0)
    q.add_argument('--before-ms',type=int,default=2500);q.add_argument('--after-ms',type=int,default=2500);q.set_defaults(func=prepare)
    q=sub.add_parser('verify');q.add_argument('packet');q.set_defaults(func=verify)
    args=p.parse_args()
    try:args.func(args)
    except (ValueError,KeyError,TypeError,OSError) as exc:
        print('ERROR: '+str(exc),file=sys.stderr);return 1
    return 0


if __name__=='__main__':sys.exit(main())
