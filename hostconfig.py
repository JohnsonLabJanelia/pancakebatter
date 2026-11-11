"""Resolve the host configuration (hosts/<hostname>/config.yml and its installed copy).

Two kinds of reader, two resolvers:

* pancakebatter's own tools edit and verify the CHECKOUT copy: ``default_config_path()``.
  ``PANCAKEBATTER_HOST`` picks another machine's files; ``PANCAKEBATTER_HOST_CONFIG`` names an
  explicit file and wins over everything.
* other programs on the machine (citrus, orange, ...) read the INSTALLED copy and must never guess a
  checkout path: ``resolve_host_config()`` = ``$PANCAKEBATTER_HOST_CONFIG`` > ``/etc/pancakebatter/host.yml``
  > error. ``install_host_config.sh`` publishes the checkout file there (root-owned copy) and
  ``rig_health_check.py`` warns when the two differ.

Command line (for shell scripts and other languages)::

    python3 hostconfig.py            # consumer resolution; exit 1 with guidance if nothing is installed
    python3 hostconfig.py --checkout # the checkout file pancakebatter edits

See docs/host_config_interface.md for the stable keys and the consumer contract.
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent
HOSTS_DIR = REPO_ROOT / "hosts"
ENV_HOST = "PANCAKEBATTER_HOST"
ENV_CONFIG = "PANCAKEBATTER_HOST_CONFIG"
INSTALLED_CONFIG = Path("/etc/pancakebatter/host.yml")
INSTALLED_SOURCE = Path("/etc/pancakebatter/host.yml.source")  # sidecar: where the copy came from


# 2020-12 keywords Draft 7 ignores or reads differently. Draft 7 also ignores keywords next to $ref
# and reads an array-valued `items` as a tuple.
DRAFT7_UNSAFE_KEYWORDS = frozenset({"prefixItems", "unevaluatedItems", "unevaluatedProperties", "dependentRequired",
                                    "dependentSchemas", "$anchor", "$dynamicRef", "$dynamicAnchor", "minContains",
                                    "maxContains"})
REF_ANNOTATIONS = frozenset({"$ref", "description", "title", "$comment"})


def draft7_unsafe(schema: Any, path: str = "") -> list[str]:
    """Where a 2020-12 schema would validate less under Draft 7 (empty = Draft 7 checks the same)."""
    out: list[str] = []
    if isinstance(schema, dict):
        out += [f"{path or '/'}: {k}" for k in sorted(DRAFT7_UNSAFE_KEYWORDS & set(schema))]
        if "$ref" in schema and set(schema) - REF_ANNOTATIONS:
            out.append(f"{path or '/'}: $ref with {sorted(set(schema) - REF_ANNOTATIONS)}")
        if isinstance(schema.get("items"), list):
            out.append(f"{path or '/'}: items as an array")
        for k, v in schema.items():
            out += draft7_unsafe(v, f"{path}/{k}")
    elif isinstance(schema, list):
        for i, v in enumerate(schema):
            out += draft7_unsafe(v, f"{path}/{i}")
    return out


def schema_validator(jsonschema, schema: dict[str, Any]):
    """A validator for a 2020-12 schema: Draft 2020-12, or Draft 7 on jsonschema < 4 (Ubuntu 22.04's
    python3-jsonschema 3.2) only when Draft 7 checks the schema the same. Never a weaker check: raises
    RuntimeError instead."""
    if hasattr(jsonschema, "Draft202012Validator"):
        return jsonschema.Draft202012Validator(schema)
    unsafe = draft7_unsafe(schema)
    if unsafe:
        raise RuntimeError(f"jsonschema {getattr(jsonschema, '__version__', '?')} has no Draft 2020-12 validator and "
                           f"Draft 7 would not check {', '.join(unsafe)}; install jsonschema >= 4")
    return jsonschema.Draft7Validator(schema)


def host_name() -> str:
    return os.environ.get(ENV_HOST) or socket.gethostname().split(".")[0]


def host_dir(host: str | None = None) -> Path:
    return HOSTS_DIR / (host or host_name())


def host_file(name: str, host: str | None = None) -> Path:
    return host_dir(host) / name


def explicit_config_path() -> Path | None:
    value = os.environ.get(ENV_CONFIG, "").strip()
    return Path(value) if value else None


def default_config_path(host: str | None = None) -> Path:
    """The file pancakebatter's tools read and edit: $PANCAKEBATTER_HOST_CONFIG if set, else the checkout copy."""
    return explicit_config_path() or host_file("config.yml", host)


def resolve_host_config(installed: Path = INSTALLED_CONFIG) -> Path:
    """Consumer resolution: $PANCAKEBATTER_HOST_CONFIG, else the installed copy, else FileNotFoundError.

    Deliberately never falls back to a checkout under someone's home directory.
    """
    explicit = explicit_config_path()
    if explicit:
        if not explicit.is_file():
            raise FileNotFoundError(f"{ENV_CONFIG}={explicit} does not exist")
        return explicit
    if installed.is_file():
        return installed
    raise FileNotFoundError(
        f"no host configuration: set {ENV_CONFIG} or install one with 'sudo ./install_host_config.sh' "
        f"from the pancakebatter checkout (expected {installed})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="print the resolved host configuration path")
    parser.add_argument("--checkout", action="store_true", help="the checkout file pancakebatter edits, not the installed copy")
    args = parser.parse_args(argv)
    try:
        print(default_config_path() if args.checkout else resolve_host_config())
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
