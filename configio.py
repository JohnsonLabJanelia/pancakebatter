"""Load, validate and safely save hosts/<hostname>/config*.yml (shared by the capture and editor tools)."""
from __future__ import annotations

import json
import shutil
import sys
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


def _ruamel():
    """ruamel.yaml's round-trip loader/dumper when installed (apt: python3-ruamel.yaml), else None."""
    try:
        from ruamel.yaml import YAML
    except ImportError:
        return None
    rt = YAML(typ="rt")
    rt.preserve_quotes = True
    rt.width = 120
    rt.indent(mapping=2, sequence=4, offset=2)   # the hosts/*/config.yml style: "nics:\n  - name:"
    return rt


def _sync(node: Any, data: Any) -> Any:
    """Return `node` (a round-trip YAML tree with comments) updated to hold the values of `data`.

    Mappings are updated key by key so comments attached to unchanged keys survive; keys missing from
    `data` are removed and new ones appended. Equal-length lists are updated element-wise; otherwise
    the list is replaced (comments inside it are lost). Equal scalars keep the original node so quoting
    style is preserved.
    """
    if isinstance(node, dict) and isinstance(data, dict):
        for key in [k for k in node if k not in data]:
            del node[key]
        for key, value in data.items():
            node[key] = _sync(node[key], value) if key in node else value
        return node
    if isinstance(node, list) and isinstance(data, list):
        if len(node) == len(data):
            for i, value in enumerate(data):
                node[i] = _sync(node[i], value)
            return node
        return data
    if node == data and type(node) is not bool and not isinstance(node, (dict, list)):
        return node
    return data


def save(path: Path, config: dict[str, Any]) -> list[str]:
    """Validate then write. Returns schema errors (file untouched) or [] on success.

    With ruamel.yaml installed the existing file is loaded round-trip and updated in place, so its
    comments, key order and quoting survive. Without it the file is re-serialised with PyYAML: only
    the leading '#' header is kept and a warning names what was lost. A timestamped backup of the old
    file is always written first.
    """
    errors = validate(config)
    if errors:
        return errors
    text = None
    header = ""
    if path.exists():
        old_text = path.read_text()
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy2(path, path.with_name(f"{path.name}.bak.{stamp}"))
        rt = _ruamel()
        if rt is not None:
            import io
            doc = rt.load(old_text)
            doc = _sync(doc if isinstance(doc, dict) else {}, config)
            buf = io.StringIO()
            rt.dump(doc, buf)
            text = buf.getvalue()
        else:
            lines = old_text.splitlines(keepends=True)
            n = 0
            while n < len(lines) and lines[n].startswith("#"):
                n += 1
            header = "".join(lines[:n])
            lost = sum(1 for l in lines[n:] if "#" in l)
            if lost:
                print(f"warning: {path} has {lost} comment line(s) that this save will drop (PyYAML cannot keep them). "
                      f"Install ruamel.yaml for this interpreter (apt: python3-ruamel.yaml) to preserve comments; "
                      f"the .bak keeps the old file.", file=sys.stderr)
    if text is None:
        text = header + yaml.safe_dump(config, sort_keys=False, width=120)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)
    return []
