"""작업판(bd) 사람 손 카드 도우미. 진입점은 board-ask·board-answer(같은 폴더).
패널(lrm-board)은 kind:decision·kind:user-todo 열린 카드를 결정 대기·할 일 칸으로 그림.
승인 성격(PR 게시·병합 허락 등)은 따로 종류를 두지 않고 결정 카드에 label gate:approval 을 붙여 배지로만 구분(11-17, kind:approval 폐지).
환경: BOARD_DIR(기본 ~/orca/board), BOARD_BD(기본 bd).
결정 카드는 '돌려 보면 알 수 있나'(--runnable) 칸이 필수. yes 면 카드를 만들지 않음 — 동작·시간·배치·출력처럼 실행해 보면 아는 사실은
사람에게 묻지 않고 시제품·실측으로 판정함(제품 방향·취향만 묻는다). 승인 성격(--gate approval)은 사실 질문이 아니라 제외.
결정 카드는 Slack 번호 카드로 그대로 보낼 수 있는 칸(배경·용어·추천·선택지 2~4개와 각 결과)이 필수. 없으면 종료 2 —
결정의 원천을 작업판 한 곳으로 두려는 것이라, 칸이 비면 뒤에 Slack 으로 못 보내고 사람이 카드를 다시 손봐야 함.
종료 코드: 0 정상(이미 있음·이미 닫힘 포함), 2 인자 오류, 3 bd 실패, 4 카드 없음, 5 돌려 보면 아는 질문이라 거부"""
import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))  # -I 실행에서도 같은 폴더의 board_slack 을 찾음

KINDS = ("decision", "user-todo")
LEGACY_APPROVAL = "approval"  # 옛 --kind approval. decision + gate:approval 로 바꿔 받음
GATES = ("approval",)
APPROVAL_OPTIONS = ("승인 => 이대로 진행", "보류 => 진행하지 않고 카드를 열어 둠")
REASON_MAX = 80
OPTION_MAX = 4  # Slack 번호 카드 선택지 상한(board_slack 도 이 값을 씀)
RESULT_SEP = "=>"  # 선택지 '문구 => 고르면 달라지는 점'
RUNNABLE = ("yes", "no")
EXIT_RUNNABLE = 5
RUNNABLE_REFUSE = ("돌려 보면 알 수 있는 질문은 결정 카드로 묻지 않음. "
                   "조치: 시제품·실측(실행·촬영·시험)으로 정하고 결과와 근거를 작업 카드 코멘트·decisions.tsv 에 남김. "
                   "제품 방향·취향이라 실행으로 못 정하면 --runnable no")

ASK_HELP = """결정·할 일 카드를 작업판에 만든다. 같은 종류·같은 제목의 열린 카드가 있으면 새로 만들지 않고 그 id 를 낸다.
승인 성격은 --gate approval(결정 카드 + label gate:approval, 선택지를 안 주면 승인|보류).
Slack 자동 보내기 스위치(~/.config/lrm-hubs/.board-slack.on)가 켜져 있으면 새 결정 카드를 바로 Slack 번호 카드로 보냄(board_slack.py).

예:
  board-ask "패널 정본을 작업판으로 옮길까?" --runnable no --parent lrm-q5o \\
    --options "옮김 => 손 칸을 없애 카드 하나로 관리|유지 => 지금처럼 손 칸을 같이 둠" --recommend 옮김 \\
    --background "패널 칸이 손 입력과 카드 두 곳이라 어긋남" --terms "패널 정본: 패널이 읽는 원본 데이터"
  board-ask "PR #812 게시" --gate approval --recommend 승인 --background "draft PR 을 올려 리뷰를 받음" --terms 없음
  board-ask "릴리스 키스토어 만들기" --kind user-todo --why "출시 전 Google 로그인 필요"

결정 카드 필수 칸: --options(2~4개, 각 '문구 => 결과') · --recommend · --background · --terms(없으면 '없음') · --runnable no

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


def option_name(opt):
    """'문구 => 결과' 의 문구. 결과가 없으면 그대로"""
    return opt.split(RESULT_SEP, 1)[0].strip()


def card_gaps(options, background, terms):
    """Slack 번호 카드 필수 칸 중 빠진 것의 이름 목록. board-ask(만들 때)와 board_slack(보낼 때)이 같은 기준을 씀"""
    gaps = [n for n, v in (("배경", background), ("용어", terms)) if not v]
    if not 2 <= len(options) <= OPTION_MAX:
        gaps.append(f"선택지 2~{OPTION_MAX}개")
    if any(RESULT_SEP not in o or not o.split(RESULT_SEP, 1)[1].strip() for o in options):
        gaps.append("선택지 결과('문구 => 고르면 달라지는 점')")
    return gaps


def ask(a):
    title = a.question.strip()
    if not title:
        raise Fail(2, "질문이 비었음. 조치: 첫 인자에 질문 한 문장")
    if a.kind == LEGACY_APPROVAL:
        print("board-ask: --kind approval 은 폐지됨 → 결정 카드 + gate:approval 로 만듦. 조치: --gate approval 사용", file=sys.stderr)
        a.kind, a.gate = "decision", "approval"
    if a.gate and a.kind != "decision":
        raise Fail(2, "--gate 는 결정 카드에만. 조치: --kind 를 빼거나 decision")
    if a.kind == "decision" and not a.gate:
        if a.runnable is None:
            raise Fail(2, "결정 카드는 '돌려 보면 알 수 있나' 칸 필수. 조치: 실행·시제품으로 알 수 있으면 --runnable yes(거부됨), 방향·취향이면 --runnable no")
        if a.runnable == "yes":
            raise Fail(EXIT_RUNNABLE, RUNNABLE_REFUSE)
    opts = [o.strip() for o in (a.options or "").split("|") if o.strip()]
    if a.gate == "approval" and not opts:
        opts = list(APPROVAL_OPTIONS)
    if a.kind == "decision":
        gaps = card_gaps(opts, a.background, a.terms)
        if a.recommend is None:
            gaps.append("추천")
        if gaps:
            raise Fail(2, f"결정 카드 필수 칸 없음: {', '.join(gaps)}. Slack 번호 카드로 못 보내는 카드가 생김. "
                          "조치: --options \"A => 결과|B => 결과\" --recommend A --background \"…\" --terms \"…\"(없으면 없음) 를 채워 다시 실행")
    names = [option_name(o) for o in opts]
    if a.recommend is not None and a.recommend not in names:
        raise Fail(2, f"추천 '{a.recommend}' 이 선택지에 없음. 조치: --options 안의 값 하나")
    label = f"kind:{a.kind}"
    for r in bd_json("list", "--label", label, "--limit", "0") or []:
        if r.get("title") == title and r.get("status") != "closed":
            return {"id": r["id"], "created": False, "kind": a.kind, "gate": a.gate}
    lines = []
    if opts:
        lines.append("선택지:")
        lines += [f"- {o}" for o in opts]
    if a.recommend is not None:
        lines.append(f"추천: {a.recommend}")
    if a.why:
        lines.append(f"이유: {a.why}")
    if a.kind == "decision" and not a.gate:
        lines.append("돌려 보면 알 수 있나: 아니오")
    if a.background:
        lines.append(f"배경: {a.background}")
    if a.terms:
        lines.append(f"용어: {a.terms}")
    labels = label + (f",gate:{a.gate}" if a.gate else "")
    args = ["create", title, "-l", labels, "-p", str(a.priority)]
    if lines:
        args += ["-d", "\n".join(lines)]
    if a.parent:
        args += ["--parent", a.parent]
    if a.dry_run:
        return {"id": None, "created": False, "kind": a.kind, "gate": a.gate, "dryRun": args}
    made = bd_json(*args)
    made = made[0] if isinstance(made, list) else made
    if not isinstance(made, dict) or "id" not in made:
        raise Fail(3, "bd create 결과에 id 없음. 조치: bd list 로 카드가 생겼는지 확인")
    if a.kind == "decision":
        import board_slack  # 서로 참조라 호출 때 불러옴
        board_slack.auto_push(made["id"])
    return {"id": made["id"], "created": True, "kind": a.kind, "gate": a.gate}


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
        ap.add_argument("--options", help='선택지 "A => 고르면 달라지는 점|B => …"(결정은 2~4개·결과 필수)')
        ap.add_argument("--recommend", help="추천 선택지 문구(선택지 중 하나, 결정은 필수)")
        ap.add_argument("--why", help="추천 이유·배경 한 줄")
        ap.add_argument("--parent", help="상위 epic id")
        ap.add_argument("--kind", choices=KINDS + (LEGACY_APPROVAL,), default="decision", help="카드 종류(기본 decision). approval 은 폐지된 옛 값")
        ap.add_argument("--gate", choices=GATES, help="승인 성격 배지(label gate:<값>). 결정 카드에만")
        ap.add_argument("--runnable", choices=RUNNABLE, help="돌려 보면 알 수 있나(결정 카드 필수, 승인 성격 제외). yes 면 거부: 시제품·실측으로 판정")
        ap.add_argument("--background", help="배경 한 줄(결정 카드 필수)")
        ap.add_argument("--terms", help="낯선 말 풀이 한 줄, 없으면 '없음'(결정 카드 필수)")
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
