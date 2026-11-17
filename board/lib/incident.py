"""사건 자동 기록(lrm-3km). <BOARD_DIR>/incidents.jsonl(또는 BOARD_INCIDENTS)에 한 줄 {at, kind, cause, source, …}.
작업판 폴더가 없으면(임시 HOME 시험 등) 쓰지 않음. 기록 실패는 호출한 쪽 흐름을 막지 않음.
종류: revert(되돌림 커밋) · merge_reject(머지 검사·병합 거부) · direct_merge(board-merge 없이 병합) · report_stopped(보고 전송 중단) · unity_wait(Unity 대기 초과)"""
import datetime
import fcntl
import json
import os


def target():
    p = os.environ.get("BOARD_INCIDENTS")
    if p:
        return p
    board = os.environ.get("BOARD_DIR", os.path.expanduser("~/orca/board"))
    return os.path.join(board, "incidents.jsonl") if os.path.isdir(board) else ""


def record(kind, cause, source="", **fields):
    path = target()
    if not path:
        return False
    rec = {"at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), "kind": kind,
           "cause": str(cause)[:500], "source": source, **fields}
    try:
        with open(path, "a", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return True
    except OSError:
        return False
