#!/usr/bin/env bash
# gh_watch.py 시험 진입점. 가짜 gh·작업판·알림과 임시 폴더만 씀(네트워크 없음)
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
chmod +x "$HERE"/fake_* 2>/dev/null
exec python3 "$HERE/test_gh_watch.py"
