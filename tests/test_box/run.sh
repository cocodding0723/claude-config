#!/usr/bin/env bash
# 격리 시험 실행기(test-box) 시험. 종료 코드: 0 전부 통과, 1 실패
cd "$(dirname "$0")" && python3 -m unittest -q test_box.py
