#!/usr/bin/env bash
# PostgreSQL 18.6 (the lab's version) from conda-forge, for the local
# integration tests only (make postgres): micromamba and every package of
# this machine's lock file are downloaded into .postgres/ (git-ignored) and
# checked against their sha256 before anything of them runs; micromamba
# then installs exactly the checked files, offline, into .postgres/env,
# with an empty environment, no configuration file and its own cache.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
dest="$(cd "$here/../.." && pwd)/.postgres"
micromamba_version=2.10.0

case "$(uname -m)" in
  x86_64)
    platform=linux-64
    micromamba_sha256=9a97976244929bbae28fb1fae504e74a3e7e6d1932870e3a03321f01b7fbd07b
    ;;
  aarch64)
    platform=linux-aarch64
    micromamba_sha256=890bc4906e6ab17f1b393f0da21547f75e000078e573a156316ec925b92a4a4c
    ;;
  *)
    echo "postgres: no locked packages for $(uname -m)" >&2
    exit 2
    ;;
esac

# fetch URL SHA256 FILE: FILE holds URL's content and has SHA256, or the script stops.
fetch() {
  if [[ -f "$3" ]] && sha256sum --check --status <<<"$2  $3"; then
    return
  fi
  curl --fail --silent --show-error --location --retry 3 --output "$3.part" "$1"
  if ! sha256sum --check --status <<<"$2  $3.part"; then
    rm -f "$3.part"
    echo "postgres: $1 does not have the sha256 $2" >&2
    exit 1
  fi
  mv "$3.part" "$3"
}

mkdir -p "$dest/download" "$dest/pkgs"
archive="$dest/download/micromamba-$micromamba_version-0.tar.bz2"
fetch "https://conda.anaconda.org/conda-forge/$platform/micromamba-$micromamba_version-0.tar.bz2" \
  "$micromamba_sha256" "$archive"
tar --extract --bzip2 --file "$archive" --directory "$dest/download" bin/micromamba

explicit="$dest/download/explicit.txt"
echo "@EXPLICIT" >"$explicit"
while read -r url sha256 || [[ -n "${url:-}" ]]; do
  [[ -z "$url" || "$url" == \#* ]] && continue
  file="$dest/pkgs/${url##*/}"
  fetch "$url" "$sha256" "$file"
  echo "file://$file#sha256:$sha256" >>"$explicit"
done <"$here/$platform.lock"

rm -rf "$dest/env"
mkdir -p "$dest/home"
env -i HOME="$dest/home" PATH=/usr/bin:/bin MAMBA_ROOT_PREFIX="$dest/root" \
  "$dest/download/bin/micromamba" create --yes --quiet --offline --no-rc \
  --prefix "$dest/env" --file "$explicit"
"$dest/env/bin/postgres" --version
