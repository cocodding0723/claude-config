#!/usr/bin/env python3
"""허브 상태 파일 도구. 허브가 자기 상태를 남기고, resume_all.sh 가 이어받기 문구를 만든다.

상태 파일: $LRM_HUB_STATUS_DIR(기본 ~/.config/lrm-hubs/hub-status)/<slug>.json
"""
import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone

DEFAULT_DIR = "~/.config/lrm-hubs/hub-status"
STALE_HOURS = 24
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
# 상태 파일은 평문 저장이라 비밀 값이 들어오면 거부함
SECRET_RE = re.compile(
    r"xox[abprs]-|xapp-|sk-[A-Za-z0-9]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{12,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY|(?i:(?:password|passwd|secret|token|api[_-]?key)\s*[=:]\s*\S{6,})"
)
LIST_FIELDS = (
    ("tasks", "진행 작업"),
    ("branches", "브랜치"),
    ("workers", "워커"),
    ("blockers", "차단"),
)
# 막힘 근거 표기: 문구 안의 [종류:ID]. 상태 파일에는 {text, refs:[{kind,id}]} 로 저장(근거 없으면 문자열 그대로)
REF_ID = {
    "pr": re.compile(r"^(?:[\w.-]+/[\w.-]+)?#\d+$"),
    "decision": re.compile(r"^D-[A-Z0-9][A-Z0-9-]*$"),
    "ticket": re.compile(r"^[A-Z][A-Z0-9]+-\d+$"),
}
REF_FORMAT = {"pr": "pr:Owner/Repo#746 또는 pr:#746", "decision": "decision:D-CFGAPPLY", "ticket": "ticket:LRM-436"}
REF_TAG = re.compile(r"\[([a-z]+):([^\]]*)\]")
GENERIC = "상태 파일 없음 — HANDOFF.md 와 run-list 로 이어받기"

EPILOG = """종료 코드: 0 성공, 1 대상 없음·파일 손상, 2 인자 오류·비밀 값 거부
막힘 근거: --blocker 문구 끝에 [pr:Owner/Repo#N] [decision:D-ID] [ticket:LRM-N] 를 붙임(여러 개 가능). PR 병합·닫힘, 결정 답이 확인되면 수집기가 막힘에서 뺌
환경: LRM_HUB_STATUS_DIR 상태 폴더, LRM_HUB_STATUS_STALE_HOURS 낡음 기준 시간(기본 24)
예:
  hub_status.py write --slug ui-layer --run run_ab12 --task "LRM-266 구현" --next "리뷰 배정"
  hub_status.py write --slug ui-layer --blocker "서버 병합 대기 [pr:Basakansoft/LoveRevolutionMaker#746]" --blocker "설정 적용 답 대기 [decision:D-CFGAPPLY]"
  hub_status.py show --slug ui-layer --json
  hub_status.py prompt --slug ui-layer
"""


class Fail(Exception):
    def __init__(self, msg, code=2):
        super().__init__(msg)
        self.code = code


def status_dir():
    return os.path.expanduser(os.environ.get("LRM_HUB_STATUS_DIR") or DEFAULT_DIR)


def status_path(slug):
    if not SLUG_RE.match(slug or ""):
        raise Fail(f"slug '{slug}' 형식 오류 — 소문자·숫자·하이픈만 허용(예: ui-layer)")
    return os.path.join(status_dir(), slug + ".json")


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def parse_iso(text):
    try:
        t = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.astimezone()


def stale_hours():
    raw = os.environ.get("LRM_HUB_STATUS_STALE_HOURS", "")
    try:
        return float(raw) if raw else STALE_HOURS
    except ValueError:
        return STALE_HOURS


def atomic_write(path, text):
    """같은 폴더 임시 파일 → fsync → os.replace. 읽는 쪽은 옛 파일 또는 새 파일만 본다."""
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load(slug):
    path = status_path(slug)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return None, path, None
    except (OSError, ValueError) as e:
        return None, path, f"{e}"
    if not isinstance(data, dict):
        return None, path, "최상위가 객체가 아님"
    bad = schema_error(data)
    if bad:
        return None, path, bad
    return data, path, None


def schema_error(data):
    """손상된 파일(손으로 고친 값 등)이 prompt·show 에서 traceback 을 내지 않게 필드 타입을 확인."""
    for key in ("updatedAt", "runId", "next"):
        if key in data and not isinstance(data[key], str):
            return f"필드 타입 오류: {key} 는 문자열이어야 함"
    for key, _ in LIST_FIELDS:
        v = data.get(key, [])
        ok = blocker_ok if key == "blockers" else lambda i: isinstance(i, str)
        if not isinstance(v, list) or not all(ok(i) for i in v):
            return f"필드 타입 오류: {key} 는 문자열 목록이어야 함" + (" (막힘은 {text, refs} 객체도 허용)" if key == "blockers" else "")
    return None


def blocker_ok(b):
    """막힘 한 건: 문자열(옛 형식·근거 없음) 또는 {text: 문자열, refs: [{kind, id}]}"""
    if isinstance(b, str):
        return True
    if not isinstance(b, dict) or not isinstance(b.get("text"), str):
        return False
    refs = b.get("refs", [])
    return isinstance(refs, list) and all(isinstance(r, dict) and isinstance(r.get("kind"), str) and isinstance(r.get("id"), str) for r in refs)


def blocker_text(b):
    return b if isinstance(b, str) else b["text"]


def blocker_refs(b):
    """막힘의 근거 목록 [{kind, id}]. 옛 문자열 막힘은 빈 목록"""
    return [] if isinstance(b, str) else list(b.get("refs") or [])


def parse_blocker(raw):
    """'문구 [pr:#746] [decision:D-X]' → 문자열(근거 없음) 또는 {text, refs}. 알 수 없는 종류·잘못된 ID 는 Fail"""
    refs = []
    for m in REF_TAG.finditer(raw):
        kind, rid = m.group(1), m.group(2).strip()
        if kind not in REF_ID:
            raise Fail(f"막힘 근거 종류 '{kind}' 미지원 — {', '.join(REF_FORMAT.values())} 중 하나로 다시 실행")
        if not REF_ID[kind].match(rid):
            raise Fail(f"막힘 근거 [{kind}:{rid}] 형식 오류 — 예: {REF_FORMAT[kind]}")
        ref = {"kind": kind, "id": rid}
        if ref not in refs:
            refs.append(ref)
    if not refs:
        return raw
    text = one_line(REF_TAG.sub("", raw))
    if not text:
        raise Fail("--blocker 에 근거만 있고 문구가 없음 — 막힌 이유를 문구로 함께 적고 다시 실행")
    return {"text": text, "refs": refs}


def item_text(key, item):
    """한 줄 표시. 막힘은 근거를 [종류:ID] 로 되살림"""
    if key != "blockers":
        return one_line(item)
    return one_line(blocker_text(item) + "".join(f" [{r['kind']}:{r['id']}]" for r in blocker_refs(item)))


def one_line(v):
    return re.sub(r"\s+", " ", str(v)).strip()


def build_prompt(slug):
    data, path, err = load(slug)
    if err:
        return f"상태 파일 읽기 실패({path}: {err}) — HANDOFF.md 와 run-list 로 이어받기"
    if data is None:
        return GENERIC
    lines = []
    at = parse_iso(data.get("updatedAt"))
    limit = stale_hours()
    if at is None:
        lines.append(f"오래된 상태(시각 불명: {one_line(data.get('updatedAt', ''))}) — 아래는 낡았을 수 있으니 HANDOFF.md 와 run-list 로 먼저 확인")
    elif datetime.now(timezone.utc) - at > timedelta(hours=limit):
        lines.append(f"오래된 상태({data.get('updatedAt')}) — 아래는 낡았을 수 있으니 HANDOFF.md 와 run-list 로 먼저 확인")
    else:
        lines.append(f"허브 상태 파일(갱신 {data.get('updatedAt')}) 기준으로 이어받기")
    if data.get("runId"):
        lines.append(f"Run: {one_line(data['runId'])}")
    for key, label in LIST_FIELDS:
        for item in data.get(key) or []:
            lines.append(f"{label}: {item_text(key, item)}")
    if data.get("next"):
        lines.append(f"다음 할 일: {one_line(data['next'])}")
    lines.append("먼저 HANDOFF.md 와 run-list 로 위 내용을 교차 확인한 뒤 이어서 진행")
    return "\n".join(lines)


def reject_secrets(values):
    for v in values:
        if SECRET_RE.search(v):
            raise Fail("비밀 값으로 보이는 문자열이 들어 있어 저장하지 않음 — 토큰·비밀번호는 빼고 다시 실행")


def cmd_write(a):
    path = status_path(a.slug)
    for name in ("task", "branch", "worker", "blocker"):
        for v in getattr(a, name):
            if not v.strip():
                raise Fail(f"--{name} 값이 비어 있음 — 내용을 넣거나 옵션을 빼고 다시 실행")
    reject_secrets([a.run or "", a.next or "", *a.task, *a.branch, *a.worker, *a.blocker])
    data = {
        "slug": a.slug,
        "updatedAt": now_iso(),
        "runId": a.run or "",
        "tasks": a.task,
        "branches": a.branch,
        "workers": a.worker,
        "next": a.next or "",
        "blockers": [parse_blocker(b) for b in a.blocker],
    }
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    if a.json:
        print(json.dumps({"ok": True, "path": path, "updatedAt": data["updatedAt"]}, ensure_ascii=False))
    else:
        print(f"기록: {path}")
    return 0


def cmd_show(a):
    data, path, err = load(a.slug)
    if err:
        raise Fail(f"상태 파일 손상: {path} ({err}) — 파일을 지우거나 write 로 다시 기록", 1)
    if data is None:
        raise Fail(f"상태 파일 없음: {path} — write 로 먼저 기록", 1)
    if a.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    print(f"slug: {data.get('slug', a.slug)}\n갱신: {data.get('updatedAt', '')}\nRun: {data.get('runId', '')}")
    for key, label in LIST_FIELDS:
        for item in data.get(key) or []:
            print(f"{label}: {item_text(key, item)}")
    print(f"다음: {one_line(data.get('next', ''))}")
    return 0


def cmd_prompt(a):
    text = build_prompt(a.slug)
    if a.json:
        print(json.dumps({"slug": a.slug, "prompt": text}, ensure_ascii=False))
    else:
        print(text)
    return 0


def parser():
    p = argparse.ArgumentParser(
        prog="hub_status.py",
        description="허브 상태 파일 기록·조회·이어받기 문구 생성. 대화형 입력 없음.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="cmd", required=True, metavar="{write,show,prompt}")
    w = sub.add_parser("write", help="상태 기록(파일 전체를 이번 값으로 교체)", description="상태 기록. 반복 옵션(--task 등)은 여러 번 지정. 지정하지 않은 필드는 비움.", epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    w.add_argument("--slug", required=True, help="허브 slug(예: ui-layer)")
    w.add_argument("--run", help="Run ID")
    w.add_argument("--task", action="append", default=[], help="진행 작업 한 줄(반복)")
    w.add_argument("--branch", action="append", default=[], help="작업 브랜치(반복)")
    w.add_argument("--worker", action="append", default=[], help="워커: 디스패치·워크트리 경로(반복)")
    w.add_argument("--next", help="다음 할 일")
    w.add_argument("--blocker", action="append", default=[], help="차단·결정 대기(반복). 끝에 근거 [pr:Owner/Repo#N]·[decision:D-ID]·[ticket:LRM-N] 가능")
    w.add_argument("--json", action="store_true", help="결과를 JSON 으로 출력")
    w.set_defaults(fn=cmd_write)
    s = sub.add_parser("show", help="상태 조회", epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    s.add_argument("--slug", required=True)
    s.add_argument("--json", action="store_true", help="저장된 JSON 그대로 출력")
    s.set_defaults(fn=cmd_show)
    q = sub.add_parser("prompt", help="resume 문구 생성(파일 없음·손상이어도 일반 문구, 종료 코드 0)", epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    q.add_argument("--slug", required=True)
    q.add_argument("--json", action="store_true", help="{slug, prompt} JSON 으로 출력")
    q.set_defaults(fn=cmd_prompt)
    return p


def main(argv=None):
    a = parser().parse_args(argv)
    try:
        return a.fn(a)
    except Fail as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return e.code
    except OSError as e:
        print(f"ERROR: 파일 입출력 실패({e}) — 경로·권한 확인 후 재실행", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
