#!/usr/bin/env python3
"""결정 카드 버튼에 매단 후속 동작 등록부. 감시기가 카드를 보낼 때 등록하고, 버튼 답이 오면 slack_socket.py 가 꺼내 실행함.

파일: $LRM_HUB_DIR(기본 ~/.config/lrm-hubs)/card-actions.json  {결정번호: {kind, args, createdAt}}
규칙: 선택지 1번이 '실행', 그 밖의 답(보류·직접 입력)은 동작 없이 항목만 지움. 항목은 꺼낼 때 지워 같은 카드를 두 번 눌러도 한 번만 실행함.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

DIR = os.environ.get("LRM_HUB_DIR") or os.path.expanduser("~/.config/lrm-hubs")
STORE = os.path.join(DIR, "card-actions.json")
TTL_S = 7 * 24 * 3600  # 답 없는 카드의 등록 보존 기간
RUN_PICK = 1  # 실행을 뜻하는 선택지 번호
EXEC_TIMEOUT_S = 600  # 허브 여러 개를 순서대로 띄우는 시간까지 감안한 상한

EPILOG = """종료 코드: 0 성공(등록 없음 포함), 1 후속 동작 실패, 2 인자 오류
환경: LRM_HUB_DIR 작업 폴더, LRM_RESUME_ALL resume_all.sh 경로
예:
  card_actions.py handle --did D-RS0123456789 --pick 1
  card_actions.py list
"""


def load() -> dict:
    try:
        with open(STORE, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save(data: dict) -> None:
    os.makedirs(DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, STORE)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def register(did: str, kind: str, args: dict, now: float | None = None) -> None:
    """카드 한 장의 후속 동작 등록. 보존 기간이 지난 항목은 같이 정리"""
    now = time.time() if now is None else now
    data = {k: v for k, v in load().items() if isinstance(v, dict) and now - v.get("createdAt", 0) < TTL_S}
    data[did] = {"kind": kind, "args": args, "createdAt": now}
    save(data)


def take(did: str) -> dict | None:
    """등록 항목을 꺼내며 지움. 등록이 없으면 None(일반 결정 카드)"""
    data = load()
    entry = data.pop(did, None)
    if entry is None:
        return None
    save(data)
    return entry if isinstance(entry, dict) else None


def run_resume(args: dict) -> tuple[int, str]:
    """대상 허브만 resume_all.sh --only 로 재기동"""
    slugs = [s for s in args.get("slugs", []) if isinstance(s, str) and s]
    if not slugs:
        return 0, "대상 허브 없음"
    script = os.environ.get("LRM_RESUME_ALL") or os.path.join(DIR, "resume_all.sh")
    cmd = ["bash", script]
    for s in slugs:
        cmd += ["--only", s]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=EXEC_TIMEOUT_S, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as err:
        return 1, f"resume_all 실행 실패 {type(err).__name__}"
    return p.returncode, f"허브 {len(slugs)}개 이어받기 요청({', '.join(slugs)})" if p.returncode == 0 else f"resume_all 종료 코드 {p.returncode}"


KINDS = {"resume": run_resume}


def execute(entry: dict, pick) -> tuple[int, str]:
    """답에 따라 실행. 1번이 아니면 동작 없음"""
    if pick != RUN_PICK:
        return 0, "보류 — 동작 없음"
    fn = KINDS.get(entry.get("kind"))
    if fn is None:
        return 1, f"알 수 없는 동작 종류: {entry.get('kind')}"
    return fn(entry.get("args") or {})


def cmd_handle(a) -> int:
    entry = take(a.did)
    if entry is None:
        print(f"등록 없음: {a.did}")
        return 0
    rc, msg = execute(entry, a.pick)
    print(msg)
    return rc


def cmd_list(_a) -> int:
    for did, v in load().items():
        print(f"{did}\t{v.get('kind')}\t{json.dumps(v.get('args'), ensure_ascii=False)}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="card_actions.py", description="결정 카드 후속 동작 등록부. 대화형 입력 없음.",
                                epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True, metavar="{handle,list}")
    h = sub.add_parser("handle", help="버튼 답 처리(등록 항목을 꺼내 실행)")
    h.add_argument("--did", required=True, help="결정 번호(D-...)")
    h.add_argument("--pick", required=True, type=int, help="누른 선택지 번호(1 = 실행)")
    h.set_defaults(fn=cmd_handle)
    sub.add_parser("list", help="대기 중인 등록 목록").set_defaults(fn=cmd_list)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
