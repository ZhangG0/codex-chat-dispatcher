import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, existsSync, rmSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { readConfig, Ledger } from '../dispatch.ts';
test('native TS runtime: configuration is read-only; ledger protects state and releases claims', () => {
    const root = mkdtempSync(join(tmpdir(), 'dispatch-runtime-'));
    try {
        mkdirSync(join(root, 'demo'));
        mkdirSync(join(root, 'config'));
        const config = join(root, 'config/routing.json');
        writeFileSync(config, JSON.stringify({ version: 1, workspace_root: '..', state_path: '../state/ledger.sqlite3', resources: ['repo:demo'], projects: { demo: { path: 'demo', host_id: 'local', thread_id: 'target', resources: ['repo:demo'] } }, entries: { entry: { host_id: 'local', thread_id: 'caller', projects: ['demo'], scopes: ['local'], resources: ['repo:demo'] } } }));
        const parsed = readConfig(config);
        assert.equal(existsSync(parsed.state), false);
        const l = new Ledger(config, 'entry', 'local', 'caller');
        try {
            assert.equal(statSync(parsed.state).mode & 0o777, 0o600);
            assert.equal(statSync(join(root, 'state')).mode & 0o777, 0o700);
            const t = l.create('request', 'demo', 'synthetic test', 'local', 'synthetic authorization');
            const p = l.prepare(t.id);
            l.receipt(t.id, p.attempt!, 'synthetic receipt');
            l.progress(t.id, 'implemented', 'synthetic diff');
            l.progress(t.id, 'verified', 'synthetic checks');
            assert.equal(l.finish(t.id, 'synthetic acceptance').status, 'completed');
            assert.deepEqual(l.summary().locks, []);
        }
        finally {
            l.close();
        }
        const direct = spawnSync(process.execPath, ['dispatch.ts', '--config', config, 'check'], { encoding: 'utf8' });
        assert.equal(direct.status, 0);
        assert.equal(JSON.parse(direct.stdout).valid, true);
    }
    finally {
        rmSync(root, { recursive: true, force: true });
    }
});
