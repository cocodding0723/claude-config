#!/usr/bin/env python3
"""남은 작업 리마인드. launchd(ai.lrm.workremind)가 30분마다 work_remind.sh 로 호출해 Slack DM 에 글 한 건을 올림.

글 순서: 내 차례(답을 기다리는 결정, 검수 대기 기능) → 진행 중(열린 기능의 단계·카드 수) → 멈춘 것(오래 갱신 없는 진행 중 카드).
보내는 조건: 내 차례가 한 건이라도 있으면 회차마다 다시 알림. 없으면 내용이 바뀐 회차에만 보냄. 남은 것이 없으면 보내지 않음.
새 글을 올린 뒤 앞 리마인드는 한 줄로 줄여 DM 에 옛 목록이 쌓이지 않게 함(줄이기 실패는 무시).
원료: 작업판(bd list, 읽기만)과 답 기록(answer_store, 읽기만). 판정 경우는 tests/work_remind/test_remind.py 의 표.

사용: work_remind.py [--dry-run] [--json]
  --dry-run  보내지 않고 글만 출력
환경: BOARD_DIR 작업판(기본 ~/orca/board) · BOARD_BD bd 실행 파일 · LRM_HUB_DIR 설정 폴더(기본 ~/.config/lrm-hubs)
      LRM_REMIND_NOTIFY 전송 스크립트(기본 같은 폴더 slack_notify.sh) · LRM_REMIND_STATE 상태 파일(기본 <설정 폴더>/work-remind.json)
      LRM_REMIND_STALL_HOURS 멈춤으로 볼 시간(기본 24) · LRM_REMIND_QUIET 보내지 않을 시간대 '시작-끝'(예 23-8, 기본 없음)
종료 코드: 0 보냄·보낼 것 없음·조용한 시간 · 3 작업판 조회 실패 · 4 전송 실패
"""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import answer_store  # noqa: E402
import pending_judge  # noqa: E402

TAG = "remind"
LIST_MAX = 8                 # 칸마다 보여 줄 줄 수. 넘으면 '그 밖에 N건'
STALL_HOURS = 24.0
NOTIFY_TIMEOUT_S = 60
REVIEW_LABEL = "stage:review"
FEATURE_HEAD, STAGE_HEAD = "feature:", "stage:"
TITLE_TAG = re.compile(r"^\s*(?:\[[^\]]*\]\s*)+")        # 제목 머리의 [LRM-684]·[개인] 같은 표시
TICKET = re.compile(r"\b[A-Z]{2,}-\d+\b")                # 본문에 티켓 번호를 넣지 않음(양식 A 규칙 5)
CLOCK = re.compile(r"\d{1,2}:\d{2}")


def flow_lib():
    """단계 이름 표(board/lib/board_flow.py). 설치본은 링크라 저장소 옆 폴더를 먼저 보고, 없으면 작업판 설치 위치. 못 찾으면 None"""
    for d in (os.path.join(HERE, "..", "..", "board", "lib"), os.path.expanduser("~/orca/board/lib")):
        if os.path.isfile(os.path.join(d, "board_flow.py")):
            sys.path.insert(0, d)
            import board_flow
            return board_flow
    return None


def name_of(card: dict) -> str:
    """사용자에게 보일 이름: 제목에서 머리 표시와 티켓 번호를 뺌"""
    text = TICKET.sub("", TITLE_TAG.sub("", str(card.get("title") or ""))).strip(" -·:")
    return re.sub(r"\s{2,}", " ", text) or str(card.get("id") or "")


def stamp(value) -> "float | None":
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def waiting(cards: list, rules: pending_judge.Rules, now: float) -> list:
    """답을 기다리는 결정 카드. cards 는 answer_store.AnswerStore.cards() (오래된 순, by = 답한 입구).
    같은 번호로 다시 올렸으면 가장 최근 카드만 봄: 옛 카드에 남은 답은 새 질문의 답이 아니고, 답한 새 카드 뒤에 옛 카드를 다시 알릴 이유도 없음"""
    latest = {}
    for c in cards:
        if pending_judge.is_decision(c) and isinstance(c.get("decision_id"), str):
            latest[c["decision_id"]] = c
    return [c for c in latest.values()
            if not c.get("by") and not pending_judge.is_auto(c, rules) and not pending_judge.is_old(c, rules, now)]


def reviews(cards: list) -> list:
    """사용자 검수를 기다리는 기능(완료 검사가 붙인 검수 대기 표시)"""
    return [{"name": name_of(c), "since": stamp(c.get("updated_at"))}
            for c in cards if c.get("issue_type") == "epic" and REVIEW_LABEL in (c.get("labels") or [])]


def features(cards: list, flow=None) -> list:
    """열린 기능(기능 label 이 있는 기능 카드)의 단계와 하위 카드 진행. 하위 카드는 id 가 '<기능 id>.' 로 시작하는 카드"""
    out = []
    for e in cards:
        labels = e.get("labels") or []
        if e.get("issue_type") != "epic" or e.get("status") == "closed" or not any(x.startswith(FEATURE_HEAD) for x in labels):
            continue
        kids = [c for c in cards if str(c.get("id") or "").startswith(e["id"] + ".")]
        key = next((x[len(STAGE_HEAD):] for x in labels if x.startswith(STAGE_HEAD)), "")
        st = flow.stage(key) if flow and key else None
        out.append({"name": name_of(e), "stage": f"{flow.order(st)} {st.name}" if st else (key or "단계 미정"),
                    "done": sum(c.get("status") == "closed" for c in kids), "total": len(kids)})
    return out


def stalled(cards: list, now: float, hours: float = STALL_HOURS) -> list:
    """진행 중인데 오래 갱신이 없는 카드(기능 카드 제외)"""
    out = []
    for c in cards:
        at = stamp(c.get("updated_at"))
        if c.get("status") == "in_progress" and c.get("issue_type") != "epic" and at and now - at > hours * 3600:
            out.append({"name": name_of(c), "days": int((now - at) // 86400)})
    return out


def section(title: str, rows: list) -> list:
    if not rows:
        return []
    more = [f"- 그 밖에 {len(rows) - LIST_MAX}건"] if len(rows) > LIST_MAX else []
    return ["", title] + rows[:LIST_MAX] + more


def build(now: float, decisions: list, reviews: list, features: list, stalled: list) -> str:
    """리마인드 글. 남은 것이 없으면 ''. 카드 번호·티켓 번호는 넣지 않음(사용자가 읽는 글)"""
    if not (decisions or reviews or features or stalled):
        return ""
    mine = len(decisions) + len(reviews)
    clock = dt.datetime.fromtimestamp(now).strftime("%H:%M")
    lines = [f"📋 남은 작업 {clock} — 내 차례 {f'{mine}건' if mine else '없음'}"]
    lines += section("🙋 내 차례", [f"- 결정: {TICKET.sub('', str(d.get('title') or d.get('decision_id'))).strip()}" for d in decisions]
                     + [f"- 검수: {r['name']}" for r in reviews])
    lines += section("🔄 진행 중", [f"- {f['name']}: {f['stage']}, 카드 {f['done']}/{f['total']}" for f in features])
    lines += section("⚠️ 멈춘 것", [f"- {s['name']}: {s['days']}일째 갱신 없음" if s["days"] else f"- {s['name']}: 하루 가까이 갱신 없음" for s in stalled])
    return "\n".join(lines)


def body_key(text: str) -> str:
    """내용이 바뀌었는지 비교할 값: 제목 줄의 시각을 뺀 글"""
    head, sep, rest = text.partition("\n")
    return CLOCK.sub("", head) + sep + rest


def should_send(text: str, mine: int, state: dict) -> bool:
    """보낼 회차인가. 내 차례가 있으면 같은 내용이어도 다시 알리고, 없으면 내용이 바뀐 때만"""
    if not text:
        return False
    return mine > 0 or body_key(text) != state.get("body")


def read_state(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def notify(script: str, args: list) -> str:
    """전송 스크립트 1회 호출 → 새 글 ts. 실패는 RuntimeError(사유 한 줄)"""
    try:
        p = subprocess.run([script, *args], capture_output=True, text=True, timeout=NOTIFY_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise RuntimeError(type(e).__name__)
    last = (p.stdout.strip().splitlines() or [""])[-1]
    if p.returncode == 0 and last.startswith("ok ") and last[3:].strip():
        return last[3:].strip()
    raise RuntimeError(" ".join((p.stderr.strip() or p.stdout.strip() or f"종료 코드 {p.returncode}").split())[:200])


def send(text: str, mine: int, script: str, state_path: str, now: float) -> str:
    """조건이 맞으면 새 글을 올리고 앞 리마인드를 한 줄로 줄임. 결과 'sent'|'skip'|'fail: 사유'.
    전송이 실패하면 상태를 그대로 둬 다음 회차에 다시 시도함"""
    state = read_state(state_path)
    if not should_send(text, mine, state):
        return "skip"
    try:
        ts = notify(script, ["--tag", TAG, "--kind", "progress", text])
    except RuntimeError as e:
        return f"fail: {e}"
    if state.get("ts"):
        try:  # 앞 글은 알림이 이미 갔으므로 본문만 줄임. 실패해도 새 글은 올라갔으니 넘어감
            notify(script, ["--update", state["ts"], f"📋 남은 작업 {state.get('clock', '')} 리마인드 — 아래 새 글로 바뀜".replace("  ", " ")])
        except RuntimeError:
            pass
    tmp = state_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"ts": ts, "body": body_key(text), "clock": dt.datetime.fromtimestamp(now).strftime("%H:%M"), "at": int(now)}, f, ensure_ascii=False)
    os.replace(tmp, state_path)
    return "sent"


def quiet_now(spec: str, hour: int) -> bool:
    """'시작-끝' 시간대(자정을 넘는 23-8 꼴 포함) 안인가. 틀린 값은 조용한 시간 없음으로 봄"""
    m = re.fullmatch(r"\s*(\d{1,2})\s*-\s*(\d{1,2})\s*", spec or "")
    if not m:
        return False
    a, b = int(m.group(1)) % 24, int(m.group(2)) % 24
    return a <= hour < b if a < b else (hour >= a or hour < b) if a != b else False


def board_cards_all() -> list:
    board = os.path.expanduser(os.environ.get("BOARD_DIR") or "~/orca/board")
    p = subprocess.run([os.environ.get("BOARD_BD", "bd"), "list", "--all", "--json", "-n", "0"], cwd=board, capture_output=True, text=True,
                       timeout=60, stdin=subprocess.DEVNULL)
    if p.returncode != 0:
        raise RuntimeError(f"bd list 실패: {(p.stderr or p.stdout).strip()[:200]}. 조치: BOARD_DIR·bd 설치 확인")
    return [c for c in json.loads(p.stdout or "[]") if isinstance(c, dict)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="work_remind.py", description="남은 작업 리마인드를 Slack DM 에 올림.", epilog=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="보내지 않고 글만 출력")
    ap.add_argument("--json", action="store_true", help="결과를 JSON 한 줄로")
    a = ap.parse_args(argv)
    hub = os.environ.get("LRM_HUB_DIR") or os.path.expanduser("~/.config/lrm-hubs")
    now = time.time()
    if not a.dry_run and quiet_now(os.environ.get("LRM_REMIND_QUIET", ""), dt.datetime.fromtimestamp(now).hour):
        print(json.dumps({"result": "quiet"}) if a.json else "조용한 시간 — 건너뜀")
        return 0
    try:
        cards = board_cards_all()
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as e:
        print(f"실패: {e}", file=sys.stderr)
        return 3
    rules = pending_judge.load_rules([hub, os.path.join(HERE, "..")], warn=lambda m: None)
    decisions = waiting(answer_store.AnswerStore(hub).cards(), rules, now)
    revs = reviews(cards)
    stall_h = float(os.environ.get("LRM_REMIND_STALL_HOURS") or STALL_HOURS)
    text = build(now, decisions, revs, features(cards, flow_lib()), stalled(cards, now, stall_h))
    mine = len(decisions) + len(revs)
    if a.dry_run:
        print(json.dumps({"result": "dry", "mine": mine, "text": text}, ensure_ascii=False) if a.json else (text or "남은 작업 없음"))
        return 0
    script = os.environ.get("LRM_REMIND_NOTIFY") or os.path.join(HERE, "slack_notify.sh")
    result = send(text, mine, script, os.environ.get("LRM_REMIND_STATE") or os.path.join(hub, "work-remind.json"), now)
    print(json.dumps({"result": result, "mine": mine}, ensure_ascii=False) if a.json else f"{dt.datetime.fromtimestamp(now):%m-%d %H:%M} {result} 내 차례 {mine}건")
    return 4 if result.startswith("fail") else 0


if __name__ == "__main__":
    sys.exit(main())
