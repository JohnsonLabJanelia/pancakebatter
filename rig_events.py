#!/usr/bin/env python3
"""Append-only rig events: dated changes to a rig that are not where its hardware is.

    rigs/<rig_id>/events.yml   (schemas/rig_events.v1.schema.json, docs/equipment_events.md "Rig events")

Kinds:
  setting_changed      a device setting went from `old` to `new` (e.g. the projector's Brightness).
                       rig.yml holds the current value; this records when it changed.
  condition_observed   a condition of a part (scratches, a crack, residue) that recordings must carry
                       until it is cleared. Opens `condition_id`.
  condition_cleared    closes `condition_id` (e.g. the part was replaced, which is also a rig revision).
  correction           as for equipment events: void an event or replace some of its fields.

When it happened: `at` with at_precision exact / on_or_before / on_or_after, or at_precision `window`
with `not_before` and `not_after` and no `at`. A recording that starts inside a window has the setting
(or condition) unknown. The same rules as equipment events apply: never edit or delete an event,
append at the end (recorded_at never decreases), corrections name the event they fix, and
tests/test_equipment_events.py checks the events on origin/main are still a prefix of the list.

Recordings pin this file (sha256) next to rig.yml, because appending an event does not change rig.yml.

    python3 rig_events.py                       # validate every rigs/*/events.yml
    python3 rig_events.py --active RIG [ARENA ...]   # conditions open now (optionally only these arenas)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

import equipment_events as ee

REPO = ee.REPO
SCHEMA_PATH = REPO / "schemas" / "rig_events.v1.schema.json"

KINDS = ("setting_changed", "condition_observed", "condition_cleared", "correction")
PRECISIONS = ee.PRECISIONS + ("window",)
COMMON = ("event_id", "kind", "at", "at_precision", "not_before", "not_after", "recorded_at", "by", "reason",
          "evidence", "evidence_files")
KIND_KEYS = {
    "setting_changed": (("item", "setting", "old", "new"), ("method", "baseline")),
    "condition_observed": (("condition_id", "arenas", "layer", "condition", "extent"),
                           ("locations", "effect", "remedy")),
    "condition_cleared": (("condition_id",), ()),
    "correction": (("corrects",), ("void",)),
}
# What a correction may replace. Ids (condition_id, item, setting) are not correctable: void and re-append.
CORRECTABLE = ("at", "at_precision", "not_before", "not_after", "old", "new", "method", "baseline", "arenas", "layer",
               "condition", "extent", "locations", "effect", "remedy", "evidence_files")
CONDITION_ID = re.compile(r"^c[1-9][0-9]*$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")

Finding = tuple[str, str]


def effective(events: list[dict[str, Any]]) -> list[tuple[int, dict[str, Any]]]:
    """(list position, event) for every non-correction event, corrections applied, voided ones dropped."""
    out: dict[str, tuple[int, dict[str, Any]]] = {}
    for i, ev in enumerate(ee._dicts(events)):
        if ev.get("kind") != "correction":
            out[ev.get("event_id")] = (i, dict(ev))
        elif ev.get("corrects") in out:
            pos, target = out[ev["corrects"]]
            if ev.get("void") is True:
                del out[ev["corrects"]]
            else:
                fixed = {**target, **{k: ev[k] for k in CORRECTABLE if k in ev}}
                if fixed.get("at_precision") == "window":
                    fixed.pop("at", None)
                else:
                    fixed.pop("not_before", None), fixed.pop("not_after", None)
                out[ev["corrects"]] = (pos, fixed)
    return list(out.values())


def starts(ev: dict[str, Any]) -> str:
    """The latest time the change may have happened: `at`, or a window's not_after."""
    return str(ev.get("not_after") if ev.get("at_precision") == "window" else ev.get("at"))


def _timeline(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [ev for _, ev in sorted(effective(events), key=lambda p: (starts(p[1]), p[0]))]


def active_conditions(events: list[dict[str, Any]], arenas: list[str] | None = None) -> list[dict[str, Any]]:
    """condition_observed events (corrections applied) not yet cleared, optionally only those touching arenas."""
    open_: dict[str, dict[str, Any]] = {}
    for ev in _timeline(events):
        if ev.get("kind") == "condition_observed":
            open_[ev["condition_id"]] = ev
        elif ev.get("kind") == "condition_cleared":
            open_.pop(ev.get("condition_id"), None)
    out = list(open_.values())
    if arenas is not None:
        out = [ev for ev in out if set(ev.get("arenas") or []) & set(arenas)]
    return out


def setting_history(events: list[dict[str, Any]], item: str, setting: str) -> list[dict[str, Any]]:
    """[{value, from, from_precision, ...}] for one setting: each change starts its value; a window change
    leaves the value unknown between not_before and not_after."""
    out = []
    for ev in _timeline(events):
        if ev.get("kind") == "setting_changed" and ev.get("item") == item and ev.get("setting") == setting:
            if ev["at_precision"] == "window":
                out.append({"value": ev["old"], "valid_until": ev["not_before"]})
                out.append({"value": ev["new"], "valid_from": ev["not_after"]})
            else:
                out.append({"value": ev["new"], "valid_from": ev["at"], "precision": ev["at_precision"]})
    return out


def _when_findings(ew: str, ev: dict[str, Any]) -> list[Finding]:
    out: list[Finding] = []
    prec = ev.get("at_precision")
    if prec not in PRECISIONS:
        return [("fail", f"{ew}: at_precision must be one of {', '.join(PRECISIONS)}")]
    recorded = ev.get("recorded_at") if isinstance(ev.get("recorded_at"), str) else None
    if prec == "window":
        if "at" in ev:
            out.append(("fail", f"{ew}: a window has not_before and not_after, not at"))
        lo, hi = ev.get("not_before"), ev.get("not_after")
        for key, v in (("not_before", lo), ("not_after", hi)):
            if not (isinstance(v, str) and ee.AT.match(v)):
                out.append(("fail", f"{ew}: {key} must be a quoted YYYY-MM-DD or ISO datetime"))
        if not out:
            if lo > hi:
                out.append(("fail", f"{ew}: not_before {lo} is after not_after {hi}"))
            if recorded and hi[:10] > recorded:
                out.append(("fail", f"{ew}: not_after {hi} is after recorded_at {recorded}"))
        return out
    if "not_before" in ev or "not_after" in ev:
        out.append(("fail", f"{ew}: not_before/not_after belong on at_precision window"))
    at = ev.get("at")
    if not (isinstance(at, str) and ee.AT.match(at)):
        out.append(("fail", f"{ew}: at must be a quoted YYYY-MM-DD or ISO datetime"))
    elif recorded and at[:10] > recorded:
        out.append(("fail", f"{ew}: at {at} is after recorded_at {recorded}"))
    return out


def _evidence_file_findings(ew: str, files: Any, check_files: bool) -> list[Finding]:
    if not isinstance(files, list) or not files:
        return [("fail", f"{ew}: evidence_files must be a non-empty list of {{path, sha256}}")]
    out: list[Finding] = []
    for j, f in enumerate(files):
        if not (isinstance(f, dict) and set(f) == {"path", "sha256"} and isinstance(f["path"], str) and f["path"]
                and isinstance(f["sha256"], str) and SHA256.match(f["sha256"])):
            out.append(("fail", f"{ew}.evidence_files[{j}]: {{path, sha256 (64 lowercase hex)}}"))
            continue
        path = Path(f["path"]).expanduser()
        if check_files and path.is_file():
            got = hashlib.sha256(path.read_bytes()).hexdigest()
            if got != f["sha256"]:
                out.append(("fail", f"{ew}.evidence_files[{j}]: {path} has sha256 {got}, recorded {f['sha256']}"))
    return out


def _value_findings(ew: str, ev: dict[str, Any], stack: dict[str, Any] | None, check_files: bool) -> list[Finding]:
    kind = ev.get("kind")
    out = _when_findings(ew, ev)
    required, optional = KIND_KEYS[kind]
    allowed = set(COMMON) | set(required) | set(optional)
    extra = sorted(set(ev) - allowed)
    if extra:
        out.append(("fail", f"{ew}: unexpected keys {extra} for kind {kind}"))
    missing = [k for k in required if k not in ev]
    if missing:
        out.append(("fail", f"{ew}: a {kind} event needs {', '.join(missing)}"))
    if "evidence_files" in ev:
        out += _evidence_file_findings(ew, ev["evidence_files"], check_files)
    if kind == "setting_changed" and not missing:
        for k in ("item", "setting"):
            if not (isinstance(ev[k], str) and ev[k]):
                out.append(("fail", f"{ew}: {k} must be a non-empty string"))
        if ev["old"] == ev["new"]:
            out.append(("fail", f"{ew}: old and new are both {ev['new']!r}"))
    if kind in ("condition_observed", "condition_cleared") and not (
            isinstance(ev.get("condition_id"), str) and CONDITION_ID.match(ev["condition_id"])):
        out.append(("fail", f"{ew}: condition_id c1, c2, ... required"))
    if kind == "condition_observed" and not missing:
        arenas = ev["arenas"]
        if not (isinstance(arenas, list) and arenas and all(isinstance(a, str) for a in arenas)):
            out.append(("fail", f"{ew}: arenas must be a non-empty list of arena ids"))
        elif stack is not None and set(arenas) - set(stack.get("arenas") or {}):
            out.append(("fail", f"{ew}: arenas {sorted(set(arenas) - set(stack.get('arenas') or {}))} are not in "
                                f"rig.yml media_stack.arenas"))
        if stack is not None and ev["layer"] not in [layer.get("id") for layer in stack.get("layers") or []]:
            out.append(("fail", f"{ew}: layer {ev['layer']!r} is not a rig.yml media_stack layer id"))
        for k in ("condition", "extent"):
            if not (isinstance(ev[k], str) and ev[k]):
                out.append(("fail", f"{ew}: {k} must be a non-empty string"))
    return out


def findings(doc: Any, rig_id: str, rig_doc: dict[str, Any] | None = None, check_files: bool = True) -> list[Finding]:
    """rigs/<rig_id>/events.yml: header, event structure, corrections, condition open/close order.
    rig_doc (rig.yml) lets arenas and layer be checked against its media_stack."""
    if not isinstance(doc, dict):
        return [("fail", "not a mapping")]
    out: list[Finding] = []
    if doc.get("schema") != {"name": "rig_events", "version": 1}:
        out.append(("fail", "schema must be {name: rig_events, version: 1}"))
    if doc.get("rig") != rig_id:
        out.append(("fail", f"rig must be {rig_id!r} (the directory name), found {doc.get('rig')!r}"))
    events = doc.get("events")
    if not isinstance(events, list):
        return out + [("fail", "events: a list")]
    stack = (rig_doc or {}).get("media_stack") if rig_doc is not None else None
    seen: dict[str, dict[str, Any]] = {}
    last_recorded = ""
    for i, ev in enumerate(events):
        ew = f"events[{i}]"
        if not isinstance(ev, dict):
            out.append(("fail", f"{ew}: not a mapping"))
            continue
        eid, kind = ev.get("event_id"), ev.get("kind")
        ew = f"events.{eid}" if isinstance(eid, str) else ew
        if not (isinstance(eid, str) and ee.EVENT_ID.match(eid)):
            out.append(("fail", f"{ew}: event_id e1, e2, ... required"))
        elif eid in seen:
            out.append(("fail", f"{ew}: event_id used twice (ids are never reused)"))
        recorded = ev.get("recorded_at")
        if not (isinstance(recorded, str) and ee.DATE.match(recorded)):
            out.append(("fail", f"{ew}: recorded_at must be a quoted YYYY-MM-DD"))
        elif recorded < last_recorded:
            out.append(("fail", f"{ew}: recorded_at {recorded} is before an earlier entry's {last_recorded}; "
                                f"events are appended, never inserted"))
        else:
            last_recorded = recorded
        if kind not in KINDS:
            out.append(("fail", f"{ew}: kind must be one of {', '.join(KINDS)}, found {kind!r}"))
        elif kind == "correction":
            extra = sorted(set(ev) - set(COMMON) - {"corrects", "void"} - set(CORRECTABLE))
            if extra:
                out.append(("fail", f"{ew}: unexpected keys {extra} for a correction"))
            target = seen.get(ev.get("corrects"))
            if target is None:
                out.append(("fail", f"{ew}: corrects must name an earlier event_id"))
            elif target.get("kind") == "correction":
                out.append(("fail", f"{ew}: correct the original event, not a correction"))
            replaces = [k for k in CORRECTABLE if k in ev]
            if (ev.get("void") is True) == bool(replaces):
                out.append(("fail", f"{ew}: either void: true or replacement fields ({', '.join(CORRECTABLE)})"))
            if not (ev.get("reason") or ev.get("evidence")):
                out.append(("fail", f"{ew}: a correction needs a reason or evidence"))
            if "evidence_files" in ev:
                out += _evidence_file_findings(ew, ev["evidence_files"], check_files)
        else:
            out += _value_findings(ew, ev, stack, check_files)
        if isinstance(eid, str):
            seen.setdefault(eid, ev)
    if any(level == "fail" for level, _ in out):
        return out
    for _, ev in effective(events):  # corrected values must be valid too
        out += _value_findings(f"events.{ev['event_id']}", ev, stack, check_files)
    if out:
        return out
    opened: set[str] = set()
    open_now: set[str] = set()
    for ev in _timeline(events):
        cid = ev.get("condition_id")
        if ev["kind"] == "condition_observed":
            if cid in opened:
                out.append(("fail", f"events.{ev['event_id']}: condition_id {cid} opened twice (never reuse an id)"))
            opened.add(cid)
            open_now.add(cid)
        elif ev["kind"] == "condition_cleared":
            if cid not in open_now:
                out.append(("fail", f"events.{ev['event_id']}: clears {cid}, which is not open at that time"))
            open_now.discard(cid)
    return out


def events_path(rig_id: str) -> Path:
    return REPO / "rigs" / rig_id / "events.yml"


def _schema_errors(doc: Any) -> list[str]:
    try:
        import jsonschema
    except ImportError:
        return []
    schema = json.loads(SCHEMA_PATH.read_text())
    return [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}"
            for e in sorted(jsonschema.Draft202012Validator(schema).iter_errors(doc), key=lambda e: list(e.absolute_path))]


def check_all(check_files: bool = True) -> tuple[list[str], list[str]]:
    """(errors, warnings) for every rigs/*/events.yml."""
    errors: list[str] = []
    warnings: list[str] = []
    for path in sorted((REPO / "rigs").glob("*/events.yml")):
        rig = path.parent.name
        doc = ee.load_yaml(path)
        rig_doc = ee.load_yaml(path.parent / "rig.yml")
        errors += [f"rigs/{rig}/events.yml: {e}" for e in _schema_errors(doc)]
        for level, message in findings(doc, rig, rig_doc, check_files):
            (errors if level == "fail" else warnings).append(f"rigs/{rig}/events.yml: {message}")
    return errors, warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="validate rig events; list a rig's open conditions")
    parser.add_argument("--active", nargs="+", metavar=("RIG", "ARENA"), help="a rig id, optionally arena ids")
    args = parser.parse_args(argv)
    if args.active:
        rig, arenas = args.active[0], args.active[1:] or None
        doc = ee.load_yaml(events_path(rig)) or {}
        for ev in active_conditions(doc.get("events") or [], arenas):
            print(f"{ev['condition_id']} ({ev['event_id']}): {ev['condition']} on {ev['layer']} at "
                  f"{', '.join(ev['arenas'])}: {ev['extent']}")
        return 0
    errors, warnings = check_all()
    for w in warnings:
        print(f"WARN: {w}", file=sys.stderr)
    for e in errors:
        print(f"ERROR: {e}", file=sys.stderr)
    if not errors:
        print("OK: rigs/*/events.yml")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
