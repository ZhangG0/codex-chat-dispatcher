# codex-chat-dispatcher

**Coordinate existing Codex chats from one conversation, with a local record of dispatches, progress, and acceptance.**

**English** | [简体中文](README.zh-CN.md)

[Who is it for?](#who-is-it-for) · [Installation](#installation) · [Configuration](#configuration) · [Usage](#usage) · [CLI guide (中文)](docs/usage.md) · [MIT License](LICENSE)

For people maintaining several projects in Codex: one coordinator chat assigns work and verifies results, while existing project chats keep their context and handle implementation. A local SQLite ledger records tasks, dispatch attempts, shared resource claims, and verification evidence.

```text
You → Coordinator chat → Native Codex chat tools → Existing project chats
              ↓                                          ↓
       Python / SQLite ← Receipts, progress, and verification evidence
```

## Why use it?

Managing several project chats creates coordination work: remembering where a task went, deciding whether a timed-out send should be retried, checking who still owns a shared resource, and collecting evidence before declaring work done. This project gives that work a repeatable process and a persistent record.

Its value is continuity and traceability across existing chats. It helps the coordinator answer: **Who owns this task? Was it sent? What was verified? Is it safe to release the resource?** Results still depend on the coordinator checking real evidence and all participating chats following the workflow.

- **Keep project context.** Delegate to existing chats instead of creating a new worker for each request.
- **Reduce duplicate dispatches.** Deduplicate by request key, record attempts before sending, and investigate uncertain delivery before retrying.
- **Coordinate shared resources.** Track claims on repositories, target chats, production environments, and browsers.
- **Make completion explicit.** Record implementation, verification, deployment, and live verification separately; release claims after acceptance.
- **Keep state local.** The ledger uses the Python standard library, with no third-party runtime dependencies.

## Who is it for?

- **Developers maintaining multiple projects in Codex**, with a long-lived chat for each project and one place to coordinate requests.
- **Solo builders managing several workstreams**, who want to delegate local changes and collect results without repeatedly explaining each project's context.
- **Users coordinating shared repositories or environments**, who need explicit resource claims and a record of safe handoffs.
- **Users resuming interrupted work**, who need to distinguish an unsent task, uncertain delivery, ongoing implementation, and verified completion.

### Example scenario

You maintain an application and its documentation in separate project chats. From a coordinator chat, you authorize a local application fix and a documentation update. Each task is routed to its existing chat with its scope and acceptance criteria recorded. If both use the same repository, they claim the same resource so participating tasks can be serialized. The coordinator collects the reports and verifies each result before closing the tasks. If sending is interrupted, the recorded attempt supports investigation before another send.

### When to skip it

A single chat handling occasional tasks may not justify the configuration and ledger. If you need an unattended worker fleet, a visual session manager, or orchestration across different agent products, those capabilities are outside the current scope. Check the host tool requirements below before installing: the full workflow is unavailable without native cross-chat tools.

## Requirements

- Python 3.9+ with the standard-library `sqlite3` module. The CLI targets macOS/Linux; native Windows support has not been verified.
- The full workflow requires a Codex host that exposes `list_threads`, `read_thread`, `send_message_to_thread`, and `wait_threads`. Installing the skill does **not** add these tools. Environments without them can still use the ledger and run tests.

## Installation

### Ask Codex to install it

Copy this prompt into Codex:

```text
Install codex-chat-dispatcher from https://github.com/ZhangG0/codex-chat-dispatcher.

Read README.md and SKILL.md first. Check whether this host exposes list_threads,
read_thread, send_message_to_thread, and wait_threads. Report which capabilities
are available.

Clone the source into a suitable local directory and run the project tests.
Install SKILL.md and agents/openai.yaml into a personal skill directory supported
by this host, preferring ~/.agents/skills/codex-chat-dispatcher.
If an installation already exists, back it up and preserve personal policy,
dependencies, and other settings. Avoid duplicate installations.

This request authorizes installation only. Do not send cross-chat messages,
bind real chats, or modify existing routing configurations or runtime databases.
Report the source location, skill location, test results, and information still
needed to configure a workspace.
```

### Install manually

```sh
git clone https://github.com/ZhangG0/codex-chat-dispatcher.git
cd codex-chat-dispatcher
python3 -m unittest discover -s . -p 'test_*.py' -v

# First installation only; stop if the destination already exists.
skill_dir="$HOME/.agents/skills/codex-chat-dispatcher"
test ! -e "$skill_dir" && mkdir -p "$skill_dir/agents" &&
  cp SKILL.md "$skill_dir/SKILL.md" &&
  cp agents/openai.yaml "$skill_dir/agents/openai.yaml"
```

Alternatively, install the same files under your repository's `.agents/skills/codex-chat-dispatcher/`. Existing hosts using `~/.codex/skills/` can retain that location. Choose one location your host discovers and avoid duplicate copies. See the [official Codex skills guide](https://learn.chatgpt.com/docs/build-skills) for discovery locations. Reopen Codex if the skill does not appear.

Back up an existing skill before upgrading and preserve personal metadata. Users migrating from `chat-dispatcher` should update the directory name, frontmatter name, and invocation in the default prompt. The database does not need to be recreated.

> The skill instructions and detailed CLI guide are currently written in Chinese. The English installation prompt and usage example can be used with Codex; translating the README does not change the CLI interface.

## Configuration

Create an isolated temporary workspace to validate the example without accessing real projects:

```sh
workspace_dir="$(mktemp -d /tmp/codex-chat-dispatcher.XXXXXX)"
mkdir -p "$workspace_dir/config" "$workspace_dir/demo"
cp examples/dispatcher.example.json "$workspace_dir/config/dispatcher.json"
python3 dispatch.py --config "$workspace_dir/config/dispatcher.json" check
```

`check` validates static configuration and directories. It does not verify chat identities or create a database.

For actual use, copy the example to your workspace's `config/dispatcher.json` and replace the project paths and chat IDs:

| Field | Description |
| --- | --- |
| `workspace_root` | Workspace path relative to the configuration directory |
| `state_path` | Database path relative to the configuration directory; must be in a dedicated subdirectory inside the workspace |
| `resources` | Resource names; projects sharing a repository should use the same resource name |
| `projects` | Existing project subdirectories, target host/thread identities, and resources |
| `entries` | Coordinator host/thread identities and allowed projects, scopes, and resources |
| `expected_title` | Optional identity-check hint; never a replacement for checking the actual chat |

Example IDs are placeholders. Verify the coordinator and target identities through native tools and confirm the bindings before use. Caller identity must come from the current host context, rather than being copied from the configuration to impersonate an entry. The example permits discussion and local tasks only.

Record the actual paths to this project's documentation, `dispatch.py`, and your configuration in the workspace README or AGENTS.md so the skill can discover them. Ignore real configuration and the `.local/` state directory in that workspace's `.gitignore`.

## Usage

In a configured coordinator chat, enter:

```text
Use $codex-chat-dispatcher to delegate the demo project's login validation fix
to its existing project chat.
Local code changes and tests are authorized; deployment is not authorized.
Acceptance criteria: empty passwords cannot be submitted, and existing tests pass.
Summarize the changes, verification results, and remaining work in this chat.
```

Workflow: verify authorization and target → create a task → claim resources → send through native tools → record the receipt → wait for a report → verify evidence → accept completion.

The project chat provides its normal final response. The coordinator reads it; workers do not need to send a message back. See the [CLI and operations guide (中文)](docs/usage.md) for commands, interrupted-send recovery, cancellation, and cursor merging.

## Limitations

The CLI is a local ledger. It does not call models, send messages, or deploy code. There is currently no persistent worker pool, visual dashboard, automatic retry system, or adapter for other agents. The full workflow depends on the Codex host.

Entry checks and resource claims help prevent mistakes within this workflow. They do not isolate malicious users or lock processes outside it. A timeout is not evidence that it is safe to resend or release a claim. A successful tool call or completed chat turn does not establish task completion. Configuration cannot grant human authorization, and an unbound entry must not bypass the ledger by sending directly.

## Development and contributing

```sh
python3 -m unittest discover -s . -p 'test_*.py' -v
```

Tests use temporary directories and synthetic data. They cover deduplication, recovery, concurrent claims, entry allowlists, verification stages, and cursor merging without sending real messages.

Issues and pull requests are welcome. Include reproduction steps, expected behavior, and validation results. Remove private information before sharing logs.

```text
SKILL.md                          Dispatch skill
agents/openai.yaml                Skill display metadata
dispatch.py                       Ledger CLI
test_dispatch.py                  Unit tests
examples/dispatcher.example.json  Synthetic configuration example
docs/usage.md                     CLI and operations guide (Chinese)
README.zh-CN.md                   Chinese README
```

## License

[MIT](LICENSE). An independent community project, not affiliated with OpenAI.
