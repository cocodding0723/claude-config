#!/usr/bin/env bash
# Map changed files -> tests and run them, per project type. Source after common.sh.
# run_scoped_tests TOP FILELIST LOG TIMEOUT  -> prints one summary line; rc 0 pass / 1 fail / 2 skip / 3 timeout

_proj_root() { # nearest ancestor of $1 (repo-relative dir) with a project marker, else $top
  local d="$top/$1" m
  while :; do
    for m in pubspec.yaml package.json pyproject.toml pytest.ini setup.cfg go.mod; do
      [ -f "$d/$m" ] && { printf '%s\n' "$d"; return; }
    done
    [ -d "$d/Assets" ] && [ -d "$d/ProjectSettings" ] && { printf '%s\n' "$d"; return; }
    [ "$d" = "$top" ] || [ "$d" = "/" ] && { printf '%s\n' "$top"; return; }
    d=$(dirname "$d")
  done
}

_detect() { # $1=root -> flutter|dart|vitest|jest|node|pytest|go|unity|unknown
  local r=$1
  if [ -f "$r/pubspec.yaml" ]; then
    grep -qE '^\s+sdk:\s*flutter|^flutter:' "$r/pubspec.yaml" && echo flutter || echo dart
  elif [ -f "$r/package.json" ]; then
    if jq -e '(.devDependencies//{}) + (.dependencies//{}) | has("vitest")' "$r/package.json" >/dev/null 2>&1; then echo vitest
    elif jq -e '(.devDependencies//{}) + (.dependencies//{}) | has("jest")' "$r/package.json" >/dev/null 2>&1; then echo jest
    else echo node; fi
  elif [ -f "$r/pyproject.toml" ] || [ -f "$r/pytest.ini" ] || [ -f "$r/setup.cfg" ]; then echo pytest
  elif [ -f "$r/go.mod" ]; then echo go
  elif [ -d "$r/Assets" ] && [ -d "$r/ProjectSettings" ]; then echo unity
  else echo unknown; fi
}

_map_dart() { # stdin: root-relative files -> existing test files
  local f t
  while IFS= read -r f; do
    case "$f" in
      test/*_test.dart) [ -f "$root/$f" ] && printf '%s\n' "$f" ;;
      lib/*.dart) t="test/${f#lib/}"; t="${t%.dart}_test.dart"; [ -f "$root/$t" ] && printf '%s\n' "$t" ;;
    esac
  done | sort -u
}
_dart_sdk() { # $1=root -> flutter 실행 파일. PATH보다 프로젝트 고정 버전(.fvmrc → .fvm/) 우선. 다른 SDK로 돌리면 pubspec.lock·.dart_tool을 덮어쓴다
  local r=$1 v cache="${FVM_CACHE_PATH:-$HOME/fvm}"
  if [ -f "$r/.fvmrc" ]; then
    v=$(jq -r '.flutter // empty' "$r/.fvmrc" 2>/dev/null)
    [ -n "$v" ] && [ -x "$cache/versions/$v/bin/flutter" ] && { echo "$cache/versions/$v/bin/flutter"; return; }
  fi
  [ -x "$r/.fvm/flutter_sdk/bin/flutter" ] && { echo "$r/.fvm/flutter_sdk/bin/flutter"; return; }
  command -v flutter
}
_dart_entry() { # $1=root-relative test file without main -> 가장 가까운 상위 디렉토리의 main 있는 *_test.dart (묶음 진입 파일)
  local d e; d=$(dirname "$1")
  while :; do
    e=$(grep -l '^void main(' "$root/$d"/*_test.dart 2>/dev/null | sed "s#^$root/##")
    [ -n "$e" ] && { printf '%s\n' "$e"; return 0; }
    case "$d" in .|test) return 1 ;; esac
    d=$(dirname "$d")
  done
}
_dart_runnable() { # stdin: test files -> 단독 실행 가능한 파일. main 없는 묶음형은 진입 파일로 치환, 못 찾으면 로그에 남기고 제외
  local t
  while IFS= read -r t; do
    if grep -q '^void main(' "$root/$t"; then printf '%s\n' "$t"
    else _dart_entry "$t" || echo "  (skip) $t: main 없고 묶음 진입 파일도 없음" >> "$log"; fi
  done | sort -u
}
_map_py() {
  local f b t
  while IFS= read -r f; do
    b=$(basename "$f" .py)
    case "$b" in test_*|*_test) [ -f "$root/$f" ] && printf '%s\n' "$f"; continue ;; esac
    find "$root/tests" "$root/test" "$(dirname "$root/$f")" -maxdepth 3 \( -name "test_$b.py" -o -name "${b}_test.py" \) 2>/dev/null | sed "s#^$root/##"
  done | sort -u
}

# _run_one ROOT FILES(newline) -> sets R_STATUS R_SUMMARY ; appends to $log
_run_one() {
  local root=$1 files=$2 kind rel tests n cmd rc rel_files
  rel=${root#$top}; rel=${rel#/}; [ -z "$rel" ] && rel=.
  rel_files=$(printf '%s\n' "$files" | sed "s#^${rel#.}/##")   # root-relative
  kind=$(_detect "$root")
  { echo "=== scoped-test: $rel ($kind) ==="; printf '%s\n' "$files"; echo; } >> "$log"

  # 1) project override script / command
  if [ -x "$root/.claude/scoped-test.sh" ]; then
    printf '%s\n' "$rel_files" | (cd "$root" && run_timeout "$timeout" "$root/.claude/scoped-test.sh") >> "$log" 2>&1; rc=$?
    _finish "$rel" "override" "$rc"; return
  fi
  cmd=$(cfg_get "$top" '.command' '')
  if [ -n "$cmd" ]; then
    local q=""; while IFS= read -r f; do q="$q '$f'"; done <<< "$rel_files"
    (cd "$root" && run_timeout "$timeout" sh -c "${cmd//\{files\}/$q}") >> "$log" 2>&1; rc=$?
    _finish "$rel" "custom" "$rc"; return
  fi

  # 2) auto-detected
  case "$kind" in
    flutter|dart)
      [ -f "$root/.dart_tool/package_config.json" ] || { R_STATUS=skip; R_SUMMARY="$rel: 의존성 미설치 (pub get 필요)"; return; }
      local fl dt; fl=$(_dart_sdk "$root"); dt="$(dirname "$fl")/dart"; [ -x "$dt" ] || dt=dart
      echo "sdk: $fl" >> "$log"
      tests=$(printf '%s\n' "$rel_files" | _map_dart | _dart_runnable)
      if [ -z "$tests" ]; then
        local dfiles; dfiles=$(printf '%s\n' "$rel_files" | grep '\.dart$')
        [ -z "$dfiles" ] && { R_STATUS=skip; R_SUMMARY="$rel: dart 파일 없음"; return; }
        (cd "$root" && run_timeout "$timeout" "$dt" analyze $dfiles) >> "$log" 2>&1; rc=$?
        _finish "$rel" "analyze only (매핑된 테스트 없음)" "$rc"; return
      fi
      n=$(printf '%s\n' "$tests" | wc -l | tr -d ' ')
      if [ "$kind" = flutter ]; then (cd "$root" && run_timeout "$timeout" "$fl" test $tests) >> "$log" 2>&1; rc=$?
      else (cd "$root" && run_timeout "$timeout" "$dt" test $tests) >> "$log" 2>&1; rc=$?; fi
      _finish "$rel" "$n test files" "$rc" ;;
    vitest|jest)
      [ -d "$root/node_modules" ] || { R_STATUS=skip; R_SUMMARY="$rel: node_modules 없음 (install 필요)"; return; }
      local jfiles; jfiles=$(printf '%s\n' "$rel_files" | grep -E '\.(ts|tsx|js|jsx|mjs|cjs)$')
      [ -z "$jfiles" ] && { R_STATUS=skip; R_SUMMARY="$rel: JS/TS 파일 없음"; return; }
      if [ "$kind" = vitest ]; then (cd "$root" && run_timeout "$timeout" npx vitest related --run $jfiles) >> "$log" 2>&1; rc=$?
      else (cd "$root" && run_timeout "$timeout" npx jest --findRelatedTests --passWithNoTests $jfiles) >> "$log" 2>&1; rc=$?; fi
      _finish "$rel" "$kind related" "$rc" ;;
    pytest)
      python3 -c 'import pytest' 2>/dev/null || { R_STATUS=skip; R_SUMMARY="$rel: pytest 미설치"; return; }
      tests=$(printf '%s\n' "$rel_files" | grep '\.py$' | _map_py)
      [ -z "$tests" ] && { R_STATUS=skip; R_SUMMARY="$rel: 매핑된 테스트 없음"; return; }
      n=$(printf '%s\n' "$tests" | wc -l | tr -d ' ')
      (cd "$root" && run_timeout "$timeout" python3 -m pytest -q $tests) >> "$log" 2>&1; rc=$?
      _finish "$rel" "$n test files" "$rc" ;;
    go)
      local pkgs; pkgs=$(printf '%s\n' "$rel_files" | grep '\.go$' | xargs -n1 dirname 2>/dev/null | sort -u | sed 's#^#./#; s#$#/...#')
      [ -z "$pkgs" ] && { R_STATUS=skip; R_SUMMARY="$rel: go 파일 없음"; return; }
      (cd "$root" && run_timeout "$timeout" go test $pkgs) >> "$log" 2>&1; rc=$?
      _finish "$rel" "$(printf '%s\n' "$pkgs" | wc -l | tr -d ' ') pkgs" "$rc" ;;
    unity) R_STATUS=skip; R_SUMMARY="$rel: Unity — .claude/scoped-test.sh 오버라이드 필요" ;;
    *)     R_STATUS=skip; R_SUMMARY="$rel: 프로젝트 타입 미인식" ;;
  esac
}
_finish() { # $1=rel $2=what $3=rc
  case "$3" in
    0)   R_STATUS=pass; R_SUMMARY="$1: PASS ($2)" ;;
    124) R_STATUS=timeout; R_SUMMARY="$1: TIMEOUT ${timeout}s ($2)" ;;
    *)   R_STATUS=fail; R_SUMMARY="$1: FAIL rc=$3 ($2)" ;;
  esac
}

run_scoped_tests() { # $1=top $2=file with repo-relative paths $3=log $4=timeout
  top=$1; log=$3; timeout=$4
  local f r roots pairs="" overall=skip summary="" any=0
  while IFS= read -r f; do [ -n "$f" ] && pairs="$pairs$(_proj_root "$(dirname "$f")")	$f
"; done < "$2"
  roots=$(printf '%s' "$pairs" | cut -f1 | sort -u)
  : > "$log"
  while IFS= read -r r; do
    root=$r
    _run_one "$r" "$(printf '%s' "$pairs" | awk -F'\t' -v r="$r" '$1==r{print $2}')"
    summary="$summary${summary:+; }$R_SUMMARY"
    case "$R_STATUS" in
      fail)    overall=fail ;;
      timeout) [ "$overall" != fail ] && overall=timeout ;;
      pass)    [ "$overall" = skip ] && overall=pass ;;
    esac
  done <<< "$roots"
  printf '%s\n' "$summary"
  case "$overall" in pass) return 0;; fail) return 1;; timeout) return 3;; *) return 2;; esac
}
