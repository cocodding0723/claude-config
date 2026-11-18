"""slack_pending.sh 의 디스코드 구현. CHAT=discord 일 때 slack_pending.sh 가 같은 인자로 넘김.

답 안 한 결정 카드 목록을 새 메시지로 보내고 이전 목록 메시지는 지움(수정은 알림이 안 가서 새로 보냄).
이력은 slack-pending-history.jsonl 에 추가. 이력 기록이 실패하면 삭제하지 않음.
슬랙판과 달리 답한 카드를 결과 한 줄로 고치지 않음.
사용: slack_pending.sh [--dry-run]. 종료 코드: 0 성공, 1 API 실패, 2 설정 오류, 75 이미 실행 중
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # -I 는 같은 폴더를 import 경로에 넣지 않음
import discord_api as dapi
from discord_poll import load_jsonl

ANSWER_ACTIONS = ("pick", "approve", "hold")
LOCK_STALE = 600  # 10분 넘은 잠금은 죽은 실행의 잔재로 봄


def pending(hub):
    answered = {d.get("decision_id") for d in load_jsonl(os.path.join(hub, "slack-inbox.jsonl"))
                if d.get("action") in ANSWER_ACTIONS and d.get("decision_id")}
    out, ids = [], set()
    for r in load_jsonl(os.path.join(hub, "slack-sent.jsonl")):
        did = r.get("decision_id")
        if r.get("form_kind") == "decide" and did and did not in answered and did not in ids:
            ids.add(did)
            out.append(r)
    return out


def main():
    dry = "--dry-run" in sys.argv[1:]
    hub = os.environ.get("LRM_HUB_DIR", os.path.expanduser("~/.config/lrm-hubs"))
    lock = os.path.join(hub, ".slack-pending.lock")
    if os.path.isdir(lock) and time.time() - os.path.getmtime(lock) > LOCK_STALE:
        os.rmdir(lock)
    try:
        os.mkdir(lock)
    except FileExistsError:
        print("이미 실행 중 — 건너뜀")
        sys.exit(75)
    try:
        run(hub, dry)
    finally:
        os.rmdir(lock)


def run(hub, dry):
    items = pending(hub)
    lines = ["🟠 **답 기다리는 결정 %d건**" % len(items)] if items else []
    for r in items:
        lines.append("- %s · %s" % (r["decision_id"], r.get("title", "")[:80]))
    text = "\n".join(lines)
    if dry:
        print(text or "답 기다리는 결정 없음")
        return
    try:
        tok, user = dapi.load_env(hub)
    except dapi.ApiError as e:
        print(e, file=sys.stderr)
        sys.exit(2)
    state_path = os.path.join(hub, "discord-pending-state.json")
    try:
        with open(state_path, encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {}
    c = dapi.Client(tok)
    try:
        ch = c.dm_channel(user)
        new_id = c.post(ch, text)["id"] if items else ""
        old = state.get("msg")
        if old:
            with open(os.path.join(hub, "slack-pending-history.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps({"at": int(time.time()), "deleted": old, "channel": ch}, ensure_ascii=False) + "\n")
            try:
                c.delete(ch, old)
            except dapi.ApiError as e:
                print("이전 목록 삭제 실패(건너뜀): %s" % e, file=sys.stderr)
    except dapi.ApiError as e:
        print(e, file=sys.stderr)
        sys.exit(1)
    state["msg"] = new_id
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f)
    print("목록 %d건 보냄" % len(items) if items else "답 기다리는 결정 없음 — 이전 목록만 정리")


if __name__ == "__main__":
    main()
