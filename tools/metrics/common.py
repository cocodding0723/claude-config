"""지표 집계 공용: 기간 인자·KST 변환·작업 폴더. 4개 집계가 같은 기간·같은 중간 파일을 씀."""
import argparse
import datetime as D
import os

KST = D.timedelta(hours=9)


def args(desc):
    ap = argparse.ArgumentParser(description=desc)
    ap.add_argument("--since", required=True, help="시작일 YYYY-MM-DD (KST, 포함)")
    ap.add_argument("--until", required=True, help="끝일 YYYY-MM-DD (KST, 포함)")
    ap.add_argument("--work", required=True, help="중간 결과 폴더(sess.json·sess2.json·msg.json)")
    a = ap.parse_args()
    a.start = D.datetime.fromisoformat(a.since)
    a.end = D.datetime.fromisoformat(a.until) + D.timedelta(days=1)
    os.makedirs(a.work, exist_ok=True)
    a.path = lambda n: os.path.join(a.work, n)
    return a


def kst(ts):
    """UTC ISO 시각 문자열(앞 19자) → KST naive datetime"""
    return D.datetime.fromisoformat(ts[:19]) + KST


def claude_base():
    return os.path.expanduser("~/.claude/projects")
