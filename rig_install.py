"""Install the rig documents a host drives next to its host.yml (run by install_host_config.sh as root).

For each rigs[].rig_id in the host config, rigs/<rig_id>/rig.yml (and rigs/<rig_id>/events.yml when it
exists) is validated, then installed root-owned 0644 as

    /etc/pancakebatter/rigs/<rig_id>.yml          (+ <rig_id>.yml.source sidecar)
    /etc/pancakebatter/rigs/<rig_id>.events.yml   (when the rig has an events ledger)

Installed rig files the host no longer binds are removed, so a missing file means "not driven by this
host", never "use a default". Consumers resolve $PANCAKEBATTER_RIG_CONFIG > the installed file and pin
rig_id, rig_geometry_revision, sha256 (and events_sha256) and the sidecar's full commit; see
rigs/README.md "Install".

    python3 rig_install.py --check HOST_CONFIG    # exit 0 if every installed rig file matches the checkout
"""
from __future__ import annotations

import datetime as dt
import hashlib
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

import rigconfig

REPO = Path(__file__).resolve().parent
DEST_DIR = Path("/etc/pancakebatter/rigs")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(repo: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def bound_rigs(host_cfg: dict[str, Any]) -> list[str]:
    return [str(b.get("rig_id")) for b in host_cfg.get("rigs") or [] if isinstance(b, dict)]


def plan(host: str, host_cfg: dict[str, Any], repo: Path = REPO, allow_dirty: bool = False) -> tuple[list[dict], list[str]]:
    """The rig files to install, or the reasons to refuse. Nothing is written."""
    errors: list[str] = []
    items: list[dict] = []
    docs: dict[str, dict] = {}
    for rid in bound_rigs(host_cfg):
        src = repo / "rigs" / rid / "rig.yml"
        if not src.is_file():
            errors.append(f"rig {rid}: missing {src}")
            continue
        doc = yaml.safe_load(src.read_text())
        errs = rigconfig.validate(doc) + rigconfig.location_errors(src, doc)
        errors += [f"rig {rid}: {e}" for e in errs]
        docs[rid] = doc
        events = src.parent / "events.yml"
        if events.is_file():
            import rig_events
            errs += [f"events.yml: {m}" for level, m in
                     rig_events.findings(yaml.safe_load(events.read_text()), rid, doc, check_files=False)
                     if level == "fail"]
        if not errs:
            items.append({"rig_id": rid, "source": src, "events": events if events.is_file() else None,
                          "rig_geometry_revision": doc["rig_geometry_revision"]})
        dirty = _git(repo, "status", "--porcelain", "--", str(src.parent))
        if dirty is None and not allow_dirty:
            errors.append(f"rig {rid}: cannot read git status of {src.parent}, so cannot pin a commit (or --allow-dirty)")
        elif dirty and not allow_dirty:
            errors.append(f"rig {rid}: uncommitted changes under {src.parent} (commit them, or --allow-dirty)")
    if not errors:
        errors += rigconfig.validate_binding(host, host_cfg, docs)
    return items, errors


def sidecar(item: dict, installed: Path, events_installed: Path | None, commit: str, dirty: bool) -> str:
    lines = [f"source: {item['source']}",
             f"commit: {commit}{' (uncommitted changes)' if dirty else ''}",
             f"installed: {dt.datetime.now().astimezone().isoformat(timespec='seconds')}",
             f"sha256: {_sha256(installed)}",
             f"rig_geometry_revision: {item['rig_geometry_revision']}"]
    if events_installed is not None:
        lines += [f"events_source: {item['events']}", f"events_sha256: {_sha256(events_installed)}"]
    return "\n".join(lines) + "\n"


def _install_file(src: Path, dest: Path) -> None:
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.write_bytes(src.read_bytes())
    os.chmod(tmp, 0o644)
    if os.geteuid() == 0:
        os.chown(tmp, 0, 0)
    os.replace(tmp, dest)


def install(items: list[dict], dest_dir: Path = DEST_DIR, repo: Path = REPO) -> list[str]:
    """Install each planned rig file with its sidecar; remove rig files no longer bound. Returns log lines."""
    dest_dir.mkdir(mode=0o755, parents=True, exist_ok=True)
    commit = _git(repo, "rev-parse", "HEAD") or "unknown"
    log = []
    keep: set[str] = set()
    for item in items:
        rid = item["rig_id"]
        dest = dest_dir / f"{rid}.yml"
        _install_file(item["source"], dest)
        events_dest = None
        if item["events"] is not None:
            events_dest = dest_dir / f"{rid}.events.yml"
            _install_file(item["events"], events_dest)
        dirty = bool(_git(repo, "status", "--porcelain", "--", str(item["source"].parent)))
        side = dest.with_name(dest.name + ".source")
        side.write_text(sidecar(item, dest, events_dest, commit, dirty))
        os.chmod(side, 0o644)
        keep |= {dest.name, side.name} | ({events_dest.name} if events_dest else set())
        log.append(f"installed {dest} (revision {item['rig_geometry_revision']}"
                   f"{', with ' + events_dest.name if events_dest else ''}) @ {commit}")
    for stale in sorted(dest_dir.iterdir()):
        if stale.name not in keep and (stale.name.endswith(".yml") or stale.name.endswith(".yml.source")):
            stale.unlink()
            log.append(f"removed {stale} (not bound by this host)")
    return log


def check(host_cfg: dict[str, Any], dest_dir: Path = DEST_DIR, repo: Path = REPO) -> list[str]:
    """Differences between the installed rig files and the checkout; [] when everything matches."""
    problems = []
    for rid in bound_rigs(host_cfg):
        src = repo / "rigs" / rid / "rig.yml"
        pairs = [(src, dest_dir / f"{rid}.yml"), (src.parent / "events.yml", dest_dir / f"{rid}.events.yml")]
        for a, b in pairs:
            if not a.is_file() and not b.is_file():
                continue
            if not b.is_file():
                problems.append(f"{b} not installed")
            elif not a.is_file():
                problems.append(f"{b} installed but {a} is gone")
            elif a.read_bytes() != b.read_bytes():
                problems.append(f"{b} differs from {a}")
    return problems


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "--check":
        problems = check(yaml.safe_load(Path(argv[1]).read_text()) or {})
        for p in problems:
            print(p)
        return 1 if problems else 0
    if not argv or argv[0] not in ("--install", "--plan"):
        print(__doc__.strip().splitlines()[-1].strip(), file=sys.stderr)
        return 2
    allow_dirty = "--allow-dirty" in argv
    rest = [a for a in argv[1:] if a != "--allow-dirty"]
    if len(rest) != 2:
        print("usage: rig_install.py --install|--plan HOST HOST_CONFIG [--allow-dirty]", file=sys.stderr)
        return 2
    host, cfg_path = rest
    items, errors = plan(host, yaml.safe_load(Path(cfg_path).read_text()) or {}, allow_dirty=allow_dirty)
    for e in errors:
        print(f"  {e}", file=sys.stderr)
    if errors:
        print("refusing to install rig files", file=sys.stderr)
        return 1
    if argv[0] == "--plan":
        for item in items:
            print(f"would install rigs/{item['rig_id']}/rig.yml (revision {item['rig_geometry_revision']})")
        return 0
    for line in install(items):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
