"""Merge freshly captured hardware facts into an existing, human-edited config.

Used by `capture_inventory.py --refresh`. Only hardware/software *facts* are updated:
NIC identity (brand/model/serials/PCIe/driver/firmware), transceiver modules, storage,
GPUs and system_info. Everything a person decided (role, managed, expected_link,
ip_address, mtu, altname, link_settings, card, cameras, pdus, kernel_tuning) is untouched.

Refresh never erases: a missing reading usually means "could not read" (e.g. no root
access), not "removed", so an existing value is only replaced by a newer non-empty one.
Anything that no longer shows up is reported, not deleted.
"""
from __future__ import annotations

import copy
from typing import Any

NIC_FACT_FIELDS = ("brand", "model", "part_number", "serial_number", "pcie_id", "pcie_slot",
                   "subsystem", "driver", "firmware_version")
TRANSCEIVER_FIELDS = ("brand", "model", "serial_number", "speed", "wavelength", "max_distance", "fiber_type")
SYSTEM_SKIP = {"hostname", "network_renderer"}  # identity / human-set; never refreshed


class RefreshError(Exception):
    pass


def _mac(nic: dict[str, Any]) -> str:
    return str(nic.get("mac_address") or "").lower()


def _set(target: dict[str, Any], key: str, new: Any, label: str, changes: list[str]) -> None:
    """Assign new into target[key] if it is non-empty and different; record the change."""
    if new is None or target.get(key) == new:
        return
    changes.append(f"{label}: {target.get(key)!r} -> {new!r}")
    target[key] = new


def merge_facts(existing: dict[str, Any], fresh: dict[str, Any]) -> tuple[dict[str, Any], list[str], list[str]]:
    """Return (merged config, changes, notes). `existing` is not modified."""
    old_host = (existing.get("system_info") or {}).get("hostname")
    new_host = (fresh.get("system_info") or {}).get("hostname")
    if old_host and new_host and old_host != new_host:
        raise RefreshError(f"config is for host {old_host!r} but this machine is {new_host!r}; "
                           "refresh must run on the machine the config describes")

    merged = copy.deepcopy(existing)
    changes: list[str] = []
    notes: list[str] = []

    # --- system_info
    info = merged.setdefault("system_info", {})
    for key, value in (fresh.get("system_info") or {}).items():
        if key not in SYSTEM_SKIP:
            _set(info, key, value, f"system_info.{key}", changes)

    # --- NICs, matched by MAC address (names are human-chosen and may differ)
    fresh_by_mac = {}
    for item in fresh.get("nics", []):
        (name, nic), = item.items()
        fresh_by_mac[_mac(nic)] = (name, nic)
    seen = set()
    for item in merged.get("nics", []):
        (name, nic), = item.items()
        mac = _mac(nic)
        if mac not in fresh_by_mac:
            notes.append(f"{name} ({mac}) is in the config but was not detected")
            continue
        seen.add(mac)
        _, new = fresh_by_mac[mac]
        for field in NIC_FACT_FIELDS:
            _set(nic, field, new.get(field), f"{name}.{field}", changes)
        trx = nic.setdefault("transceiver", {})
        for field in TRANSCEIVER_FIELDS:
            _set(trx, field, (new.get("transceiver") or {}).get(field), f"{name}.transceiver.{field}", changes)
        if not (new.get("transceiver") or {}).get("brand") and trx.get("brand"):
            notes.append(f"{name}: no module read now; kept recorded {trx.get('brand')} {trx.get('model')} "
                         "(re-run with the root probe installed, or edit it in the GUI if it was removed)")
    for mac, (name, _) in fresh_by_mac.items():
        if mac not in seen:
            notes.append(f"new NIC {name} ({mac}) found but not added; use suggest_nic_layout.py or the GUI")

    # --- storage: match by serial number, update captured keys, keep extras (e.g. benchmark speeds)
    _merge_list(merged, fresh, "storage_devices", "serial_number", "name", changes, notes)
    # --- GPUs: match by uuid when the config has them, otherwise take the fresh list
    _merge_list(merged, fresh, "gpus", "uuid", "model", changes, notes)
    return merged, changes, notes


def _merge_list(merged: dict[str, Any], fresh: dict[str, Any], section: str, key: str, label_key: str,
                changes: list[str], notes: list[str]) -> None:
    old_items = merged.get(section) or []
    new_items = fresh.get(section) or []
    if not new_items:
        if old_items:
            notes.append(f"{section}: nothing detected now; kept {len(old_items)} recorded item(s)")
        return
    if old_items and not any(i.get(key) for i in old_items):
        changes.append(f"{section}: replaced {len(old_items)} item(s) with {len(new_items)} detected "
                       f"(recorded items had no {key} to match on)")
        merged[section] = copy.deepcopy(new_items)
        return
    by_key = {i.get(key): i for i in old_items if i.get(key)}
    matched = set()
    for new in new_items:
        old = by_key.get(new.get(key))
        label = f"{section}[{new.get(label_key)}]"
        if old is None:
            old_items.append(copy.deepcopy(new))
            changes.append(f"{label}: new {section.rstrip('s')} added ({new.get(key)})")
            continue
        matched.add(new.get(key))
        for field, value in new.items():
            _set(old, field, value, f"{label}.{field}", changes)
    for k, old in by_key.items():
        if k not in matched:
            notes.append(f"{section}: {old.get(label_key)} ({k}) is recorded but was not detected")
    merged[section] = old_items
