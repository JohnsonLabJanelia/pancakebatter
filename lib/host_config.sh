# Source this file to get per-host paths under hosts/<hostname>/.
# PANCAKEBATTER_HOST overrides the short hostname (to work on another machine's files).
HOST_CONFIG_REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST_NAME="${PANCAKEBATTER_HOST:-$(hostname -s)}"
HOST_DIR="${HOST_CONFIG_REPO_ROOT}/hosts/${HOST_NAME}"
HOST_CONFIG_FILE="${HOST_DIR}/config.yml"
