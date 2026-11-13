#!/usr/bin/env python3
"""할 일 행 정리: 끝난 대상의 행을 완료로, 같은 대상의 행을 하나로 합침. 순수 함수만 있고 파일·네트워크는 건드리지 않음.

대상 키는 todo_items.targets_of 가 정함(PR 번호·결정 번호·장부 번호). 대상이 모두 끝난 행은 완료,
일부만 끝났으면 남은 대상으로 제목을 다시 쓰고, 대상이 겹치는 행은 한 행으로 합침.
"""
import todo_items


def settle(rows, closed, prefer=()):
    """(행 목록, 끝난 대상 집합, 이미 목록에 있는 키) → (남길 행, [(버린 행, 'done'|'dup', 근거 문구])
    prefer: 목록에 이미 행이 있는 키. 합칠 때 이 행을 남겨 기존 행을 재사용함(앞쪽일수록 우선)"""
    kept, dropped, live = [], [], []
    for r in rows:
        open_t = [t for t in r.targets if t not in closed]
        if r.targets and not open_t:
            dropped.append((r, "done", "대상 종료: " + ", ".join(r.targets)))
            continue
        if open_t != list(r.targets):
            todo_items.retarget(r, open_t)
        live.append(r)
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for r in live:
        for t in r.targets[1:]:
            parent[find(t)] = find(r.targets[0])
        if r.targets:
            find(r.targets[0])
    groups, order = {}, []
    for r in live:
        gid = find(r.targets[0]) if r.targets else None
        if gid is None:
            kept.append(r)
            continue
        if gid not in groups:
            groups[gid] = []
            order.append(gid)
        groups[gid].append(r)
    rank = {k: n for n, k in enumerate(prefer)}
    for gid in order:
        g = groups[gid]
        win = min(g, key=lambda r: (rank.get(r.key, len(rank)), -len(r.targets), r.since or float("inf"), r.key))
        if len(g) > 1:
            targets = list(dict.fromkeys(t for r in g for t in r.targets))
            todo_items.retarget(win, targets)
            win.urgency = max(r.urgency for r in g)
            win.since = min((r.since for r in g if r.since), default=win.since)
            for r in g:
                if r is not win:
                    dropped.append((r, "dup", f"{win.key} 로 합침"))
        kept.append(win)
    return kept, dropped
