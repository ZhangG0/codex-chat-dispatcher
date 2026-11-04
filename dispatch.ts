#!/usr/bin/env node
/** Local dispatch ledger. No network, messages, shell execution or credentials. */
import { DatabaseSync } from 'node:sqlite';
import { readFileSync, mkdirSync, chmodSync, realpathSync, existsSync, statSync } from 'node:fs';
import { resolve, dirname, relative, isAbsolute, basename } from 'node:path';
import { randomUUID } from 'node:crypto';
import { pathToFileURL } from 'node:url';
type Route = {
    path: string;
    host_id: string;
    thread_id: string;
    resources: string[];
    expected_title?: string;
};
type Policy = {
    host_id: string;
    thread_id: string;
    projects: string[];
    scopes: string[];
    resources: string[];
};
type Config = {
    version: number;
    workspace_root: string;
    state_path: string;
    resources: string[];
    projects: Record<string, Route>;
    entries: Record<string, Policy>;
};
type Payload = {
    entry: string;
    project: string;
    title: string;
    scope: string;
    authorization: string;
    route: Route;
    resources: string[];
    cwd: string;
};
type Task = {
    id: string;
    request_key: string;
    payload: Payload;
    status: string;
    stage: string;
    attempt: string | null;
    receipt: string | null;
};
type Poll = {
    thread: {
        hostId: string;
        id: string;
    };
    cursor?: string;
};
export class Conflict extends Error {
}
function fail(message: string): never { throw new Conflict(message); }
const subset = (a: string[], b: string[]) => a.every(x => b.includes(x));
const inside = (root: string, path: string) => { const r = relative(root, path); return r !== '' && r !== '..' && !r.startsWith('../') && !isAbsolute(r); };
// Resolve symlinks even when the final state file has not been created yet.
function canonical(path: string): string {
    path = resolve(path);
    if (existsSync(path))
        return realpathSync(path);
    const parent = dirname(path);
    return parent === path ? path : resolve(canonical(parent), basename(path));
}
function sorted(value: unknown): unknown {
    if (Array.isArray(value))
        return value.map(sorted);
    if (value && typeof value === 'object')
        return Object.fromEntries(Object.entries(value).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0).map(([k, v]) => [k, sorted(v)]));
    return value;
}
const equal = (a: unknown, b: unknown) => JSON.stringify(sorted(a)) === JSON.stringify(sorted(b));
// Keep Python json.dumps formatting for persisted request keys, including Unicode.
function pythonKey(entry: string, key: string): string {
    return ('[' + JSON.stringify(entry) + ', ' + JSON.stringify(key) + ']').replace(/[\u007f-\uffff]/g, c => '\\u' + c.charCodeAt(0).toString(16).padStart(4, '0'));
}
function pythonJson(value: unknown): string {
    if (Array.isArray(value))
        return '[' + value.map(pythonJson).join(', ') + ']';
    if (value && typeof value === 'object')
        return '{' + Object.entries(value).map(([k, v]) => JSON.stringify(k) + ': ' + pythonJson(v)).join(', ') + '}';
    return JSON.stringify(value);
}
export function readConfig(path: string) {
    path = canonical(path);
    const config = JSON.parse(readFileSync(path, 'utf8')) as Config;
    if (config.version !== 1)
        fail('Unsupported config version');
    const root = canonical(resolve(dirname(path), config.workspace_root));
    const state = canonical(resolve(dirname(path), config.state_path));
    if (!inside(root, state) || dirname(state) === root)
        fail('State must use a dedicated directory inside workspace');
    if (new Set(config.resources).size !== config.resources.length)
        fail('Duplicate resources');
    for (const [key, p] of Object.entries(config.projects)) {
        const folder = canonical(resolve(root, p.path));
        if (!inside(root, folder) || !existsSync(folder) || !statSync(folder).isDirectory())
            fail('Invalid project path: ' + key);
        if (!p.thread_id || !p.host_id)
            fail('Missing thread/host: ' + key);
        if (!subset(p.resources, config.resources))
            fail('Unknown project resource: ' + key);
    }
    if (!config.entries || !Object.keys(config.entries).length)
        fail('Explicit entry policy required');
    for (const p of Object.values(config.entries)) {
        if (!p.thread_id || !p.host_id)
            fail('Entry identity required');
        if (!subset(p.projects, Object.keys(config.projects)))
            fail('Unknown allowed project');
        if (!subset(p.scopes, ['discussion', 'local', 'deploy']))
            fail('Unknown allowed scope');
        if (!subset(p.resources, config.resources))
            fail('Unknown allowed resource');
    }
    return { config, root, state };
}
export class Ledger {
    config: Config;
    root: string;
    entry: string;
    policy: Policy;
    db: DatabaseSync;
    constructor(path: string, entry: string, host: string, thread: string) {
        const c = readConfig(path);
        this.config = c.config;
        this.root = c.root;
        this.entry = entry;
        const policy = c.config.entries[entry];
        if (!policy || policy.host_id !== host || policy.thread_id !== thread)
            fail('Entry/caller identity mismatch; no default entry');
        this.policy = policy;
        mkdirSync(dirname(c.state), { recursive: true, mode: 0o700 });
        chmodSync(dirname(c.state), 0o700);
        const mask = process.umask(0o077);
        try {
            this.db = new DatabaseSync(c.state);
            this.db.exec(`PRAGMA busy_timeout=10000; PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL, stage TEXT NOT NULL, attempt TEXT, receipt TEXT);
        CREATE TABLE IF NOT EXISTS locks(resource TEXT PRIMARY KEY, task_id TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY, task_id TEXT, kind TEXT NOT NULL, detail TEXT NOT NULL, recorded REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS cursors(host TEXT, thread TEXT, cursor TEXT, PRIMARY KEY(host,thread));`);
        }
        finally {
            process.umask(mask);
        }
        chmodSync(c.state, 0o600);
    }
    close() { this.db.close(); }
    tx<T>(fn: () => T): T { this.db.exec('BEGIN IMMEDIATE'); try {
        const result = fn();
        this.db.exec('COMMIT');
        return result;
    }
    catch (e) {
        this.db.exec('ROLLBACK');
        throw e;
    } }
    event(task: string, kind: string, detail: unknown) { this.db.prepare('INSERT INTO events(task_id,kind,detail,recorded) VALUES(?,?,?,?)').run(task, kind, JSON.stringify(detail), Date.now() / 1000); }
    allowed(project: string, scope: string, resources: string[]) {
        if (!this.policy.projects.includes(project) || !this.policy.scopes.includes(scope))
            fail('Entry is not allowed to use this project/scope');
        if (!subset(resources, this.policy.resources))
            fail('Entry is not allowed to claim these resources');
    }
    get(id: string): Task {
        const row = this.db.prepare('SELECT * FROM tasks WHERE id=?').get(id);
        if (!row)
            fail('Unknown task');
        const t = { ...row, payload: JSON.parse(row.payload as string) } as Task;
        if (t.payload.entry !== this.entry)
            fail('Task belongs to another entry or legacy unscoped task');
        this.allowed(t.payload.project, t.payload.scope, t.payload.resources);
        return t;
    }
    create(key: string, project: string, title: string, scope: string, authorization: string, extra: string[] = []) {
        if (!key.trim() || !title.trim())
            fail('Request key and title must not be empty');
        if (!['discussion', 'local', 'deploy'].includes(scope) || !authorization.trim())
            fail('Scope and human authorization evidence required');
        const route = this.config.projects[project];
        if (!route)
            fail('Unknown project: ' + project);
        const resources = [...new Set([...route.resources, ...extra, ...(scope === 'deploy' ? ['server:production'] : [])])].sort();
        if (!subset(resources, this.config.resources))
            fail('Unknown resource');
        this.allowed(project, scope, resources);
        const payload: Payload = { entry: this.entry, project, title, scope, authorization, route, resources, cwd: canonical(resolve(this.root, route.path)) };
        const requestKey = pythonKey(this.entry, key);
        return this.tx(() => {
            const old = this.db.prepare('SELECT id,payload FROM tasks WHERE request_key=?').get(requestKey);
            if (old) {
                if (!equal(JSON.parse(old.payload as string), payload))
                    fail('Request key already belongs to different task data');
                return this.get(old.id as string);
            }
            const id = randomUUID().replaceAll('-', '').slice(0, 16);
            this.db.prepare('INSERT INTO tasks VALUES(?,?,?,?,?,?,?)').run(id, requestKey, pythonJson(sorted(payload)), 'pending', 'planned', null, null);
            this.event(id, 'created', { scope });
            return this.get(id);
        });
    }
    prepare(id: string) {
        return this.tx(() => {
            const t = this.get(id);
            if (t.status !== 'pending' || t.payload.scope === 'discussion')
                fail('Only pending authorized execution can be dispatched');
            if (!equal(t.payload.route, this.config.projects[t.payload.project]))
                fail('Route changed; review/create a new task');
            const r = t.payload.route;
            for (const resource of [...t.payload.resources, `thread:${r.host_id}:${r.thread_id}`]) {
                const owner = this.db.prepare('SELECT task_id FROM locks WHERE resource=?').get(resource);
                if (owner)
                    fail(`Resource busy: ${resource} task=${owner.task_id}`);
                this.db.prepare('INSERT INTO locks VALUES(?,?)').run(resource, id);
            }
            const attempt = randomUUID().replaceAll('-', '');
            this.db.prepare("UPDATE tasks SET status='dispatching',attempt=? WHERE id=?").run(attempt, id);
            this.event(id, 'prepared', { attempt });
            return this.get(id);
        });
    }
    receipt(id: string, attempt: string, receipt: string) {
        return this.tx(() => {
            const t = this.get(id);
            if (t.attempt !== attempt || !['dispatching', 'needs_review'].includes(t.status) || !receipt.trim())
                fail('Invalid send receipt/attempt/state');
            this.db.prepare("UPDATE tasks SET status='running',receipt=? WHERE id=?").run(receipt, id);
            this.event(id, 'sent', { receipt });
            return this.get(id);
        });
    }
    uncertain(id: string, evidence: string) {
        return this.tx(() => {
            if (!['dispatching', 'running'].includes(this.get(id).status))
                fail('No active send/run to reconcile');
            this.db.prepare("UPDATE tasks SET status='needs_review' WHERE id=?").run(id);
            this.event(id, 'needs_review', evidence);
            return this.get(id);
        });
    }
    reconcileUnsent(id: string, evidence: string) {
        return this.tx(() => {
            const t = this.get(id);
            if (!['dispatching', 'needs_review'].includes(t.status) || t.receipt)
                fail('Not an unacknowledged attempt');
            if (!evidence.trim())
                fail('Proof of not sent required; elapsed time is not proof');
            this.db.prepare("UPDATE tasks SET status='pending',attempt=NULL WHERE id=?").run(id);
            this.db.prepare('DELETE FROM locks WHERE task_id=?').run(id);
            this.event(id, 'confirmed_not_sent', evidence);
            return this.get(id);
        });
    }
    progress(id: string, stage: string, evidence: string) {
        return this.tx(() => {
            const order = ['planned', 'implemented', 'verified', 'deployed', 'live_verified'], t = this.get(id);
            if (!['running', 'needs_review'].includes(t.status) || !order.includes(stage) || !evidence.trim())
                fail('Progress requires an active task and evidence');
            if (order.indexOf(stage) !== order.indexOf(t.stage) + 1)
                fail('Stages must advance in order');
            if (order.indexOf(stage) >= 3 && t.payload.scope !== 'deploy')
                fail('Task does not authorize production deployment');
            this.db.prepare('UPDATE tasks SET stage=? WHERE id=?').run(stage, id);
            this.event(id, stage, evidence);
            return this.get(id);
        });
    }
    finish(id: string, evidence: string) {
        return this.tx(() => {
            const t = this.get(id), required = t.payload.scope === 'deploy' ? 'live_verified' : 'verified';
            if (t.status !== 'running' || t.stage !== required || !evidence.trim())
                fail('Completion requires verification and coordinator evidence');
            this.db.prepare("UPDATE tasks SET status='completed' WHERE id=?").run(id);
            this.db.prepare('DELETE FROM locks WHERE task_id=?').run(id);
            this.event(id, 'completed', evidence);
            return this.get(id);
        });
    }
    cancel(id: string, evidence: string) {
        return this.tx(() => {
            if (['completed', 'cancelled'].includes(this.get(id).status) || !evidence.trim())
                fail('Cancellation requires a nonterminal task and safe-handoff evidence');
            this.db.prepare("UPDATE tasks SET status='cancelled' WHERE id=?").run(id);
            this.db.prepare('DELETE FROM locks WHERE task_id=?').run(id);
            this.event(id, 'cancelled_after_handoff', evidence);
            return this.get(id);
        });
    }
    allowedTargets() { return this.policy.projects.map(p => this.config.projects[p]).map(r => JSON.stringify([r.host_id, r.thread_id])); }
    visibleCursors() { return this.db.prepare('SELECT * FROM cursors ORDER BY host,thread').all().filter(r => this.allowedTargets().includes(JSON.stringify([r.host, r.thread]))); }
    mergeCursors(polls: Poll[]) {
        return this.tx(() => {
            for (const p of polls) {
                if (!this.allowedTargets().includes(JSON.stringify([p.thread.hostId, p.thread.id])))
                    fail('Poll target is outside entry allowlist');
                if (p.cursor)
                    this.db.prepare('INSERT OR REPLACE INTO cursors VALUES(?,?,?)').run(p.thread.hostId, p.thread.id, p.cursor);
            }
            return this.visibleCursors();
        });
    }
    summary() {
        const tasks = this.db.prepare('SELECT id,payload FROM tasks ORDER BY rowid').all().filter(r => JSON.parse(r.payload as string).entry === this.entry).map(r => this.get(r.id as string));
        const ids = new Set(tasks.map(t => t.id));
        return { tasks, locks: this.db.prepare('SELECT * FROM locks').all().filter(r => ids.has(r.task_id as string)), cursors: this.visibleCursors() };
    }
}
export function main(args = process.argv.slice(2)) {
    const commands: Record<string, string[]> = { check: [], list: [], create: ['key', 'project', 'title', 'scope', 'authorization', 'resource'], show: [], prepare: [], sent: ['attempt', 'receipt'], uncertain: ['evidence'], 'reconcile-unsent': ['evidence'], progress: ['stage', 'evidence'], finish: ['evidence'], cancel: ['evidence'], 'merge-polls': [] };
    const globals = ['config', 'entry', 'caller-host', 'caller-thread'];
    if (args.includes('--help') || args.includes('-h')) {
        console.log('Usage: node dispatch.ts --config FILE --entry ENTRY --caller-host HOST --caller-thread THREAD COMMAND [TASK|FILE] [OPTIONS]\nCommands: ' + Object.keys(commands).join(', ') + '\ncreate: --key --project --title --scope --authorization [--resource NAME ...]\nsent: --attempt --receipt; progress: --stage --evidence; uncertain/reconcile-unsent/finish/cancel: --evidence\nmerge-polls FILE: JSON object with polls[]; only cursors are saved');
        return;
    }
    const options: Record<string, string> = {}, resources: string[] = [], positional: string[] = [];
    let command = '';
    for (let i = 0; i < args.length; i++) {
        const arg = args[i];
        if (arg.startsWith('--')) {
            const split = arg.indexOf('='), name = arg.slice(2, split < 0 ? undefined : split);
            if (!globals.includes(name) && (!command || !commands[command].includes(name)))
                fail('Unknown option: --' + name);
            const value = split < 0 ? args[++i] : arg.slice(split + 1);
            if (value === undefined || value.startsWith('--'))
                fail('Missing value: --' + name);
            if (name === 'resource')
                resources.push(value);
            else
                options[name] = value;
        }
        else if (!command) {
            if (!(arg in commands))
                fail('Unknown command: ' + arg);
            command = arg;
        }
        else
            positional.push(arg);
    }
    if (!command)
        fail('Command required');
    const config = options.config ?? process.env.ZEO_DISPATCH_CONFIG;
    if (!config)
        fail('Provide --config or ZEO_DISPATCH_CONFIG; no implicit personal routing');
    const expectsTask = !['check', 'list', 'create'].includes(command);
    if (positional.length !== (expectsTask ? 1 : 0))
        fail('Unexpected or missing positional argument');
    for (const name of commands[command].filter(x => x !== 'resource'))
        if (options[name] === undefined)
            fail('Missing required option: --' + name);
    let result: unknown;
    if (command === 'check') {
        const c = readConfig(config);
        result = { valid: true, root: c.root, state: c.state, projects: c.config.projects };
    }
    else {
        const l = new Ledger(config, options.entry, options['caller-host'], options['caller-thread']), id = positional[0];
        try {
            switch (command) {
                case 'create':
                    result = l.create(options.key, options.project, options.title, options.scope, options.authorization, resources);
                    break;
                case 'list':
                    result = l.summary();
                    break;
                case 'show':
                    result = l.get(id);
                    break;
                case 'prepare':
                    result = l.prepare(id);
                    break;
                case 'sent':
                    result = l.receipt(id, options.attempt, options.receipt);
                    break;
                case 'uncertain':
                    result = l.uncertain(id, options.evidence);
                    break;
                case 'reconcile-unsent':
                    result = l.reconcileUnsent(id, options.evidence);
                    break;
                case 'progress':
                    result = l.progress(id, options.stage, options.evidence);
                    break;
                case 'finish':
                    result = l.finish(id, options.evidence);
                    break;
                case 'cancel':
                    result = l.cancel(id, options.evidence);
                    break;
                case 'merge-polls':
                    result = l.mergeCursors(JSON.parse(readFileSync(id, 'utf8')).polls);
                    break;
            }
        }
        finally {
            l.close();
        }
    }
    console.log(JSON.stringify(result, null, 2));
}
if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
    try {
        main();
    }
    catch (error) {
        console.error(JSON.stringify({ error: error instanceof Error ? error.message : String(error) }));
        process.exitCode = 1;
    }
}
