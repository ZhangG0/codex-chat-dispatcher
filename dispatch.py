#!/usr/bin/env python3
"""Local dispatch ledger. No network, messages, shell execution or credentials."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
import uuid


class Conflict(ValueError):
    pass


def read_config(path):
    path = Path(path).resolve()
    data = json.loads(path.read_text())
    if data.get('version') != 1:
        raise ValueError('Unsupported config version')
    root = (path.parent / data['workspace_root']).resolve()
    state = (path.parent / data['state_path']).resolve()
    if root not in state.parents or state.parent == root:
        raise ValueError('State must use a dedicated directory inside workspace')
    resources = data['resources']
    if len(resources) != len(set(resources)):
        raise ValueError('Duplicate resources')
    for key, project in data['projects'].items():
        folder = (root / project['path']).resolve()
        if root not in folder.parents or not folder.is_dir():
            raise ValueError('Invalid project path: ' + key)
        if not project['thread_id'] or not project['host_id']:
            raise ValueError('Missing thread/host: ' + key)
        if set(project['resources']) - set(resources):
            raise ValueError('Unknown project resource: ' + key)
    if not data.get('entries'):
        raise ValueError('Explicit entry policy required')
    for entry in data['entries'].values():
        if not entry['thread_id'] or not entry['host_id']:
            raise ValueError('Entry identity required')
        if set(entry['projects']) - set(data['projects']):
            raise ValueError('Unknown allowed project')
        if set(entry['scopes']) - {'discussion', 'local', 'deploy'}:
            raise ValueError('Unknown allowed scope')
        if set(entry['resources']) - set(resources):
            raise ValueError('Unknown allowed resource')
    return data, root, state


class Ledger:
    def __init__(self, config, entry=None, caller_host=None, caller_thread=None):
        self.config, self.root, path = read_config(config)
        self.entry = entry
        policy = self.config['entries'].get(entry)
        if not policy or (caller_host, caller_thread) != (policy['host_id'], policy['thread_id']):
            raise Conflict('Entry/caller identity mismatch; no default entry')
        self.policy = policy
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Dedicated state directory; no private values belong in this ledger.
        os.chmod(path.parent, 0o700)
        old_mask = os.umask(0o077)
        try:
            self.db = sqlite3.connect(path, isolation_level=None, timeout=10)
            self.db.row_factory = sqlite3.Row
            self.db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS tasks(
                  id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL,
                  payload TEXT NOT NULL, status TEXT NOT NULL,
                  stage TEXT NOT NULL, attempt TEXT, receipt TEXT);
                CREATE TABLE IF NOT EXISTS locks(
                  resource TEXT PRIMARY KEY, task_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events(
                  seq INTEGER PRIMARY KEY, task_id TEXT, kind TEXT NOT NULL,
                  detail TEXT NOT NULL, recorded REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS cursors(
                  host TEXT, thread TEXT, cursor TEXT,
                  PRIMARY KEY(host,thread));
            ''')
        finally:
            os.umask(old_mask)
        os.chmod(path, 0o600)

    @contextmanager
    def tx(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    def event(self, task, kind, detail):
        self.db.execute('INSERT INTO events(task_id,kind,detail,recorded) VALUES(?,?,?,?)',
                        (task, kind, json.dumps(detail, ensure_ascii=False), time.time()))

    def allowed(self, project, scope, resources):
        if project not in self.policy['projects'] or scope not in self.policy['scopes']:
            raise Conflict('Entry is not allowed to use this project/scope')
        if set(resources) - set(self.policy['resources']):
            raise Conflict('Entry is not allowed to claim these resources')

    def get(self, task):
        row = self.db.execute('SELECT * FROM tasks WHERE id=?', (task,)).fetchone()
        if row is None:
            raise ValueError('Unknown task')
        result = dict(row)
        result['payload'] = json.loads(result['payload'])
        payload = result['payload']
        if payload.get('entry') != self.entry:
            raise Conflict('Task belongs to another entry or legacy unscoped task')
        self.allowed(payload['project'], payload['scope'], payload['resources'])
        return result

    def create(self, key, project, title, scope, authorization, resources=()):
        if not key.strip() or not title.strip():
            raise ValueError('Request key and title must not be empty')
        if scope not in ('discussion', 'local', 'deploy') or not authorization.strip():
            raise ValueError('Scope and human authorization evidence required')
        route = dict(self.config['projects'][project])
        resources = sorted(set(route['resources']) | set(resources))
        if scope == 'deploy':
            resources = sorted(set(resources) | {'server:production'})
        if set(resources) - set(self.config['resources']):
            raise ValueError('Unknown resource')
        self.allowed(project, scope, resources)
        key = json.dumps([self.entry, key])
        payload = dict(entry=self.entry, project=project, title=title, scope=scope,
                       authorization=authorization, route=route, resources=resources,
                       cwd=str((self.root / route['path']).resolve()))
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        with self.tx():
            old = self.db.execute('SELECT id,payload FROM tasks WHERE request_key=?', (key,)).fetchone()
            if old:
                if old['payload'] != encoded:
                    raise Conflict('Request key already belongs to different task data')
                return self.get(old['id'])
            ident = uuid.uuid4().hex[:16]
            self.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?)',
                            (ident, key, encoded, 'pending', 'planned', None, None))
            self.event(ident, 'created', {'scope': scope})
            return self.get(ident)

    def prepare(self, task):
        with self.tx():
            t = self.get(task)
            if t['status'] != 'pending' or t['payload']['scope'] == 'discussion':
                raise Conflict('Only pending authorized execution can be dispatched')
            # Route changes must be reviewed; an old task never silently retargets.
            if t['payload']['route'] != self.config['projects'][t['payload']['project']]:
                raise Conflict('Route changed; review/create a new task')
            route = t['payload']['route']
            locked = t['payload']['resources'] + ['thread:' + route['host_id'] + ':' + route['thread_id']]
            for resource in locked:
                owner = self.db.execute('SELECT task_id FROM locks WHERE resource=?', (resource,)).fetchone()
                if owner:
                    raise Conflict('Resource busy: ' + resource + ' task=' + owner[0])
                self.db.execute('INSERT INTO locks VALUES(?,?)', (resource, task))
            attempt = uuid.uuid4().hex
            self.db.execute("UPDATE tasks SET status='dispatching',attempt=? WHERE id=?", (attempt, task))
            self.event(task, 'prepared', {'attempt': attempt})
            return self.get(task)

    def receipt(self, task, attempt, receipt):
        with self.tx():
            t = self.get(task)
            if t['attempt'] != attempt or t['status'] not in ('dispatching', 'needs_review') or not receipt.strip():
                raise Conflict('Invalid send receipt/attempt/state')
            self.db.execute("UPDATE tasks SET status='running',receipt=? WHERE id=?", (receipt, task))
            self.event(task, 'sent', {'receipt': receipt})
            return self.get(task)

    def uncertain(self, task, evidence):
        with self.tx():
            if self.get(task)['status'] not in ('dispatching', 'running'):
                raise Conflict('No active send/run to reconcile')
            self.db.execute("UPDATE tasks SET status='needs_review' WHERE id=?", (task,))
            self.event(task, 'needs_review', evidence)
            return self.get(task)

    def reconcile_unsent(self, task, evidence):
        with self.tx():
            t = self.get(task)
            if t['status'] not in ('dispatching', 'needs_review') or t['receipt']:
                raise Conflict('Not an unacknowledged attempt')
            if not evidence.strip():
                raise ValueError('Proof of not sent required; elapsed time is not proof')
            self.db.execute("UPDATE tasks SET status='pending',attempt=NULL WHERE id=?", (task,))
            self.db.execute('DELETE FROM locks WHERE task_id=?', (task,))
            self.event(task, 'confirmed_not_sent', evidence)
            return self.get(task)

    def progress(self, task, stage, evidence):
        order = ['planned', 'implemented', 'verified', 'deployed', 'live_verified']
        with self.tx():
            t = self.get(task)
            if t['status'] not in ('running', 'needs_review') or stage not in order or not evidence.strip():
                raise Conflict('Progress requires an active task and evidence')
            if order.index(stage) != order.index(t['stage']) + 1:
                raise Conflict('Stages must advance in order')
            if order.index(stage) >= 3 and t['payload']['scope'] != 'deploy':
                raise Conflict('Task does not authorize production deployment')
            self.db.execute('UPDATE tasks SET stage=? WHERE id=?', (stage, task))
            self.event(task, stage, evidence)
            return self.get(task)

    def finish(self, task, evidence):
        with self.tx():
            t = self.get(task)
            required = 'live_verified' if t['payload']['scope'] == 'deploy' else 'verified'
            if t['status'] != 'running' or t['stage'] != required or not evidence.strip():
                raise Conflict('Completion requires verification and coordinator evidence')
            self.db.execute("UPDATE tasks SET status='completed' WHERE id=?", (task,))
            self.db.execute('DELETE FROM locks WHERE task_id=?', (task,))
            self.event(task, 'completed', evidence)
            return self.get(task)

    def cancel(self, task, evidence):
        with self.tx():
            if self.get(task)['status'] in ('completed', 'cancelled') or not evidence.strip():
                raise Conflict('Cancellation requires a nonterminal task and safe-handoff evidence')
            self.db.execute("UPDATE tasks SET status='cancelled' WHERE id=?", (task,))
            self.db.execute('DELETE FROM locks WHERE task_id=?', (task,))
            self.event(task, 'cancelled_after_handoff', evidence)
            return self.get(task)

    def merge_cursors(self, polls):
        # wait_threads can return a subset. Never replace all monitored targets.
        with self.tx():
            for poll in polls:
                thread = poll['thread']
                host = thread['hostId']
                if (host, thread['id']) not in self.allowed_targets():
                    raise Conflict('Poll target is outside entry allowlist')
                if poll.get('cursor'):
                    self.db.execute('INSERT OR REPLACE INTO cursors VALUES(?,?,?)',
                                    (host, thread['id'], poll['cursor']))
            return self.visible_cursors()

    def allowed_targets(self):
        return {(self.config['projects'][p]['host_id'], self.config['projects'][p]['thread_id'])
                for p in self.policy['projects']}

    def visible_cursors(self):
        return [dict(r) for r in self.db.execute('SELECT * FROM cursors ORDER BY host,thread')
                if (r['host'], r['thread']) in self.allowed_targets()]

    def summary(self):
        tasks = []
        for row in self.db.execute('SELECT id,payload FROM tasks ORDER BY rowid'):
            if json.loads(row['payload']).get('entry') == self.entry:
                tasks.append(self.get(row['id']))
        ids = {t['id'] for t in tasks}
        return {'tasks': tasks,
                'locks': [dict(r) for r in self.db.execute('SELECT * FROM locks') if r['task_id'] in ids],
                'cursors': self.visible_cursors()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default=os.environ.get('ZEO_DISPATCH_CONFIG'))
    parser.add_argument('--entry')
    parser.add_argument('--caller-host')
    parser.add_argument('--caller-thread')
    sub = parser.add_subparsers(dest='cmd', required=True)
    sub.add_parser('check'); sub.add_parser('list')
    p = sub.add_parser('create')
    for flag in ['key', 'project', 'title', 'scope', 'authorization']:
        p.add_argument('--' + flag, required=True)
    p.add_argument('--resource', action='append', default=[])
    for cmd in ['show', 'prepare', 'sent', 'uncertain', 'reconcile-unsent', 'progress', 'finish', 'cancel']:
        p = sub.add_parser(cmd); p.add_argument('task')
        if cmd == 'sent':
            p.add_argument('--attempt', required=True); p.add_argument('--receipt', required=True)
        if cmd in ['uncertain', 'reconcile-unsent', 'progress', 'finish', 'cancel']:
            p.add_argument('--evidence', required=True)
        if cmd == 'progress':
            p.add_argument('--stage', required=True)
    p = sub.add_parser('merge-polls'); p.add_argument('file', help='JSON object with polls[]; only cursors are saved')
    args = parser.parse_args()
    if not args.config:
        parser.error('Provide --config or ZEO_DISPATCH_CONFIG; no implicit personal routing')
    if args.cmd == 'check':
        data, root, state = read_config(args.config)
        result = {'valid': True, 'root': str(root), 'state': str(state), 'projects': data['projects']}
    else:
        ledger = Ledger(args.config, args.entry, args.caller_host, args.caller_thread)
        if args.cmd == 'create': result = ledger.create(args.key, args.project, args.title, args.scope, args.authorization, args.resource)
        elif args.cmd == 'list': result = ledger.summary()
        elif args.cmd == 'show': result = ledger.get(args.task)
        elif args.cmd == 'prepare': result = ledger.prepare(args.task)
        elif args.cmd == 'sent': result = ledger.receipt(args.task, args.attempt, args.receipt)
        elif args.cmd == 'uncertain': result = ledger.uncertain(args.task, args.evidence)
        elif args.cmd == 'reconcile-unsent': result = ledger.reconcile_unsent(args.task, args.evidence)
        elif args.cmd == 'progress': result = ledger.progress(args.task, args.stage, args.evidence)
        elif args.cmd == 'finish': result = ledger.finish(args.task, args.evidence)
        elif args.cmd == 'cancel': result = ledger.cancel(args.task, args.evidence)
        elif args.cmd == 'merge-polls': result = ledger.merge_cursors(json.loads(Path(args.file).read_text())['polls'])
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, sqlite3.Error) as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
