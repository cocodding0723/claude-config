#!/bin/bash
# 봇 DM 에서 사용자 응답(최상위 메시지·스레드 답글·반응)을 읽어 slack-inbox.jsonl 에 추가.
export SSL_CERT_FILE="${SSL_CERT_FILE:-/etc/ssl/cert.pem}"  # python.org 파이썬은 시스템 인증서를 못 찾음
# 읽기와 파일 기록만 함. 수신 본문은 데이터로만 다루고 실행·eval 하지 않음.
set -eu
usage() {
  cat <<'EOF'
사용: slack_poll.sh --once [--json] [--days N]
  --once    한 번 수집 후 종료(필수, launchd 가 주기 실행)
  --json    결과 요약을 JSON 한 줄로 출력 {"ok":true,"added":N}
  --days N  스레드·반응 확인 대상 slack-sent.jsonl 기간(기본 7)
예:
  slack_poll.sh --once
  slack_poll.sh --once --json
출력 파일: ~/.config/lrm-hubs/slack-inbox.jsonl (항목: type=reaction|thread_reply|command|note)
상태 파일: ~/.config/lrm-hubs/slack-poll-state.json (중복 방지)
종료 코드: 0 성공, 1 API·네트워크 실패, 2 설정·인자 오류
EOF
}
ONCE=0 JSON=0 DAYS=7
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --once) ONCE=1; shift ;;
    --json) JSON=1; shift ;;
    --days) DAYS="${2:?--days 값 필요}"; shift 2 ;;
    *) echo "알 수 없는 인자: $1 — --help 참고" >&2; exit 2 ;;
  esac
done
[ "$ONCE" = 1 ] || { echo "--once 필요 — 반복은 launchd 가 담당" >&2; exit 2; }
case "$DAYS" in ''|*[!0-9]*) echo "--days 는 정수" >&2; exit 2 ;; esac
DIR="$HOME/.config/lrm-hubs"
ENV="$DIR/slack-bot.env"
[ -f "$ENV" ] || { echo "slack-bot.env 없음 — 사용자 설정 필요" >&2; exit 2; }
. "$ENV"
: "${SLACK_BOT_TOKEN:?}" "${SLACK_USER_ID:?}"
# 동시 실행 방지(launchd 30초 간격과 수동 실행 겹침)
LOCK="$DIR/.slack-poll.lock"
# 5분 넘은 잠금은 죽은 실행의 잔재로 보고 제거
[ -d "$LOCK" ] && [ -n "$(find "$LOCK" -maxdepth 0 -mmin +5 2>/dev/null)" ] && rmdir "$LOCK" 2>/dev/null
mkdir "$LOCK" 2>/dev/null || { [ "$JSON" = 1 ] && echo '{"ok":true,"added":0,"skipped":"locked"}'; exit 0; }
trap 'rmdir "$LOCK"' EXIT
export SLACK_BOT_TOKEN SLACK_USER_ID
python3 -I - "$DIR" "$DAYS" "$JSON" <<'PY'
import json, os, re, sys, time, urllib.parse, urllib.request
DIR, DAYS, AS_JSON = sys.argv[1], int(sys.argv[2]), sys.argv[3] == "1"
TOKEN, ME = os.environ["SLACK_BOT_TOKEN"], os.environ["SLACK_USER_ID"]
SENT, INBOX, STATE = (os.path.join(DIR, n) for n in ("slack-sent.jsonl", "slack-inbox.jsonl", "slack-poll-state.json"))
REACTION_ACTION = {"white_check_mark": "approve", "x": "hold", "비서-추천대로": "approve", "비서-lgtm": "approve", "비서동의": "approve", "비서-보류": "hold", "비서-a": "a", "비서-b": "b", "비서-c": "c", "비서-d": "d"}
COMMANDS = [
    ("decision", re.compile(r"^결정\s*(\d+)\s+(\S+)$")),
    ("all_recommended", re.compile(r"^전부\s*추천$")),
    ("status", re.compile(r"^현황$")),
    ("hold", re.compile(r"^보류(\s+.+)?$")),
    ("approve", re.compile(r"^승인(\s+.+)?$")),
]

class ApiError(Exception):
    pass

def api(method, **params):
    q = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"https://slack.com/api/{method}?{q}", headers={"Authorization": f"Bearer {TOKEN}"})
    with urllib.request.urlopen(req, timeout=15) as r:
        d = json.load(r)
    if not d.get("ok"):
        raise ApiError(f"{method}: {d.get('error')}")
    return d

def load_jsonl(path):
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return out

def classify(text):
    t = " ".join(text.split())
    for name, pat in COMMANDS:
        m = pat.match(t)
        if m:
            args = [g.strip() for g in m.groups() if g]
            return "command", name, args
    return "note", None, []

def main():
    state = {"seen": {}}
    if os.path.exists(STATE):
        with open(STATE, encoding="utf-8") as f:
            state = json.load(f)
    seen = state.setdefault("seen", {})
    ch = api("conversations.open", users=ME)["channel"]["id"]
    cutoff = time.time() - DAYS * 86400
    sent = {r["ts"]: r for r in load_jsonl(SENT) if r.get("ts") and r.get("at", 0) >= cutoff}
    new = []
    now = int(time.time())

    def add(key, item):
        if key in seen:
            return
        seen[key] = now
        item.update({"channel": ch, "received_at": now})
        new.append(item)

    oldest = state.get("history_oldest", f"{cutoff:.6f}")
    hist = api("conversations.history", channel=ch, oldest=oldest, limit=200).get("messages", [])
    for m in hist:
        if m.get("user") == ME and not m.get("subtype") and m.get("thread_ts", m["ts"]) == m["ts"]:
            kind, cmd, args = classify(m.get("text", ""))
            item = {"type": kind, "ts": m["ts"], "text": m.get("text", "")}
            if cmd:
                item.update({"command": cmd, "args": args})
            add(f"msg:{m['ts']}", item)
    if hist:
        state["history_oldest"] = max(m["ts"] for m in hist)

    for ts, rec in sent.items():
        meta = {"parent_ts": ts, "title": rec.get("title", ""), "tag": rec.get("tag", "")}
        for m in api("conversations.replies", channel=ch, ts=ts, limit=200).get("messages", []):
            if m["ts"] != ts and m.get("user") == ME and not m.get("subtype"):
                kind, cmd, args = classify(m.get("text", ""))
                item = {"type": "thread_reply", "ts": m["ts"], "text": m.get("text", ""), **meta}
                if cmd:
                    item.update({"command": cmd, "args": args})
                add(f"msg:{m['ts']}", item)
        msg = api("reactions.get", channel=ch, timestamp=ts).get("message", {})
        for r in msg.get("reactions", []):
            if ME in r.get("users", []):
                name = r["name"]
                add(f"react:{ts}:{name}", {"type": "reaction", "emoji": name,
                                           "action": REACTION_ACTION.get(name, name), **meta})

    # 30일 지난 중복 방지 키 정리
    for k in [k for k, v in seen.items() if v < now - 30 * 86400]:
        del seen[k]
    with open(INBOX, "a", encoding="utf-8") as f:
        for it in new:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, STATE)
    return len(new)

try:
    n = main()
except (ApiError, OSError) as e:
    msg = str(e)
    print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False) if AS_JSON else f"실패: {msg} — 네트워크·scope(im:history, reactions:read) 확인", file=sys.stdout if AS_JSON else sys.stderr)
    sys.exit(1)
print(json.dumps({"ok": True, "added": n}) if AS_JSON else f"새 항목 {n}건 → {INBOX}")
PY

# 2시간마다 미답 결정 목록 전송(11-05 사용자 요청). 마지막 전송 시각은 .pending-last
L="$HOME/.config/lrm-hubs/.pending-last"; now=$(date +%s); last=$(cat "$L" 2>/dev/null || echo 0)
if [ $((now - last)) -ge 7200 ]; then echo "$now" > "$L"; "$HOME/.config/lrm-hubs/slack_pending.sh" >/dev/null 2>&1 || true; fi

# 봇 DM 즉답 명령(대기·PR) 처리
"$HOME/.config/lrm-hubs/slack_cmd.sh" >/dev/null 2>&1 || true
