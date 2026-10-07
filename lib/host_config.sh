# Source this file to get per-host paths under hosts/<hostname>/ (the CHECKOUT copy pancakebatter edits).
# PANCAKEBATTER_HOST overrides the short hostname (to work on another machine's files);
# PANCAKEBATTER_HOST_CONFIG names an explicit file and wins over both.
#
# Other programs should not read the checkout: resolve_host_config prints the installed copy
# ($PANCAKEBATTER_HOST_CONFIG > /etc/pancakebatter/host.yml) or fails. See docs/host_config_interface.md.
HOST_CONFIG_REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST_NAME="${PANCAKEBATTER_HOST:-$(hostname -s)}"
HOST_DIR="${HOST_CONFIG_REPO_ROOT}/hosts/${HOST_NAME}"
HOST_CONFIG_FILE="${PANCAKEBATTER_HOST_CONFIG:-${HOST_DIR}/config.yml}"
HOST_CONFIG_INSTALLED=/etc/pancakebatter/host.yml

# resolve_host_config: print the host config a consumer should read, or return 1 with guidance on stderr.
resolve_host_config() {
    if [[ -n "${PANCAKEBATTER_HOST_CONFIG:-}" ]]; then
        [[ -f "$PANCAKEBATTER_HOST_CONFIG" ]] || { echo "PANCAKEBATTER_HOST_CONFIG=$PANCAKEBATTER_HOST_CONFIG does not exist" >&2; return 1; }
        printf '%s\n' "$PANCAKEBATTER_HOST_CONFIG"; return 0
    fi
    if [[ -f "$HOST_CONFIG_INSTALLED" ]]; then printf '%s\n' "$HOST_CONFIG_INSTALLED"; return 0; fi
    echo "no host configuration: set PANCAKEBATTER_HOST_CONFIG or run 'sudo ./install_host_config.sh' in the pancakebatter checkout (expected $HOST_CONFIG_INSTALLED)" >&2
    return 1
}
