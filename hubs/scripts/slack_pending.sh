#!/bin/bash
# 답 안 한 결정 목록을 새 메시지로 보내고, 이전 목록은 이력 기록 뒤 삭제. 답한 결정 메시지는 결과 한 줄로 고침.
# 새 메시지로 보내는 이유: chat.update 는 알림이 오지 않아 2시간 알림이 사라지기 때문.
# 사용: slack_pending.sh [--dry-run]
# 이력: slack-pending-history.jsonl (추가 전용). 이력 기록이 실패하면 삭제·수정하지 않음.
# 환경 변수: LRM_HUB_DIR 작업 폴더, SLACK_API_BASE API 주소(테스트용), LRM_UPDATE_MAX 회당 메시지 수정 상한(기본 20),
#           LRM_PENDING_BUDGET 재시도 시간 예산 초(기본 120)
# 한계: 수정 전 원문은 conversations.history 로 얻으며 text 가 없는 메시지(파일 첨부 등)는 null 로 기록
set -eu
export SSL_CERT_FILE="${SSL_CERT_FILE:-/etc/ssl/cert.pem}"
DIR="${LRM_HUB_DIR:-$HOME/.config/lrm-hubs}"
set -a; . "$DIR/slack-bot.env"; set +a
# 동시 실행 방지. 10분 넘은 잠금은 죽은 실행의 잔재로 보고 제거
LOCK="$DIR/.slack-pending.lock"
[ -d "$LOCK" ] && [ -n "$(find "$LOCK" -maxdepth 0 -mmin +10 2>/dev/null)" ] && rmdir "$LOCK" 2>/dev/null
# 잠금으로 건너뛰면 exit 75. 호출한 쪽(slack_cmd.sh)이 "처리 중" 안내와 실패를 구분하게 함
mkdir "$LOCK" 2>/dev/null || { echo "이미 실행 중 — 건너뜀"; exit 75; }
trap 'rmdir "$LOCK"' EXIT
LRM_PY_DIR="$(cd "$(dirname "$0")" && pwd)" python3 -I - "$DIR" "$@" <<'PY'
import json, os, re, sys, time, urllib.error, urllib.parse, urllib.request
sys.path.insert(0, os.environ["LRM_PY_DIR"])  # -I 는 같은 폴더를 import 경로에 넣지 않음
import tls_ctx
SSL_CTX = tls_ctx.ssl_context()
H = sys.argv[1]
DRY = "--dry-run" in sys.argv[2:]
TOK = os.environ["SLACK_BOT_TOKEN"]
BASE = os.environ.get("SLACK_API_BASE", "https://slack.com/api")
UPDATE_MAX = int(os.environ.get("LRM_UPDATE_MAX", "20"))  # 첫 실행에 쌓인 답한 결정이 몰려도 한도를 넘지 않게 분할
RETRY_CAP = float(os.environ.get("LRM_RETRY_CAP", "20"))  # 429 한 번당 대기 상한
# 실행 전체 재시도 시간 예산. slack_poll.sh 가 잡은 잠금(5분) 안에서 끝나야 함
DEADLINE = time.time() + float(os.environ.get("LRM_PENDING_BUDGET", "120"))
RETRIES = 2
# 다시 시도해도 소용없는 수정 오류. 이력에 남기고 재시도 목록에서 뺌
FINAL_UPDATE_ERRORS = {"message_not_found", "cant_update_message", "edit_window_closed"}
HIST = os.path.join(H, "slack-pending-history.jsonl")
# 번호 카드(slack_notify.sh --options) 답 표시. 형식은 slack_poll.sh 와 같음. 표시만 하고 결정을 확정·실행하지 않음(메인이 원문으로 확인)
DECISION_ID_RE = re.compile(r"D-[0-9A-Za-z]{1,16}", re.ASCII)
ANSWER_ACTIONS = ("pick", "approve", "hold")

class Fail(Exception):
    pass

def call(method, body=None, **params):
    url = f"{BASE}/{method}" + ("?" + urllib.parse.urlencode(params) if params else "")
    for attempt in range(RETRIES + 1):
        left = DEADLINE - time.time()
        if left <= 0:
            raise Fail(f"{method}: 재시도 시간 예산 초과")
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Authorization": "Bearer " + TOK}
        if data is not None:
            headers["Content-Type"] = "application/json"
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers), timeout=min(15, left), context=SSL_CTX) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            # 429 는 Retry-After 만큼 쉬고 재시도. 횟수·시간 예산 소진 시 실패로 올림
            if e.code != 429 or attempt == RETRIES:
                raise Fail(f"{method}: HTTP {e.code}")
            try:
                wait = min(float(e.headers.get("Retry-After", "1")), RETRY_CAP)
            except ValueError:
                wait = 1.0
            if wait >= DEADLINE - time.time():
                raise Fail(f"{method}: HTTP 429, 대기 {wait:g}초가 남은 예산을 넘김")
            time.sleep(wait)

def original_text(s):
    """수정 직전 원문. 조회 실패면 False(다음 주기에 재시도), 메시지에 text 가 없으면 None."""
    try:
        d = call("conversations.history", channel=s["channel"], latest=s["ts"], inclusive="true", limit=1)
    except Fail:
        return False
    if not d.get("ok"):
        return False
    for m in d.get("messages", []):
        if m.get("ts") == s["ts"]:
            return m.get("text")
    return None

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

def card_state(s, reactions):
    """번호 카드의 답 상태 (closed|hold|conflict, 표시 문구) 또는 None(답 없음).
    폴러가 해석한 답(pick·approve·hold)만 보며, 답이 하나로 모이고 보류가 아닐 때만 닫음. 범위 밖·이름 그대로 반응은 답이 아님"""
    did = s.get("decision_id")
    if not isinstance(did, str) or not DECISION_ID_RE.fullmatch(did):
        return None
    ans = [d for d in reactions if d.get("kind") == "decide" and d.get("decision_id") == did and d.get("action") in ANSWER_ACTIONS]
    keys = []
    for d in ans:
        n = d.get("pick")
        k = "hold" if d["action"] == "hold" else str(n) if isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= 4 else None
        if k and k not in keys:
            keys.append(k)
    if len(keys) > 1 or any(d.get("conflict") is True for d in ans):
        shown = ", ".join("보류" if k == "hold" else k for k in sorted(keys, key=lambda k: (k == "hold", k)))
        return "conflict", f"선택이 여러 개({shown}), 확인 필요"
    if not keys:
        return None
    if keys == ["hold"]:
        return "hold", "보류"
    last = [d for d in ans if d["action"] != "hold"][-1]
    return "closed", "답 도착(" + ("추천대로, " if last["action"] == "approve" else "") + f"선택 {keys[0]})"

def main():
    sent = jsonl(os.path.join(H, "slack-sent.jsonl"))
    inbox = jsonl(os.path.join(H, "slack-inbox.jsonl"))
    done_file = os.path.join(H, "slack-done.txt")
    reacted, by_msg = {}, {}
    for d in inbox:
        if d.get("type") == "reaction":
            reacted[d.get("parent_ts")] = d  # 같은 메시지에 여러 반응이면 마지막 것
            by_msg.setdefault(d.get("parent_ts"), []).append(d)
        elif d.get("type") == "thread_reply" and d.get("kind") == "decide" and d.get("action") in ANSWER_ACTIONS:
            by_msg.setdefault(d.get("parent_ts"), []).append(d)  # 스레드 글 선택·추천도 결정 답(반응과 같은 입력). 자유 글은 답이 아님
    # 번호 카드는 어떤 반응이든 닫지 않고 card_state 가 닫힘으로 본 것만 닫음. 옛 카드는 기존대로 반응이 있으면 닫음
    # 결정 번호 형식이 틀린 기록(손상·위조)은 번호 카드로 안 보고 옛 경로로 보냄: 줄에 번호를 그대로 넣지 않고, 상태가 없어 영원히 대기로 남는 일도 막음
    cards = {s["ts"]: card_state(s, by_msg.get(s["ts"], [])) for s in sent
             if s.get("ts") and s.get("form_kind") == "decide" and "options" in s
             and isinstance(s.get("decision_id"), str) and DECISION_ID_RE.fullmatch(s["decision_id"])}
    done = {t for t in reacted if t not in cards} | {t for t, st in cards.items() if st and st[0] == "closed"}
    done |= set(open(done_file).read().split()) if os.path.exists(done_file) else set()
    # 번호 카드 기록이면 번호가 손상됐어도 목록에는 남김(번호 없이 제목만 표시)
    decisions = [s for s in sent if s.get("ts") and not s.get("thread_ts")
                 and ("결정" in s.get("title", "") or s["ts"] in cards or (s.get("form_kind") == "decide" and "options" in s))]
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
        st = cards.get(s["ts"])
        action = st[1] if st else r.get("action", "완료")
        text = f"✅ {s['title']} — {action}" if st else f"✅ {s['title']} — 답: {action}"
        if DRY:
            print(f"수정 예정: {s['ts']} → {text}")
            continue
        # 수정하면 원문이 사라지므로 이력에 원문을 먼저 남김. title 은 기록 시 첫 줄 200자로 잘려 있음
        orig = original_text(s)
        if orig is False:
            continue
        log("answered_update", ts=s["ts"], title=s["title"], action=action,
            answered_at=r.get("received_at"), original_text=orig)
        try:
            res = call("chat.update", {"channel": s["channel"], "ts": s["ts"], "text": text})
        except Fail as e:
            # 429·예산 소진 예외에도 재시도 가능 표시를 남겨야 수정 사건만 있고 영구 미수정이 되는 일이 없음
            log("update_failed", ts=s["ts"], error=str(e), final=False)
            raise
        if not res.get("ok"):
            log("update_failed", ts=s["ts"], error=res.get("error"), final=res.get("error") in FINAL_UPDATE_ERRORS)

    rows, seen = [], set()
    for s in waiting:
        # 번호 카드는 제목이 같아도 결정 번호로 구분. 보류·충돌은 닫지 않고 상태를 덧붙임
        card = s["ts"] in cards
        key = ("card", s.get("decision_id"), s["ts"]) if card else s["title"]
        if key in seen:
            continue
        seen.add(key)
        link = call("chat.getPermalink", channel=s["channel"], message_ts=s["ts"]).get("permalink", "")
        row = f"• <{link}|{s['title']}>"
        if card:
            st = cards[s["ts"]]
            row += f" · {s.get('decision_id')}" + (f" — {st[1]}" if st else "")
        rows.append((s["title"], row))
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
        try:
            log("pending_posted", ts=new_ts, channel=channel, titles=[r[0] for r in rows])
        except OSError:
            # 이력에 없는 메시지는 다음 실행이 지울 수 없어 고아가 되므로 방금 보낸 것을 회수
            try:
                call("chat.delete", {"channel": channel, "ts": new_ts})
            except Fail:
                pass
            raise
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
