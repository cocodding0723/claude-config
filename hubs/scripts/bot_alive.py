#!/usr/bin/env python3
"""봇 생존 줄 출력. 근거는 slack_socket.py 의 '생존' 로그(10분 주기).

사용: bot_alive.py            → '봇: 연결됨 · 마지막 수신 N분 전' 한 줄
      bot_alive.py --warn-once → 끊김(생존 기록 STALE_MIN 분 이상 없음)이면 warn 1회 전송, 복구되면 다시 보낼 수 있게 초기화
종료 코드: 0 연결됨, 3 끊김, 2 설정 오류
"""
import os
import re
import subprocess
import sys
import time
from pathlib import Path

DIR = Path(os.environ.get("LRM_HUB_DIR", Path.home() / ".config/lrm-hubs"))
LOG = DIR / "slack-socket.log"
FLAG = DIR / "bot-alive.warned"  # 끊김 알림을 보냈다는 표시. 복구 시 삭제
STALE_MIN = 15  # 생존 기록 주기 10분 + 여유 5분
ALIVE = re.compile(r"^(\d\d-\d\d \d\d:\d\d:\d\d) \[socket\] 생존 연결=(\S+) 마지막이벤트=(\S+(?: 전)?)")


def last_alive(now):
    """(생존 기록 이후 분, 연결 여부, 마지막 이벤트 문구). 기록이 없으면 None"""
    try:
        lines = LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for ln in reversed(lines):
        m = ALIVE.match(ln)
        if not m:
            continue
        # 로그 시각에 연도가 없어 올해로 보고, 미래면 작년(연말 경계)
        lt = time.localtime(now)
        ts = time.mktime(time.strptime(f"{lt.tm_year}-{m.group(1)}", "%Y-%m-%d %H:%M:%S"))
        if ts > now + 60:
            ts = time.mktime(time.strptime(f"{lt.tm_year - 1}-{m.group(1)}", "%Y-%m-%d %H:%M:%S"))
        return int((now - ts) // 60), m.group(2) == "예", m.group(3)
    return None


def status(now=None):
    """(연결됨 여부, 한 줄)"""
    now = time.time() if now is None else now
    got = last_alive(now)
    if got is None:
        return False, "봇: 생존 기록 없음"
    age, up, last = got
    if age >= STALE_MIN or not up:
        return False, f"봇: 끊김 · 생존 기록 {age}분 전"
    return True, f"봇: 연결됨 · 마지막 수신 {last}"


def main():
    ok, line = status()
    if "--warn-once" not in sys.argv[1:]:
        print(line)
        return 0 if ok else 3
    if ok:
        FLAG.unlink(missing_ok=True)
        return 0
    if FLAG.exists():
        return 3
    notify = os.environ.get("LRM_NOTIFY", str(DIR / "slack_notify.sh"))
    msg = f"Slack 봇 연결 끊김\n\n• 무엇: {line}\n• 왜: 생존 기록이 {STALE_MIN}분 넘게 없음\n• 결과: Slack 반응·답글이 허브에 안 들어옴\n\n👉 답: launchctl kickstart -k gui/$(id -u)/ai.lrm.slacksocket 로 재시작"
    if subprocess.run([notify, "--kind", "warn", "--tag", "bot-alive", msg]).returncode == 0:
        FLAG.touch()
    return 3


if __name__ == "__main__":
    sys.exit(main())
