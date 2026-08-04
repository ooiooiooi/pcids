#!/bin/sh
set -eu

artifact="${PCIDS_ARTIFACT_PATH:-${FIRMWARE_PATH:-}}"
install_dir="${INSTALL_DIR:-/opt/pcids-app}"

[ -n "$artifact" ] || { echo '[ERROR] PCIDS_ARTIFACT_PATH is empty.'; exit 2; }
[ -f "$artifact" ] || { echo "[ERROR] Package does not exist: $artifact"; exit 2; }
mkdir -p "$install_dir"

echo "[INSTALL] OS=${PCIDS_OS_TYPE:-linux}"
echo "[INSTALL] package=$artifact"
echo "[INSTALL] directory=$install_dir"

run_privileged() {
  if [ "$(id -u)" -eq 0 ]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo -- "$@"
  else
    echo '[ERROR] Package installation requires root or sudo.'
    return 2
  fi
}

case "$artifact" in
  *.deb)
    command -v dpkg >/dev/null 2>&1 || { echo '[ERROR] dpkg is missing.'; exit 2; }
    run_privileged dpkg -i "$artifact"
    ;;
  *.rpm)
    command -v rpm >/dev/null 2>&1 || { echo '[ERROR] rpm is missing.'; exit 2; }
    run_privileged rpm -Uvh --replacepkgs "$artifact"
    ;;
  *.tar.gz|*.tgz)
    tar -xzf "$artifact" -C "$install_dir"
    ;;
  *.tar)
    tar -xf "$artifact" -C "$install_dir"
    ;;
  *.zip)
    command -v unzip >/dev/null 2>&1 || { echo '[ERROR] unzip is missing.'; exit 2; }
    unzip -o "$artifact" -d "$install_dir"
    ;;
  *.sh)
    chmod 700 "$artifact"
    INSTALL_DIR="$install_dir" sh "$artifact"
    ;;
  *)
    chmod 755 "$artifact"
    echo '[INSTALL] Package copied; unknown suffix was not executed.'
    ;;
esac

echo '[INSTALL] completed'
