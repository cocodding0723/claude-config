# 채팅 택1 분기. `. "$HERE/lib/chat.sh"; lrm_chat_route discord_notify.py "$@"` 형태로 진입점 맨 앞에서 부름.
# CHAT=discord 면 같은 인자로 디스코드 구현에 넘기고 돌아오지 않음. slack(기본)이면 아무것도 안 하고 반환.
# 값 출처: LRM_CHAT(시험·일회성 재정의) → values.env 의 CHAT → slack.
# 기록 파일(slack-sent.jsonl·slack-inbox.jsonl 등) 이름은 메인·패널이 읽으므로 디스코드도 그대로 씀.
# bash 3.2·set -eu 호환. PY(lib/py.sh)를 먼저 불러와야 함.

lrm_chat() {
  local v="${LRM_CHAT:-}"
  if [ -z "$v" ]; then
    v=$(grep -E '^CHAT=' "${CLAUDE_CONFIG_VALUES:-$HOME/.config/claude-config/values.env}" 2>/dev/null \
      | head -1 | cut -d= -f2- | tr -d '[:space:]' || true)
  fi
  printf '%s' "${v:-slack}"
}

lrm_chat_route() {  # 인자: 디스코드 구현 파일 이름, 원래 인자들
  local impl="$1" chat here; shift
  chat=$(lrm_chat)
  case "$chat" in
    slack) return 0 ;;
    discord) ;;
    *) echo "CHAT=$chat 은 허용값(slack|discord) 밖 — values.env 의 CHAT 을 고친 뒤 다시 실행" >&2; exit 2 ;;
  esac
  here="$(cd "$(dirname "${BASH_SOURCE[1]}")" && pwd)"
  [ -f "$here/$impl" ] || { echo "CHAT=discord 인데 $impl 없음 — install.sh 를 다시 실행" >&2; exit 2; }
  exec "$PY" -I "$here/$impl" "$@"
}
