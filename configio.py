"""Load, validate and safely save hosts/<hostname>/config*.yml (shared by the capture and editor tools)."""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

import hostconfig

SCHEMA_PATH = hostconfig.REPO_ROOT / "schemas" / "system_config.v1.schema.json"


def validate(config: dict[str, Any]) -> list[str]:
    try:
        import jsonschema
    except ImportError:
        return validate_with_repo_checker(config)
    schema = json.loads(SCHEMA_PATH.read_text())
    return [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}"
            for e in sorted(jsonschema.Draft202012Validator(schema).iter_errors(config), key=lambda e: list(e.absolute_path))]


def validate_with_repo_checker(config: dict[str, Any]) -> list[str]:
    """Fallback when jsonschema is missing: reuse check_network_settings' v1 checks, quietly."""
    import check_network_settings as cns

    class QuietReporter(cns.Reporter):
        def section(self, title): pass
        def ok(self, message): pass
        def warn(self, message): self.warnings.append(message)
        def error(self, message): self.errors.append(message)

    rep = QuietReporter()
    cns.check_schema_metadata(config, rep)
    cns.validate_system_config_v1(cns.parse_nic_configs(config), cns.parse_camera_configs(config), rep)
    return rep.errors


def load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text()) or {}


def save(path: Path, config: dict[str, Any]) -> list[str]:
    """Validate then write. Returns schema errors (file untouched) or [] on success.

    Keeps the file's leading '#' comment lines and a timestamped backup of the old file.
    """
    errors = validate(config)
    if errors:
        return errors
    header = ""
    if path.exists():
        lines = path.read_text().splitlines(keepends=True)
        n = 0
        while n < len(lines) and lines[n].startswith("#"):
            n += 1
        header = "".join(lines[:n])
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy2(path, path.with_name(f"{path.name}.bak.{stamp}"))
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(header + yaml.safe_dump(config, sort_keys=False, width=120))
    tmp.replace(path)
    return []
