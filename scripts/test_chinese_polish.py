import copy
import json
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import chinese_polish as p


class PolishGates(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root/'references').mkdir()
        (self.root/'references/prompt.md').write_text('Pending prompt',encoding='utf-8')
        self.config = {'enabled':True,'status':'approved','provider':'deepseek','model':p.MODEL,
                       'endpoint':p.ENDPOINT,'thinking':'disabled','max_tokens':12000,
                       'prompt':'references/prompt.md','api_key_env':'TEST_KEY','credential_name':'test'}
        self.source = self.root/'source.json'
        self.source.write_text(json.dumps({'meta':{'reference':'NEVER_SEND'},'segments':[
            {'id':str(i),'source':'Maybe we win','target':'我们可能会赢','start_ms':i*1000,
             'end_ms':(i+1)*1000,'source_checks':[]} for i in range(4)]}),encoding='utf-8')
        self.profile = self.root/'profile.json'
        self.profile.write_text(json.dumps(dict.fromkeys(p.PROFILE_KEYS,'test')|{
            'target_language':'zh-Hans','line_policy':'single'}),encoding='utf-8')
        self.root_patch = patch.object(p,'ROOT',self.root);self.root_patch.start()
        self.packet = p.prepare(self.source,'target',['1','2'],self.profile,self.config)
        self.approval = {'status':'approved','approved_by':'user','user_message':'TEST FIXTURE ONLY',
                         'approved_at':'2026-01-01','packet_digest':self.packet['packet_digest'],
                         'prompt_sha256':self.packet['prompt_sha256'],'model':p.MODEL,'endpoint':p.ENDPOINT}
    def tearDown(self):
        self.root_patch.stop();self.tmp.cleanup()
    def response(self):
        return {'items':[{'id':s['id'],'decision':'keep','proposed':s['current'],
                          'reason':'already natural','protected_meaning':['possibility retained']} for s in self.packet['items']]}
    def test_disabled_blocks_before_credentials_or_network(self):
        self.config['enabled']=False
        with patch.object(p,'read_key') as key,patch.object(p,'request_suggestions') as request:
            with self.assertRaises(ValueError):p.run(self.packet,self.approval,self.config,self.root/'out.json')
            key.assert_not_called();request.assert_not_called()
    def test_gate_rejects_changed_prompt_source_packet_config_and_authorization(self):
        p.check_gate(self.packet,self.approval,self.config)
        for field in ['packet_digest','prompt_sha256','model','endpoint','user_message']:
            auth=dict(self.approval);auth[field]=''
            with self.assertRaises(ValueError):p.check_gate(self.packet,auth,self.config)
        q=copy.deepcopy(self.packet);q['items'][0]['current']='tampered'
        with self.assertRaises(ValueError):p.check_gate(q,self.approval,self.config)
        config=dict(self.config,max_tokens=5000)
        with self.assertRaises(ValueError):p.check_gate(self.packet,self.approval,config)
        (self.root/'references/prompt.md').write_text('Changed prompt',encoding='utf-8')
        with self.assertRaises(ValueError):p.check_gate(self.packet,self.approval,self.config)
        (self.root/'references/prompt.md').write_text('Pending prompt',encoding='utf-8')
        self.source.write_text('{}',encoding='utf-8')
        with self.assertRaises(ValueError):p.check_gate(self.packet,self.approval,self.config)
    def test_prepare_excludes_metadata_and_separates_neighbors(self):
        self.assertNotIn('NEVER_SEND',json.dumps(self.packet))
        self.assertEqual([x['id'] for x in self.packet['readonly_context']],['0','3'])
        with self.assertRaises(ValueError):p.prepare(self.source,'target',['0','3'],self.profile,self.config)
    def test_output_rejects_missing_reordered_extra_ids_and_fields(self):
        for change in ('missing','reorder','extra','status'):
            r=self.response()
            if change=='missing':r['items'].pop()
            if change=='reorder':r['items'].reverse()
            if change=='extra':r['items'].append(dict(r['items'][0]))
            if change=='status':r['items'][0]['review_status']='reviewed'
            with self.assertRaises(ValueError):p.validate_response(r,self.packet)
    def test_unclear_preserves_text_and_pending_status(self):
        self.packet['items'][0]['source_checks']=[{'status':'open'}]
        r=self.response()
        with self.assertRaises(ValueError):p.validate_response(r,self.packet)
        r['items'][0]['decision']='unclear';p.validate_response(r,self.packet)
        r['items'][0]['proposed']='我们会赢'
        with self.assertRaises(ValueError):p.validate_response(r,self.packet)
    def test_rejects_newline_and_fake_keep(self):
        r=self.response();r['items'][0].update(decision='revise',proposed='我们\n可能会赢')
        with self.assertRaises(ValueError):p.validate_response(r,self.packet)
        r['items'][0].update(decision='keep',proposed='changed')
        with self.assertRaises(ValueError):p.validate_response(r,self.packet)
    def test_success_is_suggestions_only_source_unchanged_no_resubmit(self):
        before=self.source.read_bytes();out=self.root/'out.json'
        with patch.object(p,'read_key',return_value='not-a-real-key'),patch.object(p,'request_suggestions',return_value={'suggestions':self.response()}) as req:
            p.run(self.packet,self.approval,self.config,out)
            with self.assertRaises(FileExistsError):p.run(self.packet,self.approval,self.config,out)
            self.assertEqual(req.call_count,1)
        self.assertEqual(self.source.read_bytes(),before)
        self.assertEqual(p.load(out)['status'],'suggestions_only')
    def test_failure_retains_record_no_retry_or_secret_error(self):
        out=self.root/'failed.json'
        with patch.object(p,'read_key',return_value='not-a-real-key'),patch.object(p,'request_suggestions',side_effect=RuntimeError('SECRET')) as req:
            with self.assertRaises(ValueError):p.run(self.packet,self.approval,self.config,out)
            self.assertEqual(req.call_count,1)
        self.assertEqual(p.load(out)['status'],'failed_or_uncertain')
        self.assertNotIn('SECRET',out.read_text())
    def test_redirect_is_refused(self):
        with self.assertRaises(ValueError):p.NoRedirect().redirect_request(None,None,302,'',{},'https://elsewhere.test')

    def test_rejected_http_response_is_preserved_before_validation(self):
        out=self.root/'rejected.json'
        invalid=self.response();invalid['items'][0]['proposed']='unexpected keep edit'
        raw=json.dumps({'choices':[{'finish_reason':'stop','message':{'content':json.dumps(invalid)}}]}).encode()
        with patch.object(p,'read_key',return_value='SECRET_REQUEST_KEY'),patch.object(p.urllib.request,'build_opener') as opener:
            opener.return_value.open.return_value=io.BytesIO(raw)
            with self.assertRaises(ValueError):p.run(self.packet,self.approval,self.config,out)
            opener.return_value.open.assert_called_once()
        status=p.load(out);sidecar=Path(status['response_path'])
        self.assertEqual(sidecar.read_bytes(),raw)
        self.assertEqual(status['response_sha256'],p.file_hash(sidecar))
        self.assertEqual(status['status'],'failed_or_uncertain')
        self.assertNotIn('SECRET_REQUEST_KEY',out.read_text())
        report=p.diagnose(self.packet,sidecar)
        self.assertFalse(report['strict_response_valid'])
        self.assertTrue(report['items'][0]['protocol_errors'])

    def test_diagnostic_quarantines_context_and_duplicate_ids_without_picking(self):
        r=self.response();r['items'].append(dict(r['items'][0]));r['items'].append(dict(r['items'][0],id='0'))
        file=self.root/'response.json';p.save_new(file,r)
        before=self.source.read_bytes()
        result=p.diagnose(self.packet,file)
        self.assertFalse(result['strict_response_valid'])
        self.assertEqual(len(result['items'][0]['candidates']),2)
        self.assertEqual(len(result['quarantined_entries']),1)
        self.assertEqual(result['changes_to_subtitles'],0)
        self.assertEqual(before,self.source.read_bytes())

    def test_diagnostic_does_not_repair_bad_json_or_clear_open_checks(self):
        file=self.root/'bad.json';file.write_text('{broken',encoding='utf8')
        result=p.diagnose(self.packet,file)
        self.assertTrue(result['protocol_errors']);self.assertFalse(result['items'])
        packet=copy.deepcopy(self.packet);packet['items'][0]['source_checks']=[{'status':'open'}]
        packet['packet_digest']=p.digest({k:v for k,v in packet.items() if k!='packet_digest'})
        file.write_text(json.dumps(self.response()),encoding='utf8')
        result=p.diagnose(packet,file)
        self.assertIn('Open/unknown',result['items'][0]['protocol_errors'][0])

    def test_sidecar_collision_blocks_network(self):
        out=self.root/'occupied.json';(self.root/'occupied.json.response.json').write_text('{}')
        with patch.object(p,'read_key') as key,patch.object(p,'request_suggestions') as req:
            with self.assertRaises(FileExistsError):p.run(self.packet,self.approval,self.config,out)
            key.assert_not_called();req.assert_not_called()

    def test_archived_envelope_is_bound_to_packet_and_not_repaired(self):
        f=self.root/'archive.json'
        envelope=dict(content=json.dumps(self.response()),finish_reason='stop',packet_digest=self.packet['packet_digest'])
        p.save_new(f,envelope)
        self.assertTrue(p.diagnose(self.packet,f)['strict_response_valid'])
        envelope['packet_digest']='different'
        f.write_text(json.dumps(envelope),encoding='utf8')
        with self.assertRaises(ValueError):p.diagnose(self.packet,f)


if __name__=='__main__':unittest.main()
