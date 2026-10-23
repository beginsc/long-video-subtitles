"""Prepare full-video term discovery, bind researched libraries, and export audits.

GPT discovers entities and performs web research in the session. This local
script validates evidence records and checks literal consistency, not facts.
"""
import argparse
import csv
import io
import sys
from pathlib import Path

import terminology as terms
from subtitle_workbench import load, save, save_text, structural

ASSETS = Path(__file__).resolve().parent.parent / 'assets' / 'glossaries'


def book(args):
    return terms.merge_libraries([load(path) for path in args.glossary])


def init(args):
    if args.domain in ('sc2', 'cs2') and args.source_language == 'en' and args.target_locale == 'zh-CN':
        library = load(ASSETS / args.domain / 'en-zh-CN.json')
    else:
        library = {'schema_version': 1, 'domain': args.domain, 'source_language': args.source_language,
                   'target_locale': args.target_locale, 'version': '1', 'entries': []}
    terms.validate_library(library)
    save(args.output, library)
    print('Created project/library copy; expand it from actual video evidence')


def prepare(args):
    doc, library = load(args.input), book(args)
    rows = structural(doc)
    if args.max_cues < 1 or args.max_chars < 1:
        raise ValueError('Batch limits must be positive')
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=False)
    groups, group, size = [], [], 0
    for row in rows:
        if group and (len(group) >= args.max_cues or size + len(row['source']) > args.max_chars):
            groups.append(group)
            group, size = [], 0
        group.append(row)
        size += len(row['source'])
    if group:
        groups.append(group)
    offset = 0
    for index, group in enumerate(groups, 1):
        hits = {r['id']: terms.matches(r, library) for r in group}
        keys = {key for values in hits.values() for h in values for key in h['entry_keys']}
        packet = {'batch_id': f't{index:05d}', 'glossary_digest': terms.digest(library),
                  'domains': library['domains'], 'source_language': library['source_language'],
                  'target_locale': library['target_locale'],
                  'instructions': 'Read ALL core cues for new names/terms, including one-off and lower-case terms. Known matches are not exhaustive. Research candidates using session web tools.',
                  'segments': [{'id': r['id'], 'source_digest': terms.source_digest(r), 'source': r['source'],
                                'start_ms': r['start_ms'], 'end_ms': r['end_ms'], 'known_matches': hits[r['id']],
                                'visual_cues': r.get('visual_cues', []), 'source_notes': r.get('source_notes', [])} for r in group],
                  'context_before': rows[max(0, offset-3):offset],
                  'context_after': rows[offset+len(group):offset+len(group)+3],
                  'known_entries': [x for x in library['entries'] if x['key'] in keys]}
        save(out / f't{index:05d}.json', packet)
        offset += len(group)
    print(f'Prepared {len(groups)} discovery batches covering {len(rows)} cues; no terms inferred or web pages checked by this script')


def bind(args):
    doc = load(args.input)
    structural(doc)
    discovered = load(args.discovery)['segments']
    resolutions = load(args.resolutions)['segments'] if args.resolutions else []
    output = terms.bind_document(doc, book(args), discovered, resolutions)
    terms.validate_bound(output)
    save(args.output, output)
    print('Bound terminology snapshot and per-cue evidence; drafts retained, all final approvals reset')


def audit(args):
    doc = load(args.input)
    structural(doc)
    if not terms.validate_bound(doc):
        raise ValueError('No terminology snapshot; bind it before auditing')
    result = terms.findings(doc, args.field)
    save(args.output, {'field': args.field, 'errors': len(result), 'findings': result,
                       'note': 'Literal checks only; GPT must review senses, omissions, paraphrases, and cross-cue mentions.'})
    print(f'{len(result)} terminology findings')
    return 2 if result else 0


def csv_export(doc):
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow(['domain', 'id', 'source', 'sense', 'preferred_target', 'aliases', 'status',
                     'occurrence_cues', 'first_start_ms', 'sources', 'decision_note'])
    for item in doc['terminology']['entries']:
        entry = item['entry']
        rows = [r for r in doc['segments'] if any(item['key'] in h['entry_keys'] for h in r['term_context']['matches'])]
        values = [item['domain'], entry['id'], entry['source'], entry['sense'], entry.get('preferred_target', ''),
                  ' | '.join(entry.get('aliases', [])), entry['status'], ' | '.join(r['id'] for r in rows),
                  rows[0]['start_ms'] if rows else '', ' | '.join(s['locator'] for s in entry.get('sources', [])),
                  entry.get('decision_note', '')]
        writer.writerow(["'" + v if isinstance(v, str) and v.lstrip().startswith(('=', '+', '-', '@')) else v for v in values])
    return '\ufeff' + stream.getvalue()


def export(args):
    doc = load(args.input)
    structural(doc)
    if not terms.validate_bound(doc):
        raise ValueError('No bound terminology')
    save_text(args.output, csv_export(doc))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest='command', required=True)
    p = subs.add_parser('init')
    p.add_argument('output')
    p.add_argument('--domain', required=True)
    p.add_argument('--source-language', default='en')
    p.add_argument('--target-locale', default='zh-CN')
    p.set_defaults(func=init)
    for name, func in (('prepare', prepare), ('bind', bind)):
        p = subs.add_parser(name)
        p.add_argument('input')
        p.add_argument('output_dir' if name == 'prepare' else 'output')
        p.add_argument('--glossary', action='append', required=True, help='Repeat: domain library first, explicit project overlay last')
        if name == 'prepare':
            p.add_argument('--max-cues', type=int, default=60)
            p.add_argument('--max-chars', type=int, default=6000)
        else:
            p.add_argument('--discovery', required=True)
            p.add_argument('--resolutions')
        p.set_defaults(func=func)
    for name, func in (('audit', audit), ('export', export)):
        p = subs.add_parser(name)
        p.add_argument('input')
        p.add_argument('output')
        if name == 'audit':
            p.add_argument('--field', choices=('draft', 'target'), default='target')
        p.set_defaults(func=func)
    args = parser.parse_args()
    try:
        return args.func(args) or 0
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print('ERROR: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
