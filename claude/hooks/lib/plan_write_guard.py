"""계획 승인 기록·승인 원장 쓰기 판정(PreToolUse).
- 계획 승인 기록 <허브>/plan-approvals/*.json: 메인 세션만 씀(main_role.is_main).
- 원장 slack-inbox.jsonl(Slack 수신기만)·user-approvals.jsonl(사용자 '!' 실행 board-approve 만)·세션 기록(projects/*/*.jsonl):
  승인 인용 대조의 근거라 모든 Claude 세션에서 거부.
- board-approve 실행: 사용자가 프롬프트에서 '!' 로만 실행(모델 호출 거부). '!' 실행은 이 훅을 거치지 않음.
Bash 는 낱말 대신 쓰기 대상을 해석함: 리다이렉트·cp/mv/rsync/tee/sed -i/dd of=/curl -o 등의 대상, cd·변수 대입·~·glob 을 풀어
실제 경로로 비교. 읽기(cat·grep·tail … | python3 -m json.tool, 다른 곳으로 리다이렉트)는 통과.
대상을 못 푸는 경우($( )·모르는 변수)와 설치 밖 스크립트·인라인 코드는 보호 이름 조각이 보이면 거부(보수적).
단, 걸린 것이 tests/ 아래 시험 스크립트 실행뿐이면 거부 대신 격리 실행기(test-box)로 바꿔 통과시킴(boxed). 운영체제가 상자 밖 쓰기를 막아
거부와 같은 보호를 유지하면서 재시도 한 번을 없앰(11-20 집계: 이 훅 거부의 대부분이 시험 실행).
stdin: hook JSON. 출력: 거부·바꿔 실행이면 hook JSON 한 줄, 통과면 빈 출력."""
import fnmatch
import glob
import json
import os
import re
import shlex
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import main_role  # noqa: E402

DIR = "plan-approvals"
LEDGERS = ("slack-inbox.jsonl", "user-approvals.jsonl")
APPROVE_CMD = "board-approve"
FILE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
FRAGMENTS = re.compile(r"plan[-_]?appr|slack[-_]?inbox|user[-_]?approv|board[-_]?approve|projects/[^\s/]+/[^\s/]*\.jsonl", re.I)
ALL_ARGS = {"rm", "rmdir", "touch", "mkdir", "chmod", "chown", "truncate", "tee", "sponge", "unlink", "shred", "mv"}
LAST_ARG = {"cp", "ln", "install", "rsync", "scp", "ditto"}
INPLACE = {"sed", "perl", "gsed"}
SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}
INTERP = {"python", "python3", "node", "ruby", "perl", "php"}
# 셸·파이썬이 시작할 때 읽어 실행하는 파일을 가리키는 환경 변수. 신뢰 스크립트 앞에 붙이면 그 파일이 먼저 실행되므로 스크립트로 검사
STARTUP_VARS = {"BASH_ENV", "ENV", "PYTHONSTARTUP"}
WRAPPERS = {"sudo", "env", "exec", "command", "nohup", "time", "nice", "xargs", "builtin", "caffeinate"}
CODE_WRITE = re.compile(r"open\([^)]*['\"][wax+]|write_text|write_bytes|\.write\(|shutil\.|os\.(rename|replace|remove|unlink|makedirs|mkdir)|"
                        r"json\.dump\(|writeFile|appendFile|\bFile\.write|>\s*\S|\b(cp|mv|rm|tee|touch)\b")
REASON_PLAN = ("계획 승인 기록은 메인 코디네이터만 씀 — 사용자 컨펌 원문을 메인에 전달하면 메인이 "
               "~/.config/lrm-hubs/plan-approvals/<plan-id>.json 에 기록함. 메인 세션은 메인 체크아웃에서 시작한 세션(또는 ORCA_ROLE=main)")
REASON_LEDGER = ("{name} 은 사람이 낸 승인의 원장이라 세션에서 쓰지 않음(slack-inbox 는 Slack 수신기, user-approvals 는 사용자 '!' board-approve). "
                 "시험은 임시 HOME 에서")
REASON_TRANSCRIPT = "세션 기록(projects/*/*.jsonl)은 사용자 '!' 승인 대조의 근거라 세션에서 고치지 않음"
REASON_APPROVE = ("board-approve 는 사용자가 프롬프트에서 직접 '! ~/orca/board/bin/board-approve <repo> <branch> \"<원문>\"' 로 실행함. "
                  "모델은 실행하지 않고 사용자에게 그 한 줄을 안내")
HINT_TEST = " 시험 스크립트면 ~/.config/lrm-hubs/test-box <스크립트> 로 실행(임시 폴더 안에 묶여 돌아 기록을 못 고침)"
HINT_EDIT = " 파일을 고치는 중이면 Edit 도구로(대상 경로가 보여 판정 가능)"
BOX_NAME = "test-box"
BOX_SCRIPT_RE = re.compile(r"(^|/)tests/[^\s$`*?\[\]'\"\\]+$")  # 상자로 돌릴 수 있는 시험 스크립트 경로(리터럴만)
BOX_RUNNERS = r"(?:bash|sh|zsh|python3?)"  # test-box 가 확장자로 고르는 실행기와 같은 범위
BOX_NOTE = "시험 스크립트를 {box} 안에서 실행함(임시 폴더 격리: 상자 밖 쓰기·바깥 네트워크 막힘). 실패가 격리 때문인지 보려면 {box} --keep"
REASON_OPAQUE ="쓰기 대상을 해석할 수 없음({why}) — 보호 기록({frag}) 쓰기일 수 있어 거부. 경로를 리터럴로 쓰거나 다른 방법으로"


def hub_dir():
    return os.path.realpath(os.environ.get("LRM_HUB_DIR") or os.path.expanduser("~/.config/lrm-hubs"))


def config_dirs():
    out = {os.path.realpath(os.path.expanduser("~/.claude"))}
    if os.environ.get("CLAUDE_CONFIG_DIR"):
        out.add(os.path.realpath(os.environ["CLAUDE_CONFIG_DIR"]))
    return out


def under(path, top):
    return path == top or path.startswith(top.rstrip("/") + "/")


def classify(path, destructive=False):
    """실제 경로 → ('plan'|'ledger'|'transcript', 이름) 또는 None. destructive 면 보호 위치의 상위 폴더도 걸림"""
    real = os.path.realpath(path)
    plan = os.path.join(hub_dir(), DIR)
    if under(real, plan) or (destructive and under(plan, real)):
        return ("plan", DIR)
    base = os.path.basename(real)
    if base in LEDGERS:
        return ("ledger", base)
    if destructive and any(under(os.path.join(hub_dir(), n), real) for n in LEDGERS if os.path.exists(os.path.join(hub_dir(), n))):
        return ("ledger", "원장 폴더")
    for top in {os.path.realpath(os.path.join(cfg, "projects")) for cfg in config_dirs()}:
        if under(real, top) and real != top:
            rel = real[len(top) + 1:].split("/")
            if len(rel) == 2 and rel[1].endswith(".jsonl"):
                return ("transcript", base)
            if destructive and len(rel) == 1:
                return ("transcript", "세션 기록 폴더")
        elif destructive and under(top, real):
            return ("transcript", "세션 기록 폴더")
    return None


class Ctx:
    def __init__(self, cwd):
        self.cwd = cwd or os.getcwd()
        self.vars = {}
        self.hits = []      # (종류, 이름)
        self.opaque = []    # 해석 못 한 이유


def expand_word(word, ctx):
    """변수·~ 확장(경로로 만들지 않음). 못 풀면 None"""
    if "$(" in word or "`" in word:
        return None
    def sub(m):
        k = m.group(1) or m.group(2)
        if k in ctx.vars:
            return ctx.vars[k]
        if k in os.environ:
            return os.environ[k]
        raise KeyError(k)
    try:
        w = re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)", sub, word)
    except KeyError:
        return None
    return None if "$" in w else os.path.expanduser(w)


def expand(word, ctx):
    """경로로 확장(상대 경로는 추적 중인 cwd 기준). 못 풀면 None"""
    w = expand_word(word, ctx)
    if w is None:
        return None
    return w if os.path.isabs(w) else os.path.join(ctx.cwd, w)


def dest(word, ctx, destructive=False):
    p = expand(word, ctx)
    if p is None:
        ctx.opaque.append(f"대상 {word}")
        return
    cands = glob.glob(p) if any(c in p for c in "*?[") else [p]
    if any(c in p for c in "*?[") and not cands:
        for prot in [os.path.join(hub_dir(), DIR, "x.json")] + [os.path.join(hub_dir(), n) for n in LEDGERS]:
            if fnmatch.fnmatch(prot, p) or fnmatch.fnmatch(os.path.dirname(prot), p.rstrip("/").rsplit("/", 1)[0]):
                ctx.hits.append(("plan" if DIR in prot else "ledger", os.path.basename(prot)))
    for c in cands:
        hit = classify(c, destructive)
        if hit:
            ctx.hits.append(hit)


def trusted_script(path):
    """설치된 도구(설정 저장소·~/.claude·~/.config/lrm-hubs·~/orca/board)는 내용 검사 생략"""
    real = os.path.realpath(path)
    roots = [os.path.realpath(os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "..")),
             os.path.realpath(os.path.expanduser("~/.claude")), hub_dir(), os.path.realpath(os.path.expanduser("~/orca/board"))]
    return any(under(real, r) for r in roots)


def code_writes(code):
    return bool(FRAGMENTS.search(code)) and bool(CODE_WRITE.search(code))


def split_segments(cmd):
    """heredoc 을 떼어 (명령 줄, {구분자: 본문}) 로, 명령은 ; && || | 줄바꿈 단위 토큰 목록으로"""
    lines, bodies, out, end = cmd.split("\n"), [], [], None
    for line in lines:
        if end is not None:
            if (line.strip() if end[1] else line) == end[0]:
                end = None
            else:
                bodies[-1].append(line)
            continue
        out.append(line)
        m = re.search(r"<<(-?)\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2", line)
        if m and "<<<" not in line:
            end = (m.group(3), m.group(1) == "-")
            bodies.append([])
    lex = shlex.shlex("\n".join(out), posix=True, punctuation_chars=";&|<>()")
    lex.whitespace_split = True
    lex.commenters = ""
    try:
        toks = list(lex)
    except ValueError:
        return None, ["\n".join(b) for b in bodies]
    segs, cur = [], []
    for t in toks:
        if t in (";", "&&", "||", "|", "&", "(", ")", "|&", ";;"):
            if cur:
                segs.append(cur)
            cur = []
        else:
            cur.append(t)
    if cur:
        segs.append(cur)
    return segs, ["\n".join(b) for b in bodies]


def analyze(cmd, ctx, depth=0):
    if depth > 3:
        ctx.opaque.append("중첩 너무 깊음")
        return
    for inner in re.findall(r"\$\(([^()]*)\)|`([^`]*)`", cmd):
        analyze(inner[0] or inner[1], ctx, depth + 1)
    segs, bodies = split_segments(cmd)
    if segs is None:
        if FRAGMENTS.search(cmd):
            ctx.opaque.append("따옴표 짝이 안 맞음")
        return
    body_iter = iter(bodies)
    for seg in segs:
        segment(seg, ctx, body_iter, depth)


def segment(seg, ctx, bodies, depth):
    words, i = [], 0
    # 리다이렉트 먼저
    while i < len(seg):
        t = seg[i]
        if t in (">", ">>", ">|", "&>", "&>>") or re.fullmatch(r"\d?>{1,2}", t):
            if i + 1 < len(seg):
                if seg[i + 1] == "&":   # 2>&1
                    i += 3
                    continue
                dest(seg[i + 1], ctx)
            i += 2
            continue
        if t == "<":
            i += 2
            continue
        if t == "<<" or t == "<<-":
            words.append("<<HEREDOC")
            i += 2
            continue
        words.append(t)
        i += 1
    while words and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", words[0]):
        k, v = words.pop(0).split("=", 1)
        startup(k, v, ctx)
        ev = expand_word(v, ctx)
        if ev is None:
            ctx.vars.pop(k, None)
        else:
            ctx.vars[k] = ev
    while words and os.path.basename(words[0]) in WRAPPERS:
        name = os.path.basename(words.pop(0))
        while words and (words[0].startswith("-") or (name == "env" and "=" in words[0])):
            w = words.pop(0)
            if name == "env" and "=" in w and not w.startswith("-"):
                startup(*w.split("=", 1), ctx)
    if not words:
        return
    prog, args = os.path.basename(words[0]), words[1:]
    heredoc = "<<HEREDOC" in args
    args = [a for a in args if a != "<<HEREDOC"]
    body = next(bodies, "") if heredoc else ""
    if expand_word(words[0], ctx) is None:
        ctx.opaque.append(f"명령 {words[0]}")
    if prog == APPROVE_CMD:
        ctx.hits.append(("approve", APPROVE_CMD))
        return
    if prog == "export":
        for a in args:
            if "=" in a:
                k, v = a.split("=", 1)
                startup(k, v, ctx)
                ev = expand_word(v, ctx)
                if ev is not None:
                    ctx.vars[k] = ev
        return
    if prog == "cd" and args:
        p = expand(args[0], ctx)
        if p is None:
            ctx.opaque.append(f"cd {args[0]}")
            return
        g = glob.glob(p) if any(c in p for c in "*?[") else [p]
        ctx.cwd = g[0] if g else p
        return
    plain = [a for a in args if not a.startswith("-")]
    if prog in ALL_ARGS:
        for a in plain:
            dest(a, ctx, destructive=prog in ("rm", "rmdir", "mv", "shred", "unlink"))
    elif prog in LAST_ARG and plain:
        dest(plain[-1], ctx)
    elif prog in INPLACE and any(re.fullmatch(r"-[a-zA-Z]*i.*|--in-place.*", a) for a in args):
        for a in plain[1:] if prog != "perl" else plain:
            dest(a, ctx)
    elif prog == "dd":
        for a in args:
            if a.startswith("of="):
                dest(a[3:], ctx)
    elif prog in ("curl", "wget"):
        for j, a in enumerate(args):
            if a in ("-o", "--output", "-O", "--output-document") and j + 1 < len(args) and not (prog == "curl" and a == "-O"):
                dest(args[j + 1], ctx)
            elif a.startswith("--output="):
                dest(a.split("=", 1)[1], ctx)
    elif prog == "find" and any(a in ("-delete", "-exec", "-execdir") for a in args):
        for a in plain:
            dest(a, ctx, destructive=True)
    elif prog == "git":
        return
    elif prog in SHELLS or prog == "eval":
        code = next((args[j + 1] for j, a in enumerate(args) if re.fullmatch(r"-[a-zA-Z]*c", a) and j + 1 < len(args)), None)
        if prog == "eval":
            code = " ".join(args)
        if code is not None:
            analyze(code, ctx, depth + 1)
        elif heredoc or not plain:
            analyze(body, ctx, depth + 1)
        else:
            script(plain[0], ctx)
    elif prog.rstrip("0123456789.") in INTERP or prog in INTERP:
        code = next((args[j + 1] for j, a in enumerate(args) if a in ("-c", "-e", "-E", "--eval") and j + 1 < len(args)), None)
        if code is not None:
            if code_writes(code):
                ctx.hits.append(("code", "인라인 코드"))
        elif "-m" in args:
            return
        elif heredoc or plain[:1] == ["-"]:
            if code_writes(body):
                ctx.hits.append(("code", "heredoc 코드"))
        elif plain:
            script(plain[0], ctx)


def startup(key, value, ctx):
    if key in STARTUP_VARS and value:
        script(value, ctx)


def script(path, ctx):
    p = expand(path, ctx)
    if p is None:
        ctx.opaque.append(f"스크립트 {path}")
        return
    if trusted_script(p) or not os.path.isfile(p):
        return
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            text = f.read(262144)
    except OSError:
        return
    if FRAGMENTS.search(text):
        ctx.hits.append(("code", f"스크립트 {path}"))


def boxed(cmd, ctx, cwd):
    """걸린 것이 tests/ 시험 스크립트 실행뿐이면 그 실행을 test-box 로 바꾼 명령, 아니면 None.
    바꾼 명령을 다시 해석해 걸리는 것이 남으면(BASH_ENV 로 넘긴 경우 등) 바꾸지 않음"""
    box = os.path.join(hub_dir(), BOX_NAME)
    if ctx.opaque or not ctx.hits or not os.access(box, os.X_OK):
        return None
    head, paths = "스크립트 ", {}
    for kind, name in ctx.hits:
        if kind != "code" or not name.startswith(head):
            return None
        paths[name[len(head):]] = paths.get(name[len(head):], 0) + 1
    out = cmd
    for path, n in paths.items():
        if not BOX_SCRIPT_RE.search(path):
            return None
        rx = re.compile(r"(?<![\w/.$-])" + BOX_RUNNERS + r"[ \t]+" + re.escape(path) + r"(?=$|[\s;|&)])")
        if len(rx.findall(out)) != n:  # 따옴표 속 글자·다른 실행 경로가 섞이면 손대지 않음
            return None
        out = rx.sub(lambda m: shlex.quote(box) + " " + path, out)
    again = Ctx(cwd)
    analyze(out, again)
    return None if again.hits or again.opaque else out


REASON_DEV = ("계획 승인 직전 dev 기준 재확인 필요(lrm-1ce): 지금 {ref} = {sha}. 계획서의 파일·카드 범위를 이 기준과 다시 대조한 뒤 "
              "기록에 \"dev_checked\":{{\"ref\":\"{ref}\",\"sha\":\"{sha}\"}} 를 넣어 다시 씀(fetch: git -C {repo} fetch origin)")


def dev_recheck(content):
    """메인의 계획 승인 기록 Write: dev_checked.sha 가 메인 체크아웃의 지금 기준(기본 origin/dev)과 같아야 함. 기준을 못 읽으면 검사 생략"""
    try:
        rec = json.loads(content or "")
    except ValueError:
        return ""
    if not isinstance(rec, dict):
        return ""
    dc = rec.get("dev_checked") if isinstance(rec.get("dev_checked"), dict) else {}
    ref, repo = dc.get("ref") or "origin/dev", main_role.main_checkout()
    try:
        r = subprocess.run(["git", "-C", repo, "rev-parse", "-q", "--verify", ref + "^{commit}"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    sha = r.stdout.strip()
    if r.returncode != 0 or not sha:
        return ""
    return "" if str(dc.get("sha") or "") and sha.startswith(str(dc["sha"])) and len(str(dc["sha"])) >= 7 else REASON_DEV.format(ref=ref, sha=sha, repo=repo)


def decide(ev):
    tool, inp = ev.get("tool_name", ""), ev.get("tool_input") or {}
    main = main_role.is_main(ev)
    if tool in FILE_TOOLS:
        p = inp.get("file_path") or inp.get("notebook_path") or ""
        hit = classify(os.path.expanduser(p)) if p else None
        if not hit:
            return ""
        if hit[0] == "plan":
            if not main:
                return REASON_PLAN
            return dev_recheck(inp.get("content")) if tool == "Write" and p.endswith(".json") else ""
        return REASON_TRANSCRIPT if hit[0] == "transcript" else REASON_LEDGER.format(name=hit[1])
    if tool != "Bash":
        return ""
    cmd = inp.get("command") or ""
    if not FRAGMENTS.search(cmd) and "$" not in cmd and "`" not in cmd and not re.search(r"\b(bash|sh|zsh|python3?|node|perl|ruby)\b", cmd):
        return ""
    ctx = Ctx(ev.get("cwd") or "")
    analyze(cmd, ctx)
    kinds = {k for k, _ in ctx.hits}
    if "approve" in kinds:
        return REASON_APPROVE
    if "transcript" in kinds:
        return REASON_TRANSCRIPT
    if "ledger" in kinds:
        return REASON_LEDGER.format(name=next(n for k, n in ctx.hits if k == "ledger"))
    if ("plan" in kinds or "code" in kinds) and not main:
        if "plan" in kinds:
            return REASON_PLAN
        new = boxed(cmd, ctx, ev.get("cwd") or "")
        if new:
            return {"command": new}
        why = next(n for k, n in ctx.hits if k == "code")
        return REASON_OPAQUE.format(why=why, frag="이름 조각") + (HINT_TEST if why.startswith("스크립트") else HINT_EDIT)
    if "code" in kinds and main and re.search(r"slack[-_]?inbox|user[-_]?approv|board[-_]?approve", cmd, re.I):
        return REASON_LEDGER.format(name="원장")
    frag = FRAGMENTS.search(cmd)
    if ctx.opaque and frag and not main:
        return REASON_OPAQUE.format(why=ctx.opaque[0], frag=frag.group(0))
    if ctx.opaque and frag and re.search(r"slack[-_]?inbox|user[-_]?approv|board[-_]?approve", frag.group(0), re.I):
        return REASON_OPAQUE.format(why=ctx.opaque[0], frag=frag.group(0))
    return ""


def main():
    try:
        ev = json.load(sys.stdin)
    except ValueError:
        return
    if not isinstance(ev, dict):
        return
    reason = decide(ev)
    if isinstance(reason, dict):  # 시험 실행을 상자로 바꿈. updatedInput 은 입력 전체를 갈아 끼우므로 나머지 칸을 함께 넘김
        note = BOX_NOTE.format(box=os.path.join(hub_dir(), BOX_NAME))
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow",
                                                 "permissionDecisionReason": "시험 스크립트를 test-box 로 바꿔 실행",
                                                 "updatedInput": {**(ev.get("tool_input") or {}), **reason},
                                                 "additionalContext": note}}, ensure_ascii=False))
    elif reason:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                 "permissionDecisionReason": reason}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
