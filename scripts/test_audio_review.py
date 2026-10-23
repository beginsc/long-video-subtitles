import copy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import audio_review as a
import subtitle_workbench as w


class AudioReviewTest(unittest.TestCase):
    def setUp(self):
        r=w.row('unsafe/../id',11000,12000,"It can't move.")
        r['source_checks']=[dict(id='negation',kind='asr',status='open',reason='Check can versus cannot')]
        self.doc=dict(schema_version=1,meta={'quality_contract':'evidence-v2'},segments=[r])
        self.queue=dict(input_digest=w.digest(self.doc),items=[dict(id=r['id'],start_ms=11000,end_ms=12000,source=r['source'],reasons=['Check negation'])])

    def test_subclip_offset_and_context_are_clamped(self):
        original=copy.deepcopy(self.doc)
        p=a.clip_plan(self.doc,self.queue,4000,10000,2500,2500)[0]
        self.assertEqual((p['clip_start_ms'],p['clip_end_ms'],p['local_start_ms']),(10000,14000,0))
        self.assertEqual(p['focus_in_clip_ms'],[1000,2000]);self.assertEqual(self.doc,original)
        self.assertEqual(self.doc['segments'][0]['source_checks'][0]['status'],'open')

    def test_rejects_stale_queue_and_outside_media(self):
        with self.assertRaisesRegex(ValueError,'outside media'):a.clip_plan(self.doc,self.queue,1000)
        self.doc['segments'][0]['source']="It can move."
        with self.assertRaisesRegex(ValueError,'stale'):a.clip_plan(self.doc,self.queue,20000)

    def test_rejects_tampered_queue_and_duplicate_ids(self):
        self.queue['items'][0]['start_ms']=0
        with self.assertRaisesRegex(ValueError,'content'):a.clip_plan(self.doc,self.queue,20000)
        self.queue['items'][0]['start_ms']=11000;self.queue['items']*=2
        with self.assertRaisesRegex(ValueError,'Duplicate'):a.clip_plan(self.doc,self.queue,20000)

    def test_packet_verification_rejects_changed_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);clip=root/'clip-0001.wav';clip.write_bytes(b'synthetic audio fixture')
            packet={'items':[dict(id='s1',clip=clip.name,clip_sha256=a.file_hash(clip))]}
            packet['packet_digest']=w.digest(packet);w.save(root/'packet.json',packet)
            a.verify(SimpleNamespace(packet=root/'packet.json'));clip.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'audio changed'):a.verify(SimpleNamespace(packet=root/'packet.json'))


if __name__=='__main__':unittest.main()
