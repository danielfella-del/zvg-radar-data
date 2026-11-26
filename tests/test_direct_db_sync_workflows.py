"""Offline regression checks for producer-to-DB workflow coupling.

Run with: python tests/test_direct_db_sync_workflows.py
Requires PyYAML for parsing; all Git remotes used here are temporary local paths.
No production endpoint or database sync client is invoked.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / '.github' / 'workflows'
PRODUCERS = {'update-zvg.yml': 'update', 'enrich-zvg.yml': 'enrich'}


def workflow(name):
    # BaseLoader preserves the GitHub Actions "on" key (YAML 1.1 treats it as bool).
    return yaml.load((WORKFLOWS / name).read_text(), Loader=yaml.BaseLoader)


def run(args, cwd, *, env=None, check=True):
    return subprocess.run(args, cwd=cwd, env=env, check=check, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def git(cwd, *args):
    return run(['git', *args], cwd).stdout.strip()


def shell(script, cwd, **variables):
    env = dict(os.environ, **variables)
    return run(['bash', '--noprofile', '--norc', '-e', '-o', 'pipefail', '-c', script],
               cwd, env=env, check=False)


class WorkflowGraphTests(unittest.TestCase):
    def test_producers_cannot_mutate_main_from_feature_branch_or_fork(self):
        consumer_gate = workflow('sync-zvgpro-db.yml')['jobs']['sync']['if']
        for name, producer in PRODUCERS.items():
            with self.subTest(workflow=name):
                wf = workflow(name)
                self.assertEqual(wf['jobs'][producer]['if'], consumer_gate)
                self.assertEqual(wf['jobs']['sync']['if'], consumer_gate)

    def test_direct_call_requires_success_and_existing_secret(self):
        permitted = {'name', 'needs', 'if', 'permissions', 'uses', 'with', 'secrets'}
        for name, producer in PRODUCERS.items():
            with self.subTest(workflow=name):
                wf = workflow(name)
                call = wf['jobs']['sync']
                self.assertEqual(set(call), permitted)
                self.assertEqual(call['needs'], producer)
                self.assertEqual(call['uses'], './.github/workflows/sync-zvgpro-db.yml')
                self.assertEqual(call['permissions'], {'contents': 'read'})
                self.assertEqual(call['with'], {
                    'published_sha': '${{ needs.' + producer + '.outputs.published_sha }}'})
                self.assertEqual(call['secrets'], {
                    'ZVGPRO_DB_SYNC_TOKEN': '${{ secrets.ZVGPRO_DB_SYNC_TOKEN }}'})
                self.assertNotIn('always(', call['if'])
                self.assertIn("github.repository == 'danielfella-del/zvg-radar-data'", call['if'])
                self.assertIn("github.ref == 'refs/heads/main'", call['if'])
                self.assertIn("github.event_name == 'schedule'", call['if'])
                self.assertIn("github.event_name == 'workflow_dispatch'", call['if'])

    def test_locks_are_separate_and_not_held_by_caller_workflow(self):
        sync = workflow('sync-zvgpro-db.yml')
        self.assertNotIn('concurrency', sync)
        self.assertEqual(sync['jobs']['sync']['concurrency'], {
            'group': 'zvgpro-mariadb-sync', 'cancel-in-progress': 'false'})
        for name, producer in PRODUCERS.items():
            wf = workflow(name)
            self.assertNotIn('concurrency', wf)
            self.assertNotIn('concurrency', wf['jobs']['sync'])
            self.assertEqual(wf['jobs'][producer]['concurrency'], {
                'group': 'zvg-data-writer', 'cancel-in-progress': 'false'})

    def test_cron_manual_and_trust_boundary_remain(self):
        wf = workflow('sync-zvgpro-db.yml')
        self.assertEqual(set(wf['on']), {'workflow_dispatch', 'workflow_call', 'schedule'})
        self.assertEqual(wf['on']['schedule'], [{'cron': '7,22,37,52 * * * *'}])
        self.assertEqual(wf['on']['workflow_call']['inputs']['published_sha']['required'], 'true')
        self.assertEqual(wf['on']['workflow_call']['secrets'], {
            'ZVGPRO_DB_SYNC_TOKEN': {'required': 'true'}})
        self.assertEqual(wf['permissions'], {'contents': 'read'})
        condition = wf['jobs']['sync']['if']
        self.assertIn("github.repository == 'danielfella-del/zvg-radar-data'", condition)
        self.assertIn("github.ref == 'refs/heads/main'", condition)
        self.assertNotIn("event_name == 'workflow_call'", condition)
        self.assertNotIn('workflow_run', condition)
        for name, minute in [('update-zvg.yml', '17'), ('enrich-zvg.yml', '37')]:
            self.assertEqual(workflow(name)['on']['schedule'], [{'cron': minute + ' * * * *'}])

    def test_current_main_is_resolved_inside_serialized_job(self):
        steps = workflow('sync-zvgpro-db.yml')['jobs']['sync']['steps']
        checkout = next(s for s in steps if s.get('uses') == 'actions/checkout@v4')
        self.assertEqual(checkout['with']['ref'], 'main')
        self.assertEqual(checkout['with']['persist-credentials'], 'false')
        self.assertEqual(set(checkout['with']['sparse-checkout'].split()), {
            '/data/auctions.json', '/.github/scripts/sync-zvgpro-db.py',
            '/collector/publication_policy.py'})
        prepare = next(s for s in steps if 'cp data/auctions.json' in s.get('run', ''))
        self.assertLess(prepare['run'].index('publication_policy.py --check'),
                        prepare['run'].index('cp data/auctions.json'))
        self.assertEqual(steps[-1]['env'], {
            'ZVGPRO_DB_SYNC_TOKEN': '${{ secrets.ZVGPRO_DB_SYNC_TOKEN }}'})

    def test_all_run_blocks_are_valid_bash(self):
        for name in [*PRODUCERS, 'sync-zvgpro-db.yml']:
            for job in workflow(name)['jobs'].values():
                for step in job.get('steps', []):
                    if 'run' in step:
                        result = subprocess.run(
                            ['bash', '-n'], input=step['run'], text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                        self.assertEqual(result.returncode, 0, (name, step['name'], result.stderr))


class LocalGitExecutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='zvg-sync-workflow-')
        self.base = Path(self.tmp.name)
        self.origin = self.base / 'origin.git'
        run(['git', 'init', '--bare', '--initial-branch=main', str(self.origin)], self.base)
        self.author = self.base / 'author'
        run(['git', 'clone', str(self.origin), str(self.author)], self.base)
        git(self.author, 'config', 'user.name', 'Offline regression')
        git(self.author, 'config', 'user.email', 'offline@example.invalid')
        self.write_snapshot(self.author, 'A')
        (self.author / 'media').mkdir()
        (self.author / 'media' / '.keep').write_text('')
        git(self.author, 'add', '.')
        git(self.author, 'commit', '-m', 'snapshot A')
        git(self.author, 'push', 'origin', 'main')
        self.first = git(self.author, 'rev-parse', 'HEAD')
        self.producer = self.base / 'producer'
        run(['git', 'clone', str(self.origin), str(self.producer)], self.base)

    def tearDown(self):
        self.tmp.cleanup()

    def write_snapshot(self, repo, label, withdrawn=False):
        (repo / 'data').mkdir(parents=True, exist_ok=True)
        (repo / '.github/scripts').mkdir(parents=True, exist_ok=True)
        (repo / 'collector').mkdir(parents=True, exist_ok=True)
        payload = {'meta': {'marker': label}, 'results': [{'id': 'by-49303' if withdrawn else 'test-123', 'label': label}]}
        (repo / 'data/auctions.json').write_text(json.dumps(payload))
        (repo / '.github/scripts/sync-zvgpro-db.py').write_text('# snapshot ' + label + '\n')
        shutil.copyfile(ROOT / 'collector/publication_policy.py', repo / 'collector/publication_policy.py')

    def publish_author(self, label, withdrawn=False):
        self.write_snapshot(self.author, label, withdrawn)
        git(self.author, 'add', '.')
        git(self.author, 'commit', '-m', 'snapshot ' + label)
        git(self.author, 'push', 'origin', 'main')
        return git(self.author, 'rev-parse', 'HEAD')

    def producer_steps(self, name):
        steps = workflow(name)['jobs'][PRODUCERS[name]]['steps']
        save = next(s for s in steps if s['name'] in ('Änderungen speichern', 'Dossiers und Medien speichern'))
        output = next(s for s in steps if s.get('id') == 'published_snapshot')
        self.assertLess(steps.index(save), steps.index(output))
        return save['run'], output['run']

    def test_unchanged_data_still_emits_published_head_for_both_producers(self):
        for name in PRODUCERS:
            with self.subTest(workflow=name):
                save, output = self.producer_steps(name)
                result = shell(save, self.producer)
                self.assertEqual(result.returncode, 0, result.stderr)
                output_file = self.base / (name + '.out')
                result = shell(output, self.producer, GITHUB_OUTPUT=str(output_file))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(output_file.read_text(), 'published_sha=' + self.first + '\n')

    def test_rebase_then_push_outputs_final_published_commit(self):
        (self.author / 'README.txt').write_text('Concurrent independent source change\n')
        git(self.author, 'add', '.')
        git(self.author, 'commit', '-m', 'independent change')
        git(self.author, 'push', 'origin', 'main')
        # The producer's working copy is still at A. Its data commit must rebase.
        payload = json.loads((self.producer / 'data/auctions.json').read_text())
        payload['meta']['marker'] = 'producer update'
        (self.producer / 'data/auctions.json').write_text(json.dumps(payload))
        save, output = self.producer_steps('update-zvg.yml')
        result = shell(save, self.producer)
        self.assertEqual(result.returncode, 0, result.stderr)
        output_file = self.base / 'published.out'
        result = shell(output, self.producer, GITHUB_OUTPUT=str(output_file))
        self.assertEqual(result.returncode, 0, result.stderr)
        published = git(self.origin, 'rev-parse', 'refs/heads/main')
        self.assertNotEqual(published, self.first)
        self.assertEqual(output_file.read_text(), 'published_sha=' + published + '\n')
        self.assertEqual((self.producer / 'README.txt').read_text(), 'Concurrent independent source change\n')

    def sync_scripts(self):
        steps = workflow('sync-zvgpro-db.yml')['jobs']['sync']['steps']
        return steps[0]['run'], next(s['run'] for s in steps if 'cp data/auctions.json' in s.get('run', ''))

    def test_queued_old_caller_uses_new_main_and_one_immutable_checkout(self):
        second = self.publish_author('B')
        selected = self.base / 'selected'
        # Models checkout(ref=main) only after the sync lock has been acquired.
        run(['git', 'clone', '--branch', 'main', str(self.origin), str(selected)], self.base)
        self.assertEqual(git(selected, 'rev-parse', 'HEAD'), second)
        self.publish_author('C')  # Main can advance after checkout without mixing files.
        validate, prepare = self.sync_scripts()
        self.assertEqual(shell(validate, selected, PUBLISHED_SHA=self.first).returncode, 0)
        result = shell(prepare, selected, PUBLISHED_SHA=self.first)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(second, result.stdout)
        self.assertEqual(json.loads((selected / 'auctions.json').read_text())['meta']['marker'], 'B')
        self.assertEqual((selected / 'sync-zvgpro-db.py').read_text(), '# snapshot B\n')
        self.assertIn('überholt', result.stdout)

    def test_standalone_cron_and_manual_need_no_caller_sha(self):
        validate, prepare = self.sync_scripts()
        self.assertEqual(shell(validate, self.producer, PUBLISHED_SHA='').returncode, 0)
        result = shell(prepare, self.producer, PUBLISHED_SHA='')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(self.first, result.stdout)

    def test_invalid_caller_input_fails_without_shell_execution(self):
        validate, _ = self.sync_scripts()
        for value in ['main', 'a' * 39, 'A' * 40, '$(touch unexpected-file)', 'a' * 40 + '\n']:
            with self.subTest(value=value):
                result = shell(validate, self.producer, PUBLISHED_SHA=value)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.producer / 'unexpected-file').exists())

    def test_publication_guard_stops_before_preparing_any_sync_payload(self):
        self.write_snapshot(self.producer, 'withdrawn', withdrawn=True)
        _, prepare = self.sync_scripts()
        result = shell(prepare, self.producer, PUBLISHED_SHA=self.first)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('"publication_policy": "blocked"', result.stdout)
        self.assertFalse((self.producer / 'auctions.json').exists())
        self.assertFalse((self.producer / 'sync-zvgpro-db.py').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
