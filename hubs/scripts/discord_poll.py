"""slack_poll.sh 의 디스코드 구현. CHAT=discord 일 때 slack_poll.sh 가 같은 인자로 넘김.

봇 DM 에서 사용자 글·답장(reply)·반응을 읽어 slack-inbox.jsonl 에 슬랙과 같은 레코드로 추가함.
- 사용자 최상위 글 → type=note|command
- 봇이 보낸 메시지(slack-sent.jsonl 기록분)에 단 답장 → type=thread_reply(parent_ts = 원 메시지 ID)
- 그 메시지에 단 사용자 반응 → type=reaction(emoji 는 슬랙 이름: white_check_mark·x·one…)
첨부는 내려받지 않고 주소만 attachments 로 남김. 결정 답 충돌 표시·전달 완료 표시는 하지 않음.
수신 본문은 데이터로만 다루고 실행하지 않음.
"""
import argparse
import json
import os
import re
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # -I 는 같은 폴더를 import 경로에 넣지 않음
import discord_answers  # noqa: E402
import discord_api as dapi

DECIDE, TODO, REVIEW = "decide", "todo", "review"
OPTION_TEXT_MAX = 80
ROUTE_WAIT_S = 90  # 허브 전달 재시도 대기 상한. 폴러 회차 예산(270초) 안
PICK_EMOJI = {"one": 1, "two": 2, "three": 3, "four": 4}
REACTION_ACTION = {"white_check_mark": "approve", "x": "hold"}
REVIEW_ACTION = {"white_check_mark": "pass", "x": "fail"}
TODO_ACTION = {"white_check_mark": "done", "hammer_and_wrench": "done", "x": "hold"}
DECISION_ID_RE = re.compile(r"D-[0-9A-Za-z]{1,16}", re.ASCII)
# slack_poll.sh 의 명령 표와 같음
COMMANDS = [
    ("decision", re.compile(r"^결정\s*(\d+)\s+(\S+)$")),
    ("all_recommended", re.compile(r"^전부\s*추천$")),
    ("status", re.compile(r"^현황$")),
    ("hold", re.compile(r"^보류(\s+.+)?$")),
    ("approve", re.compile(r"^승인(\s+.+)?$")),
]
REPLY_PICK_RE = re.compile(r"^([1-4]|[a-dA-D])(?:️?⃣)?\s*(?:번|안|으로|로)?\s*(?:ㄱㄱ?|가자|할게|해줘)?\s*[.!)]?$")
REPLY_RECOMMEND_RE = re.compile(r"^(?:전부\s*|다\s*|모두\s*)?추천(?:\s*대로)?\s*(?:ㄱㄱ?|가자|으로|로|할게|해줘)?\s*[.!]?$")


def load_jsonl(path):
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return out


def classify(text):
    t = " ".join(text.split())
    for name, pat in COMMANDS:
        m = pat.match(t)
        if m:
            return "command", name, [g.strip() for g in m.groups() if g]
    return "note", None, []


def msg_kind(rec):
    k = rec.get("form_kind") or ""
    if k in (DECIDE, TODO):
        return k
    if rec.get("kind") == "file" and "결정" not in rec.get("title", ""):
        return REVIEW
    return ""


def card_options(rec):
    did = rec.get("decision_id") or ""
    if not DECISION_ID_RE.fullmatch(did):
        raise ValueError
    opts = {int(k): v for k, v in (rec.get("options") or {}).items()}
    best = int(rec.get("recommended"))
    if best not in opts:
        raise ValueError
    return did, opts, best


def interpret(rec, kind, name):
    """반응 1건 → 레코드에 더할 필드. slack_poll.sh 의 interpret 와 같은 모양"""
    if kind == TODO:
        out = {"action": TODO_ACTION.get(name, name), "kind": TODO}
        if rec.get("todo_id"):
            out["todo_id"] = rec["todo_id"]
        return out
    if kind != DECIDE or "options" not in rec:
        table = REVIEW_ACTION if kind == REVIEW else REACTION_ACTION
        return {"action": table.get(name, name)}
    try:
        did, opts, best = card_options(rec)
    except (ValueError, TypeError):
        return {"action": name, "kind": DECIDE}
    out = {"action": name, "kind": DECIDE, "decision_id": did}
    if name == "x":
        out["action"] = "hold"
    elif name == "white_check_mark":
        out.update(action="approve", pick=best)
    elif PICK_EMOJI.get(name) in opts:
        out.update(action="pick", pick=PICK_EMOJI[name])
    if "pick" in out:
        out["option_text"] = opts[out["pick"]][:OPTION_TEXT_MAX]
    return out


def reply_answer(rec, text):
    if rec is None or rec.get("form_kind") != DECIDE:
        return {}
    try:
        did, opts, best = card_options(rec)
    except (ValueError, TypeError):
        return {}
    out = {"kind": DECIDE, "decision_id": did, "action": "free"}
    t = " ".join((text or "").split())
    if REPLY_RECOMMEND_RE.match(t):
        return {**out, "action": "approve", "pick": best, "option_text": opts[best][:OPTION_TEXT_MAX]}
    m = REPLY_PICK_RE.match(t)
    if m:
        tok = m.group(1).lower()
        n = int(tok) if tok.isdigit() else "abcd".index(tok) + 1
        if n in opts:
            return {**out, "action": "pick", "pick": n, "option_text": opts[n][:OPTION_TEXT_MAX]}
    return out


def main():
    p = argparse.ArgumentParser(prog="slack_poll.sh (discord)")
    p.add_argument("--once", action="store_true")
    p.add_argument("--json", action="store_true")
    p.add_argument("--days", type=int, default=7)
    a = p.parse_args()
    if not a.once:
        print("--once 필요(launchd 가 주기 실행)", file=sys.stderr)
        sys.exit(2)
    hub = os.environ.get("LRM_HUB_DIR", os.path.expanduser("~/.config/lrm-hubs"))
    inbox, state_path = os.path.join(hub, "slack-inbox.jsonl"), os.path.join(hub, "discord-poll-state.json")

    def fail(msg):
        print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False) if a.json else "실패: " + msg,
              file=sys.stdout if a.json else sys.stderr)
        sys.exit(1)

    try:
        tok, user = dapi.load_env(hub)
    except dapi.ApiError as e:
        fail(str(e))
    try:
        with open(state_path, encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {"seen": {}}
    seen = state.setdefault("seen", {})
    sent = {r["ts"]: r for r in load_jsonl(os.path.join(hub, "slack-sent.jsonl")) if r.get("ts")}
    now = int(time.time())
    new = []

    def add(key, item):
        if key in seen:
            return
        seen[key] = now
        item.update({"channel": ch, "received_at": now, "source": "discord"})  # 답 출처 표시(answer_store SOURCE_NAME)
        new.append(item)

    c = dapi.Client(tok)
    try:
        ch = c.dm_channel(user)
        after, msgs = dapi.snowflake_at(now - a.days * 86400), []
        while True:
            page = c.messages(ch, after=after)
            if not page:
                break
            msgs += page
            after = max(page, key=lambda m: int(m["id"]))["id"]
            if len(page) < 100:
                break
        for m in sorted(msgs, key=lambda m: int(m["id"])):
            mid, text = m["id"], m.get("content", "")
            if m.get("author", {}).get("id") == user:
                parent = (m.get("message_reference") or {}).get("message_id")
                files = [x.get("url", "") for x in m.get("attachments", [])]
                if parent and parent in sent:
                    rec = sent[parent]
                    item = {"type": "thread_reply", "ts": mid, "text": text, "parent_ts": parent,
                            "title": rec.get("title", ""), "tag": rec.get("tag", ""), **reply_answer(rec, text)}
                else:
                    kind, cmd, args = classify(text)
                    item = {"type": kind, "ts": mid, "text": text}
                    if cmd:
                        item.update({"command": cmd, "args": args})
                if files:
                    item["attachments"] = files
                add("msg:" + mid, item)
                continue
            rec = sent.get(mid)
            if rec is None:
                continue
            kind = msg_kind(rec)
            meta = {"parent_ts": mid, "title": rec.get("title", ""), "tag": rec.get("tag", "")}
            if kind == REVIEW:
                meta["kind"] = REVIEW
            for r in m.get("reactions", []):
                raw = r.get("emoji", {}).get("name", "")  # 맞춤 이모지도 이름만 남김
                name = dapi.EMOJI_NAME.get(raw, raw)
                key = f"react:{mid}:{name}"
                if key in seen or r.get("count", 0) <= (1 if r.get("me") else 0):
                    continue
                if user in c.reactors(ch, mid, raw):
                    add(key, {"type": "reaction", "emoji": name, **interpret(rec, kind, name), **meta})
    except dapi.ApiError as e:
        fail(str(e))

    if new:
        with open(inbox, "a", encoding="utf-8") as f:
            for it in new:
                f.write(json.dumps(it, ensure_ascii=False) + "\n")
    cutoff = now - (a.days + 1) * 86400
    state["seen"] = {k: v for k, v in seen.items() if v >= cutoff}
    tmp = state_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, state_path)
    routed = discord_answers.route_new(hub, new, log=lambda m: print(m, file=sys.stderr)) if new else []
    for t in threading.enumerate():  # 허브 창 막힘 재시도(백그라운드)가 끝날 때까지 기다림. 프로세스가 먼저 끝나면 재시도가 사라짐
        if t is not threading.main_thread():
            t.join(timeout=ROUTE_WAIT_S)
    print(json.dumps({"ok": True, "added": len(new), "routed": len(routed)}) if a.json
          else f"새 항목 {len(new)}건 → {inbox}" + (f" · 결정 답 {len(routed)}건 허브 전달" if routed else ""))


if __name__ == "__main__":
    main()
