#!/usr/bin/env python3
"""변이 검증. 스크립트 사본의 핵심 방어 코드를 하나씩 망가뜨려 테스트가 잡는지 확인.

사용: python3 tests/mutate.py
미검출 변이가 하나라도 있으면 종료 코드 1. 원본 lrm-hubs/ 는 수정하지 않고 임시 사본만 변형함.
"""
import glob
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "lrm-hubs")

# (이름, 파일, [(원문, 변형)])
MUTANTS = [
    ("이력 선검사 제거", "slack_pending.sh", [('    if not DRY:\n        log("run_start"', '    if False:\n        log("run_start"')]),
    ("Retry-After 무시(poll)", "slack_poll.sh", [("time.sleep(wait)", "time.sleep(0)")]),
    ("Retry-After 무시(pending)", "slack_pending.sh", [("time.sleep(wait)", "time.sleep(0)")]),
    ("일시 오류 수정 재시도 제거", "slack_pending.sh",
     [('elif e["event"] == "update_failed" and not e.get("final"):\n            handled.discard(e["ts"])', "elif False:\n            pass")]),
    ("수정 상한 제거", "slack_pending.sh", [("[:UPDATE_MAX]", "")]),
    ("sent 범위 제거", "slack_poll.sh",
     [("if mine or ts not in sent:", "if mine:"), ("rec = sent[ts]", "rec = sent.get(ts, {})")]),
    ("latest_reply 폴백 제거", "slack_poll.sh",
     [('marker = m.get("latest_reply") or f"n{m.get(\'reply_count\')}"', 'marker = m.get("latest_reply")')]),
    ("시간 예산 제거(poll)", "slack_poll.sh", [("if wait >= DEADLINE - time.time():", "if False:")]),
    ("시간 예산 제거(pending)", "slack_pending.sh", [("if wait >= DEADLINE - time.time():", "if False:")]),
    ("상태 손상 복구 제거", "slack_poll.sh", [("    except ValueError:\n        os.replace(STATE", "    except KeyError:\n        os.replace(STATE")]),
    ("전송 직후 이력 실패 회수 제거", "slack_pending.sh",
     [('call("chat.delete", {"channel": channel, "ts": new_ts})', "pass")]),
    ("수정 전 원문 기록 제거", "slack_pending.sh", [("original_text=orig)", "original_text=None)")]),
    ("대기 조회 실패 응답 제거", "slack_cmd.sh", [('elif [ "$R" -ne 0 ]; then', "elif false; then")]),
    ("처리 중 안내 제거", "slack_cmd.sh", [('if [ "$R" -eq 75 ]; then', "if false; then")]),
    ("상태 없음 복원 제거", "slack_poll.sh", [('        return {"seen": seen_from_inbox()}\n    try:', '        return {"seen": {}}\n    try:')]),
    ("수정 예외 실패 기록 제거", "slack_pending.sh", [('log("update_failed", ts=s["ts"], error=str(e), final=False)', "pass")]),
    ("총 예산 계산 제거", "slack_poll.sh", [("local v=$((TOTAL - ($(date +%s) - T0)))", "local v=$TOTAL")]),
]


def run_tests(script_dir):
    env = dict(os.environ, LRM_SCRIPT_DIR=script_dir)
    for t in sorted(glob.glob(os.path.join(ROOT, "tests", "test_*.sh"))):
        r = subprocess.run(["/bin/bash", t], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if r.returncode != 0:
            return False
    return True


def main():
    work = tempfile.mkdtemp(prefix="lrm-mutate-")
    base = os.path.join(work, "base")
    shutil.copytree(SRC, base)
    if not run_tests(base):
        print("기준 사본이 테스트를 통과하지 못함 — 변이 검증 중단")
        return 2
    missed = 0
    for name, fname, edits in MUTANTS:
        d = os.path.join(work, "m")
        if os.path.exists(d):
            shutil.rmtree(d)
        shutil.copytree(SRC, d)
        path = os.path.join(d, fname)
        text = open(path, encoding="utf-8").read()
        for old, new in edits:
            if old not in text:
                print(f"{name} → 변형 위치를 못 찾음(스크립트와 변이 정의 불일치)")
                return 2
            text = text.replace(old, new, 1)
        open(path, "w", encoding="utf-8").write(text)
        caught = not run_tests(d)
        print(f"{name} → {'검출' if caught else '미검출'}")
        missed += 0 if caught else 1
    shutil.rmtree(work)
    return 1 if missed else 0


if __name__ == "__main__":
    sys.exit(main())
