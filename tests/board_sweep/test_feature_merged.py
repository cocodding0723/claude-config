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
from board_sweep import Sweeper, parse_time, read_table


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
        self.orca_result = (0, json.dumps({'result': {'worktrees': []}}), '')
        self.terminal_result = (0, json.dumps({'result': {'terminals': []}}), '')
        run = Sweeper.run
        def commands(sweeper, cmd, timeout):
            if cmd[0] == 'orca':
                return self.terminal_result if cmd[1] == 'terminal' else self.orca_result
            return run(sweeper, cmd, timeout)
        inventory = patch.object(Sweeper, 'run', autospec=True, side_effect=commands)
        inventory.start()
        self.addCleanup(inventory.stop)

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
        self.comments = [dict(author='board-sweep', text='정리(자동): 자동 마무리', created_at='2026-11-16T04:00:00Z')]
        self.assertEqual(self.sweep(), 0)
        self.comments[0]['created_at'] = '2026-11-16T03:00:00Z'
        self.assertEqual(self.sweep(), 1)
        self.comments *= int(self.cfg['feature_merged_max_tries'])
        self.assertEqual(self.sweep(), 0)
        self.assertEqual(self.sweep(), 0)
        log = Path(self.cfg['log_path']).read_text()
        self.assertEqual(log.count('\tretry-limit\t'), 1)
        self.assertIn('.evidence/epic-feature-close.log', log)
        self.comments.append(self.comments[0])
        self.assertEqual(self.sweep(), 0)
        self.assertEqual(Path(self.cfg['log_path']).read_text().count('\tretry-limit\t'), 2)

    def hub_inventory(self, activity=None, commit=None, registered=True, children=(), terminals=()):
        hub = Path(self.cfg['ws_root']) / 'other-repo/coord-feat-demo-hub'
        hub.mkdir(parents=True, exist_ok=True)
        rows = [dict(id='hub', path=str(hub), lastActivityAt=activity, childWorktreeIds=['listed-child'])] if registered else []
        rows += list(children)
        def run(cmd, timeout):
            if cmd[0] == 'orca':
                if cmd[1] == 'terminal':
                    return 0, json.dumps({'result': {'terminals': list(terminals)}}), ''
                return 0, json.dumps({'result': {'worktrees': rows}}), ''
            self.assertEqual(cmd, ['git', '-C', str(hub), 'log', '-1', '--format=%ct'])
            return (0, str(commit), '') if commit is not None else (1, '', 'not a repository')
        return patch.object(Sweeper, 'run', side_effect=run)

    def test_closed_hub_activity_after_marker_skips(self):
        self.card.update(status='closed', closed_at='2026-11-15T00:00:00Z')
        self.comments = [dict(author='board-sweep', text='정리(자동): 자동 마무리', created_at='2026-11-16T00:00:00Z')]
        activity = parse_time('2026-11-16T00:10:00Z').timestamp() * 1000
        with self.hub_inventory(activity=activity + 1):
            self.assertEqual(self.sweep(), 0)
        self.assertEqual(self.writes, [])
        self.assertIn('reason=hub_reopened', Path(self.cfg['log_path']).read_text())
        with self.hub_inventory(activity=activity) as run:
            self.assertEqual(self.sweep(), 1)
        self.assert_inventory_once(run)

    def test_closed_hub_without_marker_uses_closed_time(self):
        self.card.update(status='closed', closed_at='2026-11-16T00:00:00Z')
        with self.hub_inventory(activity=parse_time('2026-11-16T00:11:00Z').timestamp() * 1000):
            self.assertEqual(self.sweep(), 0)
        self.assertIn('reason=hub_reopened', Path(self.cfg['log_path']).read_text())

    def test_reopened_hub_logs_once_per_baseline(self):
        self.card.update(status='closed', closed_at='2026-11-15T00:00:00Z')
        with self.hub_inventory(activity=parse_time('2026-11-16T00:11:00Z').timestamp() * 1000):
            self.assertEqual(self.sweep(), 0)
            self.assertEqual(self.sweep(), 0)
            self.assertEqual(Path(self.cfg['log_path']).read_text().count('reason=hub_reopened'), 1)
            self.card['closed_at'] = '2026-11-16T00:00:00Z'
            self.assertEqual(self.sweep(), 0)
        self.assertEqual(Path(self.cfg['log_path']).read_text().count('reason=hub_reopened'), 2)

    def test_latest_marker_overrides_old_close_and_marker(self):
        self.card.update(status='closed', closed_at='2026-11-14T00:00:00Z')
        self.comments = [dict(author='board-sweep', text='정리(자동): 자동 마무리', created_at=t)
                         for t in ('2026-11-16T00:00:00Z', '2026-11-15T00:00:00Z')]
        with self.hub_inventory(activity=parse_time('2026-11-15T00:20:00Z').timestamp() * 1000):
            self.assertEqual(self.sweep(), 1)

    def test_hub_commit_protects_filesystem_fallback(self):
        self.card.update(status='closed', closed_at='2026-11-16T00:00:00Z')
        with self.hub_inventory(commit=int(parse_time('2026-11-16T00:11:00Z').timestamp()), registered=False):
            self.assertEqual(self.sweep(), 0)
        self.assertIn('reason=hub_reopened', Path(self.cfg['log_path']).read_text())
        with self.hub_inventory(commit=int(parse_time('2026-11-16T00:10:00Z').timestamp())):
            self.assertEqual(self.sweep(), 1)

    def test_first_attempt_recent_hub_activity_blocks(self):
        self.card['updated_at'] = '2026-11-17T00:00:00Z'
        with self.hub_inventory(activity=parse_time('2026-11-17T02:00:00Z').timestamp() * 1000) as run:
            self.assertEqual(self.sweep(), 0)
        self.assertEqual(self.writes, [])
        self.assert_inventory_once(run)

    def assert_inventory_once(self, run):
        for command, limit in [('worktree', 'feature_worktree_limit'), ('terminal', 'feature_terminal_limit')]:
            calls = [c.args[0] for c in run.call_args_list if c.args[0][:2] == ['orca', command]]
            self.assertEqual(calls, [['orca', command, 'list', '--limit', self.cfg[limit], '--json']])

    def terminal_activity(self, path, timestamp):
        return dict(worktreePath=str(path), lastOutputAt=parse_time(timestamp).timestamp() * 1000)

    def test_first_attempt_recent_hub_output_blocks(self):
        hub = Path(self.cfg['ws_root']) / 'other-repo/coord-feat-demo-hub'
        old = parse_time('2026-11-17T00:00:00Z').timestamp()
        terminals = [self.terminal_activity(hub, '2026-11-17T02:00:00Z')]
        with self.hub_inventory(activity=old * 1000, commit=int(old), terminals=terminals):
            self.assertEqual(self.sweep(), 0)
        self.assertEqual(self.writes, [])

    def test_first_attempt_recent_child_output_blocks_for_both_links(self):
        child = self.root / 'reviewer'
        for link in (dict(id='child', parentWorktreeId='hub'), dict(id='listed-child')):
            with self.subTest(link=link), self.hub_inventory(
                    children=[dict(path=str(child), **link)],
                    terminals=[self.terminal_activity(child, '2026-11-17T02:00:00Z')]):
                self.assertEqual(self.sweep(), 0)
        self.assertEqual(self.writes, [])

    def test_first_attempt_recent_child_activity_blocks_for_both_links(self):
        for link in (dict(id='child', parentWorktreeId='hub'), dict(id='listed-child')):
            child = dict(path=str(self.root / 'reviewer'),
                         lastActivityAt=parse_time('2026-11-17T02:00:00Z').timestamp() * 1000, **link)
            with self.subTest(link=link), self.hub_inventory(children=[child]):
                self.assertEqual(self.sweep(), 0)
        self.assertEqual(self.writes, [])

    def test_old_output_allows_close_and_inventory_is_cached(self):
        hub = Path(self.cfg['ws_root']) / 'other-repo/coord-feat-demo-hub'
        child = self.root / 'reviewer'
        old = parse_time('2026-11-17T00:00:00Z').timestamp()
        terminals = [self.terminal_activity(path, '2026-11-17T00:00:00Z') for path in (hub, child)]
        with self.hub_inventory(activity=old * 1000, commit=int(old),
                                children=[dict(id='listed-child', path=str(child), lastActivityAt=old * 1000)],
                                terminals=terminals) as run:
            self.assertEqual(self.sweep(), 1)
            self.sweeper.feature_hub_times('demo')
            self.sweeper.feature_hub_times('other')
        self.assert_inventory_once(run)

    def test_terminal_failure_skips_and_logs_once(self):
        for result in ((1, '', 'failed'), (127, '', 'TimeoutExpired'), (0, 'bad json', ''),
                       (0, '{}', ''), (0, '{"result":{"terminals":{}}}', ''),
                       (0, '{"result":{"terminals":[null]}}', ''),
                       (0, '{"result":{"terminals":[],"truncated":true}}', '')):
            with self.subTest(result=result):
                Path(self.cfg['log_path']).unlink(missing_ok=True)
                self.terminal_result = result
                self.assertEqual(self.sweep(), 0)
                self.sweeper.feature_hubs('other')
                self.assertFalse(self.sweeper.feature_hub_available)
                self.assertEqual(self.sweep(), 0)
                self.assertEqual(Path(self.cfg['log_path']).read_text().count('reason=hub_unknown'), 1)
                self.assertEqual(self.writes, [])

    def test_output_after_baseline_protects_hub_and_child(self):
        self.card.update(status='closed', closed_at='2026-11-16T00:00:00Z')
        hub = Path(self.cfg['ws_root']) / 'other-repo/coord-feat-demo-hub'
        child = self.root / 'reviewer'
        for path in (hub, child):
            with self.subTest(path=path), self.hub_inventory(
                    children=[dict(id='listed-child', path=str(child))],
                    terminals=[self.terminal_activity(path, '2026-11-16T00:11:00Z')]):
                self.assertEqual(self.sweep(), 0)
        self.assertIn('reason=hub_reopened', Path(self.cfg['log_path']).read_text())

    def test_unrelated_terminal_output_is_ignored(self):
        terminals = [self.terminal_activity(self.root / 'unrelated', '2026-11-17T02:00:00Z')]
        with self.hub_inventory(terminals=terminals):
            self.assertEqual(self.sweep(), 1)

    def test_first_attempt_recent_hub_commit_blocks_without_activity(self):
        self.card['updated_at'] = '2026-11-17T00:00:00Z'
        with self.hub_inventory(commit=int(parse_time('2026-11-17T02:00:00Z').timestamp())):
            self.assertEqual(self.sweep(), 0)
        self.assertEqual(self.writes, [])

    def test_first_attempt_old_hub_activity_and_commit_allow_close(self):
        self.card['updated_at'] = '2026-11-17T00:00:00Z'
        stamp = parse_time(self.card['updated_at']).timestamp()
        with self.hub_inventory(activity=stamp * 1000, commit=int(stamp)) as run:
            self.assertEqual(self.sweep(), 1)
        self.assertEqual(sum(c.args[0][0] == 'git' for c in run.call_args_list), 1)

    def test_first_attempt_without_hub_uses_card_activity(self):
        self.card['updated_at'] = '2026-11-17T00:00:00Z'
        self.assertEqual(self.sweep(), 1)
        self.card['updated_at'] = '2026-11-17T02:00:00Z'
        self.assertEqual(self.sweep(), 0)

    def test_hub_quiet_boundary_blocks_first_attempt_and_retry(self):
        for retry in (False, True):
            self.comments = ([dict(author='board-sweep', text='정리(자동): 자동 마무리',
                                   created_at='2026-11-16T00:00:00Z')] if retry else [])
            for field in ('activity', 'commit'):
                stamp = parse_time('2026-11-17T01:00:00Z').timestamp()
                values = {field: stamp * 1000 if field == 'activity' else int(stamp)}
                with self.subTest(retry=retry, field=field), self.hub_inventory(**values):
                    self.assertEqual(self.sweep(), 0)
        self.assertEqual(self.writes, [])

    def test_only_automatic_author_and_line_prefix_count(self):
        self.comments = [dict(author=author, text=text, created_at='2026-11-17T00:00:00Z')
                         for author, text in [('person', '정리(자동): 자동 마무리'),
                                              ('board-sweep', '인용: 정리(자동): 자동 마무리'),
                                              ('board-sweep', '정리(자동): 다른 규칙'),
                                              (None, '정리(자동): 자동 마무리')]]
        self.assertEqual(self.sweep(), 1)

    def test_open_retry_also_protects_reopened_hub(self):
        self.comments = [dict(author='board-sweep', text='정리(자동): 자동 마무리', created_at='2026-11-16T00:00:00Z')]
        with self.hub_inventory(activity=parse_time('2026-11-16T00:11:00Z').timestamp() * 1000):
            self.assertEqual(self.sweep(), 0)
        self.assertIn('reason=hub_reopened', Path(self.cfg['log_path']).read_text())

    def close_log(self, timestamp):
        path = self.root / '.evidence/epic-feature-close.log'
        path.parent.mkdir(exist_ok=True)
        path.touch()
        stamp = parse_time(timestamp).timestamp()
        os.utime(path, (stamp, stamp))

    def test_completion_log_after_marker_allows_retry(self):
        self.comments = [dict(author='board-sweep', text='정리(자동): 자동 마무리', created_at='2026-11-16T00:00:00Z')]
        self.close_log('2026-11-16T00:15:00Z')
        with self.hub_inventory(activity=parse_time('2026-11-16T00:12:00Z').timestamp() * 1000):
            self.assertEqual(self.sweep(), 1)

    def test_old_completion_log_does_not_hide_reopened_hub(self):
        self.comments = [dict(author='board-sweep', text='정리(자동): 자동 마무리', created_at='2026-11-16T00:00:00Z')]
        self.close_log('2026-11-15T23:45:00Z')
        with self.hub_inventory(activity=parse_time('2026-11-16T00:12:00Z').timestamp() * 1000):
            self.assertEqual(self.sweep(), 0)
        self.assertIn('reason=hub_reopened baseline=2026-11-16T00:00:00+00:00', Path(self.cfg['log_path']).read_text())

    def test_close_after_marker_is_latest_baseline(self):
        self.card.update(status='closed', closed_at='2026-11-16T00:15:00Z')
        self.comments = [dict(author='board-sweep', text='정리(자동): 자동 마무리', created_at='2026-11-16T00:00:00Z')]
        with self.hub_inventory(activity=parse_time('2026-11-16T00:12:00Z').timestamp() * 1000):
            self.assertEqual(self.sweep(), 1)

    def test_completion_log_alone_provides_baseline(self):
        self.close_log('2026-11-16T00:00:00Z')
        with self.hub_inventory(activity=parse_time('2026-11-16T00:12:00Z').timestamp() * 1000):
            self.assertEqual(self.sweep(), 0)
        self.assertIn('reason=hub_reopened', Path(self.cfg['log_path']).read_text())

    def test_failed_inventory_with_baseline_skips_and_logs_once(self):
        self.comments = [dict(author='board-sweep', text='정리(자동): 자동 마무리', created_at='2026-11-16T00:00:00Z')]
        for result in ((1, '', 'failed'), (127, '', 'TimeoutExpired'), (0, 'bad json', ''),
                       (0, '{}', ''), (0, '{"result":{"worktrees":{}}}', ''),
                       (0, '{"result":{"worktrees":[null]}}', '')):
            with self.subTest(result=result):
                Path(self.cfg['log_path']).unlink(missing_ok=True)
                self.orca_result = result
                self.assertEqual(self.sweep(), 0)
                self.assertEqual(self.sweep(), 0)
                self.assertEqual(Path(self.cfg['log_path']).read_text().count('reason=hub_unknown'), 1)
                self.assertEqual(self.writes, [])
        self.comments[0]['created_at'] = '2026-11-16T01:00:00Z'
        self.assertEqual(self.sweep(), 0)
        self.assertEqual(Path(self.cfg['log_path']).read_text().count('reason=hub_unknown'), 2)

    def test_failed_inventory_without_baseline_skips_and_logs_once(self):
        for status in ('open', 'closed'):
            with self.subTest(status=status):
                Path(self.cfg['log_path']).unlink(missing_ok=True)
                self.card['status'] = status
                if status == 'closed':
                    hub = Path(self.cfg['ws_root']) / 'other-repo/coord-feat-demo-hub'
                    hub.mkdir(parents=True)
                self.orca_result = (1, '', 'failed')
                self.assertEqual(self.sweep(), 0)
                self.assertEqual(self.sweep(), 0)
                log = Path(self.cfg['log_path']).read_text()
                self.assertEqual(log.count('reason=hub_unknown baseline=none'), 1)
                self.assertEqual(self.writes, [])

    def test_failed_inventory_closed_card_without_local_hub_skips_without_reads(self):
        self.card.update(status='closed', closed_at='2026-11-16T00:00:00Z')
        self.orca_result = (1, '', 'failed')
        self.assertEqual(self.sweep(), 0)
        self.assertEqual(self.reads, [('list', '--all', '--json', '-n', '0')])
        self.assertFalse(Path(self.cfg['log_path']).exists())

    def test_dry_skip_does_not_suppress_apply_log(self):
        self.comments = [dict(author='board-sweep', text='정리(자동): 자동 마무리', created_at='2026-11-16T00:00:00Z')]
        self.orca_result = (1, '', 'failed')
        self.assertEqual(self.sweep(dry=True), 0)
        self.assertEqual(self.sweep(), 0)
        self.assertEqual(self.sweep(), 0)
        self.assertEqual(self.sweep(dry=True), 0)
        log = Path(self.cfg['log_path']).read_text()
        self.assertEqual(log.count('\tdry\tfeature_merged\tepic\tskip\treason=hub_unknown'), 1)
        self.assertEqual(log.count('\tapply\tfeature_merged\tepic\tskip\treason=hub_unknown'), 1)

    def test_unreadable_skip_log_does_not_abort_rule(self):
        self.comments = [dict(author='board-sweep', text='정리(자동): 자동 마무리', created_at='2026-11-16T00:00:00Z')]
        self.orca_result = (1, '', 'failed')
        real_open = open
        for error in (PermissionError('denied'), UnicodeDecodeError('utf-8', b'\xff', 0, 1, 'invalid')):
            def open_log(path, mode='r', *args, **kwargs):
                if str(path) == self.cfg['log_path'] and mode == 'r':
                    raise error
                return real_open(path, mode, *args, **kwargs)
            with self.subTest(error=error), patch('builtins.open', side_effect=open_log):
                self.assertEqual(self.sweep(), 0)
                self.assertEqual(self.writes, [])
        self.assertEqual(Path(self.cfg['log_path']).read_text().count('reason=hub_unknown'), 2)

    def test_retry_limit_without_existing_log(self):
        self.comments = [dict(author='board-sweep', text='정리(자동): 자동 마무리', created_at='2026-11-16T00:00:00Z')]
        self.comments *= int(self.cfg['feature_merged_max_tries'])
        self.assertFalse(Path(self.cfg['log_path']).exists())
        self.assertEqual(self.sweep(), 0)
        self.assertEqual(self.sweep(), 0)
        self.assertEqual(Path(self.cfg['log_path']).read_text().count('\tretry-limit\t'), 1)

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
        with self.hub_inventory():
            self.assertEqual(self.sweep(), 1)
        hub.rmdir()
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
            results = [(0, output, ''), self.terminal_result]
            with patch.object(sweeper, 'run', side_effect=results) as run:
                self.assertTrue(sweeper.feature_hub_exists('demo'))
                self.assertFalse(sweeper.feature_hub_exists('absent'))
                if output == 'invalid json':
                    self.assertEqual(run.call_count, 1)
                    self.assertFalse(sweeper.feature_hub_available)
                else:
                    self.assert_inventory_once(run)
                    self.assertTrue(sweeper.feature_hub_available)


if __name__ == '__main__':
    unittest.main()
