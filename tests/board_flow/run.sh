#!/usr/bin/env bash
# 작업 흐름 규칙 시험(표 형식). 종료 코드: 0 전부 통과, 1 실패
cd "$(dirname "$0")" && python3 -m unittest -q test_flow.py
