#!/usr/bin/env python3
"""Rig physical/optical declarations (rig_config v1): rigs/<rig_id>/rig.yml.

One implementation of the parity rules, so Citrus, Orange and Palette never compute them
differently (rules: rigs/README.md, schema: schemas/rig_config.v1.schema.json).

  python3 rigconfig.py rigs/omnifin0/rig.yml     validate; print derived values and the physical digest

Every physical field is either a declared value or the string "unknown". A product with any
unknown factor is "unknown"; it never falls back to a default.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parent
RIGS = REPO / "rigs"
UNKNOWN = "unknown"

PROJECTION_ENUMS = {
    "projection_mode": ("front", "rear"),
    "projector_mount": ("table", "ceiling"),
    "projector_firmware_flip": ("none", "horizontal", "vertical", "both"),
}
CAMERA_ENUMS = {"viewing_side": ("dorsal", "ventral")}
# The camera has no such node (e.g. HB-20000-SB has no ReverseX/ReverseY): it cannot flip, parity +1.
NOT_SUPPORTED = "not_supported_by_firmware"
ROTATIONS = (0, 90, 180, 270)
PARITIES = (1, -1, UNKNOWN)
HANDEDNESS = ("preserved", "reflected", UNKNOWN)

# The fields whose change bumps rig_geometry_revision (rigs/<id>/revisions.json pins their digest).
PROJECTION_PHYSICAL = ("projection_mode", "projector_mount", "projector_mount_rotation_deg_about_optical_axis",
                       "projector_firmware_flip", "optical_mirror_count")
CAMERA_PHYSICAL = ("viewing_side", "optical_mirror_count", "reverse_x", "reverse_y", "rotation_deg")
# Projector menu state that changes where or how the canvas lands (README "Projector settings").
# Vocabulary is the BenQ X3100i menu; position_effective is what is applied (the menu may say "auto").
PROJECTOR_SETTINGS_ENUMS = {
    "position_menu": ("auto", "front", "front_ceiling", "rear", "rear_ceiling"),
    "position_effective": ("front", "front_ceiling", "rear", "rear_ceiling"),
    "aspect_ratio": ("auto", "4:3", "16:9"),
    "upscaling_4k": ("on", "off"),
}
PROJECTOR_SETTINGS_RANGES = {"keystone_h": (-30, 30), "keystone_v": (-30, 30), "digital_zoom_percent": (80, 100)}
PROJECTOR_SETTINGS = tuple(PROJECTOR_SETTINGS_ENUMS) + tuple(PROJECTOR_SETTINGS_RANGES)
# BenQ Projector Position -> net firmware mirroring (BenQ convention; the manual does not state it).
POSITION_FLIP = {"front": "none", "rear": "horizontal", "front_ceiling": "both", "rear_ceiling": "vertical"}
# Optional; recorded for the deferred parallax model, consumed by no runtime today.
CAMERA_NOMINAL = ("height_above_gel_mm", "optical_axis_tilt_from_nadir_deg")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _product(factors: list[Any]) -> int | str:
    if any(f == UNKNOWN for f in factors):
        return UNKNOWN
    p = 1
    for f in factors:
        p *= f
    return p


def _mirror_parity(count: Any) -> int | str:
    return UNKNOWN if count == UNKNOWN else (-1 if count % 2 else 1)


def _flag_parity(flag: Any) -> int | str:
    if flag == UNKNOWN:
        return UNKNOWN
    return -1 if flag is True else 1   # False or NOT_SUPPORTED


def canvas_space_parity(path: dict[str, Any]) -> int | str:
    """The canvas as it lands on the dish seen from above, relative to citrus.display_canvas_px as
    scanned out with an unreflected xrandr output. Rotations (projector_mount, mount rotation) are +1."""
    mode = {"front": 1, "rear": -1}.get(path["projection_mode"], UNKNOWN)
    flip = {"none": 1, "horizontal": -1, "vertical": -1, "both": 1}.get(path["projector_firmware_flip"], UNKNOWN)
    return _product([mode, flip, _mirror_parity(path["optical_mirror_count"])])


def recorded_space_parity(camera: dict[str, Any]) -> int | str:
    """camera.recorded_px relative to the dish seen from above. rotation_deg is +1."""
    side = {"dorsal": 1, "ventral": -1}.get(camera["viewing_side"], UNKNOWN)
    return _product([side, _mirror_parity(camera["optical_mirror_count"]),
                     _flag_parity(camera["reverse_x"]), _flag_parity(camera["reverse_y"])])


def handedness(recorded: int | str, canvas: int | str) -> str:
    """Expected handedness of a camera.recorded_px -> citrus.display_canvas_px homography."""
    p = _product([recorded, canvas])
    return p if p == UNKNOWN else ("preserved" if p == 1 else "reflected")


def derived(doc: dict[str, Any]) -> dict[str, Any]:
    paths = {pid: canvas_space_parity(p) for pid, p in doc["projection_paths"].items()}
    cams = {}
    for serial, cam in doc["cameras"].items():
        rec = recorded_space_parity(cam)
        # Per projection path, not per arena: which canvas region a camera sees is Citrus's
        # registered geometry, not a rig fact.
        cams[serial] = {"recorded_space_parity": rec,
                        "expected_camera_to_canvas_handedness": {pid: handedness(rec, v) for pid, v in paths.items()}}
    return {"projection_paths": {pid: {"canvas_space_parity": v} for pid, v in paths.items()}, "cameras": cams}


def physical_fields(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "rig_id": doc["rig_id"],
        "projection_paths": {pid: {**{k: p[k] for k in PROJECTION_PHYSICAL},
                                   "projector_settings": {k: p["projector_settings"][k] for k in PROJECTOR_SETTINGS}}
                             for pid, p in doc["projection_paths"].items()},
        "cameras": {s: {**{k: c[k] for k in CAMERA_PHYSICAL},
                        **({"nominal_geometry": c["nominal_geometry"]} if "nominal_geometry" in c else {})}
                    for s, c in doc["cameras"].items()},
    }


def physical_digest(doc: dict[str, Any]) -> str:
    canon = json.dumps(physical_fields(doc), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode()).hexdigest()


def _count_ok(v: Any) -> bool:
    return v == UNKNOWN or (isinstance(v, int) and not isinstance(v, bool) and v >= 0)


def _evidence_errors(where: str, ev: Any) -> list[str]:
    if not isinstance(ev, dict):
        return [f"{where}.evidence: missing"]
    errs = [f"{where}.evidence.{k}: missing or empty" for k in ("note", "declared_by") if not ev.get(k)]
    if not (isinstance(ev.get("declared_at"), str) and DATE.match(ev["declared_at"])):
        errs.append(f"{where}.evidence.declared_at: quoted YYYY-MM-DD required")
    return errs


def _projector_settings_errors(where: str, p: dict[str, Any]) -> list[str]:
    ps = p.get("projector_settings")
    where = f"{where}.projector_settings"
    if not isinstance(ps, dict):
        return [f"{where}: required (every key, unknown allowed)"]
    extra = sorted(set(ps) - set(PROJECTOR_SETTINGS))
    errs = [f"{where}: unexpected keys {extra}"] if extra else []
    for k, allowed in PROJECTOR_SETTINGS_ENUMS.items():
        if ps.get(k) not in allowed + (UNKNOWN,):
            errs.append(f"{where}.{k}: one of {allowed + (UNKNOWN,)}")
    for k, (lo, hi) in PROJECTOR_SETTINGS_RANGES.items():
        v = ps.get(k)
        if not (v == UNKNOWN or (isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi)):
            errs.append(f"{where}.{k}: integer {lo}..{hi} or unknown")
    menu, eff = ps.get("position_menu"), ps.get("position_effective")
    if menu in POSITION_FLIP and eff != menu:
        errs.append(f"{where}.position_effective: must equal position_menu ({menu!r}) unless the menu is auto")
    if eff in POSITION_FLIP and p.get("projector_firmware_flip") != POSITION_FLIP[eff]:
        errs.append(f"{where}: position_effective {eff!r} means projector_firmware_flip {POSITION_FLIP[eff]!r}, "
                    f"not {p.get('projector_firmware_flip')!r}")
    return errs


def validate(doc: Any) -> list[str]:
    """Structural checks plus: stored derived values equal the computed ones."""
    if not isinstance(doc, dict):
        return ["document is not a mapping"]
    errs: list[str] = []
    if doc.get("schema") != {"name": "rig_config", "version": 1}:
        errs.append("schema: must be {name: rig_config, version: 1}")
    if not (isinstance(doc.get("rig_id"), str) and re.match(r"^[a-z0-9_]+$", doc["rig_id"])):
        errs.append("rig_id: lowercase [a-z0-9_]+ required")
    rev = doc.get("rig_geometry_revision")
    if not (isinstance(rev, int) and not isinstance(rev, bool) and rev >= 1):
        errs.append("rig_geometry_revision: integer >= 1 required")
    if not doc.get("description"):
        errs.append("description: required")
    lpc = doc.get("last_physical_change")
    if not (lpc == UNKNOWN or (isinstance(lpc, str) and DATE.match(lpc))):
        errs.append("last_physical_change: quoted YYYY-MM-DD or unknown")
    prov = doc.get("provenance")
    if not isinstance(prov, dict) or not prov.get("declared_by") or not DATE.match(str(prov.get("declared_at", ""))):
        errs.append("provenance: declared_by and quoted declared_at (YYYY-MM-DD) required")

    paths = doc.get("projection_paths")
    if not isinstance(paths, dict) or not paths:
        return errs + ["projection_paths: at least one required"]
    for pid, p in paths.items():
        where = f"projection_paths.{pid}"
        if not isinstance(p, dict):
            errs.append(f"{where}: not a mapping")
            continue
        for k, allowed in PROJECTION_ENUMS.items():
            if p.get(k) not in allowed + (UNKNOWN,):
                errs.append(f"{where}.{k}: one of {allowed + (UNKNOWN,)}")
        if p.get("projector_mount_rotation_deg_about_optical_axis") not in ROTATIONS + (UNKNOWN,):
            errs.append(f"{where}.projector_mount_rotation_deg_about_optical_axis: one of {ROTATIONS} or unknown")
        if not _count_ok(p.get("optical_mirror_count")):
            errs.append(f"{where}.optical_mirror_count: integer >= 0 or unknown")
        errs += _projector_settings_errors(where, p)
        errs += _evidence_errors(where, p.get("evidence"))

    cams = doc.get("cameras")
    if not isinstance(cams, dict) or not cams:
        return errs + ["cameras: at least one required"]
    for serial, c in cams.items():
        where = f"cameras.{serial}"
        if not (isinstance(serial, str) and serial.isdigit()):
            errs.append(f"{where}: key must be the camera serial_number as a quoted string")
        if not isinstance(c, dict):
            errs.append(f"{where}: not a mapping")
            continue
        for k, allowed in CAMERA_ENUMS.items():
            if c.get(k) not in allowed + (UNKNOWN,):
                errs.append(f"{where}.{k}: one of {allowed + (UNKNOWN,)}")
        if not _count_ok(c.get("optical_mirror_count")):
            errs.append(f"{where}.optical_mirror_count: integer >= 0 or unknown")
        for k in ("reverse_x", "reverse_y"):
            if not (isinstance(c.get(k), bool) or c.get(k) in (UNKNOWN, NOT_SUPPORTED)):
                errs.append(f"{where}.{k}: true, false, {NOT_SUPPORTED} or unknown")
        if isinstance(c.get("rotation_deg"), bool) or c.get("rotation_deg") not in ROTATIONS + (UNKNOWN,):
            errs.append(f"{where}.rotation_deg: one of {ROTATIONS} or unknown")
        nominal = c.get("nominal_geometry", {})
        if not isinstance(nominal, dict) or set(nominal) - set(CAMERA_NOMINAL):
            errs.append(f"{where}.nominal_geometry: only {CAMERA_NOMINAL}")
        else:
            for k, v in nominal.items():
                if not (v == UNKNOWN or (isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0)):
                    errs.append(f"{where}.nominal_geometry.{k}: number >= 0 or unknown")
        errs += _evidence_errors(where, c.get("evidence"))
    if errs:
        return errs

    want = derived(doc)
    for pid, p in paths.items():
        got = (p.get("derived") or {}).get("canvas_space_parity")
        if got != want["projection_paths"][pid]["canvas_space_parity"]:
            errs.append(f"projection_paths.{pid}.derived.canvas_space_parity: {got!r}, computed "
                        f"{want['projection_paths'][pid]['canvas_space_parity']!r}")
    for serial, c in cams.items():
        for k, v in want["cameras"][serial].items():
            got = (c.get("derived") or {}).get(k)
            if got != v:
                errs.append(f"cameras.{serial}.derived.{k}: {got!r}, computed {v!r}")
    return errs


def validate_binding(host: str, host_cfg: dict[str, Any], rig_docs: dict[str, dict[str, Any]]) -> list[str]:
    """hosts/<host>/config.yml `rigs:` block against the rig documents it names."""
    errs = []
    serials = {str(c.get("serial_number")) for c in (host_cfg.get("cameras") or {}).values()}
    outputs_seen: set[str] = set()
    for i, b in enumerate(host_cfg.get("rigs") or []):
        rid = b.get("rig_id")
        doc = rig_docs.get(rid)
        if doc is None:
            errs.append(f"{host}: rigs[{i}].rig_id {rid!r} has no rigs/{rid}/rig.yml")
            continue
        for s in doc["cameras"]:
            if s not in serials:
                errs.append(f"{host}: rig {rid} camera {s} is not in this host's cameras")
        used = set()
        for o in b.get("display_outputs") or []:
            name, pid = o.get("output_name"), o.get("projection_path")
            if not name:
                errs.append(f"{host}: rig {rid} display output without output_name")
            elif name in outputs_seen:
                errs.append(f"{host}: display output {name} bound twice")
            outputs_seen.add(name)
            if pid not in doc["projection_paths"]:
                errs.append(f"{host}: rig {rid} output {name} names unknown projection_path {pid!r}")
            used.add(pid)
        missing = sorted(set(doc["projection_paths"]) - used)
        if missing:
            errs.append(f"{host}: rig {rid} projection_paths not driven by any output: {missing}")
    return errs


def load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(Path(path).read_text())


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__.strip().splitlines()[3], file=sys.stderr)
        return 2
    doc = load(Path(argv[0]))
    errs = validate(doc)
    for e in errs:
        print(f"  {e}", file=sys.stderr)
    if isinstance(doc, dict) and "projection_paths" in doc and "cameras" in doc:
        try:
            print(json.dumps(derived(doc), indent=2))
            print(f"physical_sha256: {physical_digest(doc)}")
        except (KeyError, TypeError) as e:
            print(f"cannot derive: {e!r}", file=sys.stderr)
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
