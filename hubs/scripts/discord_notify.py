"""slack_notify.sh 의 디스코드 구현. CHAT=discord 일 때 slack_notify.sh 가 같은 인자로 넘김.

봇 DM 에 글·파일을 올리고 slack-sent.jsonl 에 슬랙과 같은 모양으로 기록함(ts 자리에 메시지 ID).
결정 카드(--options)는 선택지 줄과 번호·✅·❌ 반응을 미리 달아 반응으로 답하게 함. 버튼은 없음(Gateway 수신기 필요).
슬랙 전용 표시(색 띠·맞춤 이모지·미리보기 펼침)와 양식 A 검사·경로 첨부 검사는 하지 않음.
--broadcast 는 받기만 함: 디스코드 답장은 원래 DM 목록에 보임.
종료 코드: 0 성공, 1 API 실패, 2 설정·인자 오류
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # -I 는 같은 폴더를 import 경로에 넣지 않음
import decide_card  # noqa: E402
import discord_api as dapi

KIND_EMOJI = {"decide": "🟠", "todo": "📌", "done": "✅", "progress": "🔵", "warn": "🔴", "doc": "📄"}
OPT_KEYS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"]
CONTENT_MAX = 2000  # 디스코드 메시지 글자 상한
CARD_ID_RE = re.compile(r"[a-z][a-z0-9]{1,15}-[a-z0-9]{1,12}(\.[0-9]{1,3})*")  # 작업판 카드 id. slack_notify.sh 와 같음


def die(msg, code=2):
    print(msg, file=sys.stderr)
    sys.exit(code)


def parse():
    p = argparse.ArgumentParser(prog="slack_notify.sh (discord)", add_help=True)
    for a in ("--tag", "--title", "--thread", "--kind", "--choices", "--emoji-set", "--options", "--recommend",
              "--decision-id", "--reversible", "--seq", "--todo-id", "--link-to", "--quote", "--to-user",
              "--from", "--subject", "--state", "--card", "--update"):
        p.add_argument(a, default="")
    p.add_argument("--attached", action="append", default=[])
    for a in ("--alive", "--no-doc", "--no-check", "--broadcast"):
        p.add_argument(a, action="store_true")
    p.add_argument("message")
    p.add_argument("file", nargs="?")
    return p.parse_args()


def card(a):
    """--options → (기록용 카드 필드, 본문에 붙일 줄). 형식은 slack_notify.sh 와 같음"""
    if not a.options:
        return {}, []
    if not (a.recommend and a.decision_id):
        die("--options 에는 --recommend·--decision-id 가 필요")
    opts, outcomes = {}, {}
    try:  # 슬랙판·미리 실행과 같은 파서: 문구 안 쉼표는 뒤에 '숫자=' 가 올 때만 구분자
        parsed = decide_card.options(a.options)
    except ValueError as e:
        die(str(e))
    for n, text, result in parsed:
        opts[n] = text
        if result:
            outcomes[n] = result
    if not 2 <= len(opts) <= 4 or set(opts) != {str(i) for i in range(1, len(opts) + 1)}:
        die("--options 범위 오류: 번호는 1부터 빠짐없이 2~4개 — 예 '1=가,2=나'")
    if a.recommend not in opts:
        die("--recommend 범위 오류: %s — 선택지 번호 중 하나" % a.recommend)
    rec = {"decision_id": a.decision_id, "options": opts, "recommended": int(a.recommend)}
    if outcomes:
        rec["outcomes"] = outcomes
    if a.reversible:
        rec["reversible"] = a.reversible
    if a.seq:
        rec["seq"] = a.seq
    lines = ["%s %s%s" % (OPT_KEYS[int(n) - 1], t, " → " + outcomes[n] if n in outcomes else "") for n, t in sorted(opts.items())]
    lines += ["⭐ 추천: %s" % OPT_KEYS[int(a.recommend) - 1], "👉 답: 번호 반응 · ✅ 추천대로 · ❌ 보류 · 답글 자유", "결정 번호: " + a.decision_id]
    return rec, lines


def dedup(a, hub, title):
    """--from 자동 알림 하루 1회 판정. 판정 도구가 실패하면 보냄(알림을 잃지 않는 쪽). 반환: 예약 ID 또는 ''"""
    if not a.from_ or a.thread or a.file or a.to_user or a.todo_id:
        return ""
    tool = os.environ.get("LRM_ALERT_DEDUP") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "alert_dedup.py")
    if not os.path.exists(tool):
        return ""
    r = subprocess.run([sys.executable, "-I", tool, "check", "--dir", hub, "--from", a.from_, "--hub", a.tag,
                        "--kind", a.kind or "text", "--title", title, "--subject", a.subject, "--state", a.state],
                       capture_output=True, text=True)
    out = r.stdout.strip()
    if r.returncode == 0 and out == "suppress":
        print("suppressed")
        sys.exit(0)
    return out[5:] if r.returncode == 0 and out.startswith("send ") and out[5:] != "-" else ""


def update(tok, me, mid, msg):
    """--update: 이 봇이 보낸 글 본문 교체. 새 글·sent 기록 없음. 같은 본문으로 다시 불러도 결과가 같음"""
    c = dapi.Client(tok)
    try:
        c.edit(c.dm_channel(me), mid, msg[:CONTENT_MAX])
    except dapi.ApiError as e:
        print(e)
        sys.exit(1)
    print("ok " + mid)


def main():
    a = parse()
    a.from_ = getattr(a, "from")
    if a.card and not CARD_ID_RE.fullmatch(a.card):
        die("--card 형식 오류: %s — 작업판 카드 id(예 lrm-q5o.2)로 지정" % a.card)
    if a.broadcast and (not a.thread or a.update):
        die("--broadcast 는 --thread 와 함께 새 답글을 보낼 때만 사용 — --thread <스레드 ts> 를 함께 지정")
    if a.update and (a.thread or a.to_user or a.options or a.file):
        die("--update 는 본문만 고침 — --thread·--to-user·--options·파일 인자를 빼고 다시 부르세요")
    if a.update and not a.update.isdigit():
        die("--update 형식 오류 — 디스코드 메시지 ID(숫자)")
    hub = os.environ.get("LRM_HUB_DIR", os.path.expanduser("~/.config/lrm-hubs"))
    if a.file and not os.path.isfile(a.file):
        die("첨부 파일 없음: " + a.file)
    try:
        tok, me = dapi.load_env(hub)
    except dapi.ApiError as e:
        die(str(e))
    rec_card, card_lines = card(a)
    msg = a.message
    if a.kind and a.kind in KIND_EMOJI and not msg.startswith(KIND_EMOJI[a.kind]):
        first, _, rest = msg.partition("\n")
        msg = "%s **%s**%s" % (KIND_EMOJI[a.kind], first, "\n" + rest if rest else "")
    if card_lines:
        msg += "\n" + "\n".join(card_lines)
    if a.todo_id:
        msg += "\n👉 끝나면 ✅ 반응 · 할 일 번호: " + a.todo_id
    title = (a.title or a.message.split("\n", 1)[0])[:200]
    if a.update:
        update(tok, me, a.update, msg)
        return
    rid = dedup(a, hub, title)

    c = dapi.Client(tok)
    try:
        to = a.to_user if re.fullmatch(r"\d{17,20}", a.to_user or "") else me  # Slack ID(U…) 등 디스코드 ID 가 아니면 본인 DM
        ch = c.dm_channel(to)
        ref = a.link_to or a.quote
        if ref:
            msg += "\n원본: " + dapi.link(ch, ref)
        if len(msg) > CONTENT_MAX:
            msg = msg[:CONTENT_MAX - 1] + "…"
        files = [a.file] if a.file else None
        d = c.post(ch, msg, reply_to=a.thread or None, files=files)
        if rec_card:
            for n in sorted(rec_card["options"]):
                c.react(ch, d["id"], OPT_KEYS[int(n) - 1])
            c.react(ch, d["id"], "✅")
            c.react(ch, d["id"], "❌")
    except dapi.ApiError as e:
        if rid:
            subprocess.run([sys.executable, "-I", os.path.join(os.path.dirname(os.path.abspath(__file__)), "alert_dedup.py"),
                            "rollback", "--dir", hub, "--rid", rid], capture_output=True)
        print(e)
        sys.exit(1)

    kind = "file" if a.file else "text"
    rec = {"at": int(time.time()), "channel": ch, "ts": d["id"], "thread_ts": a.thread, "kind": kind, "title": title, "tag": a.tag}
    if a.kind:
        rec["form_kind"] = a.kind
    if a.no_check:
        rec["unchecked"] = True
    if a.to_user:
        rec["to_user"] = a.to_user
    rec.update(rec_card)
    if a.todo_id:
        rec["todo_id"] = a.todo_id
    if a.card:
        rec["card"] = a.card
    if a.file:
        rec["file_id"] = d["id"]
        rec["path"] = a.file
    with open(os.path.join(hub, "slack-sent.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print("ok file_id=" + d["id"] if kind == "file" else "ok " + d["id"])


if __name__ == "__main__":
    main()
