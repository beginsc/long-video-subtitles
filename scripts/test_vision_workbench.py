"""Synthetic, offline tests for GPT frame packets and write-back invariants."""
import argparse
import base64
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import vision_workbench as vision
from subtitle_workbench import row, save, load

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a4foAAAAASUVORK5CYII=')


class VisionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root/'frame.png').write_bytes(PNG)
        self.doc = {'schema_version': 1, 'meta': {}, 'segments': [row('s000001', 1000, 4000, 'This option.') ]}
        self.doc['segments'][0].update(draft='这个选项。', target='旧稿。', review_status='reviewed',
                                      review_note='Old review', editing_digest='old', proofreading_status='PASS')
        save(self.root/'source.json', self.doc)
        mapping = {'media': {'identity_note': 'synthetic image fixture'}, 'segments': [
            {'id': 's000001', 'frames': [{'path': 'frame.png', 'time_ms': 2000}]}]}
        self.packet = vision.make_packet(self.doc, mapping, self.root, vision.read_config(vision.CONFIG))
        save(self.root/'packet.json', self.packet)
        self.result = {'batch_id': self.packet['batch_id'], 'input_digest': self.packet['input_digest'],
                      'producer': {'runner': 'current-session-gpt', 'image_tool': 'synthetic-test'},
                      'segments': [{'id': 's000001', 'vision_status': 'analyzed', 'viewed_frame_ids': ['f000001'],
                          'observations': ['Synthetic observation only.'], 'onscreen_text': [],
                          'uncertainties': [], 'note': 'Offline synthetic test, not real GPT inference.'}]}

    def tearDown(self):
        self.temp.cleanup()

    def apply(self, result=None):
        save(self.root/'result.json', result or self.result)
        args = argparse.Namespace(source=self.root/'source.json', packet=self.root/'packet.json',
                                  result=self.root/'result.json', output=self.root/'output.json')
        vision.apply_vision(args)
        return load(args.output)

    def test_apply_preserves_source_and_invalidates_editorial(self):
        doc = self.apply()
        r = doc['segments'][0]
        self.assertEqual(r['source'], 'This option.')
        self.assertEqual(r['start_ms'], 1000)
        self.assertEqual(r['draft'], '这个选项。')
        self.assertEqual(r['target'], '')
        self.assertNotIn('editing_digest', r)
        self.assertNotIn('proofreading_status', r)
        self.assertEqual(r['vision_status'], 'analyzed')
        self.assertEqual(r['visual_cues'][0]['frames'][0]['sha256'], vision.file_hash(self.root/'frame.png'))

    def test_unread_or_changed_frame_is_rejected(self):
        result = copy.deepcopy(self.result)
        result['segments'][0]['viewed_frame_ids'] = []
        with self.assertRaises(ValueError):
            self.apply(result)
        self.assertFalse((self.root/'output.json').exists())

    def test_changed_frame_hash_is_rejected(self):
        (self.root/'frame.png').write_bytes(PNG+b'changed')
        with self.assertRaises(ValueError):
            self.apply()
        self.assertFalse((self.root/'output.json').exists())

    def test_wrong_source_or_duplicate_ids_are_rejected(self):
        result = copy.deepcopy(self.result)
        result['segments'] *= 2
        with self.assertRaises(ValueError):
            self.apply(result)

    def test_missing_binary_and_tiny_interval(self):
        with self.assertRaises(ValueError):
            vision.executable('a-nonexistent-ffmpeg-test-binary')
        self.assertEqual(vision.sample_times(1000, 1001, 2), [1000])

    def test_extract_offset_command_and_packet(self):
        doc = {'schema_version': 1, 'meta': {}, 'segments': [row('s000001', 6000, 9000, 'Hello.') ]}
        save(self.root/'offset.json', doc)
        (self.root/'media.bin').write_bytes(b'synthetic-media-placeholder')
        calls = []
        def fake_media(command):
            calls.append(command)
            if command[0] == 'ffprobe':
                return b'{"format":{"duration":"5.0"},"streams":[{"codec_type":"video"}]}'
            Path(command[-1]).write_bytes(PNG)
            return b''
        args = argparse.Namespace(source=self.root/'offset.json', media=self.root/'media.bin',
            output_dir=self.root/'extracted', config=vision.CONFIG, start_index=0,
            media_offset_ms=5000, ffmpeg='ffmpeg', ffprobe='ffprobe')
        with patch.object(vision, 'executable', side_effect=lambda x:x), patch.object(vision, 'run_media', side_effect=fake_media):
            vision.extract(args)
        packet = load(self.root/'extracted/vision-batch.json')
        self.assertEqual([f['time_ms'] for f in packet['segments'][0]['frames']], [7000,8000])
        self.assertEqual([c[c.index('-ss')+1] for c in calls[1:]], ['2.000','3.000'])


if __name__ == '__main__':
    unittest.main()
