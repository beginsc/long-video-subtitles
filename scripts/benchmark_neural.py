"""Prepare frozen AS/t pairs, score cached neural models, and join honest comparisons.

No ASR, LLM, SSH, installation or credential handling. Run score inside a prepared
evaluation environment. Reference content is read only after a scored freeze.
"""
import argparse
import copy
import csv
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import sys


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, data):
    with Path(path).open('x', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2, allow_nan=False)


def verify_run(run):
    run = Path(run)
    frozen = read(run / 'freeze.json')
    base = read(run / 'scores/metrics.json')
    # Validate identity before allowing reference extraction or result import.
    if sha(run / 'hypothesis.srt') != frozen['hypothesis_sha256'] or base['hypothesis_sha256'] != frozen['hypothesis_sha256']:
        raise ValueError('Frozen hypothesis differs from scored candidate')
    if base['data_sha256'] != frozen['data_sha256']:
        raise ValueError('Scored reference dataset differs from freeze')
    if sha(run / 'scores/reference.zh.srt') != base['reference_sha256']:
        raise ValueError('Scored reference file changed')
    return frozen, base


def prepare(run, output, deps=None):
    from dovebench import parse_srt
    run = Path(run)
    frozen, base = verify_run(run)
    if deps:
        sys.path.insert(0, str(Path(deps).resolve()))
    from suber.file_readers import read_input_file
    from suber.hyp_to_ref_alignment import levenshtein_align_hypothesis_to_reference, time_align_hypothesis_to_reference
    from suber.utilities import segment_to_string
    hyp = read_input_file(str(run/'hypothesis.srt'), file_format='SRT')
    ref = read_input_file(str(run/'scores/reference.zh.srt'), file_format='SRT')
    if len(hyp) != base['hypothesis_segments'] or len(ref) != base['reference_segments']:
        raise ValueError('Cue counts differ from base score')
    payload = dict(schema_version=1, video_id=frozen['video']['video_id'],
                   hypothesis_sha256=frozen['hypothesis_sha256'], reference_sha256=base['reference_sha256'],
                   groups={}, candidate_cues=parse_srt(run/'hypothesis.srt'),
                   alignment='Official SubER AS/t resegmentation; fragmented rows are not standalone translation verdicts')
    for mode, fn in [('AS', levenshtein_align_hypothesis_to_reference), ('t', time_align_hypothesis_to_reference)]:
        aligned = fn(hypothesis=copy.deepcopy(hyp), reference=copy.deepcopy(ref), language='zh')
        if len(aligned) != len(ref):
            raise ValueError('Alignment coverage changed')
        payload['groups'][mode] = [dict(idx=i, start=r.start_time, end=r.end_time,
            reference=segment_to_string(r), hypothesis=segment_to_string(h)) for i,(h,r) in enumerate(zip(aligned,ref))]
    payload['coverage'] = dict(hypothesis_cues=len(hyp), reference_cues=len(ref),
        original_nonspace_chars=sum(len(''.join(segment_to_string(x).split())) for x in hyp),
        aligned_nonspace_chars={k:sum(len(''.join(x['hypothesis'].split())) for x in rows) for k,rows in payload['groups'].items()},
        empty_hypotheses={k:sum(not x['hypothesis'].strip() for x in rows) for k,rows in payload['groups'].items()})
    if payload['coverage']['aligned_nonspace_chars']['AS'] != payload['coverage']['original_nonspace_chars']:
        raise ValueError('AS alignment lost candidate text')
    validate_pairs(payload)
    write(output, payload)
    print(json.dumps(dict(pairs_sha256=sha(output), coverage=payload['coverage'])))


def validate_pairs(data):
    if set(data['groups']) != {'AS','t'}:
        raise ValueError('Require both AS and t alignments')
    n = data['coverage']['reference_cues']
    if type(n) is not int or n <= 0:
        raise ValueError('Invalid reference count')
    for mode, rows in data['groups'].items():
        if len(rows) != n or [r['idx'] for r in rows] != list(range(n)):
            raise ValueError('Do not drop, reorder or duplicate reference rows')
        for r in rows:
            if not isinstance(r['hypothesis'], str) or not isinstance(r['reference'], str) or not r['reference'].strip():
                raise ValueError('Empty hypotheses must remain strings; references must be nonempty')
            if any(type(r[k]) not in (int,float) or not math.isfinite(r[k]) for k in ('start','end')) or not 0 <= r['start'] < r['end']:
                raise ValueError('Invalid reference timing')
    if any((a['reference'],a['start'],a['end']) != (t['reference'],t['start'],t['end']) for a,t in zip(data['groups']['AS'],data['groups']['t'])):
        raise ValueError('AS and t reference rows differ')


def check_lengths(rows, tokenizer, limit, paired=True):
    lengths = ([len(tokenizer.tokenize(r['reference'])) + len(tokenizer.tokenize(r['hypothesis'])) + 3 for r in rows]
               if paired else [len(tokenizer.encode(r[k],add_special_tokens=True)) for r in rows for k in ('reference','hypothesis')])
    if max(lengths) > limit:
        raise ValueError(f'Model input would truncate: {max(lengths)} > {limit}; no rows scored')
    return max(lengths)


def score(args):
    if Path(args.output).exists():
        raise ValueError('Output exists; use a new run file')
    if sha(args.pairs) != args.expected_pairs_sha256:
        raise ValueError('Transferred pairs hash mismatch')
    data = read(args.pairs); validate_pairs(data)
    checkpoint = Path(args.bleurt_checkpoint).resolve()
    if read(checkpoint/'bleurt_config.json').get('name') != 'BLEURT-20':
        raise ValueError('This profile requires the full BLEURT-20 checkpoint')
    os.environ.setdefault('HF_HUB_OFFLINE', '1')
    os.environ.setdefault('TRANSFORMERS_OFFLINE', '1')
    os.environ.setdefault('TF_CPP_MIN_LOG_LEVEL', '2')
    import tensorflow as tf
    tf.config.set_visible_devices([], 'GPU')
    tf.config.threading.set_intra_op_parallelism_threads(4)
    tf.config.threading.set_inter_op_parallelism_threads(2)
    from bleurt import score as bleurt_score
    bleurt = bleurt_score.LengthBatchingBleurtScorer(str(checkpoint))
    maxima = {k:check_lengths(v, bleurt.tokenizer, bleurt.max_seq_length) for k,v in data['groups'].items()}
    # Compare both public scoring paths on one identical and one unrelated pair.
    probe = dict(references=['虫族开始进攻']*2, candidates=['虫族开始进攻','今天的天气很好'],batch_size=2)
    standard = bleurt_score.BleurtScorer.score(bleurt, **probe)
    dynamic = bleurt.score(**probe)
    diff = max(abs(float(x)-float(y)) for x,y in zip(standard,dynamic))
    if not math.isfinite(diff) or diff > 1e-4 or not dynamic[0] > dynamic[1]:
        raise ValueError('BLEURT dynamic padding validation failed')
    result = dict(status='completed', video_id=data['video_id'], hypothesis_sha256=data['hypothesis_sha256'],
        reference_sha256=data['reference_sha256'], pairs_sha256=sha(args.pairs), coverage=data['coverage'], groups={},
        versions={p:importlib.metadata.version(p) for p in ('bleurt','bert-score','tensorflow-cpu','transformers','torch')},
        BLEURT_scorer='official LengthBatchingBleurtScorer', dynamic_padding_max_difference=diff,
        BLEURT_max_pair_tokens=maxima, BLEURT_config_sha256=sha(checkpoint/'bleurt_config.json'),
        note='Unweighted mean including empty hypotheses; metric agreement is not accuracy percentage')
    for mode, rows in data['groups'].items():
        values = bleurt.score(references=[r['reference'] for r in rows], candidates=[r['hypothesis'] for r in rows],batch_size=8)
        if len(values)!=len(rows) or not all(math.isfinite(float(v)) for v in values):
            raise ValueError('Invalid BLEURT output')
        result['groups'][mode] = dict(BLEURT_mean=float(sum(values)/len(values)),
                                     pairs=[dict(r,BLEURT=float(v)) for r,v in zip(rows,values)])
        print(mode+' BLEURT completed',flush=True)
    bleurt.close(); del bleurt
    from bert_score import BERTScorer
    bert = BERTScorer(model_type='bert-base-chinese', num_layers=8, lang='zh', device=args.device,
                      idf=False, rescale_with_baseline=False, use_fast_tokenizer=False)
    result['BERTScore_hash'] = bert.hash
    result['BERT_max_tokens'] = {k:check_lengths(v,bert._tokenizer,512,paired=False) for k,v in data['groups'].items()}
    for mode, rows in data['groups'].items():
        p,r,f = bert.score([x['hypothesis'] for x in rows],[x['reference'] for x in rows],batch_size=32)
        lists = [x.tolist() for x in (p,r,f)]
        if any(len(x)!=len(rows) or not all(math.isfinite(v) for v in x) for x in lists):
            raise ValueError('Invalid BERTScore output')
        result['groups'][mode].update(BERTScore_P=sum(lists[0])/len(rows),BERTScore_R=sum(lists[1])/len(rows),BERTScore_F1=sum(lists[2])/len(rows))
        for row,pv,rv,fv in zip(result['groups'][mode]['pairs'],*lists):
            row.update(BERT_P=pv,BERT_R=rv,BERT_F1=fv)
    write(args.output,result)


def compare(run, pairs_path, metrics_path, output):
    frozen,base=verify_run(run); data=read(pairs_path); result=read(metrics_path); validate_pairs(data)
    if result.get('status')!='completed' or result.get('pairs_sha256')!=sha(pairs_path):
        raise ValueError('Result must be completed and bound to these pairs')
    for k,expected in [('hypothesis_sha256',frozen['hypothesis_sha256']),('reference_sha256',base['reference_sha256'])]:
        if data[k]!=expected or result[k]!=expected:
            raise ValueError('Result identity differs from scored run')
    from dovebench import parse_srt
    cues=parse_srt(Path(run)/'hypothesis.srt'); rows=[]
    for mode in ('AS','t'):
        scored=result['groups'][mode]['pairs']; original=data['groups'][mode]
        if len(scored)!=len(original) or any(any(x[k]!=y[k] for k in ('idx','reference','hypothesis','start','end')) for x,y in zip(scored,original)):
            raise ValueError('Scored rows differ from prepared pairs')
    for a,t in zip(result['groups']['AS']['pairs'],result['groups']['t']['pairs']):
        linked=[(i+1,c) for i,c in enumerate(cues) if c['start_ms']<a['end']*1000 and c['end_ms']>a['start']*1000]
        rows.append(dict(reference_row=a['idx']+1,start=a['start'],end=a['end'],
            candidate_cue_numbers=' | '.join(str(i) for i,c in linked),
            actual_complete_cues=' | '.join(c['text'] for i,c in linked),reference=a['reference'],
            AS_text=a['hypothesis'],t_text=t['hypothesis'],AS_BLEURT=a['BLEURT'],t_BLEURT=t['BLEURT'],
            AS_BERT_F1=a['BERT_F1'],t_BERT_F1=t['BERT_F1'],
            interpretation='Time-overlap linkage may repeat cues. AS/t texts may cut words; low scores require contextual review.'))
    with Path(output).open('x',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=rows[0]);writer.writeheader()
        for row in rows:
            writer.writerow({k:("'"+v if isinstance(v,str) and v.lstrip().startswith(('=','+','-','@')) else v) for k,v in row.items()})


def main():
    p=argparse.ArgumentParser(description=__doc__); sub=p.add_subparsers(dest='command',required=True)
    q=sub.add_parser('prepare');q.add_argument('--run',required=True);q.add_argument('--output',required=True);q.add_argument('--deps')
    q=sub.add_parser('score');q.add_argument('--pairs',required=True);q.add_argument('--expected-pairs-sha256',required=True);q.add_argument('--bleurt-checkpoint',required=True);q.add_argument('--output',required=True);q.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    q=sub.add_parser('compare');q.add_argument('--run',required=True);q.add_argument('--pairs',required=True);q.add_argument('--metrics',required=True);q.add_argument('--output',required=True)
    args=p.parse_args()
    try:
        if args.command=='prepare':prepare(args.run,args.output,args.deps)
        elif args.command=='score':score(args)
        else:compare(args.run,args.pairs,args.metrics,args.output)
    except (ValueError,KeyError,TypeError,OSError,ImportError) as e:
        print('ERROR: '+str(e),file=sys.stderr);return 1
    return 0


if __name__=='__main__':
    sys.exit(main())
