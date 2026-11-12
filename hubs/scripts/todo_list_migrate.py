#!/usr/bin/env python3
"""옛 할 일 목록 정리: 행 전체를 JSON 으로 백업한 뒤, 이 도구(진행 목록)가 만든 행만 삭제함.

대상은 진행 목록 상태 파일(progress-digest.state.json)에 기록된 행 ID 뿐. 손으로 넣은 행·다른 목록은 건드리지 않음.
순서: 백업(행 단위 JSON, 쓰기 확인) → 삭제. 백업을 못 쓰면 삭제하지 않음. 삭제는 반복해도 안전(이미 없는 행은 건너뜀).
새 목록으로 옮기는 쪽(남은 할 일 다시 쓰기)은 progress_digest.py 가 목록 ID 변경을 감지해 처리함.

환경: progress_digest.py 와 같음(SLACK_BOT_TOKEN·LRM_DIGEST_STATE·SLACK_API_BASE·LRM_DIGEST_LOCAL_ONLY, 토큰은 출력 안 함)
종료 코드: 0 성공, 1 Slack 오류, 2 설정 오류
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import progress_digest as pd  # noqa: E402

BACKUP_DIR = "~/Documents/90_정리기록/workspace-archive/20261112"
PAGE = 100
GONE = ("item_not_found", "record_not_found", "not_found")  # 이미 지워진 행


def fetch_rows(conf, list_id):
    """목록의 행 전체(페이지 순회)"""
    rows, cursor = [], ""
    while True:
        body = {"list_id": list_id, "limit": PAGE, **({"cursor": cursor} if cursor else {})}
        d = pd.api(conf, "slackLists.items.list", body)
        rows += d.get("items") or []
        cursor = (d.get("response_metadata") or {}).get("next_cursor") or ""
        if not cursor:
            return rows


def write_backup(rows, list_id, directory, now):
    path = os.path.join(os.path.expanduser(directory), f"todo-list-backup-{time.strftime('%Y%m%d-%H%M%S', time.localtime(now))}.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"list_id": list_id, "saved_at": now, "count": len(rows), "rows": rows}, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    return path


def mine(rows, state):
    """상태 파일에 기록된 행(이 도구가 만든 것)만. 상태의 done: 행 포함"""
    ids = {v["record"] for v in state["items"].values() if isinstance(v, dict) and v.get("record")}
    return [r for r in rows if r.get("id") in ids]


def main(argv=None):
    p = argparse.ArgumentParser(prog="todo_list_migrate.py", description="옛 목록 행 백업 후 봇이 만든 행 삭제", epilog="환경:" + __doc__.split("환경:", 1)[1])
    p.add_argument("--list-id", default=pd.DEFAULT_LIST_ID, help="정리할 옛 목록 ID")
    p.add_argument("--backup-dir", default=BACKUP_DIR, help="백업 폴더")
    p.add_argument("--dry-run", action="store_true", help="백업·삭제 없이 대상 수만 출력")
    p.add_argument("--keep", action="store_true", help="백업만 하고 삭제 안 함")
    a = p.parse_args(argv)
    conf = pd.Conf(list_id=a.list_id)
    try:
        state = pd.load_state(conf.state)
        rows = fetch_rows(conf, a.list_id)
        target = mine(rows, state)
        print(f"목록 {a.list_id}: 전체 {len(rows)}행 · 봇이 만든 행 {len(target)}행")
        if a.dry_run:
            return 0
        print("백업:", write_backup(rows, a.list_id, a.backup_dir, time.time()))
        if a.keep:
            return 0
        done = 0
        for r in target:
            try:
                pd.api(conf, "slackLists.items.delete", {"list_id": a.list_id, "id": r["id"]})
                done += 1
            except pd.Fail as e:
                if e.api_error not in GONE:
                    raise
        print(f"삭제 {done}행. 남은 행 {len(rows) - done}")
        return 0
    except pd.Fail as e:
        print(f"오류: {e}", file=sys.stderr)
        return e.code
    except OSError as e:
        print(f"오류: 백업 쓰기 실패({e.strerror}) — 폴더 권한 확인. 삭제하지 않음", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
