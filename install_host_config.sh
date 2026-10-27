#!/usr/bin/env bash
# Publish hosts/<host>/config.yml as the machine's host configuration, /etc/pancakebatter/host.yml,
# for other programs (citrus, orange, ...) to read through PANCAKEBATTER_HOST_CONFIG or that fixed path.
#
#   sudo ./install_host_config.sh            # validate, then install a root-owned copy + .source sidecar
#   sudo ./install_host_config.sh --allow-dirty   # same, even if a bound rig.yml has uncommitted changes
#   ./install_host_config.sh --check         # exit 0 if the installed copies match the checkout, 1 if stale/missing
#   sudo ./install_host_config.sh --uninstall
#
# It also installs the rig documents the host's `rigs:` block names (rigs/<rig_id>/rig.yml and, when
# present, rigs/<rig_id>/events.yml) to /etc/pancakebatter/rigs/ with their own sidecars, and removes
# rig files the host no longer binds (rig_install.py; rules in rigs/README.md "Install").
#
# The checkout stays the place to edit (capture_inventory.py, gui_config_editor.py, ...); re-run this after
# every change. rig_health_check.py warns when the installed copy differs from the checkout.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib/host_config.sh"
DEST_DIR=/etc/pancakebatter
DEST="$DEST_DIR/host.yml"
SIDECAR="$DEST.source"
RIG_DIR="$DEST_DIR/rigs"

check() {
    if [[ ! -f "$DEST" ]]; then echo "not installed: $DEST"; return 1; fi
    local rc=0
    if cmp -s "$HOST_CONFIG_FILE" "$DEST"; then
        echo "installed copy matches $HOST_CONFIG_FILE"; [[ -f "$SIDECAR" ]] && sed 's/^/  /' "$SIDECAR"
    else
        echo "installed copy is STALE: $DEST differs from $HOST_CONFIG_FILE (re-run: sudo ./install_host_config.sh)"
        diff "$DEST" "$HOST_CONFIG_FILE" | head -20 || true
        rc=1
    fi
    if ! (cd "$HERE" && /usr/bin/python3 rig_install.py --check "$HOST_CONFIG_FILE"); then
        echo "installed rig files are STALE or missing (re-run: sudo ./install_host_config.sh)"; rc=1
    fi
    return $rc
}


# Not root? Re-run through the passwordless helper if install_admin_helper.sh has set it up.
ADMIN_HELPER=/usr/local/libexec/pancakebatter/admin
if [[ "${1:-}" != "--check" && $EUID -ne 0 && -x "$ADMIN_HELPER" ]] && sudo -n -l "$ADMIN_HELPER" >/dev/null 2>&1; then
    exec sudo -n "$ADMIN_HELPER" install-host-config "$@"
fi
case "${1:-}" in
    --check) check; exit $? ;;
    --uninstall)
        [[ $EUID -eq 0 ]] || { echo "run with sudo" >&2; exit 1; }
        rm -f "$DEST" "$SIDECAR" "$RIG_DIR"/*.yml "$RIG_DIR"/*.yml.source
        rmdir "$RIG_DIR" 2>/dev/null || true; rmdir "$DEST_DIR" 2>/dev/null || true
        echo "removed $DEST"; exit 0 ;;
    ""|--allow-dirty) ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
esac

[[ $EUID -eq 0 ]] || { echo "run with sudo (or use --check)" >&2; exit 1; }
[[ -f "$HOST_CONFIG_FILE" ]] || { echo "missing $HOST_CONFIG_FILE" >&2; exit 1; }

# Validate before publishing (jsonschema if present, else the repo's own checks).
/usr/bin/python3 - "$HOST_CONFIG_FILE" "$HERE" <<'PY' || { echo "refusing to install an invalid host config" >&2; exit 1; }
import sys, yaml
sys.path.insert(0, sys.argv[2])
import configio
errors = configio.validate(yaml.safe_load(open(sys.argv[1])) or {})
for e in errors: print("  " + e, file=sys.stderr)
sys.exit(1 if errors else 0)
PY

# Validate the rig documents and the host's binding to them before writing anything.
(cd "$HERE" && /usr/bin/python3 rig_install.py --plan "$HOST_NAME" "$HOST_CONFIG_FILE" ${1:+"$1"}) \
    || { echo "refusing to install: fix the rig documents first" >&2; exit 1; }

COMMIT="$(git -C "$HERE" rev-parse HEAD 2>/dev/null || echo unknown)"
DIRTY="$(git -C "$HERE" status --porcelain -- "$HOST_CONFIG_FILE" 2>/dev/null | grep -q . && echo " (uncommitted changes)" || true)"
install -d -o root -g root -m 0755 "$DEST_DIR"
install -o root -g root -m 0644 "$HOST_CONFIG_FILE" "$DEST"
printf 'source: %s\ncommit: %s%s\ninstalled: %s\nsha256: %s\n' "$HOST_CONFIG_FILE" "$COMMIT" "$DIRTY" "$(date -Is)" "$(sha256sum "$DEST" | cut -d' ' -f1)" > "$SIDECAR"
chmod 0644 "$SIDECAR"
echo "installed $DEST from $HOST_CONFIG_FILE @ $COMMIT$DIRTY"
(cd "$HERE" && /usr/bin/python3 rig_install.py --install "$HOST_NAME" "$HOST_CONFIG_FILE" ${1:+"$1"})
echo "consumers: PANCAKEBATTER_HOST_CONFIG (optional) > $DEST; python3 hostconfig.py prints the resolution"
