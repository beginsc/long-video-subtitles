"""Decode a timestamp-preserving PCM timeline once, then slice by sample index."""
import argparse,json,math,subprocess,wave
from pathlib import Path
from vision_workbench import executable,file_hash,run_media

RATE=16000

def finite(value,label):
    if isinstance(value,bool):raise ValueError(f'Invalid {label}')
    try:value=float(value)
    except (TypeError,ValueError):raise ValueError(f'Invalid {label}')
    if not math.isfinite(value):raise ValueError(f'Invalid {label}')
    return value

def pcm_info(path):
    with wave.open(str(path),'rb') as w:
        if w.getcomptype()!='NONE' or w.getsampwidth()!=2:raise ValueError('Expected uncompressed 16-bit PCM WAV')
        return dict(sample_rate=w.getframerate(),channels=w.getnchannels(),frames=w.getnframes(),duration_seconds=w.getnframes()/w.getframerate())

def decode(media,output,ffmpeg='ffmpeg',ffprobe='ffprobe',audio_stream=0,channels=1):
    media=Path(media).resolve(strict=True);output=Path(output).resolve()
    if output.exists():raise FileExistsError(output)
    if type(audio_stream) is not int or audio_stream<0 or type(channels) is not int or channels not in (1,2):raise ValueError('Invalid stream or channel count')
    probe=json.loads(run_media([executable(ffprobe),'-v','error','-protocol_whitelist','file,pipe','-show_format','-show_streams','-of','json',str(media)]))
    streams=[s for s in probe.get('streams',[]) if s.get('codec_type')=='audio']
    if audio_stream>=len(streams):raise ValueError('Audio stream not found')
    duration=finite(probe.get('format',{}).get('duration'),'media duration')
    origin=finite(probe.get('format',{}).get('start_time',0),'format start')
    if duration<=0:raise ValueError('Media duration must be positive')
    fingerprint=(media.stat().st_size,media.stat().st_mtime_ns);media_sha=file_hash(media)
    output.parent.mkdir(parents=True,exist_ok=True)
    # No input seeking, including -ss 0. Normalize the container origin together
    # with both tracks; aresample preserves audio gaps/delayed starts via padding.
    command=[executable(ffmpeg),'-v','error','-n','-protocol_whitelist','file,pipe','-copyts','-start_at_zero','-i',str(media),'-map',f'0:a:{audio_stream}','-vn','-af',f'aresample={RATE}:async=1:first_pts=0,apad','-t',f'{duration:.9f}','-ac',str(channels),'-ar',str(RATE),'-c:a','pcm_s16le',str(output)]
    subprocess.run(command,check=True,capture_output=True)
    info=pcm_info(output)
    if abs(info['frames']-round(duration*RATE))>1:raise ValueError('Decoded duration differs from requested media timeline; retain output for inspection')
    if fingerprint!=(media.stat().st_size,media.stat().st_mtime_ns):raise ValueError('Media changed during decode')
    return dict(schema_version=1,kind='canonical-audio-timeline',media_path=str(media),media_sha256=media_sha,audio_stream=audio_stream,container_origin_seconds=origin,media_duration_seconds=duration,stream_start_seconds=streams[audio_stream].get('start_time'),video_stream_starts_seconds=[s.get('start_time') for s in probe.get('streams',[]) if s.get('codec_type')=='video'],pcm_path=str(output),pcm_sha256=file_hash(output),pcm=info,offset_ms=0,timeline='Media time zero is container format start; delayed audio/gaps padded according to timestamps; terminal silence padded to media duration',command=command,evidence_status='numeric_decode_not_listened')

def slice_pcm(source,output,start_ms,end_ms,allow_rounded_end=False):
    start=finite(start_ms,'start');end=finite(end_ms,'end')
    if not 0<=start<end:raise ValueError('Invalid slice interval')
    output=Path(output)
    if output.exists():raise FileExistsError(output)
    with wave.open(str(source),'rb') as r:
        if r.getcomptype()!='NONE' or r.getsampwidth()!=2:raise ValueError('Expected 16-bit PCM')
        rate=r.getframerate();first=round(start*rate/1000);last=round(end*rate/1000)
        if type(allow_rounded_end) is not bool:raise ValueError('allow_rounded_end must be boolean')
        requested_last=last
        # Container duration is rounded to milliseconds by queue consumers;
        # decode itself permits one sample of rounding. Never clamp a real
        # offset error or an invalid start under the guise of this tolerance.
        excess=end-r.getnframes()*1000/rate
        if last>r.getnframes() and allow_rounded_end and excess<=0.5+1000/rate+1e-9:
            last=r.getnframes()
        if not 0<=first<last<=r.getnframes():raise ValueError('Slice outside canonical timeline')
        r.setpos(first);remaining=last-first
        with output.open('xb') as raw, wave.open(raw,'wb') as w:
            w.setparams(r.getparams());w.setnframes(remaining)
            while remaining:
                count=min(remaining,65536);chunk=r.readframes(count)
                if len(chunk)!=count*r.getnchannels()*r.getsampwidth():raise ValueError('Truncated canonical PCM')
                w.writeframesraw(chunk);remaining-=count
    info=pcm_info(output)
    if info['frames']!=last-first:raise ValueError('Slice sample count mismatch')
    return dict(path=str(output.resolve()),sha256=file_hash(output),start_sample=first,end_sample=last,offset_ms=first*1000/rate,requested_interval_ms=[start,end],actual_interval_ms=[first*1000/rate,last*1000/rate],end_rounding_clamped_samples=requested_last-last,pcm=info,status='prepared_not_listened')

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--media',required=True);p.add_argument('--output-dir',type=Path,required=True);p.add_argument('--ffmpeg',default='ffmpeg');p.add_argument('--ffprobe',default='ffprobe');p.add_argument('--audio-stream',type=int,default=0);p.add_argument('--block-ms',type=int,default=600000);p.add_argument('--overlap-ms',type=int,default=5000);a=p.parse_args()
    if not 0<=a.overlap_ms<a.block_ms:p.error('Require 0 <= overlap-ms < block-ms')
    a.output_dir.mkdir(parents=True,exist_ok=False)
    meta=decode(a.media,a.output_dir/'timeline.wav',a.ffmpeg,a.ffprobe,a.audio_stream)
    duration=meta['pcm']['frames']*1000/RATE;blocks=[]
    for i,start in enumerate(range(0,math.ceil(duration),a.block_ms-a.overlap_ms),1):
        if duration-start<1:break
        blocks.append(slice_pcm(meta['pcm_path'],a.output_dir/f'a{i:02}.wav',start,min(duration,start+a.block_ms)))
    meta['blocks']=blocks
    with (a.output_dir/'manifest.json').open('x',encoding='utf8') as f:json.dump(meta,f,ensure_ascii=False,indent=2)
    print(json.dumps(dict(blocks=len(blocks),duration_seconds=duration/1000,manifest=str(a.output_dir/'manifest.json'))))

if __name__=='__main__':main()
