#!/usr/bin/env bash
# Shared helpers for ~/.claude/hooks/*. Source only. bash 3.2 compatible (macOS default).
HOOKS_DIR="${HOOKS_DIR:-$(cd "$(dirname "$(perl -MCwd=realpath -e 'print realpath shift' "${BASH_SOURCE[0]}")")/.." && pwd -P)}"
STATE_DIR="${CLAUDE_HOOKS_STATE:-$HOME/.claude/hooks/state}"
CODE_EXT_RE='\.(cs|dart|py|ts|tsx|js|jsx|mjs|cjs|kt|java|swift|go|rs|cpp|c|h|m|mm|sh|sql|yaml|yml|json|toml|gradle|cmake)$'
OV_URL="${OPENVIKING_URL:-http://localhost:1933}"
mkdir -p "$STATE_DIR/sessions" "$STATE_DIR/repos" "$STATE_DIR/lock" "$STATE_DIR/logs"

# ---- hook input -------------------------------------------------------------
hook_input() { HOOK_JSON=$(cat); }                       # call once at top of a hook
hj() { printf '%s' "$HOOK_JSON" | jq -r "$1 // \"\""; }  # hj '.cwd'

# ---- repo -------------------------------------------------------------------
repo_top()  { git -C "$1" rev-parse --show-toplevel 2>/dev/null; }
# stable id shared by all worktrees of one repo (hash of the common git dir)
repo_hash() {
  local cd; cd=$(git -C "$1" rev-parse --path-format=absolute --git-common-dir 2>/dev/null) || return 1
  printf '%s' "$cd" | shasum | cut -c1-12
}
repo_dir() { local d="$STATE_DIR/repos/$1"; mkdir -p "$d/results"; printf '%s' "$d"; }
gh_slug() { # owner/repo of the github remote, or nothing
  git -C "$1" remote get-url origin 2>/dev/null | sed -nE 's#^(git@github\.com:|https://github\.com/)([^/]+/[^/.]+)(\.git)?/?$#\2#p'
}

# ---- project config (.claude/scoped-test.json at repo top) ------------------
cfg_get() { # $1=top $2=jq path $3=default
  local f="$1/.claude/scoped-test.json" v
  [ -f "$f" ] && v=$(jq -r "$2 // empty" "$f" 2>/dev/null)
  printf '%s' "${v:-$3}"
}

# ---- scope: repo-relative code files, one per line ---------------------------
scope_worktree() { # $1=top $2=session_id (optional)
  local top=$1 sid=$2 f
  {
    git -C "$top" diff --name-only HEAD -- 2>/dev/null
    git -C "$top" ls-files --others --exclude-standard 2>/dev/null
    [ -n "$sid" ] && [ -f "$STATE_DIR/sessions/$sid.files" ] \
      && sed "s#^$top/##" "$STATE_DIR/sessions/$sid.files" | grep -v '^/'
  } | grep -E "$CODE_EXT_RE" | sort -u | while IFS= read -r f; do
    [ -f "$top/$f" ] && printf '%s\n' "$f"
  done
}
scope_staged() { git -C "$1" diff --cached --name-only --diff-filter=ACMR 2>/dev/null | grep -E "$CODE_EXT_RE" | sort -u; }

# ---- fingerprint: hash of (path, blob) pairs; equal when staged == worktree --
fp_of() { # stdin=paths $1=top $2=worktree|staged
  local top=$1 mode=$2 f h out=""
  while IFS= read -r f; do
    [ -z "$f" ] && continue
    if [ "$mode" = staged ]; then h=$(git -C "$top" ls-files -s -- "$f" 2>/dev/null | awk '{print $2}')
    else h=$(git -C "$top" hash-object -- "$f" 2>/dev/null); fi
    out="$out$f $h
"
  done
  [ -n "$out" ] && printf '%s' "$out" | shasum -a 256 | cut -c1-16
}

# ---- results ----------------------------------------------------------------
save_result() { # $1=repo_hash $2=fp $3=status $4=summary $5=log $6=session_id(optional)
  local j; j=$(jq -cn --arg fp "$2" --arg st "$3" --arg su "$4" --arg lg "$5" --arg ts "$(date +%s)" \
    '{fp:$fp,status:$st,summary:$su,log:$lg,ts:($ts|tonumber),reported:false}')
  printf '%s\n' "$j" > "$(repo_dir "$1")/results/$2.json"
  [ -n "$6" ] && printf '%s\n' "$j" > "$STATE_DIR/sessions/$6.last.json"
  return 0
}
load_result() { cat "$(repo_dir "$1")/results/$2.json" 2>/dev/null; }

# ---- per-repo lock (mkdir based; macOS has no flock) -------------------------
lock_acquire() { # $1=repo_hash ; returns 1 if held by a live process
  local d="$STATE_DIR/lock/$1" pid
  if mkdir "$d" 2>/dev/null; then printf '%s' $$ > "$d/pid"; return 0; fi
  pid=$(cat "$d/pid" 2>/dev/null)
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then return 1; fi
  rm -rf "$d"; mkdir "$d" 2>/dev/null && printf '%s' $$ > "$d/pid"
}
lock_release() { rm -rf "$STATE_DIR/lock/$1"; }

# ---- run with timeout (perl alarm; kills the process group) -----------------
run_timeout() { # $1=secs, rest=argv. exit 124 on timeout
  perl -e '
    my $t = shift; setpgrp(0, 0);
    $SIG{ALRM} = sub { $SIG{TERM} = "IGNORE"; kill "TERM", -$$; exit 124 };
    alarm $t; my $rc = system { $ARGV[0] } @ARGV;
    exit($rc == -1 ? 127 : ($rc >> 8));
  ' "$@"
}

# ---- misc -------------------------------------------------------------------
prune_state() { # delete state older than 7 days
  find "$STATE_DIR/sessions" "$STATE_DIR/logs" -type f -mtime +7 -delete 2>/dev/null
  find "$STATE_DIR/repos" -path '*/results/*.json' -mtime +7 -delete 2>/dev/null
}
json_msg() { jq -cn --arg m "$1" '{systemMessage:$m}'; }
