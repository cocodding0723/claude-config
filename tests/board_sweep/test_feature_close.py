import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[2] / 'hubs/scripts/feature_close.sh'
REGISTRY = SCRIPT.parent / 'lib/closed_hubs.sh'


class FeatureCloseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.log = self.root / 'calls'
        self.hub = self.root / 'other-repo/coord-feat-demo-hub'
        self.hub.mkdir(parents=True)
        self.env = {**os.environ, 'PATH': f'{self.bin}:{os.environ["PATH"]}',
                    'LRM_BOARD': str(self.root), 'WS_ROOT': str(self.root / 'fallback'),
                    'LRM_CLOSED_HUBS': str(self.root / 'closed'), 'TEST_CALLS': str(self.log),
                    'TEST_HUB': str(self.hub), 'TEST_MODE': 'found'}
        self.executable('orca', '''import json, os, sys
a = sys.argv[1:]
with open(os.environ['TEST_CALLS'], 'a') as f: f.write('orca ' + ' '.join(a) + '\\n')
rows = [] if os.environ['TEST_MODE'] != 'found' else [dict(id='hub', name=None, displayName='[관리] 다른 저장소 허브', path=os.environ['TEST_HUB'])]
if rows and os.environ.get('TEST_DUPLICATE'):
    rows.append(dict(id='hub2', path=os.environ['TEST_DUPLICATE']))
if rows and os.environ.get('TEST_CHILD'):
    rows.append(dict(id='child', parentWorktreeId='hub', path=os.environ['TEST_CHILD'], branch='refs/heads/fix/child'))
print(json.dumps({'result': {'worktrees': rows, 'workers': [], 'terminals': []}}))
''')
        self.executable('bd', '''import json, os, sys
a = sys.argv[1:]
with open(os.environ['TEST_CALLS'], 'a') as f: f.write('bd ' + ' '.join(a) + '\\n')
if a[0] == 'list':
    print(json.dumps([] if '--parent' in a else [dict(id='epic', issue_type='epic', status=os.environ.get('TEST_STATUS', 'open'), labels=[])]))
elif a[0] == 'comments': print('[]')
''')
        self.executable('cleanup', 'pass\n')
        self.env['LRM_WS_GC'] = str(self.bin / 'cleanup.sh')
        (self.bin / 'cleanup.sh').write_text('exit 0\n')
        self.env['LRM_HUB_REPORT'] = str(self.bin / 'cleanup')

    def executable(self, name, source):
        path = self.bin / name
        path.write_text('#!/usr/bin/env python3\n' + source)
        path.chmod(0o755)

    def run_close(self, *args):
        result = subprocess.run(['bash', str(SCRIPT), '--slug', 'demo', '--yes', '--json', *args],
                                env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.stderr = result.stderr
        return json.loads(result.stdout)

    def test_cross_repo_and_repeat(self):
        self.assertEqual(self.run_close()['hub'], str(self.hub.resolve()))
        (self.root / 'closed').write_text(f'{self.hub}\t1\n{self.hub}\t2\n')
        self.env['TEST_STATUS'] = 'closed'
        self.run_close()
        rows = (self.root / 'closed').read_text().splitlines()
        self.assertEqual(len(rows), 1)
        path, stamp = rows[0].split('\t')
        self.assertEqual(path, str(self.hub.resolve()))
        self.assertGreater(int(stamp), 2)
        self.assertEqual(self.log.read_text().count('bd close epic'), 1)

    def test_fallback(self):
        self.env['TEST_MODE'] = 'missing'
        fallback = self.root / 'fallback/coord-feat-demo-hub'
        fallback.mkdir(parents=True)
        self.assertEqual(self.run_close()['hub'], str(fallback.resolve()))

    def test_merged_child_uses_hub_repo(self):
        for args in (['init', '-b', 'feature/demo'],
                     ['-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                      'commit', '--allow-empty', '-m', 'fixture'], ['branch', 'fix/child']):
            subprocess.run(['git', '-C', str(self.hub), *args], check=True, capture_output=True)
        self.env['TEST_CHILD'] = str(self.root / 'child')
        self.env['REPO'] = str(self.root / 'wrong-repo')
        self.assertEqual(self.run_close()['unmerged_branches'], [])

    def test_symlink_hub_keeps_children(self):
        alias = self.root / 'alias/coord-feat-demo-hub'
        alias.parent.mkdir()
        alias.symlink_to(self.hub, target_is_directory=True)
        self.env['TEST_HUB'] = str(alias)
        self.env['TEST_CHILD'] = str(self.root / 'child')
        result = self.run_close('--dry-run')
        self.assertEqual(result['hub'], str(self.hub.resolve()))
        self.assertEqual(len(result['unmerged_branches']), 1)

    def test_duplicate_hubs_skip_hub_steps(self):
        other = self.root / 'second/coord-feat-demo-hub'
        other.mkdir(parents=True)
        self.env['TEST_DUPLICATE'] = str(other)
        result = self.run_close()
        self.assertEqual(result['hub'], '')
        self.assertIn('같은 이름 허브 여러 개', self.stderr)
        self.assertFalse((self.root / 'closed').exists())
        self.assertNotIn('terminal', self.log.read_text())

    def test_concurrent_set_clear_preserves_other_rows(self):
        registry = self.root / 'closed'
        doomed = str(self.root / 'old')
        registry.write_text(f'{doomed}\t1\n{self.hub}\t1')
        command = ['bash', '-c', 'source "$1"; closed_hubs_registry "$2" "$3" "$4" "$5"',
                   'registry', str(REGISTRY)]
        env = {**self.env, 'PY': os.sys.executable}
        processes = [subprocess.Popen([*command, 'clear', str(registry), doomed, '1'], env=env)]
        for n in range(12):
            processes.append(subprocess.Popen([*command, 'set', str(registry),
                                                str(self.root / f'new-{n}'), ''], env=env))
        self.run_close()
        for process in processes:
            self.assertEqual(process.wait(), 0)
        rows = dict(line.split('\t') for line in registry.read_text().splitlines())
        self.assertEqual(len(rows), 13)
        self.assertNotIn(doomed, rows)
        self.assertIn(str(self.hub.resolve()), rows)
        subprocess.run([*command, 'clear', str(registry), str(self.hub), '1'], env=env, check=True)
        self.assertEqual(len(registry.read_text().splitlines()), 13)

    def test_missing_hub_still_closes_epic(self):
        self.env['TEST_MODE'] = 'missing'
        result = self.run_close()
        self.assertEqual(result['hub'], '')
        self.assertIn('bd close epic', self.log.read_text())
        self.assertNotIn('terminal', self.log.read_text())
        self.assertFalse((self.root / 'closed').exists())

    def test_dry_run_has_no_writes(self):
        result = self.run_close('--dry-run')
        self.assertEqual(result['status'], 'dry_run')
        self.assertTrue(any(step['step'] == 'registry' and '허브 폴더 삭제' in step['message']
                            for step in result['steps']))
        self.assertFalse((self.root / 'closed').exists())
        self.assertNotIn('bd close', self.log.read_text())


if __name__ == '__main__':
    unittest.main()
