#!/usr/bin/env bash
# OPA, the policy engine the lab runs, for the policy's tests only (make
# opa): the one file that this machine's line of opa.lock names is
# downloaded into .opa/ (git-ignored) and checked against its sha256. Only
# a file that has it is made executable and put at .opa/opa; nothing of it
# runs before that.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
dest="$(cd "$here/../.." && pwd)/.opa"
machine="$(uname -m)"

version="" url="" sha256="" lines=0
while read -r locked_version locked_machine locked_url locked_sha256 || [[ -n "${locked_version:-}" ]]; do
  [[ -z "$locked_version" || "$locked_version" == \#* ]] && continue
  if [[ "$locked_machine" == "$machine" ]]; then
    version="$locked_version" url="$locked_url" sha256="$locked_sha256"
    lines=$((lines + 1))
  fi
done <"$here/opa.lock"
# One line for this machine: of two, neither is taken.
if [[ "$lines" -ne 1 || -z "$url" ]]; then
  echo "opa: opa.lock has $lines lines for $machine, not one with a URL" >&2
  exit 2
fi

mkdir -p "$dest"
file="$dest/opa"
if ! { [[ -f "$file" ]] && sha256sum --check --status <<<"$sha256  $file"; }; then
  rm -f "$file" "$file.part"
  curl --fail --silent --show-error --location --retry 3 --output "$file.part" "$url"
  if ! sha256sum --check --status <<<"$sha256  $file.part"; then
    rm -f "$file.part"
    echo "opa: $url does not have the sha256 $sha256" >&2
    exit 1
  fi
  chmod 0755 "$file.part"
  mv "$file.part" "$file"
fi
echo "opa $version ($machine), sha256 $sha256, in $file"
