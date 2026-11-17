"""메인 대화의 사용자 직접 지시를 승인으로 인정(사용자 11-17 "앞으로 내가 PR올리라고하면 올려").
대상: draft PR 생성(pr_guard). Jira 발급은 11-17 부터 승인 불필요라 대상 아님. ready 전환·병합·상태 변경·라이브 작업은 다루지 않음.
근거는 훅 입력 transcript_path 의 세션 기록 한 곳뿐이라 별도 원장·명령이 없음:
- 메인 세션만(main_role.is_main). 허브·작업자 세션의 사용자 줄은 코디네이터가 보낸 지시라 사람 승인이 아님.
- 사람 줄만: type=user, isSidechain·isMeta 아님, origin.kind=human, 본문이 문자열. 도구 출력·다른 세션 메시지·작업 알림은 제외.
  세션 기록 쓰기는 plan-approval-write-guard 가 막아 모델이 사람 줄을 지어낼 수 없음.
- 붙여 넣은 글(<pasted_content>)·태그 블록·'[보낸 곳→메인]' 머리 줄(허브 보고·Slack 전달)은 지시로 보지 않음.
- 최근 TTL 안의 한 문장에 대상 낱말 + 요청 동사가 있고 부정·질문·인용("…라고")이 없어야 함."""
import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import main_role  # noqa: E402

TTL = datetime.timedelta(minutes=60)
SKEW = datetime.timedelta(minutes=2)  # 기록 시각이 지금보다 조금 뒤로 찍힌 경우(시계 차이) 허용

RULES = {
    "pr": (re.compile(r"PR|피알|풀\s*리퀘|pull\s*request", re.I),
           re.compile(r"올려|올리자|올리고|만들어|만들고|생성\s*해|생성하고|열어|게시\s*해|띄워")),
}
NEGATIVE = re.compile(r"말고|말아|마라|지\s*마|지\s*말|지\s*않|않|안\s*(올|만들|해|돼)|금지|보류|취소|멈춰|기다려|나중에")
QUESTION = re.compile(r"\?|(까|나|냐|니|건가|는지|어때|됨|임)\s*[.!]?\s*$")
QUOTED = re.compile(r"(라|다|냐|자)고")  # "PR 올리라고 하면" 같은 조건·전달 말투는 지금 내린 지시가 아님
HEADER = re.compile(r"^\s*\[[^\]\n]*→[^\]\n]*\]")
BLOCK = re.compile(r"<([A-Za-z][\w-]*)[^>]*>.*?</\1[^>]*>", re.S)  # 닫는 태그에도 속성이 붙음(</pasted_content id="…">)
CLAUSE = re.compile(r"[^\n.!?。]+[.!?。]?")


def _config_projects():
    dirs = [os.path.expanduser("~/.claude")]
    if os.environ.get("CLAUDE_CONFIG_DIR"):
        dirs.append(os.environ["CLAUDE_CONFIG_DIR"])
    return {os.path.realpath(os.path.join(d, "projects")) for d in dirs}


def transcript(ev):
    """훅 입력의 세션 기록 경로. 설정 폴더 projects/<프로젝트>/<세션>.jsonl 형태가 아니면 ''"""
    tp = (ev or {}).get("transcript_path") or ""
    sid = str((ev or {}).get("session_id") or "")
    if not tp or not os.path.isfile(tp):
        return ""
    if sid and os.path.basename(tp) != sid + ".jsonl":
        return ""
    real = os.path.realpath(tp)
    if not any(os.path.dirname(os.path.dirname(real)) == top for top in _config_projects()):
        return ""
    return tp


def human_prompts(path, now):
    """TTL 안에 사람이 입력한 프롬프트 (시각, 본문) 목록"""
    out = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if '"human"' not in line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            msg = d.get("message") or {}
            c = msg.get("content")
            origin = d.get("origin") if isinstance(d.get("origin"), dict) else {}
            if (d.get("type") != "user" or d.get("isSidechain") or d.get("isMeta") or origin.get("kind") != "human"
                    or msg.get("role") != "user" or not isinstance(c, str)):
                continue
            try:
                t = datetime.datetime.fromisoformat(str(d.get("timestamp", "")).replace("Z", "+00:00"))
            except ValueError:
                continue
            if t.tzinfo is None or not (-SKEW <= now - t <= TTL):
                continue
            out.append((t, c))
    return out


def clauses(text):
    body = BLOCK.sub("\n", text)
    if HEADER.match(body):
        return []
    return [m.group(0).strip() for m in CLAUSE.finditer(body) if m.group(0).strip()]


def asks(clause, kind):
    target, verb = RULES[kind]
    return (bool(target.search(clause)) and bool(verb.search(clause))
            and not NEGATIVE.search(clause) and not QUESTION.search(clause) and not QUOTED.search(clause))


def find(ev, kind, now=None):
    """메인 세션에서 사람이 TTL 안에 kind(pr) 생성을 직접 지시했으면 'prompt:<시각> <문장>', 아니면 ''.
    세션 기록을 못 읽으면 OSError"""
    if kind not in RULES or not main_role.is_main(ev):
        return ""
    path = transcript(ev)
    if not path:
        return ""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    for t, text in reversed(human_prompts(path, now)):
        for c in clauses(text):
            if asks(c, kind):
                return f"prompt:{t.isoformat(timespec='seconds')} {c[:80]}"
    return ""
