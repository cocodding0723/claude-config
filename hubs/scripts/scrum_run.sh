#!/bin/bash
# 스크럼 초안 실행 래퍼. launchd(ai.lrm.scrum) 진입점. 잠자기 방지(caffeinate -i) 아래에서 scrum_post.py 실행.
# 설명·등록·끄기는 ~/.config/lrm-hubs/docs/scrum-auto.md(원본 hubs/docs/scrum-auto.md).
set -uo pipefail
# launchd 의 짧은 PATH 에서도 claude·gh·python3 를 찾도록 뒤에 덧붙임(앞의 PATH 가 우선: 시험의 가짜 명령)
export PATH="$PATH:$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/Library/Frameworks/Python.framework/Versions/3.13/bin:/usr/bin:/bin"
# claude 는 USER 로 키체인 로그인 항목을 찾음. env -i 나 짧은 환경에서 비면 인증이 풀려 '조직이 구독 접근을 막음' 오류가 나므로 채움
export USER="${USER:-$(id -un)}" LOGNAME="${LOGNAME:-$(id -un)}"
case "${1:-}" in
  -h|--help)
    cat <<'USAGE'
사용: scrum_run.sh [--dry-run] [--date YYYY-MM-DD] [--force] [--out 파일] [--preflight]
평일 스크럼 본인 칸 초안을 claude 로 쓰고 Slack 봇 DM 1건으로 보냄. 인자는 scrum_post.py 로 그대로 전달.
  --dry-run  Slack·상태 파일 없이 초안을 파일(<초안 폴더>/<날짜>.dry.md)과 표준 출력에 냄
  --date     초안 날짜(기본 오늘)
  --force    평일·'오늘 이미 보냄' 검사 생략(수동 재실행)
  --preflight  발송 없이 gh·claude·Atlassian MCP·Slack 봇 스크립트만 점검(첫 자동 실행 전)
환경: LRM_SCRUM_MODELS(쉼표, 기본 claude-fable-5-1,claude-fable-5-2,claude-opus-5-5) · LRM_SCRUM_DRAFT_DIR · LRM_SCRUM_LOG · LRM_HUB_DIR
종료 코드: 0 발송·건너뜀·dry-run 성공, 1 초안 작성 또는 발송 실패, 2 인자 오류, 3 환경 오류(python3 없음)
예:
  scrum_run.sh --dry-run --date 2026-11-12
USAGE
    exit 0 ;;
esac
command -v python3 >/dev/null 2>&1 || { echo "python3 없음 — PATH($PATH) 에 python3 위치 추가" >&2; exit 3; }
cd "$(dirname "$0")" || exit 3
if command -v caffeinate >/dev/null 2>&1; then
  exec caffeinate -i python3 "$(pwd -P)/scrum_post.py" "$@"
fi
exec python3 "$(pwd -P)/scrum_post.py" "$@"
