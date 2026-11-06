#!/bin/bash
# 답 안 한 결정 목록을 새 메시지로 보내고, 이전 목록은 이력 기록 뒤 삭제. 답한 결정 메시지는 결과 한 줄로 고침.
# 새 메시지로 보내는 이유: chat.update 는 알림이 오지 않아 2시간 알림이 사라지기 때문.
# 사용: slack_pending.sh [--dry-run]
# 이력: slack-pending-history.jsonl (추가 전용). 이력 기록이 실패하면 삭제·수정하지 않음.
# 환경 변수: LRM_HUB_DIR 작업 폴더, SLACK_API_BASE API 주소(테스트용), LRM_UPDATE_MAX 회당 메시지 수정 상한(기본 20)
set -eu
export SSL_CERT_FILE="${SSL_CERT_FILE:-/etc/ssl/cert.pem}"
DIR="${LRM_HUB_DIR:-$HOME/.config/lrm-hubs}"
set -a; . "$DIR/slack-bot.env"; set +a
# 동시 실행 방지. 10분 넘은 잠금은 죽은 실행의 잔재로 보고 제거
LOCK="$DIR/.slack-pending.lock"
[ -d "$LOCK" ] && [ -n "$(find "$LOCK" -maxdepth 0 -mmin +10 2>/dev/null)" ] && rmdir "$LOCK" 2>/dev/null
mkdir "$LOCK" 2>/dev/null || { echo "이미 실행 중 — 건너뜀"; exit 0; }
trap 'rmdir "$LOCK"' EXIT
python3 -I - "$DIR" "$@" <<'PY'
import json, os, sys, time, urllib.error, urllib.parse, urllib.request
H = sys.argv[1]
DRY = "--dry-run" in sys.argv[2:]
TOK = os.environ["SLACK_BOT_TOKEN"]
BASE = os.environ.get("SLACK_API_BASE", "https://slack.com/api")
UPDATE_MAX = int(os.environ.get("LRM_UPDATE_MAX", "20"))  # 첫 실행에 쌓인 답한 결정이 몰려도 한도를 넘지 않게 분할
RETRY_CAP = float(os.environ.get("LRM_RETRY_CAP", "20"))
RETRIES = 2
# 다시 시도해도 소용없는 수정 오류. 이력에 남기고 재시도 목록에서 뺌
FINAL_UPDATE_ERRORS = {"message_not_found", "cant_update_message", "edit_window_closed"}
HIST = os.path.join(H, "slack-pending-history.jsonl")

class Fail(Exception):
    pass

def call(method, body=None, **params):
    url = f"{BASE}/{method}" + ("?" + urllib.parse.urlencode(params) if params else "")
    for attempt in range(RETRIES + 1):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Authorization": "Bearer " + TOK}
        if data is not None:
            headers["Content-Type"] = "application/json"
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers), timeout=15) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            # 429 는 Retry-After 만큼 쉬고 재시도. 소진 시 실패로 올림
            if e.code != 429 or attempt == RETRIES:
                raise Fail(f"{method}: HTTP {e.code}")
            try:
                wait = float(e.headers.get("Retry-After", "1"))
            except ValueError:
                wait = 1.0
            time.sleep(min(wait, RETRY_CAP))

def jsonl(path):
    if not os.path.exists(path):
        return []
    out = []
    for l in open(path, encoding="utf-8"):
        if l.strip():
            try:
                out.append(json.loads(l))
            except json.JSONDecodeError:
                pass
    return out

def log(event, **fields):
    rec = {"at": int(time.time()), "event": event, **fields}
    with open(HIST, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())

def main():
    sent = jsonl(os.path.join(H, "slack-sent.jsonl"))
    inbox = jsonl(os.path.join(H, "slack-inbox.jsonl"))
    done_file = os.path.join(H, "slack-done.txt")
    reacted = {}
    for d in inbox:
        if d.get("type") == "reaction":
            reacted[d.get("parent_ts")] = d  # 같은 메시지에 여러 반응이면 마지막 것
    done = set(reacted) | (set(open(done_file).read().split()) if os.path.exists(done_file) else set())
    decisions = [s for s in sent if s.get("ts") and not s.get("thread_ts") and "결정" in s.get("title", "")]
    answered = [s for s in decisions if s["ts"] in done]
    waiting = [s for s in decisions if s["ts"] not in done]

    events = jsonl(HIST)
    handled = set()  # 수정 시도가 끝난 메시지
    for e in events:
        if e["event"] == "answered_update":
            handled.add(e["ts"])
        elif e["event"] == "update_failed" and not e.get("final"):
            handled.discard(e["ts"])
    posted = {e["ts"]: e for e in events if e["event"] == "pending_posted"}
    closed = {e["ts"] for e in events if e["event"] == "delete_done"}
    open_posts = [posted[t] for t in posted if t not in closed]

    if not DRY:
        log("run_start", waiting=len(waiting), answered_new=len([s for s in answered if s["ts"] not in handled]))  # 이력 쓰기 가능 여부 선검사

    # 답한 결정 메시지를 결과 한 줄로 고침. 이력에 원문 제목을 먼저 남기고 수정
    todo = [s for s in answered if s["ts"] not in handled][:UPDATE_MAX]
    for s in todo:
        r = reacted.get(s["ts"], {})
        action = r.get("action", "완료")
        text = f"✅ {s['title']} — 답: {action}"
        if DRY:
            print(f"수정 예정: {s['ts']} → {text}")
            continue
        log("answered_update", ts=s["ts"], title=s["title"], action=action, answered_at=r.get("received_at"))
        res = call("chat.update", {"channel": s["channel"], "ts": s["ts"], "text": text})
        if not res.get("ok"):
            log("update_failed", ts=s["ts"], error=res.get("error"), final=res.get("error") in FINAL_UPDATE_ERRORS)

    rows, seen = [], set()
    for s in waiting:
        if s["title"] in seen:
            continue
        seen.add(s["title"])
        link = call("chat.getPermalink", channel=s["channel"], message_ts=s["ts"]).get("permalink", "")
        rows.append((s["title"], f"• <{link}|{s['title']}>"))
    channel = waiting[0]["channel"] if waiting else None

    new_ts = None
    if rows:
        text = "🙋 *답을 기다리는 결정 " + str(len(rows)) + "개* — 눌러서 이동, 이모지로 답\n" + "\n".join(r[1] for r in rows)
        if DRY:
            print(text)
            return 0
        res = call("chat.postMessage", {"channel": channel, "text": text})
        if not res.get("ok"):
            raise Fail("chat.postMessage: " + str(res.get("error")))
        new_ts = res["ts"]
        log("pending_posted", ts=new_ts, channel=channel, titles=[r[0] for r in rows])
        print("ok", len(rows))
    else:
        print("대기 0")

    # 이전 목록 삭제. 삭제 전에 내용·그 뒤 처리된 결정·처리 시각·삭제 대상 ts 를 이력에 남김
    for old in [p for p in open_posts if p["ts"] != new_ts]:
        since = [e for e in jsonl(HIST) if e["event"] == "answered_update" and e["at"] >= old["at"]]
        log("pending_deleted", ts=old["ts"], channel=old["channel"], new_ts=new_ts,
            prev_titles=old.get("titles", []),
            answered_since=[{"ts": e["ts"], "title": e["title"], "action": e["action"],
                             "answered_at": e["answered_at"], "updated_at": e["at"]} for e in since])
        res = call("chat.delete", {"channel": old["channel"], "ts": old["ts"]})
        if res.get("ok") or res.get("error") == "message_not_found":
            log("delete_done", ts=old["ts"], note=res.get("error", ""))
        else:
            log("delete_failed", ts=old["ts"], error=res.get("error"))
    return 0

try:
    sys.exit(main())
except (Fail, OSError) as e:
    print(f"실패: {e} — 이력 기록 실패 시 삭제·수정을 하지 않음", file=sys.stderr)
    sys.exit(1)
PY
