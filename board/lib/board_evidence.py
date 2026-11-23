"""카드 코멘트의 증거 줄 해석. board-done(판정)과 패널 수집기(표시)가 같은 규칙을 쓰도록 한 곳에 둠.
줄 모양: `evidence:<종류>=<경로> commit=<해시> (메모)` (종류·commit·메모는 생략 가능).
줄 끝의 괄호 메모는 읽지 않고 넘김: 작업자가 '(시험 17개 통과)' 같은 말을 붙이면 메모까지 경로로 읽혀 '파일 없음'으로 거부됐음(11-20 오후 10건)."""
import os
import re

GUESS = [
    ("mapping", re.compile(r"(mapping|대응표).*\.md$", re.I)),
    ("prefab-diff", re.compile(r"prefab.*(diff|compare|비교)", re.I)),
    ("editmode", re.compile(r"editmode.*\.xml$|\.xml$", re.I)),
    ("compile", re.compile(r"compile.*\.(log|txt)$|\.log$", re.I)),
    ("screenshot", re.compile(r"\.(png|jpe?g)$", re.I)),
    ("recording", re.compile(r"\.(mp4|mov|webm)$", re.I)),
]
# 줄 끝 메모는 경로로 읽지 않음: commit=<해시> 뒤는 아무 글이나(같은 줄만), commit= 이 없으면 괄호로 끝나는 메모만(경로의 공백과 구분)
EVIDENCE_RE = re.compile(r"^\s*evidence:\s*(?:([a-z-]+)=)?(\S.*?)(?:[ \t]+commit=([0-9a-f]{7,40})(?:[ \t]+[^\n]*)?|[ \t]+\([^\n]*\))?[ \t]*$", re.M)


def parse(texts):
    """텍스트 목록(notes·코멘트, 오래된 순) → {종류: [(경로, 커밋 또는 '')…]} 등장 순서 그대로"""
    found = {}
    for t in texts:
        for kind, path, sha in EVIDENCE_RE.findall(t or ""):
            if not kind:
                kind = next((k for k, rx in GUESS if rx.search(path)), "unknown")
            found.setdefault(kind, []).append((os.path.expanduser(path), sha))
    return found
