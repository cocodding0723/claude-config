#!/usr/bin/env python3
"""저장소의 티켓 트래커 판정. jira-issue·github-issue·허브 규칙이 '어디에 발급하나'를 이 도구 하나로 정함.

사용: tracker_of.py [저장소 경로(기본 현재 폴더)] [--json]
출력: 'jira LRM' · 'jira'(키 모름) · 'github'. --json 이면 {"tracker","key","repo","source"}
순서: ~/.config/claude-config/trackers.tsv 의 처음 맞는 줄(저장소 owner/repo, * 패턴) → values.env TRACKER → jira
환경: CLAUDE_CONFIG_TRACKERS·CLAUDE_CONFIG_VALUES 로 파일 위치 변경(시험용)
종료 코드: 0 판정함, 2 표 형식 오류(줄 번호와 함께 안내)
"""
import fnmatch
import json
import os
import re
import subprocess
import sys

TABLE = os.environ.get("CLAUDE_CONFIG_TRACKERS", os.path.expanduser("~/.config/claude-config/trackers.tsv"))
VALUES = os.environ.get("CLAUDE_CONFIG_VALUES", os.path.expanduser("~/.config/claude-config/values.env"))
REMOTE_RE = re.compile(r"github\.com[:/]([\w.-]+/[\w.-]+?)(?:\.git)?/?$")
JIRA_KEY_RE = re.compile(r"[A-Z][A-Z0-9]{1,9}")


def repo_of(path):
    r = subprocess.run(["git", "-C", path, "remote", "get-url", "origin"], capture_output=True, text=True)
    m = REMOTE_RE.search(r.stdout.strip()) if r.returncode == 0 else None
    return m.group(1) if m else ""


def parse_value(v, where):
    if v == "github":
        return "github", ""
    if v.startswith("jira:") and JIRA_KEY_RE.fullmatch(v[5:]):
        return "jira", v[5:]
    raise ValueError(f"{where}: 트래커 값 '{v}' — jira:<프로젝트 키(대문자)> 또는 github")


def from_table(repo):
    try:
        lines = open(TABLE, encoding="utf-8").read().splitlines()
    except OSError:
        return None
    for i, line in enumerate(lines, 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        cols = line.split("\t")
        if len(cols) != 2:
            raise ValueError(f"{TABLE}:{i}: 탭으로 나눈 두 칸(저장소·트래커)이어야 함")
        tracker, key = parse_value(cols[1].strip(), f"{TABLE}:{i}")
        if repo and fnmatch.fnmatch(repo.lower(), cols[0].strip().lower()):
            return tracker, key
    return None


def from_values():
    try:
        for line in open(VALUES, encoding="utf-8"):
            k, _, v = line.strip().partition("=")
            if k == "TRACKER" and v.strip():
                return v.strip()
    except OSError:
        pass
    return "jira"


def main(argv):
    as_json = "--json" in argv
    args = [a for a in argv if a != "--json"]
    path = args[0] if args else "."
    repo = repo_of(path)
    try:
        hit = from_table(repo)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2
    if hit:
        (tracker, key), source = hit, "trackers.tsv"
    else:
        tracker, key, source = from_values(), "", "values.env"
    if as_json:
        print(json.dumps({"tracker": tracker, "key": key, "repo": repo, "source": source}, ensure_ascii=False))
    else:
        print(f"{tracker} {key}".strip())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
