import array,json,os,subprocess,tempfile,unittest,wave
from pathlib import Path
from audio_timeline import slice_pcm,pcm_info,decode

class PcmSlices(unittest.TestCase):
    def test_rounded_tail_is_bounded_recorded_and_sample_exact(self):
        with tempfile.TemporaryDirectory()as tmp:
            d=Path(tmp);source=d/'source.wav';samples=array.array('h',range(15992))
            with wave.open(str(source),'wb')as w:w.setparams((1,2,16000,0,'NONE','not compressed'));w.writeframes(samples.tobytes())
            with self.assertRaises(ValueError):slice_pcm(source,d/'strict.wav',990,1000)
            cut=slice_pcm(source,d/'rounded.wav',990,1000,allow_rounded_end=True)
            self.assertEqual(cut['requested_interval_ms'],[990,1000]);self.assertEqual(cut['actual_interval_ms'],[990,999.5]);self.assertEqual(cut['end_rounding_clamped_samples'],8)
            with wave.open(str(d/'rounded.wav'),'rb')as w:self.assertEqual(w.readframes(w.getnframes()),samples[15840:].tobytes())
            for start,end in [(990,1001),(1000,1000.01),(-1,1000)]:
                with self.assertRaises(ValueError):slice_pcm(source,d/'bad.wav',start,end,allow_rounded_end=True)

    def test_zero_overlap_tail_and_exact_samples(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);source=d/'source.wav';samples=array.array('h',range(16000))
            with wave.open(str(source),'wb') as w:w.setparams((1,2,16000,0,'NONE','not compressed'));w.writeframes(samples.tobytes())
            for i,(start,end) in enumerate([(0,500),(250,750),(750,1000)]):
                out=d/f'{i}.wav';r=slice_pcm(source,out,start,end)
                with wave.open(str(out),'rb') as w:actual=w.readframes(w.getnframes())
                self.assertEqual(actual,samples[start*16:end*16].tobytes());self.assertEqual(r['offset_ms'],start)
            with self.assertRaises(FileExistsError):slice_pcm(source,d/'0.wav',0,500)
            for start,end in [(-1,2),(2,1),(0,1001),(float('nan'),1),(True,2)]:
                with self.assertRaises(ValueError):slice_pcm(source,d/'bad.wav',start,end)

@unittest.skipUnless(os.environ.get('FFMPEG_TEST_BIN') and os.environ.get('FFPROBE_TEST_BIN'),'Explicit FFmpeg integration binaries required')
class MediaTimeline(unittest.TestCase):
    def test_delayed_video_does_not_discard_first_audio(self):
        ff=os.environ['FFMPEG_TEST_BIN'];fp=os.environ['FFPROBE_TEST_BIN']
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);src=d/'fixture.mkv'
            # Nonperiodic known PCM: first audio starts0, video starts2seconds.
            audio=d/'in.wav';samples=array.array('h',((i*7919)%30001-15000 for i in range(5*16000)))
            with wave.open(str(audio),'wb') as w:w.setparams((1,2,16000,0,'NONE','not compressed'));w.writeframes(samples.tobytes())
            subprocess.run([ff,'-v','error','-n','-i',str(audio),'-itsoffset','2','-f','lavfi','-i','color=c=black:s=64x64:r=10:d=3','-map','0:a','-map','1:v','-c:a','pcm_s16le','-c:v','ffv1',str(src)],check=True)
            meta=decode(src,d/'canonical.wav',ff,fp)
            with wave.open(meta['pcm_path'],'rb') as w:got=w.readframes(16000)
            self.assertEqual(got,samples[:16000].tobytes());self.assertEqual(meta['pcm']['frames'],80000)
            cut=slice_pcm(meta['pcm_path'],d/'first.wav',0,1000)
            self.assertEqual(cut['pcm']['frames'],16000)
            # Exercise the review packet path, which previously sought the MP4
            # independently and could lose the beginning of an early clip.
            import subtitle_workbench as sw
            from audio_review import prepare,verify
            from types import SimpleNamespace
            row=sw.row('s1',100,200,'Check the first spoken word.')
            row['source_checks']=[dict(id='q1',kind='asr',status='open',reason='Early speech')]
            doc=dict(schema_version=1,meta={'quality_contract':'evidence-v2'},segments=[row])
            queue=dict(input_digest=sw.digest(doc),items=[dict(id='s1',start_ms=100,end_ms=200,source=row['source'],reasons=['Early speech'])])
            sw.save(d/'source.json',doc);sw.save(d/'queue.json',queue)
            prepare(SimpleNamespace(source=d/'source.json',queue=d/'queue.json',media=src,output_dir=d/'review',ffmpeg=ff,ffprobe=fp,audio_stream=0,media_offset_ms=0,before_ms=100,after_ms=0))
            with wave.open(str(d/'review/clip-0001.wav'),'rb') as w:got=w.readframes(w.getnframes())
            self.assertEqual(got,samples[:3200].tobytes());verify(SimpleNamespace(packet=d/'review/packet.json'))

    def test_delayed_audio_keeps_initial_silence(self):
        ff=os.environ['FFMPEG_TEST_BIN'];fp=os.environ['FFPROBE_TEST_BIN']
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);src=d/'late-audio.mkv'
            subprocess.run([ff,'-v','error','-n','-f','lavfi','-i','color=c=black:s=64x64:r=10:d=4','-itsoffset','1','-f','lavfi','-i','sine=frequency=440:sample_rate=16000:duration=2','-map','0:v','-map','1:a','-c:v','ffv1','-c:a','pcm_s16le',str(src)],check=True)
            meta=decode(src,d/'canonical.wav',ff,fp)
            with wave.open(meta['pcm_path'],'rb') as w:
                before=w.readframes(16000);during=w.readframes(16000);w.setpos(3*16000);tail=w.readframes(16000)
            self.assertFalse(any(before));self.assertTrue(any(during));self.assertFalse(any(tail));self.assertEqual(meta['pcm']['frames'],64000)

if __name__=='__main__':unittest.main()
