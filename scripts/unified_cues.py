"""Backend-neutral, standard-library subtitle grouping. No ASR or file mutation.

API (all documents are JSON-compatible dicts, returned as new objects):
    prepare(reference, plan=None, parameters=None) -> schema-1 draft with segments
    compose(units_doc, reference, parameters=None) -> schema-1 draft with segments

reference.segments contains unique id and asr_words (word, start/end in seconds).
asr_words_offset_ms on each parent, or reference.meta.offset_ms, is added once.
Parent boundaries are provenance only. prepare uses one DP over the word stream.
plan = {"boundaries": [{"before": {"parent_id": "p", "word_index": 2},
                        "kind": "hard" | "soft", "reason": "later answer"}]}
Hard boundaries cannot be crossed; soft ones carry a cost. No plan means a draft
awaiting semantic boundary review, never a claim that replies are protected.

compose accepts units_doc.units, or units_doc.segments. Each complete semantic
unit has id/source/target/source_word_ranges. Mapping must cover the reference
once, contiguously and in order. Only join_next=true plus nonempty join_reason
authorizes crossing a unit boundary; the next unit's break_before=true vetoes it.
The DP can choose fewer joins than authorized. It never splits translated units.
An attached boundary_plan is also enforced during composition; a unit crossing
one of its hard boundaries is invalid. A recorded reference hash must match.
Targets and sources change only in whitespace. Original units are retained under
output.units; output segments carry unit_ids and original word ranges. Issues use
stable unit_ids (old cue_ids are migrated if needed), and current cue_ids.

A unit may explicitly request tail_evidence={reviewed:true, reason:"...",
evidence:"path/interval", start_ms:..., end_ms:..., display_end_ms:...}.
Its reviewed interval must enclose the entire requested tail. The request must
not exceed max_tail_ms or the immediately following reference word onset. An
unknown next onset forbids a tail. Internal-unit tails are not applied to merged
cues. Declaring reviewed is not machine proof of listening or human approval.

Missing interior word times are retained inside groups with real enclosing word
boundaries; an impossible boundary raises ValueError rather than interpolating.
Zero-duration words may join a positive-duration group. Reversed/nonfinite times
are rejected. Existing overlaps remain visible; gaps are never padded by default.
Display thresholds are preferences/diagnostics, not permission to alter meaning.

Metadata under meta.unified_cues records the engine version, effective parameters
and canonical-JSON SHA256 hashes of inputs (not file-byte hashes). CLI output uses
exclusive creation and refuses existing paths. No inputs, raw ASR or sidecars are
written. Run --help for prepare/compose commands. All output is a review draft.
"""

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import re
import unicodedata


ENGINE_VERSION = "unified-cues-1.3.0"
DEFAULT_PARAMETERS = {
    "min_duration_ms": 800, "max_duration_ms": 7000,
    "target_duration_ms": 3200, "max_width": 54, "max_lines": 1,
    "target_cps": 12.0, "max_tail_ms": 750,
    "max_words_per_group": 80, "max_units_per_group": 64,
    "soft_boundary_penalty": 8.0,
}


def canonical_hash(value):
    """SHA256 of canonical UTF-8 JSON; independent of indentation/key order."""
    data = json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _parameters(overrides):
    if overrides is not None and not isinstance(overrides, dict):
        raise ValueError("parameters must be an object")
    p = dict(DEFAULT_PARAMETERS)
    unknown = set(overrides or {}) - set(p)
    if unknown:
        raise ValueError("Unknown parameters: " + ", ".join(sorted(unknown)))
    p.update(overrides or {})
    for key, value in p.items():
        numeric = key in ("target_cps", "soft_boundary_penalty")
        if ((type(value) not in (int, float) if numeric else type(value) is not int)
                or not math.isfinite(value) or value <= 0):
            raise ValueError(key + " must be a positive finite " + ("number" if numeric else "integer"))
    if not p["min_duration_ms"] <= p["target_duration_ms"] <= p["max_duration_ms"]:
        raise ValueError("Require min_duration_ms <= target_duration_ms <= max_duration_ms")
    return p


def _schema(doc, name):
    if not isinstance(doc, dict) or type(doc.get("schema_version")) is not int or doc["schema_version"] != 1:
        raise ValueError(name + " must have schema_version: 1")


def _reference(reference):
    _schema(reference, "reference")
    if not isinstance(reference.get("meta", {}), dict):
        raise ValueError("reference.meta must be an object")
    parents = reference.get("segments")
    if not isinstance(parents, list) or not parents:
        raise ValueError("reference.segments must be nonempty")
    flat, lookup, lengths = [], {}, {}
    previous_start = -1
    for parent in parents:
        if not isinstance(parent, dict):
            raise ValueError("Invalid reference parent")
        pid = parent.get("id")
        if not isinstance(pid, str) or not pid or pid in lengths:
            raise ValueError("Missing/duplicate reference parent ID")
        words = parent.get("asr_words")
        if not isinstance(words, list) or not words:
            raise ValueError("Each reference parent needs nonempty asr_words")
        offset = parent.get("asr_words_offset_ms", reference.get("meta", {}).get("offset_ms", 0))
        if type(offset) is not int or offset < 0:
            raise ValueError("Word offset must be a nonnegative integer")
        lengths[pid] = len(words)
        for wi, word in enumerate(words):
            if not isinstance(word, dict) or not isinstance(word.get("word"), str) or not word["word"].strip():
                raise ValueError("Each word needs nonempty word text")
            times = []
            for field in ("start", "end"):
                value = word.get(field)
                if value is None:
                    times.append(None)
                elif type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                    raise ValueError("Invalid word timestamp: " + pid + ":" + str(wi))
                else:
                    times.append(round(value * 1000) + offset)
            start, end = times
            if start is not None and end is not None and end < start:
                raise ValueError("Reversed word timestamps")
            if start is not None:
                if start < previous_start:
                    raise ValueError("Reference word starts must be ordered; inspect chunk overlap")
                previous_start = start
            lookup[(pid, wi)] = len(flat)
            flat.append(dict(parent_id=pid, word_index=wi, text=word["word"], start_ms=start, end_ms=end))
    return flat, lookup, lengths


def _ranges(words):
    ranges = []
    for word in words:
        pid, wi = word["parent_id"], word["word_index"]
        if ranges and ranges[-1]["parent_id"] == pid and ranges[-1]["end_word_index_exclusive"] == wi:
            ranges[-1]["end_word_index_exclusive"] = wi + 1
        else:
            ranges.append(dict(parent_id=pid, start_word_index=wi, end_word_index_exclusive=wi + 1))
    return ranges


def _envelope(words):
    # Untimed interior words stay in the same exact-once range, never synthesized.
    if words[0]["start_ms"] is None or words[-1]["end_ms"] is None:
        return None
    end = max(w["end_ms"] for w in words if w["end_ms"] is not None)
    start = words[0]["start_ms"]
    return (start, end) if end > start else None


def _fold(text):
    return " ".join(text.split())


def display_width(text):
    """Approximate columns, not pixel width or a full grapheme-layout engine."""
    return sum(0 if unicodedata.category(c) in ("Mn", "Me", "Cf") else
               2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in text)


def wrap_text(text, max_width=42, keep_together=None):
    from layout import wrap
    return wrap(text, max_width, keep_together)


def _duration_cost(duration, p):
    cost = 1.0 + ((duration - p["target_duration_ms"]) / p["target_duration_ms"]) ** 2
    if duration < p["min_duration_ms"]:
        cost += 30 * ((p["min_duration_ms"] - duration) / p["min_duration_ms"]) ** 2
    if duration > p["max_duration_ms"]:
        cost += 50 * ((duration - p["max_duration_ms"]) / p["max_duration_ms"]) ** 2
    return cost


def _solve(count, candidates):
    costs, previous = [math.inf] * (count + 1), [None] * (count + 1)
    costs[0] = 0
    for begin in range(count):
        if not math.isfinite(costs[begin]):
            continue
        for end, cost in candidates(begin):
            if costs[begin] + cost < costs[end] - 1e-12:
                costs[end], previous[end] = costs[begin] + cost, begin
    if previous[count] is None:
        raise ValueError("No grouping with real enclosing word boundaries; inspect missing times/hard plan or group limits")
    groups, end = [], count
    while end:
        begin = previous[end]
        groups.append((begin, end))
        end = begin
    return list(reversed(groups))


def _plan(plan, lookup):
    if plan is None:
        return {}, []
    if not isinstance(plan, dict) or set(plan) - {"boundaries", "schema_version", "note"}:
        raise ValueError("plan accepts boundaries, optional schema_version/note")
    if "schema_version" in plan and (type(plan["schema_version"]) is not int or plan["schema_version"] != 1):
        raise ValueError("plan.schema_version must be 1")
    entries = plan.get("boundaries", [])
    if not isinstance(entries, list):
        raise ValueError("plan.boundaries must be a list")
    positions = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) - {"before", "kind", "reason"}:
            raise ValueError("Invalid plan boundary")
        before = entry.get("before")
        if not isinstance(before, dict) or set(before) != {"parent_id", "word_index"} or not isinstance(before["parent_id"], str) or type(before["word_index"]) is not int:
            raise ValueError("before needs parent_id and integer word_index")
        key = (before["parent_id"], before["word_index"])
        if key not in lookup or entry.get("kind") not in ("hard", "soft") or not isinstance(entry.get("reason"), str) or not entry["reason"].strip():
            raise ValueError("Unknown plan position or missing kind/reason")
        index = lookup[key]
        if index in positions:
            raise ValueError("Duplicate plan boundary")
        positions[index] = entry["kind"]
    return positions, copy.deepcopy(entries)


def _metadata(operation, reference, p, plan=None, units_doc=None):
    return dict(engine_version=ENGINE_VERSION, operation=operation,
                effective_parameters=copy.deepcopy(p), reference_sha256=canonical_hash(reference),
                plan_sha256=canonical_hash(plan) if plan is not None else None,
                units_sha256=canonical_hash(units_doc) if units_doc is not None else None,
                hash_method="canonical-json-sha256", semantic_review_performed=False)


def _cue(cid, words, source, target, envelope):
    ranges = _ranges(words)
    start, end = envelope
    result = dict(id=cid, source=_fold(source), target=target,
                  parent_ids=list(dict.fromkeys(r["parent_id"] for r in ranges)),
                  source_word_ranges=ranges, start_ms=start, end_ms=end,
                  speech_start_ms=start, speech_end_ms=end,
                  display_start_ms=start, display_end_ms=end, timing_precision="word")
    missing = [dict(parent_id=w["parent_id"], word_index=w["word_index"])
               for w in words if w["start_ms"] is None or w["end_ms"] is None]
    if missing:
        result["untimed_words"] = missing
        result["timing_note"] = "Untimed words retained inside actual enclosing word boundaries; no interpolation."
    return result


def _risks(cues, p, target=True):
    risks = []
    from layout import quality
    for i, c in enumerate(cues):
        if target:
            risks.extend(dict(item, cue_ids=[c["id"]]) for item in quality(c["target"], c.get("keep_together")))
        duration = c["end_ms"] - c["start_ms"]
        if duration < p["min_duration_ms"]:
            risks.append(dict(code="short_flash" if duration < 500 else "short_duration", cue_ids=[c["id"]], duration_ms=duration))
        if duration > p["max_duration_ms"]:
            risks.append(dict(code="long_duration", cue_ids=[c["id"]], duration_ms=duration))
        for old in cues[:i]:
            if old["end_ms"] > c["start_ms"]:
                risks.append(dict(code="overlap", cue_ids=[old["id"], c["id"]]))
        if target:
            lines = c["target"].split("\n")
            widths = [display_width(line) for line in lines]
            cps = sum(not x.isspace() for x in c["target"]) * 1000 / duration
            if len(lines) > p["max_lines"]:
                risks.append(dict(code="line_count", cue_ids=[c["id"]], observed=len(lines)))
            if max(widths) > p["max_width"]:
                risks.append(dict(code="line_width", cue_ids=[c["id"]], observed=max(widths)))
            if cps > p["target_cps"]:
                risks.append(dict(code="reading_speed", cue_ids=[c["id"]], observed=cps))
    return risks


def prepare(reference, plan=None, parameters=None):
    """Return an untranslated draft; parents never act as display boundaries.

    References with existing scoped issues/source_constraints are rejected until
    the source owner migrates them to unit scope, avoiding silent scope loss.
    """
    words, lookup, _ = _reference(reference)
    if reference.get("issues") or reference.get("source_constraints") or any(
            s.get("issues") or s.get("source_constraints") for s in reference["segments"]):
        raise ValueError("Reference has unmigrated issues/source_constraints; source owner must explicitly migrate scope")
    p = _parameters(parameters)
    boundaries, _ = _plan(plan, lookup)

    def candidates(begin):
        soft, gaps = 0, 0
        for end in range(begin + 1, min(len(words), begin + p["max_words_per_group"]) + 1):
            if end > begin + 1:
                boundary = boundaries.get(end - 1)
                if boundary == "hard":
                    break
                soft += boundary == "soft"
                a, b = words[end - 2]["end_ms"], words[end - 1]["start_ms"]
                if a is not None and b is not None:
                    gaps += max(0, b - a - 600) / 1000
            envelope = _envelope(words[begin:end])
            if envelope:
                duration = envelope[1] - envelope[0]
                punctuation = -0.15 if words[end - 1]["text"].rstrip().endswith((".", "?", "!", "。", "？", "！")) else 0.1
                yield end, _duration_cost(duration, p) + soft * p["soft_boundary_penalty"] + gaps * 2 + punctuation

    groups = _solve(len(words), candidates)
    cues = []
    for i, (begin, end) in enumerate(groups, 1):
        ws = words[begin:end]
        cue = _cue(f"u{i:06d}", ws, " ".join(w["text"] for w in ws), "", _envelope(ws))
        cue["break_before"] = boundaries.get(begin) == "hard"
        cues.append(cue)
    return dict(schema_version=1, timing_mode="rebuild", status="draft",
                meta={"unified_cues": _metadata("prepare", reference, p, plan)},
                boundary_plan=copy.deepcopy(plan), segments=cues,
                coverage=dict(words=len(words), exact_once=True, meaning_checked=False),
                semantic_boundary_review_required=True, display_risks=_risks(cues, p, False))


def _units(units_doc, words, lookup, lengths):
    _schema(units_doc, "units_doc")
    if not isinstance(units_doc.get("meta", {}), dict):
        raise ValueError("units_doc.meta must be an object")
    units = units_doc.get("units", units_doc.get("segments"))
    if not isinstance(units, list) or not units:
        raise ValueError("units_doc needs nonempty units or segments")
    seen, spans, cursor = set(), [], 0
    for unit in units:
        if not isinstance(unit, dict):
            raise ValueError("Invalid unit")
        uid = unit.get("id")
        if not isinstance(uid, str) or not uid or uid in seen:
            raise ValueError("Missing/duplicate unit ID")
        seen.add(uid)
        for field in ("source", "target"):
            omitted_target = field == "target" and "display_omission" in unit and unit.get(field) == ""
            if not omitted_target and (not isinstance(unit.get(field), str) or not unit[field].strip()):
                raise ValueError("Every complete semantic unit needs " + field)
        for flag in ("join_next", "break_before"):
            if flag in unit and type(unit[flag]) is not bool:
                raise ValueError(flag + " must be a boolean")
        if unit.get("join_next") and (not isinstance(unit.get("join_reason"), str) or not unit["join_reason"].strip()):
            raise ValueError("join_next=true requires a nonempty join_reason")
        from layout import phrases
        phrases(unit.get("keep_together"))
        rs = unit.get("source_word_ranges")
        if not isinstance(rs, list) or not rs:
            raise ValueError("Every unit needs source_word_ranges")
        begin, pids = cursor, []
        for r in rs:
            if not isinstance(r, dict):
                raise ValueError("Invalid source word range")
            pid, a, b = r.get("parent_id"), r.get("start_word_index"), r.get("end_word_index_exclusive")
            if not isinstance(pid, str) or pid not in lengths or type(a) is not int or type(b) is not int or not 0 <= a < b <= lengths[pid]:
                raise ValueError("Unknown parent or out-of-bounds/empty word range")
            pids.append(pid)
            for wi in range(a, b):
                if lookup[(pid, wi)] != cursor:
                    raise ValueError("Word ranges must cover reference exactly once in order, without gaps or duplicates")
                cursor += 1
        if "parent_ids" in unit and unit["parent_ids"] != list(dict.fromkeys(pids)):
            raise ValueError("parent_ids disagree with word ranges")
        if _envelope(words[begin:cursor]) is None:
            raise ValueError("Semantic unit lacks real enclosing positive word boundaries")
        spans.append((begin, cursor))
    if cursor != len(words):
        raise ValueError("Word ranges do not cover the entire reference")
    return units, spans


def _tail(unit, words, span, p):
    evidence = unit.get("tail_evidence")
    if evidence is None:
        return None
    if not isinstance(evidence, dict) or evidence.get("reviewed") is not True:
        raise ValueError("tail_evidence must be explicitly reviewed")
    for field in ("reason", "evidence"):
        if not isinstance(evidence.get(field), str) or not evidence[field].strip():
            raise ValueError("tail_evidence needs " + field)
    for field in ("start_ms", "end_ms", "display_end_ms"):
        if type(evidence.get(field)) is not int or evidence[field] < 0:
            raise ValueError("tail_evidence needs nonnegative integer " + field)
    begin, end = span
    speech_end = _envelope(words[begin:end])[1]
    if 'binding_sha256' in evidence:
        from tail_proposals import binding
        if evidence['binding_sha256'] != binding(unit, _envelope(words[begin:end])):
            raise ValueError('Tail evidence binding is stale for current text/words')
    requested = evidence["display_end_ms"]
    if not evidence["start_ms"] <= speech_end < requested <= evidence["end_ms"]:
        raise ValueError("Reviewed interval must enclose the complete requested tail")
    if requested - speech_end > p["max_tail_ms"]:
        raise ValueError("Requested tail exceeds max_tail_ms")
    if end < len(words):
        onset = words[end]["start_ms"]
        margin = evidence.get('next_guard_ms', 0)
        if type(margin) is not int or margin < 0:
            raise ValueError('Tail next_guard_ms must be a nonnegative integer')
        if onset is None or requested > onset - margin:
            raise ValueError("Tail reaches past next word onset or into an unknown gap")
    return requested


def _remap(items, unit_to_cue, name):
    if items is None:
        return []
    if not isinstance(items, list):
        raise ValueError(name + " must be a list of scoped objects")
    result = copy.deepcopy(items)
    for item in result:
        if not isinstance(item, dict):
            raise ValueError(name + " entries must be objects")
        ids = item.get("unit_ids", item.get("cue_ids"))
        if not isinstance(ids, list) or not ids or any(not isinstance(uid, str) or uid not in unit_to_cue for uid in ids):
            raise ValueError(name + " needs valid stable unit_ids (or input cue_ids)")
        item["unit_ids"] = list(dict.fromkeys(ids))
        item["cue_ids"] = list(dict.fromkeys(unit_to_cue[uid] for uid in ids if unit_to_cue[uid] is not None))
        omitted = [uid for uid in ids if unit_to_cue[uid] is None]
        if omitted:
            item["omitted_unit_ids"] = omitted
    return result


def compose(units_doc, reference, parameters=None):
    """Compose only explicitly approved joins of complete translated units.

    Unsafe tail requests and invalid coverage raise ValueError; display preference
    failures remain explicit risks. This function never approves source repairs.
    """
    words, lookup, lengths = _reference(reference)
    units, spans = _units(units_doc, words, lookup, lengths)
    from display_omissions import validate as validate_omission, scoped_issues
    omitted = [u for u in units if "display_omission" in u]
    for unit in omitted:
        validate_omission(unit, reference, (words, lookup, lengths))
    omitted_ids = {u["id"] for u in omitted}
    scoped_issues(units_doc, omitted_ids)
    if len(omitted) == len(units):
        raise ValueError("All units are omitted; no subtitle cues to export")
    recorded_engine = units_doc.get("meta", {}).get("unified_cues", {})
    if not isinstance(recorded_engine, dict):
        raise ValueError("meta.unified_cues must be an object")
    recorded_hash = recorded_engine.get("reference_sha256")
    if recorded_hash is not None and recorded_hash != canonical_hash(reference):
        raise ValueError("Recorded reference hash does not match the supplied reference")
    boundaries, _ = _plan(units_doc.get("boundary_plan"), lookup)
    hard = {position for position, kind in boundaries.items() if kind == "hard"}
    for begin, end in spans:
        if any(begin < position < end for position in hard):
            raise ValueError("Semantic unit crosses a hard boundary_plan boundary")
    from layout import phrases
    p = _parameters(parameters)
    tails = [_tail(u, words, span, p) for u, span in zip(units, spans)]
    cache = {}

    def evaluate(begin, end):
        key = (begin, end)
        if key in cache:
            return cache[key]
        ws = words[spans[begin][0]:spans[end - 1][1]]
        speech = _envelope(ws)
        display_end = max(speech[1], tails[end - 1] or speech[1])
        raw_target = " ".join(" ".join(u["target"].split()) for u in units[begin:end])
        target = raw_target if p["max_lines"] == 1 else wrap_text(raw_target, p["max_width"],
                           [phrase for u in units[begin:end] for phrase in phrases(u.get("keep_together"))])
        duration = display_end - speech[0]
        lines = target.split("\n")
        cps = sum(not x.isspace() for x in target) * 1000 / duration
        cost = _duration_cost(duration, p)
        cost += 30 * max(0, cps / p["target_cps"] - 1) ** 2
        cost += 30 * max(0, len(lines) - p["max_lines"]) ** 2
        cost += sum(30 * max(0, display_width(line) / p["max_width"] - 1) ** 2 for line in lines)
        cache[key] = (cost, target, speech, display_end)
        return cache[key]

    def candidates(begin):
        if units[begin]["id"] in omitted_ids:
            yield begin + 1, 0
            return
        for end in range(begin + 1, min(len(units), begin + p["max_units_per_group"]) + 1):
            if units[end - 1]["id"] in omitted_ids:
                break
            if end > begin + 1 and (not units[end - 2].get("join_next") or units[end - 1].get("break_before") or spans[end - 1][0] in hard):
                break
            yield end, evaluate(begin, end)[0]

    groups = _solve(len(units), candidates)
    cues, unit_to_cue = [], {uid: None for uid in omitted_ids}
    for begin, end in groups:
        if units[begin]["id"] in omitted_ids:
            continue
        i = len(cues) + 1
        _, target, speech, display_end = evaluate(begin, end)
        ws = words[spans[begin][0]:spans[end - 1][1]]
        cue = _cue(f"c{i:06d}", ws, " ".join(u["source"] for u in units[begin:end]), target, speech)
        cue["unit_ids"] = [u["id"] for u in units[begin:end]]
        cue["keep_together"] = list(dict.fromkeys(phrase for u in units[begin:end] for phrase in phrases(u.get("keep_together"))))
        cue["end_ms"] = cue["display_end_ms"] = display_end
        cue["approved_joins"] = [dict(after_unit_id=u["id"], reason=u["join_reason"]) for u in units[begin:end - 1]]
        if display_end > speech[1]:
            cue["tail_evidence"] = copy.deepcopy(units[end - 1]["tail_evidence"])
        for uid in cue["unit_ids"]:
            unit_to_cue[uid] = cue["id"]
        cues.append(cue)
    meta = copy.deepcopy(units_doc.get("meta", {}))
    meta["unified_cues"] = _metadata("compose", reference, p, units_doc.get("boundary_plan"), units_doc)
    return dict(schema_version=1, timing_mode="rebuild", status="draft", meta=meta,
                units=copy.deepcopy(units), segments=cues, omitted_units=copy.deepcopy(omitted),
                boundary_plan=copy.deepcopy(units_doc.get("boundary_plan")),
                issues=_remap(units_doc.get("issues"), unit_to_cue, "issues"),
                source_constraints=_remap(units_doc.get("source_constraints"), unit_to_cue, "source_constraints"),
                display_profile={"preset": "general", **{k: p[k] for k in ("min_duration_ms", "max_duration_ms", "max_width", "max_lines", "target_cps")}},
                coverage=dict(words=len(words), units=len(units), omitted_units=len(omitted),
                              omitted_words=sum(b - a for u, (a, b) in zip(units, spans) if u["id"] in omitted_ids),
                              displayed_words=sum(b - a for u, (a, b) in zip(units, spans) if u["id"] not in omitted_ids),
                              exact_once=True, meaning_checked=False),
                display_risks=_risks(cues, p), semantic_review_required=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="operation", required=True)
    prep = sub.add_parser("prepare", help="DP source draft from fixed reference words; semantic review required")
    prep.add_argument("reference")
    prep.add_argument("--plan", help="JSON hard/soft word-boundary plan")
    comp = sub.add_parser("compose", help="Compose translated units only across approved joins")
    comp.add_argument("units")
    comp.add_argument("--reference", required=True)
    for command in (prep, comp):
        command.add_argument("--parameters", help="JSON object overriding documented effective parameters")
        command.add_argument("--output", required=True, help="New JSON path; existing files are refused")
    args = parser.parse_args(argv)
    load = lambda path: json.loads(Path(path).read_text(encoding="utf-8-sig"))
    try:
        output = Path(args.output)
        if output.exists():
            raise ValueError("Output already exists; choose a new path")
        parameters = load(args.parameters) if args.parameters else None
        reference = load(args.reference)
        result = prepare(reference, load(args.plan) if args.plan else None, parameters) if args.operation == "prepare" else compose(load(args.units), reference, parameters)
        serialized = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        with output.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(serialized)
    except (ValueError, OSError, TypeError) as exc:
        parser.error(str(exc))
    print(json.dumps(dict(output=str(output.resolve()), cues=len(result["segments"]), engine_version=ENGINE_VERSION), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
