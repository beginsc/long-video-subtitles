"""Deterministic display screening, with local exceptions bound only to affected cues."""
import hashlib
import json
import math
import unicodedata


DEFAULTS = {
    "max_lines": 1, "max_width": 54, "target_cps": 12.0,
    "min_duration_ms": 800, "max_duration_ms": 7000, "flash_ms": 500,
    "bilingual_target_lines": 1, "source_max_lines": 1,
    "source_max_width": 72, "source_cps": 20.0,
}


def resolve_profile(doc, override=None, cps=None):
    saved = doc.get("display_profile", {})
    if not isinstance(saved, dict) or (override is not None and not isinstance(override, dict)):
        raise ValueError("display_profile/profile must be an object")
    values = dict(saved)
    if override is not None:
        values.update(override)
    if set(values) - (set(DEFAULTS) | {"preset"}):
        raise ValueError("Unknown display profile fields: " + ", ".join(sorted(set(values) - (set(DEFAULTS) | {"preset"}))))
    preset = values.pop("preset", "zh-game" if doc.get("domain") in ("sc2", "cs2", "game", "gameplay") else "general")
    if preset not in ("general", "zh-game"):
        raise ValueError("Display preset must be general or zh-game")
    profile = dict(DEFAULTS, preset=preset)
    if preset == "zh-game":
        profile["max_lines"] = 1
    profile.update(values)
    if cps is not None:
        profile["target_cps"] = cps
    for key in DEFAULTS:
        value = profile[key]
        if key in ("target_cps", "source_cps"):
            valid = type(value) in (int, float) and math.isfinite(value) and value > 0
        else:
            valid = type(value) is int and value > 0
        if not valid:
            raise ValueError(f"{key} must be a positive finite {'number' if key.endswith('cps') else 'integer'}")
    if not profile["flash_ms"] <= profile["min_duration_ms"] <= profile["max_duration_ms"]:
        raise ValueError("Require flash_ms <= min_duration_ms <= max_duration_ms")
    return profile


def display_width(text):
    """East Asian column approximation, NOT font pixels or a grapheme renderer."""
    return sum(0 if unicodedata.category(c) in ("Mn", "Me", "Cf") else
               2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in text)


def screen(current, profile, bilingual=False):
    risks = []
    by_id = {r["id"]: r for r in current}

    def add(code, ids, message, track="target", **details):
        affected = [{k: by_id[sid].get(k) for k in ("id", "source", "target", "start_ms", "end_ms", "keep_together")} for sid in ids]
        relevant = {
            "short_flash": ["flash_ms"], "short_duration": ["flash_ms", "min_duration_ms"],
            "long_duration": ["max_duration_ms"], "overlap": [], "orphan_line": [], "split_phrase": [],
            "line_width": ["source_max_width" if track == "source" else "max_width"],
            "line_count": ["source_max_lines" if track == "source" else "bilingual_target_lines" if bilingual else "max_lines"],
            "reading_speed": ["source_cps" if track == "source" else "target_cps"],
        }[code]
        binding = {"code": code, "cues": affected, "settings": {k:profile[k] for k in relevant},
                   "track": track, "bilingual": bilingual}
        digest = hashlib.sha256(json.dumps(binding, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        risks.append({"risk_id": f"{code}:{digest}", "code": code, "cue_ids": ids,
                      "track": track, "message": message, **details})

    for cue in current:
        sid = cue["id"]
        duration = cue["end_ms"] - cue["start_ms"]
        from layout import quality
        for item in quality(cue.get("target", ""), cue.get("keep_together")):
            code = item.pop("code")
            add(code, [sid], f"{sid}: {code}", **item)
        if duration < profile["flash_ms"]:
            add("short_flash", [sid], f"{sid}: duration {duration} ms", duration_ms=duration)
        elif duration < profile["min_duration_ms"]:
            add("short_duration", [sid], f"{sid}: duration {duration} ms", duration_ms=duration)
        elif duration > profile["max_duration_ms"]:
            add("long_duration", [sid], f"{sid}: duration {duration} ms", duration_ms=duration)
        tracks = [("target", cue.get("target", ""),
                   profile["bilingual_target_lines"] if bilingual else profile["max_lines"],
                   profile["max_width"], profile["target_cps"])]
        if bilingual:
            tracks.append(("source", cue["source"], profile["source_max_lines"], profile["source_max_width"], profile["source_cps"]))
        for track, text, max_lines, max_width, cps in tracks:
            lines = text.split("\n")
            if len(lines) > max_lines:
                add("line_count", [sid], f"{sid}: {track} has {len(lines)} lines (limit {max_lines})", track,
                    observed=len(lines), limit=max_lines)
            widths = [display_width(line) for line in lines]
            if max(widths) > max_width:
                add("line_width", [sid], f"{sid}: {track} width {max(widths)} (limit {max_width})", track,
                    observed=max(widths), limit=max_width, line_widths=widths)
            count = sum(not c.isspace() for c in text)
            if count * 1000 / duration > cps:
                add("reading_speed", [sid], f"{sid}: {track} exceeds {cps:g} nonspace characters/second", track,
                    observed=count * 1000 / duration, limit=cps)
    active = []
    for cue in current:
        active = [old for old in active if old["end_ms"] > cue["start_ms"]]
        for old in active:
            add("overlap", [old["id"], cue["id"]], f"overlap: {old['id']} / {cue['id']}", "both")
        active.append(cue)
    return risks


def apply_exceptions(risks, exceptions):
    """A reason is a declaration, never a machine certification of human viewing."""
    if exceptions is None:
        exceptions = []
    if not isinstance(exceptions, list):
        raise ValueError("display_exceptions must be a list")
    current = {r["risk_id"]: r for r in risks}
    used, stale, seen = [], [], set()
    for entry in exceptions:
        if not isinstance(entry, dict):
            raise ValueError("Each display exception must be an object")
        key = entry.get("risk_id")
        if not isinstance(key, str) or not key or key in seen:
            raise ValueError("Display exception risk_id must be unique and nonempty")
        seen.add(key)
        for field in ("reason", "evidence"):
            if not isinstance(entry.get(field), str) or not entry[field].strip():
                raise ValueError(f"Display exception requires {field}")
        if key in current:
            used.append(dict(entry))
        else:
            stale.append(key)
    accepted = {x["risk_id"] for x in used}
    return used, stale, [r for r in risks if r["risk_id"] not in accepted]


def unresolved_issues(doc):
    issues = doc.get("issues", [])
    if not isinstance(issues, list):
        raise ValueError("issues must be a list")
    unresolved = []
    ids = {r["id"] for r in doc["segments"]}
    for issue in issues:
        if not isinstance(issue, dict) or issue.get("status") not in ("open", "provisional", "resolved"):
            raise ValueError("Each issue requires status open/provisional/resolved")
        scope = issue.get("cue_ids")
        omitted = {u['id'] for u in doc.get('omitted_units', [])}
        omitted_scope = issue.get('omitted_unit_ids', [])
        resolved_omission = (issue['status'] == 'resolved' and isinstance(omitted_scope, list)
                            and bool(omitted_scope) and set(omitted_scope) <= omitted
                            and set(issue.get('unit_ids', [])) == set(omitted_scope))
        if not isinstance(scope, list) or (not scope and not resolved_omission) or any(not isinstance(x, str) or x not in ids for x in scope):
            raise ValueError("Issue cue_ids must name current cues")
        if not isinstance(issue.get("detail"), str) or not issue["detail"].strip():
            raise ValueError("Issue requires a nonempty detail")
        if issue["status"] != "resolved":
            unresolved.append(issue)
    return unresolved
