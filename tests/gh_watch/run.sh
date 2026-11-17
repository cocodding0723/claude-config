#!/usr/bin/env bash
# gh_watch.py 시험 진입점. 가짜 gh·작업판·알림과 임시 폴더만 씀(네트워크 없음)
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
chmod +x "$HERE"/fake_* 2>/dev/null
python3 "$HERE/test_gh_watch.py"; rc=$?
REPO=$(cd "$HERE/../.." && pwd -P)
PL="$REPO/hubs/launchd/ai.lrm.ghwatch.plist"
chk() { if eval "$2"; then echo "PASS  $1"; else echo "FAIL  $1"; rc=1; fi; }
chk "plist: 형식 정상·상주(KeepAlive)·python 자리표시·run 인자" 'plutil -lint "$PL" >/dev/null && python3 -c "import plistlib,sys;p=plistlib.load(open(sys.argv[1],\"rb\"));assert p[\"KeepAlive\"] is True and p[\"ProgramArguments\"]==[\"__PYTHON3__\",\"__HOME__/.config/lrm-hubs/gh_watch.py\",\"run\"]" "$PL"'
chk "links.tsv: 감시기 파일·plist 등록" '( for f in hubs/scripts/gh_watch.py hubs/scripts/gh_watch_sinks.py hubs/scripts/gh-watch hubs/launchd/ai.lrm.ghwatch.plist; do grep -q "^$f	" "$REPO/links.tsv" || exit 1; done )'
chk "진입점 gh-watch --help: 종료 0·명령 목록" 'out=$(bash "$REPO/hubs/scripts/gh-watch" --help) && grep -q kick <<<"$out"'
chk "후속 명령 예시: JSON 정상·전부 꺼짐" 'python3 -c "import json,sys;d=json.load(open(sys.argv[1]));assert d[\"actions\"] and all(a[\"enabled\"] is False for a in d[\"actions\"])" "$REPO/hubs/gh-watch-actions.example.json"'
exit "$rc"
