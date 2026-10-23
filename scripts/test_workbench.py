"""Behavioral tests; synthetic text only, no media or cloud access."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).with_name('subtitle_workbench.py')


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def run_tool(self, *args, code=0):
        result = subprocess.run([sys.executable, str(TOOL), *map(str, args)],
                                cwd=self.root, capture_output=True, encoding='utf-8')
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return result

    def get(self, name):
        return json.loads((self.root / name).read_text(encoding='utf-8-sig'))

    def put(self, name, value):
        (self.root / name).write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')

    def fixture(self):
        (self.root / 'input.srt').write_bytes((
            '\ufeff1\r\n01:00:01,125 --> 01:00:04,250\r\nKeep the meaning.\r\n\r\n'
            '2\r\n01:00:05,000 --> 01:00:08,000\r\nDo not omit 15%.\r\n\r\n'
            '3\r\n01:00:09,000 --> 01:00:12,000\r\nFinish here.\r\n').encode('utf-8'))
        self.run_tool('import-srt', 'input.srt', 'source.json', '--duration-ms', '3613000')

    def process(self, source, directory, stage):
        self.run_tool('chunks', source, directory, '--max-cues', '1')
        current = source
        for i in range(1, 4):
            batch = self.get(f'{directory}/b{i:05}.json')
            sid = batch['segments'][0]['id']
            change = {'id': sid, 'draft': ['保留原意。', '不要漏掉15%。', '到此结束。'][i-1]}
            if stage == 'review':
                change = {'id': sid, 'target': batch['segments'][0]['draft'],
                          'review_status': 'reviewed', 'review_note': '已对照原文核查含义与数字。', 'issues': []}
            response = {'batch_id': batch['batch_id'], 'input_digest': batch['input_digest'], 'segments': [change]}
            self.put(f'{stage}{i}.json', response)
            output = f'{stage}.v{i}.json'
            self.run_tool('apply', current, f'{directory}/b{i:05}.json', f'{stage}{i}.json', output, '--stage', stage)
            current = output
        return current

    def test_end_to_end_and_gates(self):
        self.fixture()
        source = self.get('source.json')
        self.assertEqual(source['segments'][0]['start_ms'], 3601125)
        self.run_tool('export', 'source.json', 'blocked', '--baseline', 'source.json', code=1)
        self.assertFalse((self.root / 'blocked').exists())
        draft = self.process('source.json', 'translate', 'draft')
        final = self.process(draft, 'review', 'review')
        self.run_tool('qa', final, 'qa.json', '--baseline', 'source.json', '--bilingual')
        self.assertEqual(self.get('qa.json')['errors'], 0)
        for fmt in ('srt', 'vtt', 'ass'):
            self.run_tool('export', final, fmt, '--baseline', 'source.json', '--bilingual', '--format', fmt)
            output = (self.root / fmt / f'final.{fmt}').read_text(encoding='utf-8')
            self.assertIn('保留原意。', output)
            self.assertIn('Keep the meaning.', output)
        self.run_tool('export', final, 'srt', '--baseline', 'source.json', code=1)
        # A stale response cannot replace a newer checkpoint.
        self.run_tool('apply', draft, 'translate/b00001.json', 'draft1.json', 'stale.json', '--stage', 'draft', code=1)
        self.assertFalse((self.root / 'stale.json').exists())
        # Missing IDs cannot pass final delivery coverage.
        changed = self.get(final)
        changed['segments'].pop()
        self.put('missing.json', changed)
        self.run_tool('qa', 'missing.json', 'missing-qa.json', '--baseline', 'source.json', code=2)
        self.assertTrue(any(f['code'] == 'missing_source_id' for f in self.get('missing-qa.json')['findings']))

    def test_duplicate_response_is_rejected(self):
        self.fixture()
        self.run_tool('chunks', 'source.json', 'chunks')
        batch = self.get('chunks/b00001.json')
        response = {'batch_id': batch['batch_id'], 'input_digest': batch['input_digest'],
                    'segments': [{'id': 's000001', 'draft': '甲'}] * 3}
        self.put('bad.json', response)
        self.run_tool('apply', 'source.json', 'chunks/b00001.json', 'bad.json', 'bad-output.json', '--stage', 'draft', code=1)
        self.assertFalse((self.root / 'bad-output.json').exists())

    def test_asr_offsets_invalid_srt_and_timing(self):
        self.put('asr.json', {'segments': [{'start': 0.25, 'end': 1.5, 'text': 'Hello',
                 'words': [{'word': 'Hello', 'start': 0.25, 'end': 1.5}]}]})
        self.run_tool('import-asr', 'asr.json', 'source.json', '--offset-ms', '3600000')
        doc = self.get('source.json')
        self.assertEqual(doc['segments'][0]['start_ms'], 3600250)
        self.assertEqual(doc['segments'][0]['asr_words_offset_ms'], 3600000)
        self.run_tool('qa', 'source.json', 'report.json', '--duration-ms', '1000', code=2)
        codes = {x['code'] for x in self.get('report.json')['findings']}
        self.assertTrue({'outside_media', 'missing_translation', 'unreviewed'} <= codes)
        (self.root / 'bad.srt').write_text('1\n00:00:01,000 --> 00:00:02,000\nValid\n\n2\nwrong time\nLost', encoding='utf-8')
        self.run_tool('import-srt', 'bad.srt', 'bad-import.json', code=1)
        self.assertFalse((self.root / 'bad-import.json').exists())

    def test_long_video_coverage_and_unresolved_review(self):
        # 2 h 40 min / 2,400 cues exercises all batch boundaries and tail.
        self.put('long-asr.json', {'segments': [
            {'start': i * 4, 'end': i * 4 + 3, 'text': f'Sentence {i}.'}
            for i in range(2400)]})
        self.run_tool('import-asr', 'long-asr.json', 'source.json')
        self.run_tool('chunks', 'source.json', 'long-batches')
        packets = [self.get(str(p.relative_to(self.root))) for p in sorted((self.root / 'long-batches').glob('*.json'))]
        self.assertEqual(len(packets), 40)
        core_ids = [r['id'] for b in packets for r in b['segments']]
        expected = [r['id'] for r in self.get('source.json')['segments']]
        self.assertEqual(core_ids, expected)
        self.assertEqual(len(core_ids), len(set(core_ids)))
        self.assertEqual(packets[0]['context_before'], [])
        self.assertEqual(packets[-1]['context_after'], [])
        self.assertEqual(packets[-1]['segments'][-1]['end_ms'], 9599000)
        doc = self.get('source.json')
        for r in doc['segments']:
            r.update(draft='测试。', target='测试。', review_status='reviewed', review_note='合成测试数据。')
        doc['segments'][5]['issues'] = ['待听校专名']
        self.put('reviewed.json', doc)
        self.run_tool('export', 'reviewed.json', 'blocked', '--baseline', 'source.json', code=1)
        self.assertFalse((self.root / 'blocked').exists())

    def test_vidove_proofreader_editor_and_evidence_invalidation(self):
        self.fixture()
        doc = self.get('source.json')
        doc['meta']['workflow'] = 'vidove-inspired-v1'
        for r in doc['segments']:
            r.update(vision_status='skipped', visual_cues=[], source_notes=['Synthetic SRT-only input.'])
        self.put('context.json', doc)
        self.run_tool('chunks', 'context.json', 'draft-batches')
        batch = self.get('draft-batches/b00001.json')
        def respond(batch, changes, name):
            self.put(name, {'batch_id': batch['batch_id'], 'input_digest': batch['input_digest'], 'segments': changes})
        respond(batch, [{'id': r['id'], 'draft': text} for r, text in zip(doc['segments'],
                  ['保留原意。', '不要漏掉50%。', '到此结束。'])], 'draft-result.json')
        self.run_tool('apply', 'context.json', 'draft-batches/b00001.json', 'draft-result.json', 'draft.json', '--stage', 'draft')
        self.run_tool('chunks', 'draft.json', 'proof-batches')
        batch = self.get('proof-batches/b00001.json')
        changes = [{'id': r['id'], 'proofreading_status': 'PASS', 'suggestions': [],
                    'proofreading_note': 'No change needed.'} for r in doc['segments']]
        changes[1].update(proofreading_status='REVISE', suggestions=['Source says 15%, not 50%.'])
        respond(batch, changes, 'proof-result.json')
        # A proofreader cannot rewrite the translation in the suggestion stage.
        wrong = [dict(r) for r in changes]
        wrong[0]['target'] = '保留原意。'
        respond(batch, wrong, 'proof-wrong.json')
        self.run_tool('apply', 'draft.json', 'proof-batches/b00001.json', 'proof-wrong.json', 'blocked-proof.json', '--stage', 'proofread', code=1)
        self.run_tool('apply', 'draft.json', 'proof-batches/b00001.json', 'proof-result.json', 'proofread.json', '--stage', 'proofread')
        proof = self.get('proofread.json')
        self.assertEqual(proof['segments'][1]['draft'], '不要漏掉50%。')
        self.assertEqual(proof['segments'][1]['target'], '')
        self.run_tool('export', 'proofread.json', 'blocked-final', '--baseline', 'source.json', code=1)
        self.run_tool('chunks', 'proofread.json', 'edit-batches')
        batch = self.get('edit-batches/b00001.json')
        changes = [{'id': r['id'], 'target': text, 'review_status': 'reviewed',
                    'review_note': 'Checked the source; accepted the numeric correction where applicable.', 'issues': []}
                   for r, text in zip(doc['segments'], ['保留原意。', '不要漏掉15%。', '到此结束。'])]
        respond(batch, changes, 'edit-result.json')
        self.run_tool('apply', 'proofread.json', 'edit-batches/b00001.json', 'edit-result.json', 'old-review.json', '--stage', 'review', code=1)
        self.run_tool('apply', 'proofread.json', 'edit-batches/b00001.json', 'edit-result.json', 'edited.json', '--stage', 'edit')
        self.run_tool('qa', 'edited.json', 'edited-qa.json', '--baseline', 'source.json')
        self.run_tool('export', 'edited.json', 'final-export', '--baseline', 'source.json')
        # Changes in visual evidence must invalidate downstream approval.
        changed = self.get('edited.json')
        changed['segments'][0]['visual_cues'] = [{'cue_id': 'synthetic-new-evidence'}]
        self.put('changed-evidence.json', changed)
        self.run_tool('qa', 'changed-evidence.json', 'changed-qa.json', '--baseline', 'source.json', code=2)
        self.assertTrue(any(f['code'] == 'proofreading_missing_or_stale' for f in self.get('changed-qa.json')['findings']))
        # A manual edit to approved text must be reviewed again.
        changed = self.get('edited.json')
        changed['segments'][1]['target'] = '不要漏掉51%。'
        self.put('changed-target.json', changed)
        self.run_tool('qa', 'changed-target.json', 'changed-target-qa.json', '--baseline', 'source.json', code=2)
        self.assertTrue(any(f['code'] == 'editing_missing_or_stale' for f in self.get('changed-target-qa.json')['findings']))


if __name__ == '__main__':
    unittest.main()
