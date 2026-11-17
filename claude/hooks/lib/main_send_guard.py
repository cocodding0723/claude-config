"""메인 터미널 대상 orca terminal send 판정(main-send-guard.sh 가 부름). stdin: 원본 명령.
출력: 거부면 hook JSON 한 줄(deny), 통과면 빈 출력. 판정 불가(main.env 읽기 실패)는 통과 + stderr 한 줄.
셸 분리: 줄바꿈·; && || | 로 나눈 단계마다 검사, 따옴표 안 글자도 단계로 해석(bash -c "..." 포함), 래퍼 접두(env·sudo 등)는 무시.
대상 판정: --terminal 값이 리터럴이면 main.env 의 MAIN_TERMINAL 과 접두 일치(짧은·긴 핸들 둘 다), 변수면 이름에 MAIN 이 든 것만 메인으로 봄.
main.env 는 main_terminal.read_handle 로 MAIN_TERMINAL 줄만 읽음."""
import json
import os
import re
import shlex
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
HOME = os.path.expanduser("~")
REASON = "메인 창 입력창은 사용자 전용 — 보고는 맡은 카드에 bd comments add <카드>, 카드가 없으면 ~/.config/lrm-hubs/board_notify.py post --kind other --source <허브> '<본문>'"
SEP = {";", "&&", "||", "|", "&", "|&", "(", ")"}
SHELLS = {"bash", "sh", "zsh", "dash"}
MIN_PREFIX = len("term_") + 4  # 이보다 짧은 접두는 아무 핸들과 맞아 오탐
VAR_RE = re.compile(r"^\$\{?([A-Za-z_][A-Za-z0-9_]*)")


def reader():
    """main_terminal.read_handle 재사용. 저장소 hubs/scripts → 설치된 ~/.config/lrm-hubs 순"""
    for d in (os.environ.get("LRM_HUB_SCRIPTS"), os.path.join(HERE, "..", "..", "..", "hubs", "scripts"), os.path.join(HOME, ".config", "lrm-hubs")):
        if d and os.path.isfile(os.path.join(d, "main_terminal.py")):
            sys.path.insert(0, os.path.realpath(d))
            import main_terminal
            return main_terminal.read_handle
    return None


def segments(cmd, depth=0):
    """명령을 단계(토큰 목록)로 분리. 셸 -c 문자열·$( ) 안은 다시 분리"""
    out, buf = [], ""
    for line in cmd.split("\n"):
        buf = f"{buf}\n{line}" if buf else line
        try:
            toks = list(_lex(buf))
        except ValueError:
            continue  # 따옴표가 줄을 넘어 이어짐
        buf = ""
        cur = []
        for t in toks:
            if t in SEP:
                cur and out.append(cur)
                cur = []
            else:
                cur.append(t)
        cur and out.append(cur)
    if buf:
        out.append(buf.split())  # 닫히지 않은 따옴표는 공백 분리로 대충 검사
    if depth < 3:
        for seg in list(out):
            for i, t in enumerate(seg):
                nested = []
                if i > 0 and seg[0].rsplit("/", 1)[-1] in SHELLS and re.fullmatch(r"-[a-zA-Z]*c", seg[i - 1]):
                    nested.append(t)
                nested += re.findall(r"\$\(([^()]*)\)", t) + re.findall(r"`([^`]*)`", t)
                for n in nested:
                    out += segments(n, depth + 1)
    return out


def _lex(text):
    lx = shlex.shlex(text, posix=True, punctuation_chars=";&|()")
    lx.whitespace_split = True
    lx.commenters = ""
    return lx


def send_target(seg):
    """orca terminal send 단계의 --terminal 값|None(해당 없음·대상 없음)"""
    for i in range(len(seg) - 1):
        if seg[i] == "terminal" and seg[i + 1] == "send" and any("orca" in t.rsplit("/", 1)[-1] for t in seg[:i]):
            rest = seg[i + 2:]
            for j, t in enumerate(rest):
                if t == "--terminal" and j + 1 < len(rest):
                    return rest[j + 1]
                if t.startswith("--terminal="):
                    return t.split("=", 1)[1]
    return None


def is_main(target, main):
    m = VAR_RE.match(target)
    if m:
        return "MAIN" in m.group(1).upper()
    if target.startswith("$"):
        return False  # $( ) 등 풀 수 없는 치환은 통과
    if not (target.startswith("term_") and len(target) >= MIN_PREFIX) or not main:
        return False
    return main.startswith(target) or target.startswith(main)


def main():
    cmd = sys.stdin.read()
    targets = [t for t in (send_target(s) for s in segments(cmd)) if t]
    if not targets:
        return
    env = os.environ.get("LRM_MAIN_ENV") or os.path.join(HOME, ".config", "lrm-hubs", "main.env")
    read = reader()
    handle = read(env) if read else None
    if not handle and any(not VAR_RE.match(t) for t in targets):
        print("main-send-guard: main.env 의 MAIN_TERMINAL 을 읽지 못함 — 리터럴 대상은 검사 없이 통과", file=sys.stderr)
    if any(is_main(t, handle) for t in targets):
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": REASON}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
