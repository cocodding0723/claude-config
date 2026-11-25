"""Orca 밖(launchd)에서 부르는 claude 의 실행 파일과 로그인 프로필 선택."""
import os
import pwd
import shutil
from pathlib import Path

# Orca 가 선택 계정의 설정 폴더 절대 경로를 적는 파일. LRM_ORCA_CLAUDE_POINTER 로 대체
POINTER = "Library/Application Support/orca/claude-profiles/selected-host"
CANDIDATES = (".local/bin/claude", "/opt/homebrew/bin/claude", "/usr/local/bin/claude")


def find_claude(env=None):
    """claude 경로. LRM_CLAUDE → PATH → 알려진 위치 순. 못 찾으면 맨 'claude'"""
    env = os.environ if env is None else env
    home = Path(env.get("HOME") or Path.home())
    return (env.get("LRM_CLAUDE") or shutil.which("claude", path=env.get("PATH"))
            or next((str(home / c) for c in CANDIDATES if os.access(home / c, os.X_OK)), "claude"))


def claude_env(env=None):
    """claude 하위 프로세스 환경 사본. 계정 전환을 따라가도록 호출 때마다 선택 프로필을 읽음.
    CLAUDE_CONFIG_DIR 이 이미 있으면 유지. 선택 파일이 없거나 가리키는 폴더가 없으면 기본 설정(~/.claude) 사용"""
    out = dict(os.environ if env is None else env)
    if not out.get("USER"):  # claude 가 USER 로 키체인 로그인 항목을 찾음
        out["USER"] = pwd.getpwuid(os.getuid()).pw_name
    if out.get("CLAUDE_CONFIG_DIR"):
        return out
    out.pop("CLAUDE_CONFIG_DIR", None)
    home = Path(out.get("HOME") or Path.home())
    pointer = Path(out.get("LRM_ORCA_CLAUDE_POINTER") or home / POINTER)
    try:
        selected = pointer.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return out
    if os.path.isabs(selected) and os.path.isdir(selected):
        out["CLAUDE_CONFIG_DIR"] = selected
    return out
