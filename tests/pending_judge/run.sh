#!/usr/bin/env bash
# 대기 목록 판정 시험(표 형식). 파일·Slack 을 쓰지 않음. 종료 코드: 0 전부 통과, 1 실패
cd "$(dirname "$0")" && python3 -m unittest -q test_judge.py
