"""번호 카드 형식(slack_notify.sh --options·slack_poll.sh 번호 반응 해석) Slack 기록 생성. 사용: fixtures_cards.py <작업 폴더> <화면 표식>
main 형식(fixtures.py)과 섞어 둠: 옛 결정 카드 2건 + 번호 카드 7건. 화면 표식은 답 문구에 심어 산출물에 새지 않는지 봄."""
import json
import os
import sys
import time

hub, mark = sys.argv[1:3]
now = int(time.time())
OPTS = {"1": "오전 9시", "2": "오전 10시"}


def card(ts, did, title, age):
    return {"at": now - age, "channel": "C1", "ts": ts, "thread_ts": "", "title": title, "kind": "decide",
            "form_kind": "decide", "decision_id": did, "options": OPTS, "recommended": 1}


def answer(ts, did, action, pick=None, **extra):
    d = {"type": "reaction", "parent_ts": ts, "action": action, "kind": "decide", "decision_id": did,
         "option_text": f"{mark} 고른 문구"}
    if pick is not None:
        d["pick"] = pick
    return {**d, **extra}


sent = [
    {"at": now - 900, "channel": "C1", "ts": "1.1", "thread_ts": "", "title": "🙋 결정 옛 카드 대기", "kind": "decide"},
    {"at": now - 800, "channel": "C1", "ts": "1.2", "thread_ts": "", "title": "🙋 결정 옛 카드 반응", "kind": "decide"},
    card("2.1", "D-0001", "알림 시각", 700),        # 선택 1 → 닫힘
    card("2.2", "D-0002", "배포 순서", 600),        # 보류 → 대기(state hold)
    card("2.3", "D-0003", "문서 위치", 500),        # 1·2 둘 다 → 대기(state conflict)
    card("2.4", "D-0004", "같은 제목", 400),        # 답 없음 → 대기
    card("2.5", "D-0005", "같은 제목", 300),        # 제목이 같아도 번호 카드는 따로
    card("2.6", "D-0006", "이름 그대로 반응", 200),  # kind 없는 반응(해석 안 된 이모지) → 답 아님, 대기
    card("2.7", "D-0007", "완료 기록", 100),        # slack-done.txt → 닫힘
    card("2.8", "D-0008", "추천대로", 50),          # ✅ approve → 닫힘
]
inbox = [
    {"type": "reaction", "parent_ts": "1.2", "action": "white_check_mark"},
    answer("2.1", "D-0001", "pick", 1),
    answer("2.2", "D-0002", "hold"),
    answer("2.3", "D-0003", "pick", 1),
    answer("2.3", "D-0003", "pick", 2, conflict=True, choices=[1, 2]),
    {"type": "reaction", "parent_ts": "2.6", "action": "eyes"},
    answer("2.8", "D-0008", "approve", 1),
]
os.makedirs(hub, exist_ok=True)
with open(os.path.join(hub, "slack-sent.jsonl"), "w", encoding="utf-8") as f:
    for s in sent:
        f.write(json.dumps(s, ensure_ascii=False) + "\n")
with open(os.path.join(hub, "slack-inbox.jsonl"), "w", encoding="utf-8") as f:
    for d in inbox:
        f.write(json.dumps(d, ensure_ascii=False) + "\n")
open(os.path.join(hub, "slack-done.txt"), "w").write("2.7\n")
