#!/usr/bin/env python3
"""Append-only equipment events: where a piece of hardware was, and when.

Two files use the same event list:

  equipment/shared.yml      items[].events      (equipment that moves between hosts and rigs)
  hosts/<host>/events.yml   components[].events (parts of one host: drives, cameras, lenses, ...)

Rig events (setting changes, part conditions) live in rigs/<rig_id>/events.yml; see rig_events.py.

An event is never edited or deleted. A mistake is fixed by appending a `correction` that names the
event it corrects (`void: true`, or replacement fields). `at` is when the hardware changed, with
`at_precision` exact / on_or_before / on_or_after. `recorded_at` is when someone wrote it down.
The current location and the presence intervals are derived, never stored, except
`current_location` on a shared item, which must equal the derived value. tests/test_equipment_events.py
checks that the events on origin/main are still a prefix of each list (nothing edited, nothing
inserted before them). Run records (SpecSail bundles, Citrus captures) remain the ground truth
for which serial was used in which run.

    python3 equipment_events.py                    # validate shared.yml events, every hosts/*/events.yml and rigs/*/events.yml
    python3 equipment_events.py --history ID       # presence intervals of a shared item id or a host component serial

See docs/equipment_events.md.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

import hostconfig

REPO = hostconfig.REPO_ROOT
SHARED_PATH = REPO / "equipment" / "shared.yml"
HOST_EVENTS_SCHEMA_PATH = REPO / "schemas" / "host_events.v1.schema.json"

LOCATION_KINDS = ("installed", "removed", "moved")
ATTACH_KINDS = ("attached", "detached")
KINDS = LOCATION_KINDS + ATTACH_KINDS + ("correction",)
PRECISIONS = ("exact", "on_or_before", "on_or_after")
# What a correction may replace. Who/why/when-recorded belong to the correction itself; to change an
# event's kind, void it and append the right event.
CORRECTABLE = ("at", "at_precision", "to", "from", "rig", "with", "position")
EVENT_KEYS = ("event_id", "kind", "at", "at_precision", "recorded_at", "to", "from", "rig", "with", "position",
              "by", "reason", "evidence", "corrects", "void")
EVENT_ID = re.compile(r"^e[1-9][0-9]*$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
AT = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2})?(Z|[+-]\d{2}:\d{2})?)?$")
# A host id, a rig id, storage[:<place>], owner:<person or lab> (returned to whoever owns it), or unknown.
LOCATION = re.compile(r"^(storage(:.+)?|owner:.+|unknown|[a-z0-9_]+)$")

Finding = tuple[str, str]


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def effective(events: list[dict[str, Any]]) -> list[tuple[int, dict[str, Any]]]:
    """(list position, event) for every non-correction event, corrections applied, voided ones dropped."""
    out: dict[str, tuple[int, dict[str, Any]]] = {}
    for i, ev in enumerate(_dicts(events)):
        if ev.get("kind") != "correction":
            out[ev.get("event_id")] = (i, dict(ev))
        elif ev.get("corrects") in out:
            pos, target = out[ev["corrects"]]
            if ev.get("void") is True:
                del out[ev["corrects"]]
            else:
                out[ev["corrects"]] = (pos, {**target, **{k: ev[k] for k in CORRECTABLE if k in ev}})
    return list(out.values())


def _timeline(events: list[dict[str, Any]], kinds: tuple[str, ...]) -> list[dict[str, Any]]:
    """Effective events of these kinds by physical time; a tie keeps list order. ISO strings sort by time
    as long as datetimes in one list use the same zone form."""
    return [ev for _, ev in sorted(((pos, ev) for pos, ev in effective(events) if ev.get("kind") in kinds),
                                   key=lambda p: (str(p[1].get("at")), p[0]))]


def current_location(events: list[dict[str, Any]]) -> str | None:
    """The `to` of the latest location event, or None when there is none."""
    timeline = _timeline(events, LOCATION_KINDS)
    return timeline[-1].get("to") if timeline else None


def attached_to(events: list[dict[str, Any]]) -> set[str]:
    """Ids this item is attached to now."""
    now: set[str] = set()
    for ev in _timeline(events, ATTACH_KINDS):
        (now.add if ev["kind"] == "attached" else now.discard)(ev.get("with"))
    return now


def history(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Presence intervals: [{location, rig?, since, since_precision, until, until_precision}], until None = now."""
    out: list[dict[str, Any]] = []
    for ev in _timeline(events, LOCATION_KINDS):
        if out:
            out[-1].update(until=ev.get("at"), until_precision=ev.get("at_precision"))
        out.append({"location": ev.get("to"), **({"rig": ev["rig"]} if ev.get("rig") else {}),
                    "since": ev.get("at"), "since_precision": ev.get("at_precision"),
                    "until": None, "until_precision": None})
    return out


def findings(where: str, events: Any, known_ids: set[str] | None = None) -> list[Finding]:
    """Structure and consistency of one event list. known_ids: ids an attach `with` may name."""
    if not isinstance(events, list):
        return [("fail", f"{where}.events: a list")]
    out: list[Finding] = []
    seen: dict[str, dict[str, Any]] = {}
    last_recorded = ""
    for i, ev in enumerate(events):
        ew = f"{where}.events[{i}]"
        if not isinstance(ev, dict):
            out.append(("fail", f"{ew}: not a mapping"))
            continue
        eid, kind = ev.get("event_id"), ev.get("kind")
        ew = f"{where}.events.{eid}" if isinstance(eid, str) else ew
        extra = sorted(set(ev) - set(EVENT_KEYS))
        if extra:
            out.append(("fail", f"{ew}: unexpected keys {extra}"))
        if not (isinstance(eid, str) and EVENT_ID.match(eid)):
            out.append(("fail", f"{ew}: event_id e1, e2, ... required"))
        elif eid in seen:
            out.append(("fail", f"{ew}: event_id used twice (ids are never reused)"))
        if kind not in KINDS:
            out.append(("fail", f"{ew}: kind must be one of {', '.join(KINDS)}, found {kind!r}"))
        recorded = ev.get("recorded_at")
        if not (isinstance(recorded, str) and DATE.match(recorded)):
            out.append(("fail", f"{ew}: recorded_at must be a quoted YYYY-MM-DD"))
        elif recorded < last_recorded:
            out.append(("fail", f"{ew}: recorded_at {recorded} is before an earlier entry's {last_recorded}; "
                                f"events are appended, never inserted"))
        else:
            last_recorded = recorded
        if kind == "correction":
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
        else:
            out += _event_value_findings(ew, ev, known_ids)
        if isinstance(eid, str):
            seen.setdefault(eid, ev)
    if any(level == "fail" for level, _ in out):
        return out
    for _, ev in effective(events):  # corrected values must be valid too
        out += _event_value_findings(f"{where}.events.{ev['event_id']}", ev, known_ids)
    if out:
        return out
    previous = None
    for ev in _timeline(events, LOCATION_KINDS):
        if "from" in ev and previous is not None and ev["from"] != previous:
            out.append(("fail", f"{where}.events.{ev['event_id']}: from {ev['from']!r} but it was at {previous!r}"))
        previous = ev.get("to")
    now: set[str] = set()
    for ev in _timeline(events, ATTACH_KINDS):
        if ev["kind"] == "detached" and ev.get("with") not in now:
            out.append(("fail", f"{where}.events.{ev['event_id']}: detached from {ev.get('with')!r}, which it was "
                                f"not attached to"))
        (now.add if ev["kind"] == "attached" else now.discard)(ev.get("with"))
    return out


def _event_value_findings(ew: str, ev: dict[str, Any], known_ids: set[str] | None) -> list[Finding]:
    out: list[Finding] = []
    kind = ev.get("kind")
    at = ev.get("at")
    if not (isinstance(at, str) and AT.match(at)):
        out.append(("fail", f"{ew}: at must be a quoted YYYY-MM-DD or ISO datetime"))
    elif isinstance(ev.get("recorded_at"), str) and at[:10] > ev["recorded_at"]:
        out.append(("fail", f"{ew}: at {at} is after recorded_at {ev['recorded_at']}"))
    if ev.get("at_precision") not in PRECISIONS:
        out.append(("fail", f"{ew}: at_precision must be one of {', '.join(PRECISIONS)}"))
    for key in ("to", "from"):
        if key in ev and not (isinstance(ev[key], str) and LOCATION.match(ev[key])):
            out.append(("fail", f"{ew}: {key} must be a host id, rig id, storage[:<place>], owner:<who> or unknown"))
    if kind in LOCATION_KINDS and "to" not in ev:
        out.append(("fail", f"{ew}: a {kind} event needs to"))
    if kind in ATTACH_KINDS:
        if not isinstance(ev.get("with"), str):
            out.append(("fail", f"{ew}: a {kind} event needs with: <item id>"))
        elif known_ids is not None and ev["with"] not in known_ids:
            out.append(("fail", f"{ew}: with {ev['with']!r} is not a known item"))
    elif "with" in ev:
        out.append(("fail", f"{ew}: with belongs on attached/detached events"))
    return out


def shared_item_findings(item: dict[str, Any], known_ids: set[str]) -> list[Finding]:
    """An equipment/shared.yml item's events, and its current_location against them."""
    if "events" not in item:
        return []
    where = f"items.{item.get('id')}"
    out = findings(where, item["events"], known_ids - {item.get("id")})
    derived = current_location(item["events"]) if not out else None
    if derived is not None and item.get("current_location") != derived:
        out.append(("fail", f"{where}: current_location is {item.get('current_location')!r} but its events put it at "
                            f"{derived!r} (append an event when it moves; do not edit only current_location)"))
    return out


def _serials(value: Any) -> set[str]:
    """Every serial_number anywhere in a host config (cameras, lenses, drives, NICs, ...)."""
    found: set[str] = set()
    if isinstance(value, dict):
        for k, v in value.items():
            if k == "serial_number" and v not in (None, ""):
                found.add(str(v))
            else:
                found |= _serials(v)
    elif isinstance(value, list):
        for v in value:
            found |= _serials(v)
    return found


def host_findings(doc: Any, host: str, config: dict[str, Any] | None = None) -> list[Finding]:
    """hosts/<host>/events.yml: schema header, components, their events, and the host config's inventory."""
    if not isinstance(doc, dict):
        return [("fail", f"hosts/{host}/events.yml: not a mapping")]
    out: list[Finding] = []
    if doc.get("schema") != {"name": "host_events", "version": 1}:
        out.append(("fail", "schema must be {name: host_events, version: 1}"))
    if doc.get("host") != host:
        out.append(("fail", f"host must be {host!r} (the directory name), found {doc.get('host')!r}"))
    components = doc.get("components")
    if not isinstance(components, list):
        return out + [("fail", "components: a list")]
    serials: set[str] = set()
    inventory = _serials(config) if config is not None else None
    for i, comp in enumerate(components):
        serial = str(comp.get("serial_number")) if isinstance(comp, dict) and comp.get("serial_number") else None
        where = f"components.{serial}" if serial else f"components[{i}]"
        if serial is None:
            out.append(("fail", f"{where}: serial_number required"))
            continue
        if serial in serials:
            out.append(("fail", f"{where}: listed twice"))
        serials.add(serial)
        if not comp.get("component"):
            out.append(("fail", f"{where}: component (e.g. storage_device, camera, lens) required"))
        found = findings(where, comp.get("events"))
        out += found
        if found or inventory is None:
            continue
        here = current_location(comp.get("events") or []) == host
        if here and serial not in inventory:
            out.append(("warn", f"{where}: its events put it in {host}, but hosts/{host}/config.yml does not list it"))
        elif not here and serial in inventory:
            out.append(("warn", f"{where}: its events put it at {current_location(comp['events'])!r}, but "
                                f"hosts/{host}/config.yml still lists it"))
    return out


def host_events_path(host: str) -> Path:
    return REPO / "hosts" / host / "events.yml"


def load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text()) if path.is_file() else None


def _host_doc_schema_errors(doc: Any) -> list[str]:
    try:
        import jsonschema
    except ImportError:
        return []
    schema = json.loads(HOST_EVENTS_SCHEMA_PATH.read_text())
    return [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}"
            for e in sorted(jsonschema.Draft202012Validator(schema).iter_errors(doc), key=lambda e: list(e.absolute_path))]


def check_all() -> tuple[list[str], list[str]]:
    """(errors, warnings) for every hosts/*/events.yml. shared.yml events are checked by
    instruments.validate_shared, which calls shared_item_findings."""
    errors: list[str] = []
    warnings: list[str] = []
    for path in sorted((REPO / "hosts").glob("*/events.yml")):
        host = path.parent.name
        doc = load_yaml(path)
        config = load_yaml(path.parent / "config.yml")
        errors += [f"hosts/{host}/events.yml: {e}" for e in _host_doc_schema_errors(doc)]
        for level, message in host_findings(doc, host, config):
            (errors if level == "fail" else warnings).append(f"hosts/{host}/events.yml: {message}")
    return errors, warnings


def _find_events(ident: str) -> tuple[str, list[dict[str, Any]]] | None:
    for item in _dicts((load_yaml(SHARED_PATH) or {}).get("items")):
        if item.get("id") == ident:
            return f"equipment/shared.yml {ident}", item.get("events") or []
    for path in sorted((REPO / "hosts").glob("*/events.yml")):
        for comp in _dicts((load_yaml(path) or {}).get("components")):
            if str(comp.get("serial_number")) == ident:
                return f"hosts/{path.parent.name}/events.yml {ident}", comp.get("events") or []
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="validate equipment events; print an item's presence history")
    parser.add_argument("--history", metavar="ID", help="a shared item id or a host component serial_number")
    args = parser.parse_args(argv)
    if args.history:
        found = _find_events(args.history)
        if found is None:
            print(f"no shared item or host component {args.history!r}", file=sys.stderr)
            return 1
        label, events = found
        print(label)
        for span in history(events):
            prec = {"exact": "", "on_or_before": " (on or before)", "on_or_after": " (on or after)", None: ""}
            until = f"{span['until']}{prec[span['until_precision']]}" if span["until"] else "now"
            rig = f" [{span['rig']}]" if span.get("rig") else ""
            print(f"  {span['location']}{rig}: {span['since']}{prec[span['since_precision']]} -> {until}")
        attached = sorted(attached_to(events))
        if attached:
            print(f"  attached to: {', '.join(attached)}")
        return 0
    import instruments
    errors, warnings = instruments.validate_shared(instruments.load_shared())
    host_errors, host_warnings = check_all()
    import rig_events
    rig_errors, rig_warnings = rig_events.check_all()
    errors, warnings = errors + host_errors + rig_errors, warnings + host_warnings + rig_warnings
    for w in warnings:
        print(f"WARN: {w}", file=sys.stderr)
    for e in errors:
        print(f"ERROR: {e}", file=sys.stderr)
    if not errors:
        print("OK: equipment/shared.yml, hosts/*/events.yml and rigs/*/events.yml events")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
