#!/bin/bash

APT_HELPER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APT_MANIFEST_DIR="${APT_HELPER_DIR}/../packages/apt"

trim_apt_manifest_line() {
    local line="$1"

    line="${line%%#*}"
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"

    printf '%s' "$line"
}

collect_apt_manifest_packages() {
    local manifest_name
    local manifest_file
    local line
    local trimmed_line
    local -A seen=()

    APT_MANIFEST_PACKAGES=()

    if [ "$#" -eq 0 ]; then
        echo "ERROR: No apt package manifests were provided." >&2
        return 1
    fi

    for manifest_name in "$@"; do
        manifest_file="${APT_MANIFEST_DIR}/${manifest_name}.txt"
        if [ ! -f "$manifest_file" ]; then
            echo "ERROR: Apt package manifest not found: $manifest_file" >&2
            return 1
        fi

        while IFS= read -r line || [ -n "$line" ]; do
            trimmed_line="$(trim_apt_manifest_line "$line")"
            if [ -z "$trimmed_line" ]; then
                continue
            fi

            if [ -z "${seen[$trimmed_line]+x}" ]; then
                APT_MANIFEST_PACKAGES+=("$trimmed_line")
                seen["$trimmed_line"]=1
            fi
        done < "$manifest_file"
    done
}

install_apt_manifest_packages() {
    local description="$1"
    shift

    collect_apt_manifest_packages "$@" || return 1

    if [ "${#APT_MANIFEST_PACKAGES[@]}" -eq 0 ]; then
        echo "ERROR: No apt packages were found in the requested manifests." >&2
        return 1
    fi

    echo "Updating apt package list..."
    apt-get update || return 1

    echo "Installing ${description}..."
    apt-get install -y "${APT_MANIFEST_PACKAGES[@]}"
}
