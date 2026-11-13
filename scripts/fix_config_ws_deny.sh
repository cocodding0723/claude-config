#!/usr/bin/env bash
# claude-config 작업 공간들의 .claude/settings.local.json 에서 Slack 스크립트·토큰 Bash 차단 2개를 제거하는 1회 스크립트. 기본은 --dry-run.
# 규칙 근거: create_worker_ws.sh 의 deny 주석(11-13 D-CFGAPPLY). Read(토큰 파일) 와 다른 규칙은 그대로 둠.
set -euo pipefail
ROOT="${LRM_CONFIG_WS_ROOT:-$HOME/orca/workspaces/claude-config}" APPLY=0
usage() {
  cat <<'USAGE'
사용: fix_config_ws_deny.sh [--dry-run | --apply] [--root 폴더]
claude-config 작업 공간(<root>/*/.claude/settings.local.json)에서 permissions.deny 의
  Bash(*slack_notify.sh*), Bash(*slack-bot.env*)
를 제거함. Read(토큰 파일) 차단은 유지. 이미 없으면 건너뜀. 여러 번 실행해도 결과 같음.
  --dry-run  (기본) 바꿀 파일만 출력
  --apply    실제로 수정(파일마다 .bak-deny 백업 1개)
  --root     작업 공간 루트(기본 ~/orca/workspaces/claude-config, 환경 변수 LRM_CONFIG_WS_ROOT)
종료 코드: 0 정상, 2 인자 오류·루트 없음
예:
  fix_config_ws_deny.sh               # 미리보기
  fix_config_ws_deny.sh --apply
USAGE
}
while [ $# -gt 0 ]; do case "$1" in
  --dry-run) APPLY=0; shift;; --apply) APPLY=1; shift;; --root) ROOT="${2:?--root 값 필요}"; shift 2;;
  -h|--help) usage; exit 0;;
  *) echo "알 수 없는 인자: $1 — --help 참고" >&2; exit 2;; esac; done
[ -d "$ROOT" ] || { echo "루트 폴더 없음: $ROOT — --root 로 지정" >&2; exit 2; }
exec python3 -I - "$ROOT" "$APPLY" <<'PY'
import glob, json, os, shutil, sys
root, apply = sys.argv[1], sys.argv[2] == "1"
DROP = {"Bash(*slack_notify.sh*)", "Bash(*slack-bot.env*)"}
changed = skipped = 0
for f in sorted(glob.glob(os.path.join(root, "*", ".claude", "settings.local.json"))):
    try:
        d = json.load(open(f))
        deny = d["permissions"]["deny"]
    except (OSError, ValueError, KeyError, TypeError):
        skipped += 1
        print("건너뜀(읽기 실패·deny 없음): " + f)
        continue
    hit = [r for r in deny if r in DROP]
    if not hit:
        continue
    changed += 1
    print(("수정: " if apply else "수정 예정: ") + f + " — 제거 " + ", ".join(hit))
    if apply:
        shutil.copy2(f, f + ".bak-deny")
        d["permissions"]["deny"] = [r for r in deny if r not in DROP]
        json.dump(d, open(f, "w"), indent=2)
print("%s %d개, 건너뜀 %d개%s" % ("수정" if apply else "수정 예정", changed, skipped, "" if apply else " (--apply 로 실행)"))
PY
