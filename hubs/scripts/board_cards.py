"""작업판(bd) 사람 손 카드 도우미. 진입점은 board-ask·board-answer(같은 폴더).
패널(lrm-board)은 kind:decision·kind:user-todo·kind:approval 열린 카드를 결정·할 일·승인 칸으로 그림.
환경: BOARD_DIR(기본 ~/orca/board), BOARD_BD(기본 bd).
종료 코드: 0 정상(이미 있음·이미 닫힘 포함), 2 인자 오류, 3 bd 실패, 4 카드 없음"""
import argparse
import json
import os
import subprocess
import sys

KINDS = ("decision", "user-todo", "approval")
REASON_MAX = 80

ASK_HELP = """결정·할 일·승인 카드를 작업판에 만든다. 같은 종류·같은 제목의 열린 카드가 있으면 새로 만들지 않고 그 id 를 낸다.

예:
  board-ask "패널 정본을 작업판으로 옮길까?" --options "옮김|유지" --recommend 옮김 --parent lrm-q5o
  board-ask "릴리스 키스토어 만들기" --kind user-todo --why "출시 전 Google 로그인 필요"
  board-ask "PR #812 게시" --kind approval --json

출력: 카드 id 한 줄(--json 이면 {"id","created","kind"})"""

ANSWER_HELP = """사람 답 원문을 카드 코멘트로 남기고 카드를 닫는다. 이미 닫힌 카드면 아무것도 하지 않는다.

예:
  board-answer lrm-abc "옮김. 단 Slack 칸은 숨겨"
  board-answer lrm-abc "내일 다시 볼게" --keep-open

출력: 처리 결과 한 줄(--json 이면 {"id","closed","commented"})"""


class Fail(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


def bd(*args):
    cmd = [os.environ.get("BOARD_BD", "bd"), *args]
    try:
        p = subprocess.run(cmd, cwd=os.environ.get("BOARD_DIR") or os.path.expanduser("~/orca/board"),
                           capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Fail(3, f"bd 실행 실패({type(e).__name__}). 조치: bd 설치·BOARD_DIR 확인")
    if p.returncode != 0:
        raise Fail(3, f"bd {args[0]} 실패: {(p.stderr or p.stdout).strip()[:200]}. 조치: BOARD_DIR 가 작업판인지 확인")
    return p.stdout


def bd_json(*args):
    try:
        return json.loads(bd(*args, "--json") or "null")
    except ValueError:
        raise Fail(3, f"bd {args[0]} 출력이 JSON 아님. 조치: bd 버전 확인")


def ask(a):
    title = a.question.strip()
    if not title:
        raise Fail(2, "질문이 비었음. 조치: 첫 인자에 질문 한 문장")
    opts = [o.strip() for o in (a.options or "").split("|") if o.strip()]
    if a.kind == "decision" and len(opts) < 2:
        raise Fail(2, '결정 카드는 선택지 2개 이상 필요. 조치: --options "A|B"')
    if a.recommend is not None and a.recommend not in opts:
        raise Fail(2, f"추천 '{a.recommend}' 이 선택지에 없음. 조치: --options 안의 값 하나")
    label = f"kind:{a.kind}"
    for r in bd_json("list", "--label", label, "--limit", "0") or []:
        if r.get("title") == title and r.get("status") != "closed":
            return {"id": r["id"], "created": False, "kind": a.kind}
    lines = []
    if opts:
        lines.append("선택지:")
        lines += [f"- {o}" for o in opts]
    if a.recommend is not None:
        lines.append(f"추천: {a.recommend}")
    if a.why:
        lines.append(f"이유: {a.why}")
    args = ["create", title, "-l", label, "-p", str(a.priority)]
    if lines:
        args += ["-d", "\n".join(lines)]
    if a.parent:
        args += ["--parent", a.parent]
    if a.dry_run:
        return {"id": None, "created": False, "kind": a.kind, "dryRun": args}
    made = bd_json(*args)
    made = made[0] if isinstance(made, list) else made
    if not isinstance(made, dict) or "id" not in made:
        raise Fail(3, "bd create 결과에 id 없음. 조치: bd list 로 카드가 생겼는지 확인")
    return {"id": made["id"], "created": True, "kind": a.kind}


def answer(a):
    text = a.text.strip()
    if not text:
        raise Fail(2, "답 원문이 비었음. 조치: 두 번째 인자에 사람 답 그대로")
    rows = bd_json("list", "--all", "--id", a.id, "--limit", "0") or []
    card = next((r for r in rows if r.get("id") == a.id), None)
    if card is None:
        raise Fail(4, f"카드 {a.id} 없음. 조치: bd list --label kind:decision 로 id 확인")
    if card.get("status") == "closed":
        return {"id": a.id, "closed": True, "commented": False, "already": True}
    if a.dry_run:
        return {"id": a.id, "closed": False, "commented": False, "dryRun": True}
    bd("comments", "add", a.id, f"사용자 답: {text}")
    if a.keep_open:
        return {"id": a.id, "closed": False, "commented": True}
    reason = text if len(text) <= REASON_MAX else text[:REASON_MAX - 1] + "…"
    bd("close", a.id, "--reason", f"답: {reason}")
    return {"id": a.id, "closed": True, "commented": True}


def main(argv):
    if not argv or argv[0] not in ("ask", "answer"):
        print("사용: board_cards.py ask|answer ... (진입점 board-ask·board-answer)", file=sys.stderr)
        return 2
    cmd, rest = argv[0], argv[1:]
    fmt = argparse.RawDescriptionHelpFormatter
    if cmd == "ask":
        ap = argparse.ArgumentParser(prog="board-ask", description=ASK_HELP, formatter_class=fmt)
        ap.add_argument("question", help="카드 제목. 결정이면 질문 한 문장")
        ap.add_argument("--options", help='선택지 "A|B|C"(결정은 필수)')
        ap.add_argument("--recommend", help="추천 선택지(선택지 중 하나)")
        ap.add_argument("--why", help="추천 이유·배경 한 줄")
        ap.add_argument("--parent", help="상위 epic id")
        ap.add_argument("--kind", choices=KINDS, default="decision", help="카드 종류(기본 decision)")
        ap.add_argument("--priority", type=int, choices=range(5), default=1, help="0 최우선~4(기본 1)")
    else:
        ap = argparse.ArgumentParser(prog="board-answer", description=ANSWER_HELP, formatter_class=fmt)
        ap.add_argument("id", help="카드 id")
        ap.add_argument("text", help="사람 답 원문(고치지 않고 그대로)")
        ap.add_argument("--keep-open", action="store_true", help="코멘트만 남기고 닫지 않음")
    ap.add_argument("--json", action="store_true", help="JSON 한 줄 출력")
    ap.add_argument("--dry-run", action="store_true", help="작업판을 바꾸지 않고 할 일만 출력")
    try:
        a = ap.parse_args(rest)
    except SystemExit as e:
        return 0 if e.code == 0 else 2
    try:
        out = ask(a) if cmd == "ask" else answer(a)
    except Fail as e:
        print(f"board-{cmd}: {e}", file=sys.stderr)
        return e.code
    if a.json:
        print(json.dumps(out, ensure_ascii=False))
    elif cmd == "ask":
        print(out["id"] if out["id"] else f"(dry-run) bd {' '.join(out['dryRun'])}")
    else:
        print(f"{a.id}: {'이미 닫힘' if out.get('already') else '닫음' if out['closed'] else '코멘트만 남김' if out['commented'] else '(dry-run)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
