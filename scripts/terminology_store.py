"""Persistent, locale/profile-scoped terminology. Python standard library only."""
from __future__ import annotations

import argparse
import copy
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import uuid

SKILL = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def save_new(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temp.open("x", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def config_path():
    base = os.environ.get("APPDATA") if os.name == "nt" else os.environ.get("XDG_CONFIG_HOME")
    return (Path(base) if base else Path.home() / ".config") / "long-video-subtitles" / "terminology.json"


def root_path(value=None):
    chosen = value or os.environ.get("LONG_VIDEO_SUBTITLES_TERMS")
    if not chosen and config_path().exists():
        chosen = read(config_path()).get("library_root")
    return Path(chosen or config_path().parent / "terminology-library").expanduser().resolve()


def component(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value):
        raise ValueError(f"Invalid library identifier: {value!r}")
    return value


def tag(value):
    component(value)
    parts = value.split("-")
    return "-".join([parts[0].lower()] + [p.upper() if len(p) == 2 else p.title() for p in parts[1:]])


def scope(domain, source_language, target_locale, profile):
    return dict(domain=component(domain).lower(), source_language=tag(source_language),
                target_locale=tag(target_locale), profile=component(profile).lower())


def slot(root, spec):
    return root / spec["domain"] / (spec["source_language"] + "--" + spec["target_locale"]) / spec["profile"]


@contextmanager
def locked(directory):
    directory.mkdir(parents=True, exist_ok=True)
    lock = directory / ".write.lock"
    try:
        with lock.open("x", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(), "created_at": now()}, f)
    except FileExistsError:
        raise ValueError(f"Library busy: {lock}. Retry after the writer finishes; inspect a stale lock before removing it.")
    try:
        yield
    finally:
        lock.unlink()


def now():
    return datetime.now(timezone.utc).isoformat()


def latest(directory):
    versions = sorted(directory.glob("v[0-9][0-9][0-9][0-9][0-9][0-9].json"))
    if not versions:
        return None
    data = read(versions[-1])
    if data.get("revision") != int(versions[-1].stem[1:]):
        raise ValueError(f"Invalid library revision: {versions[-1]}")
    return data


def seed_catalog():
    return read(SKILL / "assets/glossaries/catalog.json")["libraries"]


def canonical_entry(entry):
    e = copy.deepcopy(entry)
    if "target" not in e and "preferred_target" in e:
        e["target"] = e.pop("preferred_target")
    return e


def validate_entry(e):
    for key in ("id", "source", "target", "sense"):
        if not isinstance(e.get(key), str) or not e[key].strip():
            raise ValueError(f"Term requires nonempty {key}")
    component(e["id"])
    if not isinstance(e.get("aliases", []), list) or any(not isinstance(x, str) or not x for x in e.get("aliases", [])):
        raise ValueError(f"Invalid aliases: {e['id']}")


def identity(e):
    return (e["source"].casefold().strip(), e["sense"].strip())


def validate_library(data, spec):
    if any(data.get(k) != v for k, v in spec.items()):
        raise ValueError("Library language/domain/profile mismatch")
    ids, senses = set(), set()
    for e in data["entries"]:
        validate_entry(e)
        if e["id"] in ids or identity(e) in senses:
            raise ValueError(f"Duplicate term id/sense: {e['id']}")
        ids.add(e["id"])
        senses.add(identity(e))


def commit(directory, data):
    dest = directory / f"v{data['revision']:06d}.json"
    if dest.exists():
        raise ValueError(f"Revision already exists: {dest}")
    atomic_write(dest, data)


def load_library(root, spec):
    directory = slot(root, spec)
    with locked(directory):
        data = latest(directory)
        if data is None:
            data = {"schema_version": 1, **spec, "revision": 1, "updated_at": now(), "entries": []}
            candidates = [x for x in seed_catalog() if all(x.get(k) == v for k, v in spec.items())]
            if candidates:
                seed_info = candidates[0]
                seed = read(SKILL / seed_info["path"])
                data["entries"] = [canonical_entry(e) for e in seed["entries"]]
                data["seed"] = {"resource": seed_info["path"], "version": seed["version"],
                                "profile": seed.get("profile"), "source_catalog": seed.get("source_catalog", [])}
            validate_library(data, spec)
            commit(directory, data)
        validate_library(data, spec)
        return data


def snapshot(root, domains, source_language, target_locale, profile):
    specs = [scope(d, source_language, target_locale, profile) for d in dict.fromkeys(domains)]
    return {"schema_version": 1, "library_root": str(root), "loaded_at": now(),
            "libraries": [load_library(root, s) for s in specs]}


# Public, reusable term facts only. Project occurrences and ASR guesses stay in the job.
TERM_FIELDS = {"id", "source", "target", "sense", "aliases", "allowed_targets", "forbidden_targets",
               "case_sensitive", "requires_context", "status", "sources", "decision_note", "usage_note",
               "era", "category", "reference_target", "target_aliases", "faction"}


def prepared_addition(raw):
    if raw.get("status") != "confirmed" or raw.get("reusable") is not True:
        return None, "Only confirmed reusable terms enter the persistent library"
    if raw.get("origin") in {"benchmark", "reference_translation", "asr_guess"}:
        return None, "Evaluation answers and unresolved ASR guesses are not reusable evidence"
    e = canonical_entry(raw)
    validate_entry(e)
    sources = e.get("sources", [])
    if not sources or any(not isinstance(s, dict) or not re.match(r"^https://[^/\s]+", s.get("url", s.get("locator", "")))
                          or not s.get("checked_at") or not (s.get("note") or s.get("scope")) for s in sources):
        return None, "Reusable additions need checked public source URLs and a note stating what each supports"
    if not e.get("decision_note"):
        return None, "Reusable additions need an adoption reason"
    return {k: v for k, v in e.items() if k in TERM_FIELDS}, None


def promote(root, base, additions):
    if Path(base["library_root"]).resolve() != root:
        raise ValueError("Snapshot belongs to a different persistent root")
    bases = {x["domain"]: x for x in base["libraries"]}
    if len(bases) != len(base["libraries"]):
        raise ValueError("One profile/language combination per domain in a snapshot")
    grouped = {}
    for raw in additions:
        domain = component(raw.get("domain", "")).lower()
        if domain not in bases:
            raise ValueError(f"Load domain before promoting terms: {domain}")
        spec = scope(domain, bases[domain]["source_language"], bases[domain]["target_locale"], bases[domain]["profile"])
        if any(raw.get(k, v) != v for k, v in spec.items() if k != "domain"):
            raise ValueError("Addition language/profile mismatch")
        entry, error = prepared_addition(raw)
        grouped.setdefault(domain, []).append((raw, entry, error))
    result = {"schema_version": 1, "library_root": str(root), "updated_at": now(), "results": []}
    for domain, changes in grouped.items():
        old = bases[domain]
        spec = scope(domain, old["source_language"], old["target_locale"], old["profile"])
        directory = slot(root, spec)
        with locked(directory):
            current = latest(directory)
            if current is None:
                raise ValueError("Persistent library disappeared after load")
            validate_library(current, spec)
            before = copy.deepcopy(current["entries"])
            by_id = {e["id"]: e for e in current["entries"]}
            old_by_id = {e["id"]: e for e in old["entries"]}
            for raw, entry, error in changes:
                row = {"domain": domain, "id": raw.get("id"), "status": "skipped", "reason": error}
                result["results"].append(row)
                if error:
                    continue
                previous = by_id.get(entry["id"])
                duplicate = next((x for x in by_id.values() if identity(x) == identity(entry) and x["id"] != entry["id"]), None)
                if duplicate:
                    row.update(status="conflict", reason="Same source/sense already has id " + duplicate["id"])
                elif previous == entry:
                    row.update(status="unchanged", reason=None)
                elif previous is not None and previous != old_by_id.get(entry["id"]):
                    row.update(status="conflict", reason="This term changed since this project loaded its snapshot")
                elif previous is not None and (raw.get("replaces_target") != previous["target"] or not raw.get("change_reason")):
                    row.update(status="conflict", reason="Existing term changes need replaces_target and change_reason")
                else:
                    by_id[entry["id"]] = entry
                    row.update(status="updated" if previous else "added", reason=raw.get("change_reason"))
            current["entries"] = list(by_id.values())
            validate_library(current, spec)
            if current["entries"] != before:
                current["revision"] += 1
                current["updated_at"] = now()
                current["change_summary"] = [r for r in result["results"] if r["domain"] == domain and r["status"] in {"added", "updated"}]
                commit(directory, current)
            for row in result["results"]:
                if row["domain"] == domain:
                    row["library_revision"] = current["revision"]
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", help="Override persistent root for this invocation; otherwise use env/config/default")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("configure", help="Save --root as the user's persistent library location")
    sub.add_parser("list", help="List available local libraries and bundled seeds, without reading old projects")
    load = sub.add_parser("load", help="Create an immutable project snapshot; seed a new matching library once")
    load.add_argument("--domain", action="append", required=True)
    load.add_argument("--source-language", required=True)
    load.add_argument("--target-locale", required=True)
    load.add_argument("--profile", required=True)
    load.add_argument("--output", type=Path, required=True)
    merge = sub.add_parser("promote", help="Accumulate verified reusable additions; report conflicts without overwriting them")
    merge.add_argument("--snapshot", type=Path, required=True)
    merge.add_argument("--additions", type=Path, required=True)
    merge.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    root = root_path(a.root)
    if a.command == "configure":
        if not a.root:
            raise ValueError("configure requires --root")
        root.mkdir(parents=True, exist_ok=True)
        atomic_write(config_path(), {"library_root": str(root)})
        print(json.dumps({"config": str(config_path()), "library_root": str(root)}))
    elif a.command == "list":
        libraries = []
        for directory in sorted(root.glob("*/*/*")):
            if directory.is_dir() and (d := latest(directory)) is not None:
                libraries.append({**{k: d[k] for k in ("domain", "source_language", "target_locale", "profile", "revision")}, "entries": len(d["entries"])})
        print(json.dumps({"root": str(root), "local": libraries, "bundled": seed_catalog()}, ensure_ascii=False))
    else:
        if a.output.exists():
            raise ValueError(f"Output already exists: {a.output}; use a new project version")
        if a.command == "load":
            result = snapshot(root, a.domain, a.source_language, a.target_locale, a.profile)
        else:
            result = promote(root, read(a.snapshot), read(a.additions)["entries"])
        save_new(a.output, result)
        print(json.dumps({"output": str(a.output), "library_root": str(root),
                          **({"libraries": [{"domain": x["domain"], "revision": x["revision"], "entries": len(x["entries"])} for x in result["libraries"]]}
                             if a.command == "load" else {"results": result["results"]})}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, TypeError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
