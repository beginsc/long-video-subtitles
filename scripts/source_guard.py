#!/usr/bin/env python3
"""Check declared source uncertainty and review bindings, not semantic truth."""
import argparse
import hashlib
import json
import re
from pathlib import Path

VERSION = "1"


def normalized(text):
    return "".join(str(text).split())


def scoped_units(doc, issue):
    items = doc.get("units", doc.get("segments", []))
    by_id = {u["id"]: u for u in items}
    if len(by_id) != len(items):
        raise ValueError("duplicate semantic unit IDs")
    scope = issue.get("unit_ids", issue.get("cue_ids"))
    if not isinstance(scope, list) or not scope or len(set(scope)) != len(scope):
        raise ValueError("issue requires nonempty unique unit_ids")
    if any(sid not in by_id for sid in scope):
        raise ValueError("unknown issue semantic unit")
    return [by_id[sid] for sid in scope]


def content_fingerprint(doc, issue):
    units = scoped_units(doc, issue)
    payload = {
        "version": VERSION, "issue_id": issue.get("id"),
        "constraint": issue.get("source_constraint"), "status": issue.get("status"),
        "disposition": {k:v for k,v in issue.get("disposition", {}).items() if k != "reviewed_sha256"},
        "units": [{"id": u["id"], "source": normalized(u.get("source", "")),
                   "target": normalized(u.get("target", "")),
                   "source_word_ranges": u.get("source_word_ranges", []),
                   "parent_ids": u.get("parent_ids", [u["id"]])} for u in units],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def expanded_ranges(item):
    result = []
    for r in item.get('source_word_ranges', []):
        a, b = r['start_word_index'], r['end_word_index_exclusive']
        if type(a) is not int or type(b) is not int or not 0 <= a < b:
            raise ValueError('invalid source word range')
        result.extend((r['parent_id'], n) for n in range(a, b))
    return result


def validate_entity_evidence(disposition):
    """Validate scoped declarations, never infer a name or certify evidence truth."""
    if 'entity_evidence' not in disposition:
        return  # Existing reviewed records remain compatible.
    data = disposition['entity_evidence']
    if not isinstance(data, dict) or data.get('resolution_scope') not in ('referent_only', 'source_word'):
        raise ValueError('entity_evidence requires referent_only or source_word scope')
    records, claims = data.get('evidence'), data.get('claims')
    if not isinstance(records, list) or not records or not isinstance(claims, list) or not claims:
        raise ValueError('entity_evidence requires evidence and scoped claims')
    anchor_kinds = {'source_context', 'visual_reference', 'audio_observation', 'author_transcript'}
    kinds = anchor_kinds | {'external_fact', 'name_glossary', 'asr_hypothesis'}
    lookup = {}
    for e in records:
        if not isinstance(e, dict) or not all(isinstance(e.get(k), str) and e[k].strip() for k in ('id','kind','locator','observation')):
            raise ValueError('entity evidence requires id, kind, locator and actual observation')
        if e['id'] in lookup or e['kind'] not in kinds:
            raise ValueError('entity evidence ID duplicated or kind unknown')
        lookup[e['id']] = e
    seen, unknown, source_repairs = set(), False, []
    for claim in claims:
        if not isinstance(claim, dict) or not isinstance(claim.get('slot'), str) or not claim['slot'].strip() or claim['slot'] in seen:
            raise ValueError('entity claims require unique nonempty slots')
        seen.add(claim['slot'])
        state, value = claim.get('state'), claim.get('value')
        refs = claim.get('evidence_ids')
        if not isinstance(refs, list) or any(not isinstance(r, str) or r not in lookup for r in refs):
            raise ValueError('entity claim has unknown evidence reference')
        if type(claim.get('source_form_confirmed')) is not bool:
            raise ValueError('entity claim must distinguish source form confirmation')
        if state == 'unknown':
            unknown = True
            if value != '' or claim['source_form_confirmed']:
                raise ValueError('unknown entity slot cannot assert a value or confirmed source form')
        elif state == 'supported':
            if not isinstance(value, str) or not value.strip():
                raise ValueError('supported entity slot requires a value')
            if not any(lookup[r]['kind'] in anchor_kinds for r in refs):
                raise ValueError('entity identity needs a localized source anchor, not only glossary, external fact or ASR agreement')
        else:
            raise ValueError('entity claim state must be supported or unknown')
        if claim['source_form_confirmed']:
            lexical = claim.get('lexical_evidence_ids')
            if not isinstance(lexical, list) or not lexical or any(r not in refs or lookup[r]['kind'] not in ('audio_observation','author_transcript') for r in lexical):
                raise ValueError('source form confirmation requires actual audio observation or author transcript evidence')
        repairs_word = claim.get('repairs_source_word', False)
        if type(repairs_word) is not bool:
            raise ValueError('repairs_source_word must be boolean')
        if repairs_word:
            if data['resolution_scope'] != 'source_word' or state != 'supported' or not claim['source_form_confirmed']:
                raise ValueError('source_word repair cannot be justified by referent-only evidence')
            source_repairs.append(claim['slot'])
    if data['resolution_scope'] == 'source_word' and not source_repairs:
        raise ValueError('source_word scope requires explicitly marked source-word repair slots')
    for field in ('preserved_facts', 'conflicts'):
        if not isinstance(data.get(field), list) or any(not isinstance(v, str) or not v.strip() for v in data[field]):
            raise ValueError('entity evidence requires textual ' + field)
    if not isinstance(data.get('limitations'), str) or not data['limitations'].strip():
        raise ValueError('entity evidence requires explicit limitations')
    if disposition.get('action') == 'verified_repair' and (unknown or data['conflicts']):
        raise ValueError('unresolved entity slots or conflicts cannot be marked verified_repair')


def audit(doc, reference=None):
    """Return export blockers. Saving working JSON is always possible separately."""
    from display_omissions import audit as audit_omissions
    blockers = audit_omissions(doc, reference)
    if doc.get('source_constraints'):
        blockers.append('Migrate source_constraints into the single issues queue before export')
    units = doc.get("units")
    if units is not None:
        if not isinstance(units, list) or not units:
            return ["source guard: units must be a nonempty list"]
        by_id = {u["id"]: u for u in units}
        used = []
        for cue in doc.get("segments", []):
            scope = cue.get("unit_ids", [])
            if not scope or any(sid not in by_id for sid in scope):
                blockers.append(f"{cue['id']}: missing/unknown composed unit_ids")
                continue
            used.extend(scope)
            # Layout is allowed to add whitespace, but cannot rewrite the saved translation.
            for field in ("source", "target"):
                joined = "".join("".join(by_id[sid].get(field, "").split()) for sid in scope)
                actual = "".join(cue.get(field, "").split())
                if actual != joined:
                    blockers.append(f"{cue['id']}: composed {field} differs from reviewed units")
            try:
                expected_ranges = [v for sid in scope for v in expanded_ranges(by_id[sid])]
                if expanded_ranges(cue) != expected_ranges:
                    blockers.append(f"{cue['id']}: composed word ranges differ from reviewed units")
                expected_parents = list(dict.fromkeys(pid for pid, _ in expected_ranges))
                if cue.get('parent_ids') != expected_parents:
                    blockers.append(f"{cue['id']}: composed parent IDs differ from reviewed units")
            except (ValueError, TypeError, KeyError) as exc:
                blockers.append(f"{cue['id']}: {exc}")
        omitted_ids = {u['id'] for u in doc.get('omitted_units', [])}
        if used != [u["id"] for u in units if u['id'] not in omitted_ids]:
            blockers.append("composed semantic unit coverage/order differs")
    seen = set()
    for issue in doc.get("issues", []):
        iid = issue.get("id", "<missing>")
        if iid in seen:
            blockers.append(f"{iid}: duplicate issue ID")
        seen.add(iid)
        constraint = issue.get("source_constraint")
        # Existing resolved records remain readable. Unresolved source issues need migration.
        if constraint is None:
            if 'entity_evidence' in issue.get('disposition', {}):
                blockers.append(f"{iid}: declared entity_evidence requires source_constraint and full independent review")
            elif issue.get("status") != "resolved" and issue.get("kind") != "display":
                blockers.append(f"{iid}: unresolved issue needs source_constraint and disposition")
            continue
        try:
            scoped = scoped_units(doc, issue)
            if not isinstance(constraint, dict):
                raise ValueError("source_constraint must be an object")
            for key in ("unknown", "safe_content", "risk"):
                if not isinstance(constraint.get(key), str) or not constraint[key].strip():
                    raise ValueError(f"source_constraint requires {key}")
            forbidden = constraint.get("forbidden_inference")
            if not forbidden or not isinstance(forbidden, (str, list)):
                raise ValueError("source_constraint requires forbidden_inference")
            if isinstance(forbidden, list) and any(not isinstance(x, str) or not x.strip() for x in forbidden):
                raise ValueError("forbidden_inference entries must be nonempty text")
            if type(constraint.get("material")) is not bool:
                raise ValueError("source_constraint requires material boolean")
            unknown_dimensions = constraint.get('unresolved_dimensions', [])
            if not isinstance(unknown_dimensions, list) or any(not isinstance(v, str) or not v.strip() for v in unknown_dimensions):
                raise ValueError('unresolved_dimensions must be a list of named unresolved dimensions')
            if units is not None:
                expected = [c["id"] for c in doc["segments"] if set(c["unit_ids"]) & {u["id"] for u in scoped}]
                if issue.get("cue_ids") != expected:
                    raise ValueError("issue cue_ids are stale after composition")
            elif issue.get("cue_ids") != [u["id"] for u in scoped]:
                raise ValueError("issue cue_ids differ from semantic unit scope")
            disposition = issue.get("disposition", {})
            action = disposition.get("action")
            if action == "hold":
                if any(u.get("target", "").strip() for u in scoped):
                    raise ValueError("hold must have empty target; guessed text cannot be exported")
                raise ValueError("held source issue blocks subtitle export; keep working JSON")
            if action not in ("safe_partial", "verified_repair"):
                raise ValueError("choose hold, safe_partial or verified_repair")
            if not all(u.get("target", "").strip() or (
                    action == 'verified_repair' and issue.get('status') == 'resolved'
                    and u in doc.get('omitted_units', [])) for u in scoped):
                raise ValueError("reviewed treatment requires a saved translation")
            if action == "safe_partial":
                if issue.get("status") not in ("open", "provisional"):
                    raise ValueError("safe_partial must remain unresolved and draft-only")
                risks = [constraint['risk']] + unknown_dimensions
                if any(v.strip().lower().startswith('polarity') or 'polarity' in re.split(r'[^a-z]+', v.lower()) for v in risks):
                    raise ValueError("unresolved polarity requires hold or evidence-backed repair")
            elif issue.get("status") != "resolved":
                raise ValueError("verified_repair must be resolved")
            elif unknown_dimensions:
                raise ValueError('verified_repair cannot leave declared unresolved dimensions')
            for key in ("translator", "reviewer", "rationale", "evidence"):
                if not isinstance(disposition.get(key), str) or not disposition[key].strip():
                    raise ValueError(f"disposition requires {key}")
            if disposition["translator"] == disposition["reviewer"]:
                raise ValueError("source treatment requires independent reviewer")
            validate_entity_evidence(disposition)
            if disposition.get("reviewed_sha256") != content_fingerprint(doc, issue):
                raise ValueError("review binding missing or stale; review the saved content")
        except (ValueError, KeyError, TypeError) as exc:
            blockers.append(f"{iid}: {exc}")
    return blockers


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input")
    parser.add_argument("--reference", help="Original word reference; required to audit display omissions")
    parser.add_argument("--fingerprint", help="Print content hash for one issue; does not approve it")
    args = parser.parse_args()
    doc = json.loads(Path(args.input).read_text(encoding="utf-8-sig"))
    if args.fingerprint:
        matching = [i for i in doc.get("issues", []) if i.get("id") == args.fingerprint]
        if len(matching) != 1:
            parser.error("expected exactly one matching issue")
        print(content_fingerprint(doc, matching[0]))
    else:
        reference = json.loads(Path(args.reference).read_text(encoding="utf-8-sig")) if args.reference else None
        blockers = audit(doc, reference)
        print(json.dumps({"ok": not blockers, "source_blockers": blockers,
                          "meaning_checked": False}, ensure_ascii=False, indent=2))
        raise SystemExit(bool(blockers))


if __name__ == "__main__":
    main()
