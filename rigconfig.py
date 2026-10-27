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
# Optional picture settings (Picture menu): they change how bright the canvas is, not where it lands.
# Physical when present; a rig that omits them keeps its digest. None = no upper bound stated.
PROJECTOR_PICTURE_RANGES = {"brightness": (0, 100), "contrast": (0, 100), "sharpness": (0, None)}
# BenQ Projector Position -> net firmware mirroring (BenQ convention; the manual does not state it).
POSITION_FLIP = {"front": "none", "rear": "horizontal", "front_ceiling": "both", "rear_ceiling": "vertical"}
# Optional; recorded for the deferred parallax model, consumed by no runtime today.
CAMERA_NOMINAL = ("height_above_gel_mm", "optical_axis_tilt_from_nadir_deg")
# Optional, per projection path: the canvas point straight above the lens front glass (not where the
# optical axis lands) and the lens-glass-to-film height. Physical when present; consumed by nothing yet.
PROJECTION_NOMINAL = ("lens_nadir_canvas_px", "lens_nadir_uncertainty_px", "lens_glass_to_film_mm")
# Optional room_orientation (README "Room orientation"): canvas axes in the operator's frame, standing at
# the door looking at the rig from above.
ROOM_DIRECTIONS = ("toward_operator", "away_from_operator", "operator_left", "operator_right")
ROOM_AXES = {"toward_operator": "depth", "away_from_operator": "depth", "operator_left": "width",
             "operator_right": "width"}
CANVAS_DIRECTIONS = ("canvas_plus_x", "canvas_minus_x", "canvas_plus_y", "canvas_minus_y")
ROOM_KEYS = ("canvas_minus_x", "canvas_minus_y", "projector_offset_toward", "evidence")
# Optional media_stack (README "Media stack"): the layers light crosses from the projector to the fish.
MEDIA = ("air", "water", "acrylic", "polystyrene", "glass", "film", "other")
LAYER_PHYSICAL = ("medium", "part_id", "thickness_mm", "refractive_index")
LAYER_KEYS = ("id",) + LAYER_PHYSICAL + ("refractive_index_source", "evidence")
ARENA_OVERRIDES = ("part_id", "thickness_mm", "refractive_index")
STACK_ASSUMPTIONS = ("fish_eye_height_above_dish_floor_mm",)
STACK_KEYS = ("air_between_film_and_fish", "layers", "arenas", "assumptions", "evidence", "derived")
LAYER_ID = re.compile(r"^[a-z0-9_]+$")
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


def arena_layers(stack: dict[str, Any], arena: str) -> list[dict[str, Any]]:
    """The stack's layers with one arena's overrides applied."""
    overrides = (stack.get("arenas") or {}).get(arena) or {}
    return [{**layer, **(overrides.get(layer["id"]) or {})} for layer in stack["layers"]]


def eye_to_film(layers: list[dict[str, Any]], eye_height: Any) -> dict[str, Any]:
    """Distance from the film's fish-side face to the fish eye, which sits eye_height above the floor of
    the last layer (the water it swims in). Layers between film and that water count whole.

    eye_to_film_mm is the geometric sum. eye_to_film_paraxial_mm is the distance a film feature would
    have if every layer were the eye's medium, n_eye * sum(t_i / n_i): with flat parallel layers and
    small angles, the visual angle at the eye of a feature at lateral offset x is x / that distance."""
    film = next(i for i, layer in enumerate(layers) if layer["medium"] == "film")
    between, eye_medium = layers[film + 1:-1], layers[-1]
    thicknesses = [layer["thickness_mm"] for layer in between] + [eye_height]
    if UNKNOWN in thicknesses:
        return {"eye_to_film_mm": UNKNOWN, "eye_to_film_paraxial_mm": UNKNOWN}
    geometric = round(sum(thicknesses), 3)
    indices = [layer["refractive_index"] for layer in between] + [eye_medium["refractive_index"]]
    if UNKNOWN in indices:
        return {"eye_to_film_mm": geometric, "eye_to_film_paraxial_mm": UNKNOWN}
    n_eye = eye_medium["refractive_index"]
    paraxial = n_eye * sum(layer["thickness_mm"] / layer["refractive_index"] for layer in between) + eye_height
    return {"eye_to_film_mm": geometric, "eye_to_film_paraxial_mm": round(paraxial, 3)}


def derived_media_stack(stack: dict[str, Any]) -> dict[str, Any]:
    eye = (stack.get("assumptions") or {}).get("fish_eye_height_above_dish_floor_mm", UNKNOWN)
    return {arena: eye_to_film(arena_layers(stack, arena), eye) for arena in stack["arenas"]}


def derived(doc: dict[str, Any]) -> dict[str, Any]:
    paths = {pid: canvas_space_parity(p) for pid, p in doc["projection_paths"].items()}
    cams = {}
    for serial, cam in doc["cameras"].items():
        rec = recorded_space_parity(cam)
        # Per projection path, not per arena: which canvas region a camera sees is Citrus's
        # registered geometry, not a rig fact.
        cams[serial] = {"recorded_space_parity": rec,
                        "expected_camera_to_canvas_handedness": {pid: handedness(rec, v) for pid, v in paths.items()}}
    out = {"projection_paths": {pid: {"canvas_space_parity": v} for pid, v in paths.items()}, "cameras": cams}
    if "media_stack" in doc:
        out["media_stack"] = derived_media_stack(doc["media_stack"])
    return out


def physical_fields(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "rig_id": doc["rig_id"],
        "projection_paths": {pid: {**{k: p[k] for k in PROJECTION_PHYSICAL},
                                   "projector_settings": {k: p["projector_settings"][k]
                                                          for k in PROJECTOR_SETTINGS + tuple(PROJECTOR_PICTURE_RANGES)
                                                          if k in p["projector_settings"]},
                                   **({"nominal_geometry": p["nominal_geometry"]} if "nominal_geometry" in p else {})}
                             for pid, p in doc["projection_paths"].items()},
        "cameras": {s: {**{k: c[k] for k in CAMERA_PHYSICAL},
                        **({"nominal_geometry": c["nominal_geometry"]} if "nominal_geometry" in c else {})}
                    for s, c in doc["cameras"].items()},
        # Optional, so a rig without one keeps its digest. Evidence, sources and derived do not bump.
        **({"media_stack": {
            "air_between_film_and_fish": doc["media_stack"]["air_between_film_and_fish"],
            "layers": [{k: layer[k] for k in ("id",) + LAYER_PHYSICAL if k in layer}
                       for layer in doc["media_stack"]["layers"]],
            "arenas": doc["media_stack"]["arenas"],
            "assumptions": doc["media_stack"].get("assumptions", {}),
        }} if "media_stack" in doc else {}),
        **({"room_orientation": {k: doc["room_orientation"][k] for k in ROOM_KEYS[:3]}}
           if "room_orientation" in doc else {}),
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
    extra = sorted(set(ps) - set(PROJECTOR_SETTINGS) - set(PROJECTOR_PICTURE_RANGES))
    errs = [f"{where}: unexpected keys {extra}"] if extra else []
    for k, allowed in PROJECTOR_SETTINGS_ENUMS.items():
        if ps.get(k) not in allowed + (UNKNOWN,):
            errs.append(f"{where}.{k}: one of {allowed + (UNKNOWN,)}")
    for k, (lo, hi) in PROJECTOR_SETTINGS_RANGES.items():
        v = ps.get(k)
        if not (v == UNKNOWN or (isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi)):
            errs.append(f"{where}.{k}: integer {lo}..{hi} or unknown")
    for k, (lo, hi) in PROJECTOR_PICTURE_RANGES.items():
        v = ps.get(k, UNKNOWN)
        if not (v == UNKNOWN or (isinstance(v, int) and not isinstance(v, bool) and lo <= v
                                 and (hi is None or v <= hi))):
            bound = f"{lo}..{hi}" if hi is not None else f">= {lo}"
            errs.append(f"{where}.{k}: optional; integer {bound} or unknown")
    menu, eff = ps.get("position_menu"), ps.get("position_effective")
    if menu in POSITION_FLIP and eff != menu:
        errs.append(f"{where}.position_effective: must equal position_menu ({menu!r}) unless the menu is auto")
    if eff in POSITION_FLIP and p.get("projector_firmware_flip") != POSITION_FLIP[eff]:
        errs.append(f"{where}: position_effective {eff!r} means projector_firmware_flip {POSITION_FLIP[eff]!r}, "
                    f"not {p.get('projector_firmware_flip')!r}")
    return errs


def _number_or_unknown(v: Any, minimum: float) -> bool:
    return v == UNKNOWN or (isinstance(v, (int, float)) and not isinstance(v, bool) and v >= minimum)


def _layer_value_errors(where: str, values: dict[str, Any]) -> list[str]:
    errs = []
    if "part_id" in values and not (isinstance(values["part_id"], str) and
                                    re.match(r"^[a-z0-9_]+(\.[a-z0-9_]+)+$", values["part_id"])):
        errs.append(f"{where}.part_id: a hardware_library part id (omit while there is none)")
    if "thickness_mm" in values and not _number_or_unknown(values["thickness_mm"], 0):
        errs.append(f"{where}.thickness_mm: number >= 0 or unknown")
    if "refractive_index" in values and not _number_or_unknown(values["refractive_index"], 1):
        errs.append(f"{where}.refractive_index: number >= 1 or unknown")
    return errs


def _xy_errors(where: str, v: Any) -> list[str]:
    if v == UNKNOWN:
        return []
    if not (isinstance(v, dict) and set(v) == {"x", "y"} and all(_number_or_unknown(v[a], 0) for a in "xy")):
        return [f"{where}: {{x, y}} numbers >= 0 (or unknown), or unknown"]
    return []


def _projection_nominal_errors(where: str, p: dict[str, Any]) -> list[str]:
    if "nominal_geometry" not in p:
        return []
    nominal, where = p["nominal_geometry"], f"{where}.nominal_geometry"
    if not isinstance(nominal, dict) or set(nominal) - set(PROJECTION_NOMINAL):
        return [f"{where}: only {PROJECTION_NOMINAL}"]
    errs = []
    for k in ("lens_nadir_canvas_px", "lens_nadir_uncertainty_px"):
        if k in nominal:
            errs += _xy_errors(f"{where}.{k}", nominal[k])
    if "lens_glass_to_film_mm" in nominal and not _number_or_unknown(nominal["lens_glass_to_film_mm"], 0):
        errs.append(f"{where}.lens_glass_to_film_mm: number >= 0 or unknown")
    return errs


def _room_orientation_errors(room: Any) -> list[str]:
    where = "room_orientation"
    if not isinstance(room, dict):
        return [f"{where}: not a mapping"]
    errs = [f"{where}: required keys {list(ROOM_KEYS)}, no others"] if set(room) != set(ROOM_KEYS) else []
    for k in ("canvas_minus_x", "canvas_minus_y"):
        if room.get(k) not in ROOM_DIRECTIONS + (UNKNOWN,):
            errs.append(f"{where}.{k}: one of {ROOM_DIRECTIONS + (UNKNOWN,)}")
    mx, my = room.get("canvas_minus_x"), room.get("canvas_minus_y")
    if mx in ROOM_AXES and my in ROOM_AXES and ROOM_AXES[mx] == ROOM_AXES[my]:
        errs.append(f"{where}: canvas_minus_x {mx!r} and canvas_minus_y {my!r} are the same room axis")
    if room.get("projector_offset_toward") not in CANVAS_DIRECTIONS + ("none", UNKNOWN):
        errs.append(f"{where}.projector_offset_toward: one of {CANVAS_DIRECTIONS + ('none', UNKNOWN)}")
    return errs + _evidence_errors(where, room.get("evidence"))


def _media_stack_errors(stack: Any) -> list[str]:
    where = "media_stack"
    if not isinstance(stack, dict):
        return [f"{where}: not a mapping"]
    errs = [f"{where}: unexpected keys {sorted(set(stack) - set(STACK_KEYS))}"] if set(stack) - set(STACK_KEYS) else []
    air = stack.get("air_between_film_and_fish")
    if not (isinstance(air, bool) or air == UNKNOWN):
        errs.append(f"{where}.air_between_film_and_fish: true, false or unknown")
    layers = stack.get("layers")
    if not isinstance(layers, list) or len(layers) < 2 or not all(isinstance(x, dict) for x in layers):
        return errs + [f"{where}.layers: a list of at least two layers, projector side first, the fish's water last"]
    ids = [layer.get("id") for layer in layers]
    for i, layer in enumerate(layers):
        lw = f"{where}.layers[{i}]"
        if not (isinstance(layer.get("id"), str) and LAYER_ID.match(layer["id"])):
            errs.append(f"{lw}.id: lowercase [a-z0-9_]+ required")
        extra = sorted(set(layer) - set(LAYER_KEYS))
        if extra:
            errs.append(f"{lw}: unexpected keys {extra}")
        if layer.get("medium") not in MEDIA + (UNKNOWN,):
            errs.append(f"{lw}.medium: one of {MEDIA + (UNKNOWN,)}")
        for k in ("thickness_mm", "refractive_index"):
            if k not in layer:
                errs.append(f"{lw}.{k}: required (unknown allowed)")
        errs += _layer_value_errors(lw, layer)
        if "evidence" in layer:
            errs += _evidence_errors(lw, layer["evidence"])
    if len(set(ids)) != len(ids):
        errs.append(f"{where}.layers: ids must be unique")
    films = [i for i, layer in enumerate(layers) if layer.get("medium") == "film"]
    if len(films) != 1 or films[0] == len(layers) - 1:
        errs.append(f"{where}.layers: exactly one film layer (the projected surface), not the last")
    elif air is False and any(layer.get("medium") == "air" for layer in layers[films[0] + 1:]):
        errs.append(f"{where}: air_between_film_and_fish is false but a layer after the film is air")
    arenas = stack.get("arenas")
    if not isinstance(arenas, dict) or not arenas:
        errs.append(f"{where}.arenas: at least one arena id (overrides may be empty)")
    else:
        for arena, overrides in arenas.items():
            aw = f"{where}.arenas.{arena}"
            if not isinstance(overrides, dict):
                errs.append(f"{aw}: a mapping of layer id -> overrides ({{}} for none)")
                continue
            for lid, values in overrides.items():
                if lid not in ids:
                    errs.append(f"{aw}.{lid}: not a layer id")
                elif not isinstance(values, dict) or set(values) - set(ARENA_OVERRIDES):
                    errs.append(f"{aw}.{lid}: only {ARENA_OVERRIDES}")
                else:
                    errs += _layer_value_errors(f"{aw}.{lid}", values)
    assumptions = stack.get("assumptions", {})
    if not isinstance(assumptions, dict) or set(assumptions) - set(STACK_ASSUMPTIONS):
        errs.append(f"{where}.assumptions: only {STACK_ASSUMPTIONS}")
    else:
        eye = assumptions.get("fish_eye_height_above_dish_floor_mm", UNKNOWN)
        if not _number_or_unknown(eye, 0):
            errs.append(f"{where}.assumptions.fish_eye_height_above_dish_floor_mm: number >= 0 or unknown")
        elif eye != UNKNOWN and not errs:
            for arena in arenas:
                depth = arena_layers(stack, arena)[-1].get("thickness_mm")
                if depth != UNKNOWN and eye > depth:
                    errs.append(f"{where}: {arena} eye height {eye} mm is above its water depth {depth} mm")
    errs += _evidence_errors(where, stack.get("evidence"))
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
        errs += _projection_nominal_errors(where, p)
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
    if "media_stack" in doc:
        errs += _media_stack_errors(doc["media_stack"])
    if "room_orientation" in doc:
        errs += _room_orientation_errors(doc["room_orientation"])
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
    if "media_stack" in doc:
        got = doc["media_stack"].get("derived")
        if got != want["media_stack"]:
            errs.append(f"media_stack.derived: {got!r}, computed {want['media_stack']!r}")
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


def location_errors(path: Path, doc: dict[str, Any]) -> list[str]:
    """A rig document's rig_id must name the directory it lives in (rigs/<rig_id>/rig.yml)."""
    path = Path(path)
    if path.name == "rig.yml" and isinstance(doc, dict) and doc.get("rig_id") != path.parent.name:
        return [f"rig_id {doc.get('rig_id')!r} does not match its directory rigs/{path.parent.name}/"]
    return []


def load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(Path(path).read_text())


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__.strip().splitlines()[3], file=sys.stderr)
        return 2
    doc = load(Path(argv[0]))
    errs = validate(doc) + location_errors(Path(argv[0]), doc)
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
