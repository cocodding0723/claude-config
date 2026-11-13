#!/usr/bin/env python3
"""봇 전용 Slack 목록 '강비서 할 일' 생성 + 사용자에게 소유 권한 공유. 반복 실행 안전.

옛 목록은 건드리지 않음. 이 스크립트가 만든 목록만 상태 파일(bot-list.json)에 기록하고, 기록이 있으면 재생성 안 함.

환경:
  SLACK_BOT_TOKEN   봇 토큰(출력·로그 금지). 없으면 봇 환경 파일에서 이 키 한 줄만 직접 읽음
  SLACK_USER_ID     권한을 받을 사용자 Slack ID(--user 로 대체 가능). 없으면 같은 파일에서 읽음
  LRM_HUB_DIR       봇 환경 파일이 있는 폴더(기본 ~/.config/lrm-hubs)
  SLACK_API_BASE    API 주소(기본 https://slack.com/api)
  LRM_BOTLIST_STATE 상태 파일(기본 ~/.config/lrm-hubs/bot-list.json)
  LRM_BOTLIST_LOCAL_ONLY=1  로컬 주소가 아니면 거부(시험용)

종료 코드: 0 성공(이미 있음 포함), 1 Slack 오류, 2 설정 오류
"""
import argparse
import ipaddress
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import tls_ctx  # noqa: E402
import todo_items  # noqa: E402

DEFAULT_API = "https://slack.com/api"
DEFAULT_NAME = "강비서 할 일"
DEFAULT_STATE = "~/.config/lrm-hubs/bot-list.json"
BOT_ENV_NAME = "slack-bot.env"
ENV_KEYS = ("SLACK_BOT_TOKEN", "SLACK_USER_ID")  # 이 두 키만 읽음. 파일의 다른 값은 메모리에도 올리지 않음
API_TIMEOUT_S = 15
ACCESS_LEVEL = "owner"
USER_ID_RE = re.compile(r"^[UW][A-Z0-9]{6,}$")
# 진행 목록(progress_digest.py)이 쓰는 칸과 같은 구성. 종류 선택지는 todo_items.KINDS 가 정본
# 정렬: 급함 높은 순 → 종류 → 오래된 순을 숫자 하나로 만든 값(todo_items.order_value). Slack 목록 API 에 행 순서 변경이 없어 보기 정렬(오름차순)로 씀
# 보관: 끝난 행을 일정 시간 뒤 체크함. Slack 에 보기 필터 API 가 없어, 목록 보기에서 '보관 해제만' 필터를 한 번 걸어 숨김
SCHEMA = [
    {"key": "title", "name": "할 일", "type": "text", "is_primary_column": True},
    {"key": "kind", "name": "종류", "type": "select",
     "options": {"format": "single_select", "choices": [{"value": k, "label": label, "color": color} for (k, label), color in zip(todo_items.KINDS, ("red", "blue", "yellow", "green"))]}},
    {"key": "urgency", "name": "급함", "type": "number"},
    {"key": "hub", "name": "허브", "type": "text"},
    {"key": "answer", "name": "답하는 법", "type": "text"},
    {"key": "done", "name": "완료", "type": "checkbox"},
    {"key": "link", "name": "링크", "type": "text"},
    {"key": "archive", "name": "보관", "type": "checkbox"},
    {"key": "order", "name": "정렬", "type": "number"},
]


class Fail(Exception):
    def __init__(self, msg, code=1):
        super().__init__(msg)
        self.code = code


def scrub(text, token):
    return text.replace(token, "***") if token else text


class Slack:
    def __init__(self, base, token):
        self.base, self.token = base.rstrip("/"), token

    def check_local(self, enforce):
        if not enforce:
            return
        host = urllib.parse.urlparse(self.base).hostname or ""
        try:
            local = host == "localhost" or ipaddress.ip_address(host).is_loopback
        except ValueError:
            local = False
        if not local:
            raise Fail(f"LRM_BOTLIST_LOCAL_ONLY=1 인데 API 주소가 로컬이 아님({self.base}). SLACK_API_BASE 를 127.0.0.1 로 지정", 2)

    def call(self, method, body):
        req = urllib.request.Request(
            f"{self.base}/{method}", data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json; charset=utf-8"})
        opener = urllib.request.build_opener(  # 환경 프록시 우회 금지
            urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=tls_ctx.ssl_context()))
        try:
            with opener.open(req, timeout=API_TIMEOUT_S) as r:
                data = json.load(r)
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise Fail(f"{method} 호출 실패({scrub(str(e), self.token)[:60]}). 네트워크·인증서 확인 후 재실행")
        if not data.get("ok"):
            err = data.get("error", "알 수 없음")
            hint = ""
            if err == "missing_scope":
                hint = f" — 필요 권한: {data.get('needed', 'lists:write')}. Slack 앱 설정에서 권한 추가 후 앱 재설치(사용자 작업)"
            elif err in ("invalid_auth", "not_authed", "token_revoked"):
                hint = " — 봇 토큰 확인(slack-bot.env)"
            raise Fail(f"{method} 오류({err}){hint}")
        return data


def load_state(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        raise Fail(f"상태 파일 읽기 실패({path}). 내용 확인 후 고치거나 지우기. 지우면 목록이 중복 생성될 수 있음", 2)


def save_state(path, st):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".bot-list.")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def list_url(slack, list_id):
    """auth.test 의 워크스페이스 주소로 목록 링크 조립. 실패하면 빈 문자열"""
    try:
        d = slack.call("auth.test", {})
    except Fail:
        return ""
    base, team = (d.get("url") or "").rstrip("/"), d.get("team_id")
    return f"{base}/lists/{team}/{list_id}" if base and team else ""


def column_ids(resp):
    """응답의 칸 ID 를 key 기준으로 매핑. 없으면 빈 dict"""
    meta = resp.get("list_metadata") or {}
    out = {}
    for col in meta.get("schema") or []:
        if col.get("key") and col.get("id"):
            out[col["key"]] = col["id"]
    return out


def column_options(resp):
    """select 칸의 선택지 → {칸 key: {선택 key: Slack 이 정한 값}}. 라벨로 짝지음(Slack 이 값을 새로 매길 수 있음)"""
    labels = {k: label for k, label in todo_items.KINDS}
    out = {}
    for col in (resp.get("list_metadata") or {}).get("schema") or []:
        choices = (col.get("options") or {}).get("choices") or []
        if col.get("key") and choices:
            by_label = {c.get("label"): c.get("value") for c in choices}
            out[col["key"]] = {k: by_label.get(labels.get(k), k) for k in labels} if col["key"] == "kind" else {}
    return out


def read_bot_env(env):
    """봇 환경 파일에서 ENV_KEYS 만 추출. 파일이 없거나 못 읽으면 빈 dict"""
    path = os.path.join(os.path.expanduser(env.get("LRM_HUB_DIR") or "~/.config/lrm-hubs"), BOT_ENV_NAME)
    found = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                key, sep, value = line.strip().removeprefix("export ").partition("=")
                if sep and key.strip() in ENV_KEYS:
                    found[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return found


def run(a, env):
    if not (env.get("SLACK_BOT_TOKEN") and (a.user or env.get("SLACK_USER_ID"))):
        env = {**read_bot_env(env), **{k: v for k, v in env.items() if v}}  # 환경 변수가 파일보다 우선
    state_path = os.path.expanduser(a.state or env.get("LRM_BOTLIST_STATE") or DEFAULT_STATE)
    user = a.user or env.get("SLACK_USER_ID", "")
    token = env.get("SLACK_BOT_TOKEN", "")
    st = load_state(state_path)
    rec = st.get("lists", {}).get(a.name)
    if a.replace and rec:  # 스키마가 바뀐 새 목록으로 교체: 옛 기록은 replaced 에 남김(옛 목록은 건드리지 않음)
        st.setdefault("replaced", []).append(rec)
        rec = None

    if not USER_ID_RE.match(user):
        raise Fail("사용자 Slack ID 없음 또는 형식 오류. SLACK_USER_ID 를 설정하거나 --user U… 로 지정", 2)
    plan = {"name": a.name, "state_file": state_path, "user": user, "access_level": ACCESS_LEVEL,
            "schema": [c["key"] for c in SCHEMA], "existing": rec}
    if rec and rec.get("shared"):
        out = {"action": "none", **plan, "id": rec["id"], "url": rec.get("url", "")}
        emit(a, out, f"이미 있음: {rec['id']} {rec.get('url', '')} — 변화 없음")
        return 0
    action = "share" if rec else "create+share"
    if a.dry_run:
        emit(a, {"action": action, "dry_run": True, **plan}, f"(--dry-run) {action}: '{a.name}' → 사용자 {user} {ACCESS_LEVEL}. Slack 호출·상태 쓰기 없음")
        return 0
    if not token:
        raise Fail("SLACK_BOT_TOKEN 없음. 환경 변수로 주거나 봇 환경 파일(slack-bot.env)에 추가", 2)

    slack = Slack(a.api or env.get("SLACK_API_BASE") or DEFAULT_API, token)
    slack.check_local(env.get("LRM_BOTLIST_LOCAL_ONLY") == "1")
    if not rec:
        d = slack.call("slackLists.create", {"name": a.name, "schema": SCHEMA})
        list_id = d.get("list_id") or (d.get("list") or {}).get("id")
        if not list_id:
            raise Fail("slackLists.create 응답에 목록 ID 없음. Slack 상태 확인 후 목록 직접 점검(재실행 시 중복 가능)")
        rec = {"id": list_id, "url": list_url(slack, list_id), "columns": column_ids(d), "options": column_options(d), "shared": False}
        st.setdefault("lists", {})[a.name] = rec
        save_state(state_path, st)  # 공유가 실패해도 다음 실행이 재생성하지 않게 먼저 기록
    slack.call("slackLists.access.set", {"list_id": rec["id"], "access_level": ACCESS_LEVEL, "user_ids": [user]})
    rec["shared"] = True
    save_state(state_path, st)
    emit(a, {"action": action, **plan, "id": rec["id"], "url": rec["url"], "columns": rec["columns"]},
         f"완료: {rec['id']} {rec['url']} (사용자 {user} {ACCESS_LEVEL})")
    return 0


def emit(a, data, line):
    print(json.dumps(data, ensure_ascii=False, indent=1) if a.json else line)


def parser():
    p = argparse.ArgumentParser(
        prog="slack_list_create.py", formatter_class=argparse.RawDescriptionHelpFormatter,
        description="봇이 Slack 목록을 새로 만들고 사용자에게 소유 권한을 공유함. 이미 만들었으면 아무것도 안 함.",
        epilog="환경:" + __doc__.split("환경:", 1)[1] + "\n예:\n  slack_list_create.py --dry-run    계획만 출력\n  slack_list_create.py --json       결과를 JSON 으로 출력\n")
    p.add_argument("--dry-run", action="store_true", help="계획만 출력. Slack 호출·상태 파일 쓰기 없음")
    p.add_argument("--json", action="store_true", help="결과를 JSON 으로 출력")
    p.add_argument("--name", default=DEFAULT_NAME, help=f"목록 이름(기본 {DEFAULT_NAME})")
    p.add_argument("--replace", action="store_true", help="같은 이름의 기록이 있어도 새 목록을 만들고 기록을 교체(옛 목록은 그대로)")
    p.add_argument("--user", help="권한을 받을 사용자 Slack ID(기본 SLACK_USER_ID)")
    p.add_argument("--state", help=f"상태 파일(기본 {DEFAULT_STATE})")
    p.add_argument("--api", help="API 주소(기본 SLACK_API_BASE 또는 slack.com)")
    return p


def main(argv=None, env=None):
    a = parser().parse_args(argv)
    try:
        return run(a, os.environ if env is None else env)
    except Fail as e:
        print(f"오류: {scrub(str(e), (env or os.environ).get('SLACK_BOT_TOKEN', ''))}", file=sys.stderr)
        return e.code


if __name__ == "__main__":
    sys.exit(main())
