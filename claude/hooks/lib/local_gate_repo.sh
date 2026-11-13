#!/usr/bin/env bash
# 로컬 게이트 대상 판정 공용(pre-push·git-guard 가 source). 값은 이 파일 한 곳에만 둠.
LG_REPO_NAME=LoveRevolutionMaker
LG_SCRIPT=Tools/ci/local-gate.sh
LG_STORE=${LRM_LOCAL_GATE_DIR:-$HOME/.config/lrm-hubs/local-gate}

# 공통 git 디렉터리 기준 저장소 이름(워크트리여도 원본 저장소 이름)
lg_repo_name() {
  local c; c=$(git -C "${1:-.}" rev-parse --path-format=absolute --git-common-dir 2>/dev/null) || return 1
  c=${c%/}; [ "$(basename "$c")" = .git ] && c=$(dirname "$c")
  basename "$c" .git
}
lg_cfg() { git -C "${1:-.}" config --get "$2" 2>/dev/null; }

# 게이트 적용 대상 저장소: 이름 일치 또는 hooks.localGate=on. off 로 꺼도 이름이 맞으면 대상(우회 감지용)
lg_applies() {
  case "$(lg_cfg "$1" hooks.localGate)" in on|true|1) return 0;; esac
  [ "$(lg_repo_name "$1")" = "$LG_REPO_NAME" ]
}
# run=실행 / off=대상인데 꺼짐(경고) / skip=대상 아님
lg_state() {
  case "$(lg_cfg "$1" hooks.localGate)" in
    on|true|1) echo run; return;;
    off|false|0) lg_applies "$1" && echo off || echo skip; return;;
  esac
  lg_applies "$1" && echo run || echo skip
}
