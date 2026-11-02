import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from dispatch import Conflict, Ledger, read_config


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for folder in ('config', 'finance', 'portal'):
            (self.root / folder).mkdir()
        self.path = self.root / 'config/routing.json'
        self.config = {'version': 1, 'workspace_root': '..', 'state_path': '../state/tasks.sqlite3',
                       'resources': ['repo:finance', 'repo:portal', 'server:production'],
                       'projects': {
                           'finance': {'path': 'finance', 'thread_id': 'finance-id', 'host_id': 'local', 'resources': ['repo:finance']},
                           'portal': {'path': 'portal', 'thread_id': 'portal-id', 'host_id': 'local', 'resources': ['repo:portal']}}}
        self.config['entries'] = {'entry': {'host_id': 'local', 'thread_id': 'entry-id', 'projects': ['finance', 'portal'], 'scopes': ['discussion', 'local', 'deploy'], 'resources': self.config['resources']}}
        self.path.write_text(json.dumps(self.config))
        self.l = Ledger(self.path, 'entry', 'local', 'entry-id')

    def tearDown(self):
        self.l.db.close()
        self.temp.cleanup()

    def task(self, key='one', project='finance', scope='local', resources=()):
        return self.l.create(key, project, 'test work', scope, 'human requested this test', resources)

    def running(self, **kwargs):
        t = self.l.prepare(self.task(**kwargs)['id'])
        return self.l.receipt(t['id'], t['attempt'], 'native tool acknowledged')

    def test_request_dedup_and_different_payload_conflict(self):
        self.assertEqual(self.task()['id'], self.task()['id'])
        with self.assertRaises(Conflict): self.task(scope='deploy')

    def test_discussion_never_dispatched(self):
        with self.assertRaises(Conflict): self.l.prepare(self.task(scope='discussion')['id'])

    def test_prepare_committed_before_send_and_no_automatic_retry(self):
        t = self.l.prepare(self.task()['id'])
        other = Ledger(self.path, 'entry', 'local', 'entry-id')
        self.assertEqual(other.get(t['id'])['status'], 'dispatching')
        with self.assertRaises(Conflict): other.prepare(t['id'])
        other.db.close()
        self.l.uncertain(t['id'], 'transport timed out')
        with self.assertRaises(Conflict): self.l.prepare(t['id'])
        self.assertTrue(self.l.summary()['locks'])

    def test_failed_lock_acquisition_rolls_back_all_claims(self):
        t = self.running()
        b = self.task(key='two', project='portal', resources=['repo:finance'])
        with self.assertRaises(Conflict): self.l.prepare(b['id'])
        locks = self.l.summary()['locks']
        self.assertTrue(all(x['task_id'] == t['id'] for x in locks))
        self.assertEqual(self.l.get(b['id'])['status'], 'pending')

    def test_shared_server_serialized_across_projects_and_connections(self):
        self.running(scope='deploy')
        b = self.task(key='two', project='portal', scope='deploy')
        other = Ledger(self.path, 'entry', 'local', 'entry-id')
        with self.assertRaises(Conflict): other.prepare(b['id'])
        other.db.close()

    def test_stale_receipt_rejected_after_verified_unsent_retry(self):
        t = self.l.prepare(self.task()['id'])
        self.l.reconcile_unsent(t['id'], 'destination history verified no message')
        new = self.l.prepare(t['id'])
        with self.assertRaises(Conflict): self.l.receipt(t['id'], t['attempt'], 'late receipt')
        self.l.receipt(t['id'], new['attempt'], 'confirmed')

    def test_delayed_ack_can_resolve_uncertainty_without_resending(self):
        t = self.l.prepare(self.task()['id'])
        self.l.uncertain(t['id'], 'timeout')
        self.assertEqual(self.l.receipt(t['id'], t['attempt'], 'found matching message')['status'], 'running')
        with self.assertRaises(Conflict): self.l.reconcile_unsent(t['id'], 'not permitted')

    def test_stage_and_authorization_gates(self):
        t = self.running()
        with self.assertRaises(Conflict): self.l.finish(t['id'], 'tool returned')
        with self.assertRaises(Conflict): self.l.progress(t['id'], 'verified', 'skip implementation')
        self.l.progress(t['id'], 'implemented', 'diff')
        self.l.progress(t['id'], 'verified', 'checks')
        with self.assertRaises(Conflict): self.l.progress(t['id'], 'deployed', 'no deploy scope')
        self.l.finish(t['id'], 'coordinator reviewed report')
        self.assertFalse(self.l.summary()['locks'])

    def test_deployment_completion_requires_live_verification(self):
        t = self.running(scope='deploy')
        for stage in ['implemented', 'verified', 'deployed']:
            self.l.progress(t['id'], stage, 'evidence')
        with self.assertRaises(Conflict): self.l.finish(t['id'], 'no online check')
        self.l.progress(t['id'], 'live_verified', 'online check')
        self.l.finish(t['id'], 'reviewed')

    def test_subset_polls_keep_other_cursor_and_dont_complete_tasks(self):
        t = self.running()
        self.l.merge_cursors([{'thread': {'id': 'finance-id', 'hostId': 'local'}, 'cursor': '1'},
                              {'thread': {'id': 'portal-id', 'hostId': 'local'}, 'cursor': '2'}])
        rows = self.l.merge_cursors([{'thread': {'id': 'finance-id', 'hostId': 'local'}, 'cursor': '3',
                                     'latestTurn': {'status': 'completed'}}])
        self.assertEqual({r['thread']: r['cursor'] for r in rows}, {'finance-id': '3', 'portal-id': '2'})
        self.assertEqual(self.l.get(t['id'])['status'], 'running')

    def test_route_drift_stops_dispatch(self):
        t = self.task()
        self.config['projects']['finance']['thread_id'] = 'different'
        self.path.write_text(json.dumps(self.config))
        other = Ledger(self.path, 'entry', 'local', 'entry-id')
        with self.assertRaises(Conflict): other.prepare(t['id'])
        other.db.close()

    def test_path_escape_rejected(self):
        self.config['projects']['finance']['path'] = '../elsewhere'
        self.path.write_text(json.dumps(self.config))
        with self.assertRaises(ValueError): read_config(self.path)

    def test_cancel_requires_handoff_and_releases_locks(self):
        t = self.running()
        with self.assertRaises(Conflict): self.l.cancel(t['id'], '')
        self.l.cancel(t['id'], 'human cancelled; executor confirmed stopped')
        self.assertFalse(self.l.summary()['locks'])

    def test_concurrent_dispatch_only_one_can_claim_server(self):
        a = self.task(scope='deploy')['id']
        b = self.task(key='two', project='portal', scope='deploy')['id']
        barrier = Barrier(2)
        def claim(ident):
            ledger = Ledger(self.path, 'entry', 'local', 'entry-id')
            barrier.wait()
            try:
                ledger.prepare(ident)
                return True
            except Conflict:
                return False
            finally:
                ledger.db.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, [a, b]))
        self.assertEqual(sorted(results), [False, True])

    def restricted_entry(self):
        self.config['entries']['other'] = {
            'host_id': 'local', 'thread_id': 'x-id', 'projects': ['portal'],
            'scopes': ['local'], 'resources': ['repo:portal']}
        self.path.write_text(json.dumps(self.config))
        return Ledger(self.path, 'other', 'local', 'x-id')

    def test_missing_or_mismatched_entry_rejected(self):
        for args in [(), ('entry', 'local', 'wrong'), ('unknown', 'local', 'entry-id')]:
            with self.assertRaises(Conflict): Ledger(self.path, *args)

    def test_cross_group_target_scope_and_resource_rejected(self):
        other = self.restricted_entry()
        try:
            for project, scope, resources in [('finance', 'local', []),
                                               ('portal', 'deploy', []),
                                               ('portal', 'local', ['repo:finance'])]:
                with self.assertRaises(Conflict):
                    other.create('denied', project, 'work', scope, 'human request', resources)
            self.assertEqual(other.summary()['tasks'], [])
        finally:
            other.db.close()

    def test_other_entry_cannot_read_cancel_or_finish_task(self):
        t = self.running()
        other = self.restricted_entry()
        try:
            with self.assertRaises(Conflict): other.get(t['id'])
            with self.assertRaises(Conflict): other.cancel(t['id'], 'attempt to clear lock')
            with self.assertRaises(Conflict): other.finish(t['id'], 'fake completion')
            self.assertEqual(other.summary()['tasks'], [])
            self.assertEqual(other.summary()['locks'], [])
        finally:
            other.db.close()
        self.assertEqual(self.l.get(t['id'])['status'], 'running')

    def test_poll_allowlist_transaction_rollback(self):
        other = self.restricted_entry()
        try:
            with self.assertRaises(Conflict):
                other.merge_cursors([
                    {'thread': {'id': 'portal-id', 'hostId': 'local'}, 'cursor': 'valid'},
                    {'thread': {'id': 'finance-id', 'hostId': 'local'}, 'cursor': 'forbidden'}])
            self.assertEqual(other.visible_cursors(), [])
        finally:
            other.db.close()

    def test_policy_revoked_before_prepare(self):
        t = self.task()
        self.config['entries']['entry']['projects'] = ['portal']
        self.path.write_text(json.dumps(self.config))
        fresh = Ledger(self.path, 'entry', 'local', 'entry-id')
        try:
            with self.assertRaises(Conflict): fresh.prepare(t['id'])
        finally:
            fresh.db.close()

    def test_request_keys_are_scoped_to_entry(self):
        t = self.task(project='portal')
        other = self.restricted_entry()
        try:
            new = other.create('one', 'portal', 'test work', 'local', 'human request')
            self.assertNotEqual(new['id'], t['id'])
        finally:
            other.db.close()


if __name__ == '__main__':
    unittest.main()
