#!/usr/bin/env python3
"""Small offline subtitle file tools. No model calls or editorial approval."""
import argparse
import copy
import json
import re
import sys
import unicodedata
from pathlib import Path
from display_checks import resolve_profile, screen, apply_exceptions, unresolved_issues
from source_guard import audit as audit_source
from display_omissions import audit as audit_omissions


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    # Use a new filename for each revision; never destroy a previous result.
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(text)


def rows(doc):
    if not isinstance(doc, dict) or doc.get("schema_version") != 1:
        raise ValueError("Expected schema_version: 1")
    result = doc.get("segments")
    if not isinstance(result, list) or not result:
        raise ValueError("segments must be a nonempty list")
    seen, previous = set(), -1
    for item in result:
        if not isinstance(item, dict):
            raise ValueError("Each segment must be an object")
        sid = item.get("id")
        if not isinstance(sid, str) or not sid.strip() or sid in seen:
            raise ValueError("Missing/duplicate string ID")
        seen.add(sid)
        a, b = item.get("start_ms"), item.get("end_ms")
        if type(a) is not int or type(b) is not int or not 0 <= a < b:
            raise ValueError(f"{sid}: require integer 0 <= start_ms < end_ms")
        if a < previous:
            raise ValueError(f"{sid}: out of chronological order")
        previous = a
        for key in ("source", "target"):
            text = item.get(key, "")
            if not isinstance(text, str) or any(unicodedata.category(c) == "Cc" and c != "\n" for c in text):
                raise ValueError(f"{sid}: invalid {key} text")
            if text and any(not line.strip() for line in text.split("\n")):
                raise ValueError(f"{sid}: blank lines inside a cue are unsupported")
        if not item.get("source", "").strip():
            raise ValueError(f"{sid}: empty source")
        parents = item.get("parent_ids", [sid])
        if (not isinstance(parents, list) or not parents
                or any(not isinstance(p, str) or not p for p in parents)
                or len(set(parents)) != len(parents)):
            raise ValueError(f"{sid}: invalid parent_ids")
    return result


def parse_stamp(text):
    match = re.fullmatch(r"(?:(\d+):)?([0-5]\d):([0-5]\d)[,.](\d{3})", text)
    if not match:
        raise ValueError(f"Invalid timestamp: {text}")
    h, m, s, ms = match.groups()
    return ((int(h or 0) * 60 + int(m)) * 60 + int(s)) * 1000 + int(ms)


def import_file(path):
    path = Path(path)
    if path.suffix.lower() == ".json":
        doc = load(path)
        rows(doc)
        return doc
    if path.suffix.lower() not in (".srt", ".vtt"):
        raise ValueError("Import supports SRT, basic VTT, or schema_version 1 JSON")
    text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n\s*\n", text.strip())
    result = []
    for block in blocks:
        lines = block.splitlines()
        if not lines:
            continue
        if lines[0].startswith("WEBVTT"):
            if any("-->" in line for line in lines[1:]):
                raise ValueError("VTT header must be separated from cues by a blank line")
            continue
        if lines[0].startswith(("NOTE", "STYLE", "REGION")):
            raise ValueError("VTT metadata/style blocks require a preserving converter")
        timing_index = 0 if "-->" in lines[0] else 1
        if len(lines) <= timing_index + 1:
            raise ValueError("Missing timing or text")
        match = re.fullmatch(r"(\S+)\s+-->\s+(\S+)", lines[timing_index])
        if not match:
            raise ValueError("Unsupported cue settings or malformed timing")
        ident = lines[0].strip() if timing_index else str(len(result) + 1)
        result.append({"id": ident, "start_ms": parse_stamp(match[1]),
                       "end_ms": parse_stamp(match[2]), "source": "\n".join(lines[timing_index + 1:]),
                       "target": ""})
    doc = {"schema_version": 1, "timing_mode": "fixed", "segments": result}
    rows(doc)
    return doc


def check_word_ranges(current, original, failures, warnings):
    """Check existing raw-word index spans, without inventing word alignment."""
    counts = {r["id"]: len(r["asr_words"]) for r in original
              if isinstance(r.get("asr_words"), list) and r["asr_words"]}
    coverage = {"level": "parent_ids", "word_parents_checked": 0, "words_checked": 0}
    if not any("source_word_ranges" in r for r in current) or not counts:
        if counts:
            warnings.append("word coverage not checked: no source_word_ranges; only parent IDs checked")
        return coverage
    collected = {pid: [] for pid in counts}
    for item in current:
        sid = item["id"]
        expected = set(item.get("parent_ids", [sid])) & counts.keys()
        spans = item.get("source_word_ranges", [])
        if not isinstance(spans, list):
            failures.append(f"{sid}: source_word_ranges must be a list")
            continue
        found = set()
        for span in spans:
            if not isinstance(span, dict):
                failures.append(f"{sid}: invalid source word range")
                continue
            pid = span.get("parent_id")
            if not isinstance(pid, str) or pid not in expected:
                failures.append(f"{sid}: word range parent is not a declared parent with reference words")
                continue
            found.add(pid)
            a, b = span.get("start_word_index"), span.get("end_word_index_exclusive")
            if type(a) is not int or type(b) is not int or not 0 <= a < b <= counts[pid]:
                failures.append(f"{sid}: invalid word bounds for {pid}")
                continue
            collected[pid].append((a, b))
        if expected - found:
            failures.append(f"{sid}: missing word ranges for " + ", ".join(sorted(expected - found)))
    for pid, spans in collected.items():
        cursor = 0
        for a, b in sorted(spans):
            if a > cursor:
                failures.append(f"word gap: {pid} [{cursor}, {a})")
            elif a < cursor:
                failures.append(f"duplicate/overlapping word coverage: {pid} [{a}, {min(b, cursor)})")
            cursor = max(cursor, b)
        if cursor < counts[pid]:
            failures.append(f"word gap: {pid} [{cursor}, {counts[pid]})")
    coverage.update(level="word_ranges" if len(counts) == len(original) else "mixed",
                    word_parents_checked=len(counts), words_checked=sum(counts.values()))
    if len(counts) < len(original):
        warnings.append("Some reference parents have no word list; their coverage is parent-ID-only")
    return coverage


def check(doc, reference=None, mode=None, require_target=True, cps=None,
          status="draft", profile=None, exceptions=None, bilingual=False):
    current = rows(doc)
    mode = mode or doc.get("timing_mode", "rebuild")
    if mode not in ("fixed", "rebuild") or status not in ("draft", "final"):
        raise ValueError("Invalid mode or status")
    if status == "final" and not require_target:
        raise ValueError("Source-only checks cannot use final status")
    effective_profile = resolve_profile(doc, profile, cps)
    failures, warnings = [], []
    omission_blockers = audit_omissions(doc, reference)
    failures.extend(omission_blockers)
    omitted = doc.get('omitted_units', []) if not omission_blockers else []
    coverage_rows = current + omitted
    for item in current:
        sid = item["id"]
        target = item.get("target", "")
        if require_target and not target.strip():
            failures.append(f"{sid}: missing translation")
    risks = screen(current, effective_profile, bilingual) if require_target else []
    warnings.extend(r["message"] for r in risks)
    accepted, stale, unhandled = apply_exceptions(risks, doc.get("display_exceptions") if exceptions is None else exceptions)
    if stale:
        warnings.append("Ignored stale/unmatched display exceptions: " + ", ".join(stale))
    issues = unresolved_issues(doc)
    coverage = {"level": "not_checked", "word_parents_checked": 0, "words_checked": 0}
    if reference is not None:
        original = rows(reference)
        if mode == "fixed":
            coverage["level"] = "fixed_identity"
            identity = lambda seq: [(r["id"], r["start_ms"], r["end_ms"]) for r in seq]
            if identity(current) != identity(original):
                failures.append("fixed: IDs/order/count/times differ from reference")
        else:
            expected = {r["id"] for r in original}
            actual = {p for r in coverage_rows for p in r.get("parent_ids", [r["id"]])}
            if expected - actual:
                failures.append("missing source IDs: " + ", ".join(sorted(expected - actual)))
            if actual - expected:
                failures.append("unknown source IDs: " + ", ".join(sorted(actual - expected)))
            coverage = check_word_ranges(coverage_rows, original, failures, warnings)
            if omitted:
                count_words = lambda seq: sum(r['end_word_index_exclusive'] - r['start_word_index'] for u in seq for r in u.get('source_word_ranges', []))
                coverage.update(displayed_words=count_words(current), omitted_words=count_words(omitted), omitted_units=len(omitted))
    blockers = []
    if status == "final":
        if reference is None:
            blockers.append("Final export requires the original source reference for coverage/fixed-axis checks")
        elif reference is doc:
            blockers.append("Current document cannot be its own original reference")
        blockers.extend(r["message"] for r in unhandled)
        blockers.extend("Unresolved issue: " + i["detail"] for i in issues)
    source_blockers = audit_source(doc, reference) if require_target else []
    return {"ok": not failures and not blockers and not source_blockers, "structural_ok": not failures, "status": status,
            "cues": len(current), "failures": failures, "warnings": warnings, "final_blockers": blockers,
            "display_profile": effective_profile, "display_risks": risks,
            "accepted_exceptions": accepted, "stale_exceptions": stale, "unhandled_risks": len(unhandled),
            "unresolved_issues": issues, "source_blockers": source_blockers,
            "coverage": coverage, "meaning_checked": False,
            "note": "Coverage and declared exceptions do not certify meaning, independent review, listening or viewing."}


def assemble(parts, reference, mode):
    merged = {"schema_version": 1, "timing_mode": mode, "segments": []}
    for key in ("display_profile", "domain"):
        choices = [part[key] for part in parts if key in part]
        if key in reference:
            choices.append(reference[key])
        if choices:
            if any(value != choices[0] for value in choices):
                raise ValueError(f"Conflicting {key}; choose one project setting before assembly")
            merged[key] = copy.deepcopy(choices[0])
    provenance = []
    for part in parts:
        merged["segments"].extend(copy.deepcopy(rows(part)))
        for key in ("issues", "display_exceptions", "units", "source_constraints", "omitted_units"):
            if key in part:
                if not isinstance(part[key], list):
                    raise ValueError(f"{key} must be a list")
                merged.setdefault(key, []).extend(copy.deepcopy(part[key]))
        if part.get("meta"):
            provenance.append(copy.deepcopy(part["meta"]))
        plan = part.get("boundary_plan")
        if plan:
            merged.setdefault("boundary_plan", {"boundaries": []})["boundaries"].extend(copy.deepcopy(plan["boundaries"]))
    if provenance:
        merged["meta"] = {"assembly_parts": provenance}
        if len(parts) == 1:
            merged["meta"].update(copy.deepcopy(parts[0].get("meta", {})))
    report = check(merged, reference, mode)
    if not report["ok"]:
        raise ValueError("; ".join(report["failures"] + report["source_blockers"] + report["final_blockers"]))
    return merged


def stamp(value, fmt):
    if fmt == "ass":
        # ASS has centisecond precision; do not silently truncate to whole seconds.
        value = (value + 5) // 10
        h, rem = divmod(value, 360000)
        m, rem = divmod(rem, 6000)
        s, cs = divmod(rem, 100)
        return f"{h}:{m:02}:{s:02}.{cs:02}"
    h, rem = divmod(value, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02}:{m:02}:{s:02}{',' if fmt == 'srt' else '.'}{ms:03}"


ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Source Han Sans SC,64,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,1,2,60,60,60,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def export(doc, fmt, bilingual=False, *, reference=None, mode=None,
           status="draft", profile=None, exceptions=None):
    if fmt not in ("srt", "vtt", "ass"):
        raise ValueError("Unsupported export format")
    report = check(doc, reference, mode, status=status, profile=profile, exceptions=exceptions, bilingual=bilingual)
    if not report["ok"]:
        raise ValueError("; ".join(report["failures"] + report["source_blockers"] + report["final_blockers"]))
    output = [ASS_HEADER] if fmt == "ass" else ["WEBVTT\n\n"] if fmt == "vtt" else []
    for index, item in enumerate(rows(doc), 1):
        text = item["target"]
        if not bilingual and report["display_profile"]["max_lines"] == 1 and ("\n" in text or "\r" in text):
            raise ValueError(f"{item['id']}: single-line export requires a reviewed single-line target")
        if bilingual:
            text += "\n" + item["source"]
        a, b = stamp(item["start_ms"], fmt), stamp(item["end_ms"], fmt)
        if fmt == "ass":
            if a == b:
                raise ValueError(f"{item['id']}: duration collapses at ASS precision")
            if any(char in text for char in "{}\\"):
                raise ValueError("Literal braces/backslashes need a dedicated ASS authoring tool")
            output.append(f"Dialogue: 0,{a},{b},Default,,0,0,0,," + text.replace("\n", "\\N") + "\n")
        else:
            ident = item["id"] if fmt == "vtt" or (mode or doc.get("timing_mode")) == "fixed" else str(index)
            if fmt == "srt" and not ident.isdecimal():
                raise ValueError("SRT needs numeric IDs; retain nonnumeric IDs in JSON or export VTT")
            output.append(f"{ident}\n{a} --> {b}\n{text}\n\n")
    return "".join(output)


def index_map(doc, exported_path, bilingual=False):
    """Read back the actual SRT/VTT and map its IDs to the saved cue IDs."""
    if Path(exported_path).suffix.lower() not in (".srt", ".vtt"):
        raise ValueError("index-map requires an exported SRT or VTT file")
    current, rendered = rows(doc), rows(import_file(exported_path))
    if len(current) != len(rendered):
        raise ValueError("Exported cue count differs from current document")
    mapping = []
    for before, after in zip(current, rendered):
        text = before["target"] + ("\n" + before["source"] if bilingual else "")
        if (before["start_ms"], before["end_ms"], text) != (after["start_ms"], after["end_ms"], after["source"]):
            raise ValueError(f"{before['id']}: exported text/time differs from current document")
        mapping.append({"cue_id": before["id"], "exported_id": after["id"],
                        "start_ms": after["start_ms"], "end_ms": after["end_ms"]})
    return {"schema_version": 1, "exported_file": str(Path(exported_path).resolve()),
            "text_and_times_match": True, "mapping": mapping}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("import")
    p.add_argument("input"); p.add_argument("--output", required=True)
    p = sub.add_parser("check")
    p.add_argument("input"); p.add_argument("--reference")
    p.add_argument("--mode", choices=("fixed", "rebuild"))
    p.add_argument("--source-only", action="store_true")
    p.add_argument("--cps", type=float)
    p.add_argument("--status", choices=("draft", "final"), default="draft")
    p.add_argument("--profile"); p.add_argument("--exceptions")
    p.add_argument("--bilingual", action="store_true")
    p = sub.add_parser("assemble")
    p.add_argument("parts", nargs="+"); p.add_argument("--reference", required=True)
    p.add_argument("--mode", choices=("fixed", "rebuild"), required=True)
    p.add_argument("--output", required=True)
    p = sub.add_parser("export")
    p.add_argument("input"); p.add_argument("--format", choices=("srt", "vtt", "ass"), default="srt")
    p.add_argument("--bilingual", action="store_true"); p.add_argument("--output", required=True)
    p.add_argument("--status", choices=("draft", "final"), required=True)
    p.add_argument("--reference"); p.add_argument("--mode", choices=("fixed", "rebuild"))
    p.add_argument("--profile"); p.add_argument("--exceptions")
    p = sub.add_parser("index-map")
    p.add_argument("input"); p.add_argument("--exported", required=True)
    p.add_argument("--bilingual", action="store_true"); p.add_argument("--output", required=True)
    args = parser.parse_args()
    if (args.command in ("check", "export") and args.status == "final" and args.reference
            and Path(args.input).samefile(args.reference)):
        raise ValueError("Current file cannot be its own original reference; use the preserved source file")
    if args.command == "import":
        doc = import_file(args.input); write(args.output, doc)
    elif args.command == "check":
        doc = load(args.input)
        result = check(doc, load(args.reference) if args.reference else None,
                       args.mode, not args.source_only, args.cps, status=args.status,
                       profile=load(args.profile) if args.profile else None,
                       exceptions=load(args.exceptions) if args.exceptions else None, bilingual=args.bilingual)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["ok"] else 1
    elif args.command == "assemble":
        write(args.output, assemble([load(p) for p in args.parts], load(args.reference), args.mode))
    elif args.command == "index-map":
        write(args.output, index_map(load(args.input), args.exported, args.bilingual))
    else:
        doc = load(args.input)
        options = dict(reference=load(args.reference) if args.reference else None, mode=args.mode, status=args.status,
                       profile=load(args.profile) if args.profile else None,
                       exceptions=load(args.exceptions) if args.exceptions else None)
        write(args.output, export(doc, args.format, args.bilingual, **options))
        print(json.dumps({"output": str(Path(args.output).resolve()), "status": args.status,
                          "check": check(doc, bilingual=args.bilingual, **options)}, ensure_ascii=False))
        return 0
    print(json.dumps({"output": str(Path(args.output).resolve())}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(2)
