#!/usr/bin/env python3
"""아침 결재함 보고: 밤사이 night-ask.sh 가 막고 적어 둔 확인 요청(night-inbox.jsonl)을 묶어 보고 1건으로 올림.

보낸 기록은 night-inbox/<YYYYMMDD-HHMM>.jsonl 로 옮겨 다음 회차에 다시 보내지 않음. 비어 있으면 아무것도 안 함.
보고는 hub_report.py add --auto(main 허브, info) 로 올리고 옮긴 파일을 첨부함. 보고 실패면 옮기지 않음(다음 회차 재시도).

환경: LRM_HUB_DIR 작업 폴더 · LRM_NIGHT_INBOX 결재함 · LRM_HUB_REPORT hub_report.py 경로 · LRM_PYTHON3 실행기
종료 코드: 0 성공(빈 결재함 포함), 1 보고·파일 실패, 2 인자 오류
"""
import argparse
import collections
import datetime
import json
import os
import subprocess
import sys

REASON_CUT = 40    # 사유 묶음 키 길이. 같은 훅의 사유는 앞부분이 같고 뒤에 명령별 값이 붙음


def paths():
    hub = os.environ.get("LRM_HUB_DIR") or os.path.expanduser("~/.config/lrm-hubs")
    inbox = os.environ.get("LRM_NIGHT_INBOX") or os.path.join(hub, "night-inbox.jsonl")
    report = os.environ.get("LRM_HUB_REPORT") or os.path.join(hub, "hub_report.py")
    return inbox, os.path.join(os.path.dirname(inbox), "night-inbox"), report


def load(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def summary(rows):
    by_reason = collections.Counter((r.get("reason") or "?")[:REASON_CUT] for r in rows)
    places = {os.path.basename((r.get("cwd") or "").rstrip("/")) or "?" for r in rows}
    top, n = by_reason.most_common(1)[0]
    lines = [f"밤사이 사람 확인이 필요해 멈춘 일 {len(rows)}건(작업 폴더 {len(places)}곳, 실행은 안 함)",
             f"- 가장 많은 것: {top}… {n}건",
             "- 할 일: 첨부 목록을 보고 필요한 것만 승인해 다시 실행"]
    return "\n".join(lines)[:400]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="보고·파일 이동 없이 본문만 출력")
    a = ap.parse_args()
    inbox, done_dir, report = paths()
    if not os.path.isfile(inbox) or os.path.getsize(inbox) == 0:
        return 0
    rows = load(inbox)
    if not rows:
        return 0
    text = summary(rows)
    if a.dry_run:
        print(text)
        return 0
    os.makedirs(done_dir, exist_ok=True)
    moved = os.path.join(done_dir, datetime.datetime.now().strftime("%Y%m%d-%H%M") + ".jsonl")
    os.replace(inbox, moved)  # 옮긴 뒤 보고: 보고 중 새로 쌓인 줄은 새 결재함에 남음
    py = os.environ.get("LRM_PYTHON3") or sys.executable
    r = subprocess.run([py, report, "add", "--auto", "--hub", "main", "--task", "없음", "--kind", "info",
                        "--text", text, "--file", moved], capture_output=True, text=True)
    if r.returncode != 0:
        if os.path.exists(inbox):
            _merge_back(moved, inbox)
        else:
            os.replace(moved, inbox)
        print(f"보고 실패(종료 {r.returncode}): {(r.stderr or r.stdout).strip()[-200:]} — 결재함은 그대로 둠", file=sys.stderr)
        return 1
    print(f"보고 {len(rows)}건 → {moved}")
    return 0


def _merge_back(moved, inbox):
    """보고 실패 때 옮긴 줄을 새로 쌓인 결재함 앞에 되돌림"""
    with open(moved, encoding="utf-8") as f:
        old = f.read()
    with open(inbox, encoding="utf-8") as f:
        new = f.read()
    with open(inbox, "w", encoding="utf-8") as f:
        f.write(old + new)
    os.remove(moved)


if __name__ == "__main__":
    sys.exit(main())
