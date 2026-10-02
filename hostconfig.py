"""Resolve per-host files under hosts/<hostname>/.

Every machine keeps its inventory and system config in ``hosts/<hostname>/``
(``config.yml``, ``nvidia_driver_upgrade.json``, ...). The host is the short
hostname unless ``PANCAKEBATTER_HOST`` is set, which lets you read or edit
another machine's files (e.g. drafting dumpling's config from elsewhere).
"""
from __future__ import annotations

import os
import socket
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
HOSTS_DIR = REPO_ROOT / "hosts"


def host_name() -> str:
    return os.environ.get("PANCAKEBATTER_HOST") or socket.gethostname().split(".")[0]


def host_dir(host: str | None = None) -> Path:
    return HOSTS_DIR / (host or host_name())


def host_file(name: str, host: str | None = None) -> Path:
    return host_dir(host) / name


def default_config_path(host: str | None = None) -> Path:
    return host_file("config.yml", host)
