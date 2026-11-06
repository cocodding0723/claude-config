---
name: worktree-gc
description: List git worktrees of the current repo with their status and remove the stale ones one by one after explicit per-item confirmation. Use when the user asks to clean up, prune, or garbage-collect worktrees, or after session-start reports stale worktrees.
---

# worktree-gc

Clean up accumulated git worktrees **safely**. Deletion is always per-item and always confirmed.

## Steps

1. `git worktree prune` (safe: only drops entries whose directory is already gone).
2. Collect the linked worktrees (exclude the main one): `git worktree list --porcelain`.
   For each, gather:
   - branch, last commit age (`git -C <wt> log -1 --format=%cr`)
   - uncommitted changes count (`git -C <wt> status --porcelain | wc -l`)
   - merged into the default branch? (`git merge-base --is-ancestor <branch> <main>`)
   - pushed? (`git rev-parse --abbrev-ref <branch>@{upstream}` exists and `git log <upstream>..<branch>` is empty)
3. Show a table: path · branch · age · merged · pushed · dirty.
4. Classify:
   - **safe**: merged (or pushed with nothing local) AND clean.
   - **risky**: dirty, or unpushed commits. Never remove these without the user saying so explicitly for that item, and remind that unpushed/uncommitted work will be lost.
5. Use AskUserQuestion (multiSelect) listing the candidates with their classification. Only remove what the user selected.
6. For each selected: `git worktree remove <path>` (add `--force` only for a *risky* item the user explicitly chose). Then offer `git branch -d <branch>` for merged branches; never `-D` (git-guard blocks it anyway).
7. Report what was removed and what was kept.

Never run `git worktree remove` on the worktree the current session is in.
