"""메인 코디네이터 세션 판별. 계획 승인 기록 쓰기 권한에 씀.
ORCA_ROLE 환경변수가 있으면 그 값이 main 일 때만 메인."""
import os


def is_main(ev=None):
    return os.environ.get("ORCA_ROLE") == "main"
