"""PR 퀴즈 보기 순서 섞기 + 정답 위치 편중 검사.

opts·miss 를 같은 순열로 옮기고 ans 를 새 위치로 갱신함. 시드 = PR 번호(재실행 결정적).
사용: build_v4.py 가 자동 호출. 기존 HTML 일괄: shuffle_opts.py <quiz.html> [--seed N] [--dry]
"""
import json, random, re, sys

# 보기 위치를 가리키는 해설은 섞으면 깨짐 → 해당 문항 제외
POSITIONAL = re.compile(r'[①②③④]|(?:첫|두|세|네) ?번째 보기|(?:위|아래|앞|뒤)(?:의)? 보기|(?<![A-Za-z])[ABCD] ?(?:번|보기)|보기 ?[ABCD1-4]')


def is_positional(q):
    texts = [q.get('ask', ''), q.get('why', '')] + list(q['opts']) + [m or '' for m in q['miss']]
    return any(POSITIONAL.search(t) for t in texts)


def shuffle_questions(qs, seed):
    """제자리 변경. 제외한 문항 인덱스 목록 반환."""
    rng = random.Random(seed)
    skipped = [i for i, q in enumerate(qs) if is_positional(q)]
    count = {}
    for i in skipped:
        count[qs[i]['ans']] = count.get(qs[i]['ans'], 0) + 1
    order = [i for i in range(len(qs)) if i not in skipped]
    rng.shuffle(order)
    for i in order:
        q = qs[i]; k = len(q['opts'])
        low = min(count.get(p, 0) for p in range(k))
        p = rng.choice([p for p in range(k) if count.get(p, 0) == low])
        count[p] = count.get(p, 0) + 1
        old = q['ans']
        rest = [j for j in range(k) if j != old]; rng.shuffle(rest)
        perm = rest[:p] + [old] + rest[p:]  # perm[새 위치] = 옛 인덱스
        q['opts'] = [q['opts'][j] for j in perm]
        q['miss'] = [q['miss'][j] for j in perm]
        q['ans'] = p
    check_balance(qs)
    return skipped


def distribution(qs):
    k = max(len(q['opts']) for q in qs)
    return [sum(1 for q in qs if q['ans'] == p) for p in range(k)]


def check_balance(qs):
    """모든 문항이 가진 위치(0..최소 보기 수-1)의 횟수 편차 ≤ 1 이어야 함."""
    kmin = min(len(q['opts']) for q in qs)
    c = distribution(qs)[:kmin]
    if max(c) - min(c) > 1:
        raise SystemExit('정답 위치 편중: %s' % c)
    return c


def pairs(qs):
    return [({(o, m or '') for o, m in zip(q['opts'], q['miss'])}, q['opts'][q['ans']]) for q in qs]


DATA = re.compile(r'(<script id="data" type="application/json">)(.*?)(</script>)', re.S)


def main():
    path = sys.argv[1]
    s = open(path, encoding='utf-8').read()
    m = DATA.search(s)
    raw = m.group(2)
    d = json.loads(raw.replace('<\\/', '</'))
    assert json.dumps(d, ensure_ascii=False).replace('</', '<\\/') == raw, '직렬화 왕복 불일치'
    seed = int(sys.argv[sys.argv.index('--seed') + 1]) if '--seed' in sys.argv else d['pr']['num']
    before = pairs(d['q']); dist0 = distribution(d['q']); n0 = [len(q['opts']) for q in d['q']]
    skipped = shuffle_questions(d['q'], seed)
    assert pairs(d['q']) == before and [len(q['opts']) for q in d['q']] == n0, '쌍 집합 불일치'
    print('%s n=%d 전 %s → 후 %s 제외 %s PROOF-OK' % (path.split('/')[-1], len(d['q']), dist0, distribution(d['q']), [i + 1 for i in skipped]))
    if '--dry' not in sys.argv:
        out = json.dumps(d, ensure_ascii=False).replace('</', '<\\/')
        open(path, 'w', encoding='utf-8').write(s[:m.start(2)] + out + s[m.end(2):])


if __name__ == '__main__':
    main()
