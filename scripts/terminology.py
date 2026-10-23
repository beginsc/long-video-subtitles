"""Domain-scoped terminology snapshots and literal diagnostics; no model/network calls."""
import copy
import hashlib
import json
import re
import unicodedata
from datetime import date
from urllib.parse import urlparse


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


def text(value):
    return isinstance(value, str) and bool(value.strip())


def normalized(value, sensitive=False):
    value = unicodedata.normalize('NFKC', value)
    return value if sensitive else value.casefold()


def cjk(char):
    return '\u3400' <= char <= '\u9fff' or '\u3040' <= char <= '\u30ff' or '\uac00' <= char <= '\ud7af'


def word(char):
    return bool(char) and (char.isalnum() or char == '_') and not cjk(char)


def spans(source, literal, sensitive=False):
    """Return original Python character offsets after case/width normalization.

    Latin word boundaries prevent SCV matching SCVs unless explicitly aliased.
    CJK does not use whitespace word boundaries. No stemming/fuzzy replacement.
    """
    folded, offsets = '', []
    for i, char in enumerate(source):
        part = normalized(char, sensitive)
        folded += part
        offsets.extend([i] * len(part))
    needle = normalized(literal, sensitive)
    if not needle:
        return []
    found, pos = [], 0
    while True:
        start = folded.find(needle, pos)
        if start < 0:
            break
        end, pos = start + len(needle), start + 1
        if start and word(needle[0]) and word(folded[start-1]):
            continue
        if end < len(folded) and word(needle[-1]) and word(folded[end]):
            continue
        found.append((offsets[start], offsets[end-1] + 1))
    return sorted(set(found))


def validate_library(library):
    if library.get('schema_version') != 1:
        raise ValueError('Glossary requires schema_version 1')
    for key in ('domain', 'source_language', 'target_locale', 'version'):
        if not text(library.get(key)):
            raise ValueError('Glossary requires ' + key)
    if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', library['domain']):
        raise ValueError('Domain must be a lowercase slug')
    if not isinstance(library.get('entries'), list):
        raise ValueError('Glossary entries must be a list')
    ids, senses = set(), set()
    for entry in library['entries']:
        for key in ('id', 'source', 'sense', 'category'):
            if not text(entry.get(key)):
                raise ValueError('Entry requires ' + key)
        if entry['id'] in ids or ':' in entry['id']:
            raise ValueError('Entry IDs must be unique and contain no colon')
        ids.add(entry['id'])
        identity = (normalized(entry['source']), entry['sense'])
        if identity in senses:
            raise ValueError('Duplicate source/sense: merge aliases or explicitly resolve the conflict')
        senses.add(identity)
        if entry.get('status') not in ('proposed', 'confirmed', 'rejected'):
            raise ValueError('Entry status must be proposed/confirmed/rejected')
        if not isinstance(entry.get('preferred_target', ''), str):
            raise ValueError('preferred_target must be text')
        if entry['status'] == 'confirmed' and not text(entry.get('preferred_target')):
            raise ValueError('Confirmed entry requires one preferred_target')
        for key in ('aliases', 'allowed_targets', 'forbidden_targets'):
            values = entry.get(key, [])
            if not isinstance(values, list) or any(not text(x) for x in values):
                raise ValueError(key + ' must contain nonempty literal strings')
            if len({normalized(x) for x in values}) != len(values):
                raise ValueError('Duplicate aliases/variants in ' + key)
        accepted = [entry.get('preferred_target', '')] + entry.get('allowed_targets', [])
        if {normalized(x) for x in accepted} & {normalized(x) for x in entry.get('forbidden_targets', [])}:
            raise ValueError('Accepted and forbidden target variants conflict')
        for key in ('case_sensitive', 'requires_context'):
            if type(entry.get(key, False)) is not bool:
                raise ValueError(key + ' must be boolean')
        if not isinstance(entry.get('sources', []), list):
            raise ValueError('sources must be a list')
        for evidence in entry.get('sources', []):
            if evidence.get('kind') not in ('official', 'community', 'project', 'user'):
                raise ValueError('Unknown evidence kind')
            for key in ('locator', 'checked_at', 'note'):
                if not text(evidence.get(key)):
                    raise ValueError('Evidence requires ' + key)
            date.fromisoformat(evidence['checked_at'])
            if evidence['kind'] in ('official', 'community'):
                parsed = urlparse(evidence['locator'])
                if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
                    raise ValueError('Web evidence requires a credential-free HTTPS page URL')
        if entry['status'] == 'confirmed' and (not entry.get('sources') or not text(entry.get('decision_note'))):
            raise ValueError('Confirmed entries require evidence and a decision_note; presence is not fact verification')
    return library


def merge_libraries(libraries):
    """Explicit scoped overlays only; never pick the last conflicting translation."""
    entries, provenance, languages = {}, [], set()
    for library in libraries:
        validate_library(library)
        languages.add((library['source_language'], library['target_locale']))
        provenance.append({'domain': library['domain'], 'version': library['version'], 'sha256': digest(library)})
        for entry in library['entries']:
            key = library['domain'] + ':' + entry['id']
            old = entries.get(key)
            if old is not None and old['entry'] != entry:
                if entry.get('replaces_digest') != digest(old['entry']) or not text(entry.get('override_reason')):
                    raise ValueError('Conflicting entry ' + key + '; explicit replaces_digest and override_reason required')
            elif old is None and entry.get('replaces_digest'):
                raise ValueError('Override has no matching base entry: ' + key)
            entries[key] = {'key': key, 'domain': library['domain'], 'entry': copy.deepcopy(entry)}
    if len(languages) != 1:
        raise ValueError('Select at least one library; all must have the same source language and target locale')
    source_language, target_locale = next(iter(languages))
    # Also catch different IDs accidentally introducing the same concept.
    seen = set()
    for item in entries.values():
        identity = (item['domain'], normalized(item['entry']['source']), item['entry']['sense'])
        if identity in seen:
            raise ValueError('Duplicate domain/source/sense across libraries')
        seen.add(identity)
    return {'source_language': source_language, 'target_locale': target_locale,
            'domains': sorted({x['domain'] for x in provenance}), 'libraries': provenance,
            'entries': list(entries.values())}


def source_digest(row):
    # Recheck terminology after source, timing, domain or audiovisual evidence changes.
    return digest({key: row.get(key) for key in ('id', 'source', 'start_ms', 'end_ms', 'term_domains',
                   'source_notes', 'visual_cues', 'vision_status', 'audio_cues', 'parent_ids')})


def matches(row, book):
    domains = row.get('term_domains', book['domains'])
    if not isinstance(domains, list) or not domains or set(domains) - set(book['domains']):
        raise ValueError('term_domains must select one or more loaded domains')
    hits = {}
    for item in book['entries']:
        e = item['entry']
        if item['domain'] not in domains or e['status'] == 'rejected':
            continue
        for literal in [e['source']] + e.get('aliases', []):
            for start, end in spans(row['source'], literal, e.get('case_sensitive', False)):
                hits.setdefault((start, end), set()).add(item['key'])
    # Keep every overlap and demand sense decisions; never hide a shorter named
    # entity merely because a longer, different term overlaps it.
    return [{'start': a, 'end': b, 'surface': row['source'][a:b], 'entry_keys': sorted(keys)}
            for (a, b), keys in sorted(hits.items(), key=lambda x: (x[0][0], -x[0][1]))
            if not any(c <= a and b <= d and (c, d) != (a, b) and other == keys
                       for (c, d), other in hits.items())]


def validate_discovery(row, record, hits):
    if record.get('id') != row['id'] or record.get('source_digest') != source_digest(row):
        raise ValueError('Discovery source/evidence is stale for ' + row['id'])
    if not text(record.get('note')) or not isinstance(record.get('candidates'), list):
        raise ValueError('Every cue requires a recorded terminology discovery conclusion')
    for candidate in record['candidates']:
        surface = candidate.get('surface')
        if not text(surface) or surface not in row['source'] or not text(candidate.get('note')):
            raise ValueError('Candidate must be an exact source substring with a reason')
        if candidate.get('decision') == 'not_term':
            continue
        if candidate.get('decision') not in ('known', 'add'):
            raise ValueError('Candidate decision must be known/add/not_term')
        if not any(candidate.get('entry_key') in h['entry_keys'] and surface == h['surface'] for h in hits):
            raise ValueError('Discovered term must map to a matching glossary entry; add a proposed entry first')


def context(row, book, discovery, decisions):
    hits = matches(row, book)
    validate_discovery(row, discovery, hits)
    keys = {k for h in hits for k in h['entry_keys']}
    if not isinstance(decisions, list):
        raise ValueError('Term decisions must be a list')
    identities = set()
    for decision in decisions:
        identity = (decision.get('start'), decision.get('end'))
        if identity in identities or not any((h['start'], h['end']) == identity for h in hits):
            raise ValueError('Decision must identify one unique matched occurrence')
        identities.add(identity)
        if not text(decision.get('note')) or decision.get('action') not in ('use', 'not_term'):
            raise ValueError('Decision requires use/not_term and a reason')
        hit = next(h for h in hits if (h['start'], h['end']) == identity)
        if decision['action'] == 'use' and decision.get('entry_key') not in hit['entry_keys']:
            raise ValueError('Selected sense does not match this occurrence')
    return {'source_digest': source_digest(row), 'discovery': copy.deepcopy(discovery),
            'matches': hits, 'entries': [x for x in book['entries'] if x['key'] in keys],
            'decisions': copy.deepcopy(decisions)}


def bind_document(doc, book, discovery_records, resolution_records):
    result = copy.deepcopy(doc)
    ids = [r['id'] for r in result['segments']]
    discovered = {r['id']: r for r in discovery_records}
    resolved = {r['id']: r for r in resolution_records}
    if len(discovered) != len(discovery_records) or set(discovered) != set(ids):
        raise ValueError('Discovery must cover every cue exactly once, including the final chapter')
    if len(resolved) != len(resolution_records) or set(resolved) - set(ids):
        raise ValueError('Duplicate or unknown resolution cue ID')
    meta = result.setdefault('meta', {})
    for key, expected in (('source_language', book['source_language']), ('target_locale', book['target_locale'])):
        if key in meta and meta[key] != expected:
            raise ValueError('Project and glossary disagree on ' + key)
        meta[key] = expected
    result['terminology'] = copy.deepcopy(book)
    meta.update(terminology_policy='domain-v1', terminology_digest=digest(book))
    for row in result['segments']:
        if row['id'] in resolved and resolved[row['id']].get('source_digest') != source_digest(row):
            raise ValueError('Stale occurrence resolutions for ' + row['id'])
        row['term_context'] = context(row, book, discovered[row['id']], resolved.get(row['id'], {}).get('decisions', []))
        # Conservatively invalidate every row, because the project-wide snapshot
        # can also change neighboring/context meanings. Keep drafts as working text.
        row.update(target='', review_status='pending', review_note='')
        for key in ('proofreading_status', 'suggestions', 'proofreading_note', 'proofreading_input_digest', 'editing_digest', 'uncertainty_resolution'):
            row.pop(key, None)
        row.pop('term_exceptions', None)
    return result


def validate_bound(doc):
    meta, book = doc.get('meta', {}), doc.get('terminology')
    active = meta.get('terminology_policy') or book is not None or any('term_context' in r for r in doc['segments'])
    if not active:
        return False
    if meta.get('terminology_policy') != 'domain-v1' or not isinstance(book, dict) or meta.get('terminology_digest') != digest(book):
        raise ValueError('Missing or modified terminology snapshot; bind again')
    if meta.get('source_language') != book['source_language'] or meta.get('target_locale') != book['target_locale']:
        raise ValueError('Terminology language/locale mismatch')
    for row in doc['segments']:
        old = row.get('term_context', {})
        fresh = context(row, book, old.get('discovery', {}), old.get('decisions', []))
        if fresh != old:
            raise ValueError('Stale terminology bindings for ' + row['id'] + '; prepare/discover/bind again')
    return True


def findings(doc, field='target'):
    try:
        if not validate_bound(doc):
            return []
    except (ValueError, KeyError, TypeError) as exc:
        return [{'level': 'error', 'id': '*', 'code': 'terminology_stale', 'message': str(exc)}]
    output = []
    for row in doc['segments']:
        ctx = row['term_context']
        lookup = {x['key']: x['entry'] for x in ctx['entries']}
        selected = []
        def flag(code, message):
            output.append({'level': 'error', 'id': row['id'], 'code': code, 'message': message})
        for hit in ctx['matches']:
            decision = next((d for d in ctx['decisions'] if (d['start'], d['end']) == (hit['start'], hit['end'])), None)
            overlaps = any(h is not hit and h['start'] < hit['end'] and hit['start'] < h['end'] for h in ctx['matches'])
            if decision and decision['action'] == 'not_term':
                continue
            if decision:
                key = decision['entry_key']
            elif len(hit['entry_keys']) != 1 or overlaps or lookup[hit['entry_keys'][0]].get('requires_context', False):
                flag('term_ambiguous', f"{hit['surface']}: resolve this occurrence using context, not automatic replacement")
                continue
            else:
                key = hit['entry_keys'][0]
            selected.append((key, hit))
        for key, hit in selected:
            entry = lookup[key]
            if entry['status'] != 'confirmed':
                flag('term_unconfirmed', f'{key}: research or document a project decision before final delivery')
                continue
            target = row.get(field, '')
            accepted = [entry['preferred_target']] + entry.get('allowed_targets', [])
            if not any(spans(target, value) for value in accepted):
                exceptions = row.get('term_exceptions', []) if field == 'target' else []
                valid_exception = any(e.get('entry_key') == key and text(e.get('reason')) and
                                      e.get('input_digest') == digest({'source_digest': source_digest(row),
                                          'target': target, 'term_context': ctx}) for e in exceptions)
                if not valid_exception:
                    flag('term_missing_target', f"{hit['surface']} ({key}): expected {entry['preferred_target']}")
            for forbidden in entry.get('forbidden_targets', []):
                if spans(target, forbidden):
                    flag('term_forbidden_target', f'{key}: found forbidden variant {forbidden}')
    return output
