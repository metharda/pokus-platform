#!/bin/sh
# Runs once in the build instance, after the files are in place.
set -eu
chmod 0755 /usr/local/bin/ledger-compactor
systemctl enable ledger-compactor.timer
