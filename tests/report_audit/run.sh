#!/usr/bin/env bash
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
exec python3 -B "$HERE/test_audit.py"
