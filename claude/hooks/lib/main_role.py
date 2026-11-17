"""메인 코디네이터 세션 판별. 계획 승인 기록 쓰기 권한에 씀.
1) ORCA_ROLE 이 비어 있지 않으면 그 값이 main 일 때만 메인(명시 우선).
2) 없으면 세션 기록 위치로 판별: 훅 입력 transcript_path 의 프로젝트 폴더 이름이 메인 체크아웃 경로
   (LRM_PUSH_MAIN, 기본 ~/orca/LoveRevolutionMaker)를 Claude Code 방식으로 바꾼 이름과 같으면 메인.
   프로젝트 폴더는 세션을 시작한 폴더로 정해져 cd 로 바뀌지 않음. 허브·작업자는 작업 공간에서 시작하므로 메인이 아님.
   Orca 의 agent launch 에는 환경변수를 넘길 방법이 없어, 메인을 다시 띄우지 않고도 판별되게 함."""
import os
import re


def main_checkout():
    return os.path.expanduser(os.environ.get("LRM_PUSH_MAIN") or "~/orca/LoveRevolutionMaker")


def project_key(path):
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def is_main(ev=None):
    role = os.environ.get("ORCA_ROLE", "")
    if role:
        return role == "main"
    tp = (ev or {}).get("transcript_path") or ""
    if not tp:
        return False
    key = os.path.basename(os.path.dirname(tp))
    m = main_checkout()
    return key in {project_key(m.rstrip("/")), project_key(os.path.abspath(m)), project_key(os.path.realpath(m))}
