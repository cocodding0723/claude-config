import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'hubs/scripts'))
from board_sweep import Sweeper, read_table


class FeatureMergedTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        for args in (['init', '-b', 'main'], ['-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                                             'commit', '--allow-empty', '-m', 'fixture'], ['branch', 'feature/demo']):
            subprocess.run(['git', '-C', str(self.repo), *args], check=True, capture_output=True)
        self.cfg = read_table(ROOT / 'hubs/board_sweep.tsv')
        self.cfg.update(log_path=str(self.root / 'sweep.log'), ws_root=str(self.root / 'workspaces'),
                        feature_close_script=str(ROOT / 'hubs/scripts/feature_close.sh'))
        self.card = dict(id='epic', issue_type='epic', status='open', title='demo',
                         labels=[f'repo:{self.root}/missing', f'repo:{self.repo}', 'branch:feature/demo',
                                 'feature:demo', 'stage:verify'], updated_at='2026-11-16T00:00:00Z')
        self.rows = [dict(number=7, state='MERGED', title='demo', headRefName='feature/demo')]
        self.comments = []
        self.children = []
        self.labeled = []
        self.child_comments = []
        self.writes = []
        self.gh_calls = 0
        self.fail_gh = False
        self.fail_comments = False
        self.reads = []
        self.env = patch.dict(os.environ, BOARD_SWEEP_NOW='2026-11-17T03:00:00Z', BOARD_DIR=str(self.root), BOARD_GH='gh')
        self.env.start()
        self.addCleanup(self.env.stop)

    def bd(self, *args):
        self.reads.append(args)
        if args[0] == 'list':
            return 0, json.dumps(self.children if '--parent' in args else [self.card, *self.labeled]), ''
        if args[0] == 'comments':
            return (1, '', 'failed') if self.fail_comments else (0, json.dumps(self.comments if args[1] == self.card['id'] else self.child_comments), '')
        self.writes.append(args)
        return 0, '', ''

    def commands(self, cmd, **kwargs):
        if cmd[0] == 'gh':
            self.gh_calls += 1
            return SimpleNamespace(returncode=int(self.fail_gh), stdout=json.dumps(self.rows), stderr='')
        return subprocess.run(cmd, **kwargs)

    def sweep(self, dry=False):
        sweeper = Sweeper(self.cfg, dry)
        sweeper.bd = self.bd
        sweeper.feature_pr_call = self.commands
        cards = sweeper.cards()
        spawn = Mock()
        real_popen = subprocess.Popen
        def popen(cmd, **kwargs):
            return spawn(cmd, **kwargs) if cmd[0] == 'bash' else real_popen(cmd, **kwargs)
        with patch('board_sweep.subprocess.Popen', side_effect=popen):
            sweeper.rule_feature_merged(cards)
        self.spawn = spawn
        self.sweeper = sweeper
        return spawn.call_count

    def test_merged_uses_first_valid_repo_and_detaches(self):
        self.assertEqual(self.sweep(), 1)
        self.assertEqual(self.gh_calls, 1)
        self.assertIn('PR #7 병합', self.writes[0][2])
        self.assertIn(self.cfg['feature_merged_marker'], self.writes[0][2])
        self.assertTrue(self.spawn.call_args.kwargs['start_new_session'])
        self.assertEqual(self.spawn.call_args.args[0][-3:], ['--slug', 'demo', '--yes'])

    def test_open_child_blocks(self):
        self.children = [dict(id='child', status='open')]
        self.assertEqual(self.sweep(), 0)
        self.assertEqual(self.writes, [])
        self.assertFalse(any(a[0] == 'comments' for a in self.reads))

    def test_open_or_unmerged_pr_blocks(self):
        for state in ('OPEN', 'CLOSED'):
            self.rows.append(dict(number=8, state=state, title='demo', headRefName='feature/demo'))
            self.assertEqual(self.sweep(), 0)
            self.rows.pop()
        self.assertFalse(any(a[0] == 'comments' for a in self.reads))

    def test_recent_update_or_comment_blocks(self):
        self.card['updated_at'] = '2026-11-17T02:00:00Z'
        self.assertEqual(self.sweep(), 0)
        self.card['updated_at'] = '2026-11-16T00:00:00Z'
        self.comments = [dict(text='activity', created_at='2026-11-17T01:00:00Z')]
        self.assertEqual(self.sweep(), 0)

    def test_local_merge_requires_verified_stage(self):
        self.rows = []
        self.assertEqual(self.sweep(), 1)
        self.assertIn('main 로컬 병합', self.writes[0][2])
        self.card['labels'].remove('stage:verify')
        self.card['labels'].append('stage:build')
        self.assertEqual(self.sweep(), 0)

    def test_nonancestor_branch_blocks(self):
        self.rows = []
        subprocess.run(['git', '-C', str(self.repo), 'checkout', 'feature/demo'], check=True, capture_output=True)
        subprocess.run(['git', '-C', str(self.repo), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                        'commit', '--allow-empty', '-m', 'unmerged'], check=True, capture_output=True)
        self.assertEqual(self.sweep(), 0)

    def test_failed_pr_or_comments_are_unknown(self):
        self.fail_gh = True
        self.rows = []
        self.assertEqual(self.sweep(), 0)
        self.fail_gh = False
        self.fail_comments = True
        self.assertEqual(self.sweep(), 0)

    def test_retry_interval_and_limit(self):
        self.comments = [dict(text='정리(자동): 자동 마무리', created_at='2026-11-16T04:00:00Z')]
        self.assertEqual(self.sweep(), 0)
        self.comments[0]['created_at'] = '2026-11-16T03:00:00Z'
        self.assertEqual(self.sweep(), 1)
        self.comments *= int(self.cfg['feature_merged_max_tries'])
        self.assertEqual(self.sweep(), 0)
        log = Path(self.cfg['log_path']).read_text()
        self.assertIn('retry-limit', log)
        self.assertIn('.evidence/epic-feature-close.log', log)

    def test_title_only_match_does_not_close(self):
        self.card['title'] = '[LRM-731] demo'
        self.rows[0].update(title='[LRM-731] other feature', headRefName='feature/other')
        self.assertEqual(self.sweep(), 0)

    def test_label_only_open_card_blocks(self):
        self.labeled = [dict(id='separate', issue_type='task', status='blocked', labels=['feature:demo'])]
        self.assertEqual(self.sweep(), 0)

    def test_related_update_and_comment_block(self):
        child = dict(id='child', status='closed', updated_at='2026-11-17T02:00:00Z')
        self.children = [child]
        self.assertEqual(self.sweep(), 0)
        child['updated_at'] = '2026-11-16T00:00:00Z'
        self.child_comments = [dict(text='activity', created_at='2026-11-17T02:00:00Z')]
        self.assertEqual(self.sweep(), 0)
        self.child_comments = []
        self.assertEqual(self.sweep(), 1)
        self.children = []
        self.labeled = [dict(child, labels=['feature:demo'])]
        self.child_comments = [dict(text='activity', created_at='2026-11-17T02:00:00Z')]
        self.assertEqual(self.sweep(), 0)

    def test_gh_cap_logs_skip(self):
        sweeper = Sweeper(self.cfg, True)
        sweeper.bd = self.bd
        cards = sweeper.cards()
        sweeper.gh_calls = int(self.cfg['gh_calls_max'])
        sweeper.rule_feature_merged(cards)
        self.assertEqual(self.writes, [])
        self.assertIn('feature_merged\t-\tgh-cap', Path(self.cfg['log_path']).read_text())

    def test_closed_feature_requires_remaining_hub(self):
        self.card['status'] = 'closed'
        hub = Path(self.cfg['ws_root']) / 'other-repo/coord-feat-demo-hub'
        hub.mkdir(parents=True)
        with patch.object(Sweeper, 'run', return_value=(0, json.dumps({'result': {'worktrees': [dict(path=str(hub))]}}), '')):
            self.assertEqual(self.sweep(), 1)
        hub.rmdir()
        with patch.object(Sweeper, 'run', return_value=(0, '{"result":{"worktrees":[]}}', '')):
            self.assertEqual(self.sweep(), 0)

    def test_dry_run_logs_without_writes_or_spawn(self):
        self.assertEqual(self.sweep(dry=True), 0)
        self.assertEqual(self.writes, [])
        self.assertFalse((self.root / '.evidence').exists())
        self.assertIn('\tdry\tfeature_merged\tepic\tfeature-close\tdemo:', Path(self.cfg['log_path']).read_text())

    def test_skip_and_protect(self):
        self.card['labels'].append(self.cfg['skip_label'])
        self.assertEqual(self.sweep(), 0)
        self.card['labels'].pop()
        self.cfg['protect_ids'] = 'epi*'
        self.assertEqual(self.sweep(), 0)

    def test_all_prs_must_merge(self):
        self.rows.append(dict(number=8, state='MERGED', title='demo', headRefName='feature/demo'))
        self.assertEqual(self.sweep(), 1)
        self.assertIn('#7, #8', self.writes[0][2])

    def test_configured_target_and_grace(self):
        self.rows = []
        self.cfg['merged_into'] = 'missing,dev'
        subprocess.run(['git', '-C', str(self.repo), 'branch', 'dev'], check=True, capture_output=True)
        self.assertEqual(self.sweep(), 1)
        self.assertIn('dev 로컬 병합', self.writes[0][2])
        self.cfg['feature_merged_hours'] = '48'
        self.assertEqual(self.sweep(), 0)

    def test_shared_index_skips_nonrepository_directory(self):
        self.card['labels'].insert(0, f'repo:{self.root}')
        self.assertEqual(self.sweep(), 1)
        self.assertEqual(self.gh_calls, 1)

    def test_hub_inventory_is_cached_and_bad_json_falls_back(self):
        hub = Path(self.cfg['ws_root']) / 'other-repo/coord-feat-demo-hub'
        hub.mkdir(parents=True)
        for output in ('invalid json', '{"result":{"worktrees":[]}}'):
            sweeper = Sweeper(self.cfg, True)
            with patch.object(sweeper, 'run', return_value=(0, output, '')) as run:
                self.assertTrue(sweeper.feature_hub_exists('demo'))
                self.assertFalse(sweeper.feature_hub_exists('absent'))
                self.assertEqual(run.call_count, 1)
                self.assertIn(self.cfg['feature_worktree_limit'], run.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
