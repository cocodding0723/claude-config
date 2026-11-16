"""계획 승인 기록 조회(사용자 11-16 "전부 추천대로"). 계획서 컨펌 1회가 그 계획서에 적힌 티켓 발급·draft PR 생성·Jira 상태 전환을 함께 승인.
기록: <허브 폴더>/plan-approvals/<plan_id>.json =
  {plan_id, approved_at, approver_quote, source:{slack_ts}, tickets:[{summary}], prs:[{repo, branch}], jira_transitions:[{key, to}]}
유효 조건: plan_id 가 파일 이름과 같음 + approved_at(ISO 8601, 시간대 포함)이 지금부터 7일 안 + approver_quote(사용자 원문 인용)가 비지 않음
+ source.slack_ts 가 Slack 수신기가 쓴 slack-inbox.jsonl 의 사용자 답(thread_reply·note·button) 한 줄을 가리키고 그 본문에 인용문이 들어 있음.
인용 대조는 메인(모델)이 사용자 말 없이 기록을 지어내는 것을 막기 위함. slack-inbox.jsonl 쓰기는 모든 Claude 세션에서 훅이 막음(수신기는 launchd 프로세스).
형식이 틀리거나 만료된 파일은 승인으로 치지 않음. 만료는 오래된 컨펌이 새 작업을 승인하지 않게 하려는 것. 기록 파일 쓰기는 메인만(plan-approval-write-guard.sh).
ready 전환·병합·라이브 작업은 이 기록으로 허용하지 않음(호출하는 쪽이 조회하지 않음)."""
import datetime
import json
import os
import re

DIR = "plan-approvals"
TTL = datetime.timedelta(days=7)


def norm(s):
    return re.sub(r"\s+", " ", s or "").strip()


def key_norm(k):
    return re.sub(r"[^A-Za-z0-9]", "", k or "").upper()


def fresh(approved_at, now=None):
    """approved_at 이 미래가 아니고 TTL 안이면 참. 시간대 없는·해석 불가 값은 거짓"""
    try:
        t = datetime.datetime.fromisoformat(approved_at)
    except (TypeError, ValueError):
        return False
    if t.tzinfo is None:
        return False
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return datetime.timedelta(0) <= now - t <= TTL


INBOX = "slack-inbox.jsonl"
ANSWER_TYPES = {"thread_reply", "note", "button"}


def inbox_texts(hub):
    """ts → 사용자 답 본문. 파일이 없으면 빈 사전, 못 읽으면 OSError"""
    p = os.path.join(hub, INBOX)
    if not os.path.exists(p):
        return {}
    out = {}
    with open(p, encoding="utf-8") as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if isinstance(d, dict) and d.get("type") in ANSWER_TYPES and d.get("ts"):
                out[str(d["ts"])] = norm(str(d.get("text") or ""))
    return out


def quoted(rec, texts):
    src = rec.get("source")
    ts = str(src.get("slack_ts") or "") if isinstance(src, dict) else ""
    q = norm(rec.get("approver_quote"))
    return bool(ts) and ts in texts and q in texts[ts]


def valid(rec, name, texts):
    return (isinstance(rec, dict) and rec.get("plan_id") == name and fresh(rec.get("approved_at"))
            and isinstance(rec.get("approver_quote"), str) and rec["approver_quote"].strip()
            and quoted(rec, texts))


def records(hub):
    """유효한 계획 승인 기록 목록. 폴더 없으면 빈 목록, 폴더·파일을 못 읽으면 OSError"""
    d = os.path.join(hub, DIR)
    if not os.path.isdir(d):
        return []
    out = []
    texts = inbox_texts(hub)
    for f in sorted(os.listdir(d)):
        if not f.endswith(".json"):
            continue
        with open(os.path.join(d, f), encoding="utf-8") as fh:
            try:
                rec = json.load(fh)
            except ValueError:
                continue
        if valid(rec, f[:-5], texts):
            out.append(rec)
    return out


def _items(rec, field):
    v = rec.get(field)
    return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []


def find_pr(hub, repo, branch):
    """repo:branch 가 적힌 계획의 plan_id, 없으면 ''"""
    for r in records(hub):
        if any(x.get("repo") == repo and x.get("branch") == branch for x in _items(r, "prs")):
            return r["plan_id"]
    return ""


def find_ticket(hub, summary):
    s = norm(summary)
    for r in records(hub):
        if any(norm(x.get("summary")) == s for x in _items(r, "tickets")):
            return r["plan_id"]
    return ""


def find_transition(hub, key, to=""):
    """이슈 키가 같고, 기록·요청 둘 다 대상 상태가 있으면 그것도 같아야 함"""
    k = key_norm(key)
    for r in records(hub):
        for x in _items(r, "jira_transitions"):
            if key_norm(x.get("key")) == k and (not to or not x.get("to") or norm(x["to"]) == norm(to)):
                return r["plan_id"]
    return ""
