"""Cross-checks for the instrument sections (daqs, photodetectors, light_arrays) and equipment/shared.yml.

The JSON Schema says what each entry looks like; this module checks what the schema cannot: that a
reference names something that exists (a camera serial, a PDU outlet, a DAQ channel, a shared item),
that a photodetector's fitted filter is the one it was calibrated with, and that no calibration is
overdue. Findings are ``[(level, message)]`` with level ``fail`` or ``warn``, as in check_kernel_tuning;
configio turns fails into validation errors and keeps warnings separate, so an overdue calibration
never blocks saving or installing a config.

Runs with or without jsonschema (``/usr/bin/python3`` has none), so every lookup is defensive.

    python3 instruments.py              # validate equipment/shared.yml
    python3 instruments.py --host NAME  # also the instrument sections of hosts/NAME/config.yml

See docs/host_config_interface.md (Instruments, Shared equipment).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

import yaml

import hostconfig

SHARED_PATH = hostconfig.REPO_ROOT / "equipment" / "shared.yml"
SHARED_SCHEMA_PATH = hostconfig.REPO_ROOT / "schemas" / "shared_equipment.v1.schema.json"
# Fields a host instance takes from its shared item instead of repeating them.
SHARED_FIELDS = ("part_id", "serial_number", "calibration")
SOURCE_KINDS = ("camera_gpio", "photodetector", "light_array", "host", "other")
STROBE_SOURCES = ("camera_gpio", "daq", "none", "unknown")


def _list(value: Any) -> list[dict[str, Any]]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _filter_key(value: Any) -> Any:
    """Comparable identity of a filterRef: 'none', (part_id, serial_or_lot), or None when not recorded."""
    if value == "none":
        return "none"
    if isinstance(value, dict):
        return (value.get("part_id"), value.get("serial_or_lot"))
    return None


def _describe_filter(key: Any) -> str:
    if key == "none":
        return "no filter"
    if key is None:
        return "nothing recorded"
    return f"{key[0]} ({key[1] or 'no serial/lot'})"


def load_shared(path: Path = SHARED_PATH) -> dict[str, Any]:
    """equipment/shared.yml, or an empty document when the file does not exist."""
    if not path.is_file():
        return {"schema": {"name": "shared_equipment", "version": 1}, "items": []}
    return yaml.safe_load(path.read_text()) or {}


def shared_items(doc: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in _list((doc or {}).get("items")) if isinstance(item.get("id"), str)}


def resolve_shared(instance: dict[str, Any], items: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """The instance with part_id, serial_number and calibration filled from its shared item (if any)."""
    item = items.get(instance.get("shared_id"))
    if not item:
        return instance
    merged = dict(instance)
    for field in SHARED_FIELDS:
        if field in item:
            merged[field] = item[field]
    return merged


def calibration_findings(where: str, calibration: Any, today: date) -> list[tuple[str, str]]:
    if not isinstance(calibration, dict):
        return []
    out = []
    due = _date(calibration.get("due"))
    if due is None:
        out.append(("fail", f"{where}.calibration.due must be a quoted ISO date, found {calibration.get('due')!r}"))
    elif due < today:
        cert = f" {calibration['certificate_id']}" if calibration.get("certificate_id") else ""
        out.append(("warn", f"{where}: calibration{cert} was due {due.isoformat()} "
                            f"(calibrated {calibration.get('calibrated_at')} by {calibration.get('performed_by')})"))
    if _date(calibration.get("calibrated_at")) is None:
        out.append(("fail", f"{where}.calibration.calibrated_at must be a quoted ISO date, found {calibration.get('calibrated_at')!r}"))
    return out


def _unique_names(section: str, entries: list[dict[str, Any]], out: list[tuple[str, str]]) -> dict[str, dict[str, Any]]:
    by_name: dict[str, dict[str, Any]] = {}
    for i, entry in enumerate(entries):
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            out.append(("fail", f"{section}[{i}]: name is required"))
        elif name in by_name:
            out.append(("fail", f"{section}: name {name!r} is used twice"))
        else:
            by_name[name] = entry
    return by_name


def _shared_ref_findings(where: str, instance: dict[str, Any], items: dict[str, dict[str, Any]],
                         hostname: Any) -> list[tuple[str, str]]:
    shared_id = instance.get("shared_id")
    if shared_id is None:
        if instance.get("serial_number") in (None, ""):
            return [("fail", f"{where}: needs serial_number or shared_id")]
        return []
    if shared_id not in items:
        return [("fail", f"{where}: shared_id {shared_id!r} is not an item in equipment/shared.yml")]
    out = [("fail", f"{where}: {field} comes from shared item {shared_id!r}; remove it here")
           for field in SHARED_FIELDS if field in instance]
    location = items[shared_id].get("current_location")
    if hostname and location != hostname:
        out.append(("warn", f"{where}: shared item {shared_id!r} has current_location {location!r}, not {hostname!r}; "
                            f"update equipment/shared.yml when it moves"))
    return out


def _daq_channel_findings(where: str, ref: Any, daqs: dict[str, dict[str, Any]]) -> tuple[list[tuple[str, str]], dict | None]:
    """Check a {daq, channel} reference; return (findings, the channel entry or None)."""
    if not isinstance(ref, dict):
        return [("fail", f"{where} must be {{daq, channel}}")], None
    daq = daqs.get(ref.get("daq"))
    if daq is None:
        return [("fail", f"{where}: daq {ref.get('daq')!r} is not in daqs")], None
    channel = next((c for c in _list(daq.get("channels")) if c.get("id") == ref.get("channel")), None)
    if channel is None:
        return [("fail", f"{where}: daq {ref.get('daq')!r} has no channel {ref.get('channel')!r}")], None
    return [], channel


def findings(config: dict[str, Any], shared: dict[str, Any] | None = None,
             today: date | None = None) -> list[tuple[str, str]]:
    """[(level, message)] for a host config's daqs, photodetectors and light_arrays.

    shared: the equipment/shared.yml document; loaded from the repo only when an instance uses shared_id.
    today: for the overdue check (default: date.today()).
    """
    today = today or date.today()
    out: list[tuple[str, str]] = []
    daq_list = _list(config.get("daqs"))
    pd_list = _list(config.get("photodetectors"))
    la_list = _list(config.get("light_arrays"))
    if not (daq_list or pd_list or la_list):
        return out
    if shared is None and any("shared_id" in e for e in daq_list + pd_list):
        shared = load_shared()
    items = shared_items(shared)
    hostname = (config.get("system_info") or {}).get("hostname")
    serials = {str(cam.get("serial_number")) for cam in (config.get("cameras") or {}).values() if isinstance(cam, dict)}
    pdus = {p.get("name"): p for p in _list(config.get("pdus"))}

    daqs = _unique_names("daqs", daq_list, out)
    photodetectors = _unique_names("photodetectors", pd_list, out)
    light_arrays = _unique_names("light_arrays", la_list, out)

    for name, daq in daqs.items():
        where = f"daqs.{name}"
        out += _shared_ref_findings(where, daq, items, hostname)
        resolved = resolve_shared(daq, items)
        out += calibration_findings(where, resolved.get("calibration"), today)
        seen: set[str] = set()
        for channel in _list(daq.get("channels")):
            cid = channel.get("id")
            cwhere = f"{where}.channels.{cid}"
            if cid in seen:
                out.append(("fail", f"{where}: channel {cid!r} is listed twice"))
            seen.add(cid)
            source = channel.get("source") if isinstance(channel.get("source"), dict) else {}
            kind = source.get("kind")
            if kind not in SOURCE_KINDS:
                out.append(("fail", f"{cwhere}: source.kind must be one of {', '.join(SOURCE_KINDS)}, found {kind!r}"))
            elif kind == "camera_gpio" and str(source.get("camera_serial")) not in serials:
                out.append(("fail", f"{cwhere}: camera_serial {source.get('camera_serial')!r} is not in cameras"))
            elif kind == "photodetector" and source.get("name") not in photodetectors:
                out.append(("fail", f"{cwhere}: photodetector {source.get('name')!r} is not in photodetectors"))
            elif kind == "light_array" and source.get("name") not in light_arrays:
                out.append(("fail", f"{cwhere}: light array {source.get('name')!r} is not in light_arrays"))
            elif kind == "other" and not source.get("note"):
                out.append(("fail", f"{cwhere}: source kind other needs a note saying what is wired there"))

    for name, pd in photodetectors.items():
        where = f"photodetectors.{name}"
        out += _shared_ref_findings(where, pd, items, hostname)
        if "daq_channel" in pd:
            found, channel = _daq_channel_findings(f"{where}.daq_channel", pd["daq_channel"], daqs)
            out += found
            source = (channel or {}).get("source") or {}
            if channel is not None and (source.get("kind"), source.get("name")) != ("photodetector", name):
                out.append(("fail", f"{where}.daq_channel: {pd['daq_channel'].get('daq')}/{channel.get('id')} is wired to "
                                    f"{source.get('kind')} {source.get('name') or ''}, not this photodetector".rstrip()))
        resolved = resolve_shared(pd, items)
        calibration = resolved.get("calibration")
        if isinstance(calibration, dict):
            out += calibration_findings(where, calibration, today)
            fitted, calibrated = _filter_key(pd.get("filter")), _filter_key(calibration.get("filter"))
            if calibrated is None:
                out.append(("fail", f"{where}: calibration does not record the filter it was calibrated with"))
            elif fitted != calibrated:
                out.append(("fail", f"{where}: fitted filter is {_describe_filter(fitted)} but it was calibrated with "
                                    f"{_describe_filter(calibrated)}; the calibration no longer applies (recalibrate or refit)"))

    for name, la in light_arrays.items():
        where = f"light_arrays.{name}"
        pdu = pdus.get(la.get("pdu"))
        outlets = (pdu or {}).get("outlets") or {}
        outlet = next((o for k, o in outlets.items() if str(k) == str(la.get("outlet"))), None)
        if pdu is None:
            out.append(("fail", f"{where}: pdu {la.get('pdu')!r} is not in pdus"))
        elif not isinstance(outlet, dict):
            out.append(("fail", f"{where}: pdu {la.get('pdu')!r} has no outlet {la.get('outlet')!r}"))
        elif outlet.get("name") != name:
            out.append(("fail", f"{where}: {la.get('pdu')} outlet {la.get('outlet')} is named {outlet.get('name')!r}; "
                                f"the light array and its outlet must share a name"))
        strobe = la.get("strobe") if isinstance(la.get("strobe"), dict) else {}
        source = strobe.get("source")
        if source not in STROBE_SOURCES:
            out.append(("fail", f"{where}.strobe.source must be one of {', '.join(STROBE_SOURCES)}, found {source!r}"))
        elif source == "camera_gpio" and str(strobe.get("camera_serial")) not in serials:
            out.append(("fail", f"{where}.strobe: camera_serial {strobe.get('camera_serial')!r} is not in cameras"))
        elif source == "daq":
            out += _daq_channel_findings(f"{where}.strobe.daq_channel", strobe.get("daq_channel"), daqs)[0]
    return out


def validate_shared(doc: dict[str, Any], today: date | None = None) -> tuple[list[str], list[str]]:
    """(errors, warnings) for an equipment/shared.yml document: schema, unique ids, calibration dates."""
    errors: list[str] = []
    try:
        import jsonschema
    except ImportError:
        jsonschema = None
    if jsonschema is not None:
        schema = json.loads(SHARED_SCHEMA_PATH.read_text())
        errors += [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}"
                   for e in sorted(jsonschema.Draft202012Validator(schema).iter_errors(doc),
                                   key=lambda e: list(e.absolute_path))]
    elif (doc.get("schema") or {}) != {"name": "shared_equipment", "version": 1}:
        errors.append("schema must be {name: shared_equipment, version: 1}")
    warnings: list[str] = []
    seen: set[str] = set()
    for i, item in enumerate(_list(doc.get("items"))):
        iid = item.get("id")
        if not isinstance(iid, str) or not iid:
            errors.append(f"items[{i}]: id is required")
            continue
        if iid in seen:
            errors.append(f"items: id {iid!r} is used twice")
        seen.add(iid)
        for field in ("serial_number", "description", "home_location", "current_location"):
            if jsonschema is None and item.get(field) in (None, ""):
                errors.append(f"items.{iid}: {field} is required")
        for level, message in calibration_findings(f"items.{iid}", item.get("calibration"), today or date.today()):
            (errors if level == "fail" else warnings).append(message)
    return errors, warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="validate equipment/shared.yml and a host's instrument sections")
    parser.add_argument("--shared", type=Path, default=SHARED_PATH)
    parser.add_argument("--host", help="also check hosts/HOST/config.yml's daqs, photodetectors and light_arrays")
    args = parser.parse_args(argv)
    shared = load_shared(args.shared)
    errors, warnings = validate_shared(shared)
    if args.host:
        config = yaml.safe_load(hostconfig.host_file("config.yml", args.host).read_text()) or {}
        for level, message in findings(config, shared):
            (errors if level == "fail" else warnings).append(message)
    for w in warnings:
        print(f"WARN: {w}", file=sys.stderr)
    for e in errors:
        print(f"ERROR: {e}", file=sys.stderr)
    if not errors:
        print(f"OK: {args.shared}" + (f" and hosts/{args.host} instruments" if args.host else ""))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
