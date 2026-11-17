"""사용자 터미널 승인(board-approve) 대조. 기록 <허브>/user-approvals.jsonl 의 한 줄은
그 세션 기록(<설정 폴더>/projects/*/<session_id>.jsonl)에 사용자가 프롬프트에서 친 '!' 명령
(type=user, 본문이 문자열, '<bash-input>' 으로 시작)으로 같은 저장소·브랜치·원문이 있을 때만 인정함.
모델은 '!' 줄을 만들 수 없음: 모델 명령은 assistant tool_use, 출력은 tool_result 목록으로 남고, 원장·세션 기록 쓰기는 훅이 막음."""
import datetime
import glob
import json
import os
import shlex

LEDGER = "user-approvals.jsonl"
TTL = datetime.timedelta(hours=24)
SKEW_S = 600  # 기록 시각과 세션 기록의 '!' 줄 시각 차이 상한


def _config_dirs():
    out = [os.path.expanduser("~/.claude")]
    if os.environ.get("CLAUDE_CONFIG_DIR"):
        out.append(os.environ["CLAUDE_CONFIG_DIR"])
    return out


def _transcripts(sid):
    seen, out = set(), []
    for d in _config_dirs():
        for p in glob.glob(os.path.join(d, "projects", "*", glob.escape(sid) + ".jsonl")):
            r = os.path.realpath(p)
            if r not in seen:
                seen.add(r)
                out.append(p)
    return out


def _bash_inputs(path):
    """세션 기록에서 사용자가 친 '!' 명령들 (시각 epoch, 명령 문자열)"""
    out = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if "<bash-input>" not in line or "board-approve" not in line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            msg = d.get("message") or {}
            c = msg.get("content")
            if d.get("type") != "user" or d.get("isSidechain") or msg.get("role") != "user" or not isinstance(c, str):
                continue
            c = c.strip()
            if not (c.startswith("<bash-input>") and c.endswith("</bash-input>")):
                continue
            try:
                t = datetime.datetime.fromisoformat(str(d.get("timestamp", "")).replace("Z", "+00:00")).timestamp()
            except ValueError:
                continue
            out.append((t, c[len("<bash-input>"):-len("</bash-input>")]))
    return out


def _matches(cmd, rec):
    try:
        toks = shlex.split(cmd)
    except ValueError:
        return False
    for i, t in enumerate(toks):
        if os.path.basename(t) == "board-approve":
            args = [x for x in toks[i + 1:i + 4]]
            return args == [rec.get("repo_arg"), rec.get("branch"), rec.get("text")]
    return False


def verified(rec):
    sid = str(rec.get("session_id") or "")
    if not sid or "/" in sid:
        return False
    for p in _transcripts(sid):
        for t, cmd in _bash_inputs(p):
            if abs(t - float(rec.get("ts") or 0)) <= SKEW_S and _matches(cmd, rec):
                return True
    return False


def find_pr(hub, repo, branch, now=None):
    """repo:branch draft PR 생성을 인정하는 터미널 승인이 있으면 'terminal:<시각>', 없으면 ''. 원장을 못 읽으면 OSError"""
    p = os.path.join(hub, LEDGER)
    if not os.path.exists(p):
        return ""
    now = (now or datetime.datetime.now(datetime.timezone.utc)).timestamp()
    with open(p, encoding="utf-8") as f:
        lines = f.readlines()
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict) or rec.get("type") != "terminal" or rec.get("kind") != "pr":
            continue
        if str(rec.get("repo", "")).lower() != repo.lower() or rec.get("branch") != branch:
            continue
        age = now - float(rec.get("ts") or 0)
        if 0 <= age <= TTL.total_seconds() and verified(rec):
            return "terminal:" + str(rec.get("at"))
    return ""
