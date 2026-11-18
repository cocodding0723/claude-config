"""집은 카드의 생존 신호(heartbeat)와 완료 증거 기록. board-beat·board-claim 이 같이 씀.
저장 위치는 beads 가 이미 가진 칸: 갱신 시각 = 임대(lease)의 heartbeat_at(bd heartbeat), 한 줄 메모·증거 = 카드 metadata.
코멘트는 쓰지 않음(갱신마다 쌓여 카드가 길어짐). 패널 수집기는 `bd list --json` 한 번에서 이 칸을 읽음."""
import os
import re
import subprocess

NOTE_KEY = "beat_note"
EVIDENCE_PREFIX = "evidence_"          # metadata evidence_<종류> = 증거를 낸 커밋 해시
NOTE_MAX = 80
KIND_RE = re.compile(r"^[a-z][a-z-]{0,19}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{7,40}$")
EXIT_USAGE, EXIT_ENV, EXIT_NOT_OWNER = 2, 4, 13


class BeatError(Exception):
    def __init__(self, msg, code):
        super().__init__(msg)
        self.code = code


def _bd(args, actor):
    env = dict(os.environ, BD_ACTOR=actor)
    return subprocess.run(["bd", *args], capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL)


def clean_note(note):
    """개행·연속 공백을 한 칸으로, NOTE_MAX 자로 자름. 줄이 여러 개여도 한 줄만 남기기 위함"""
    return " ".join((note or "").split())[:NOTE_MAX]


def beat(card, actor, note=None, evidence=None):
    """heartbeat 갱신 + 선택: 한 줄 메모, 증거 (종류, 커밋). 소유자가 아니면 BeatError(13)"""
    if evidence is not None:
        kind, commit = evidence
        if not KIND_RE.match(kind or "") or not COMMIT_RE.match(commit or ""):
            raise BeatError(f"거부: 증거 형식 오류 kind={kind!r} commit={commit!r}. 조치: 종류는 compile·editmode·test, 커밋은 16진수 7~40자", EXIT_USAGE)
    r = _bd(["heartbeat", card], actor)
    if r.returncode != 0:
        err = (r.stderr or r.stdout).strip()
        low = err.lower()
        if "no issue found" in low:
            raise BeatError(f"거부: 카드 {card} 없음. 조치: bd list 로 id 확인", EXIT_USAGE)
        if "claimed by" in low or "not claimable" in low or "not in_progress" in low or "closed" in low:
            raise BeatError(f"거부: {card} 의 heartbeat 를 {actor} 가 갱신할 수 없음({err[:120]}). 조치: bd show {card} 로 담당·상태 확인. 집지 않은 카드면 board-claim 먼저", EXIT_NOT_OWNER)
        raise BeatError(f"실패: bd heartbeat {card}: {err[:160]}. 조치: BOARD_DIR·bd 설치 확인", EXIT_ENV)
    sets = []
    if note is not None and clean_note(note):
        sets.append(f"{NOTE_KEY}={clean_note(note)}")
    if evidence is not None:
        sets.append(f"{EVIDENCE_PREFIX}{evidence[0]}={evidence[1]}")
    if sets:
        args = ["update", card]
        for s in sets:
            args += ["--set-metadata", s]
        r = _bd(args, actor)
        if r.returncode != 0:
            raise BeatError(f"실패: 메모·증거 기록 {r.stderr.strip()[:160]}. 조치: bd update {card} --set-metadata 로 직접 확인", EXIT_ENV)
