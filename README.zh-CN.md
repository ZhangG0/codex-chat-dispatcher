# codex-superagent-sample

[English](README.md) | **简体中文**

**一个入口，协调多个已有 Codex 聊天，跟踪任务进度，并依据证据验收结果。一个轻量的 SuperAgent 实践示例。**

[适用人群](#适用人群) · [安装](#安装) · [使用](#使用) · [配置](#配置) · [CLI 文档](docs/usage.md) · [MIT License](LICENSE)

本示例探索如何让协调 agent 将需求交给已有 Codex 项目聊天执行，并依据证据验收结果。项目由 Skill、TypeScript CLI 与本地 SQLite 台账组成。

面向同时维护多个项目的 Codex 用户。协调聊天负责派发和验收，项目聊天保留自己的上下文并完成实现；本地 SQLite 台账记录任务、发送尝试、共享资源占用和验收证据。

```text
你 → 协调聊天 → Codex 原生聊天工具 → 已有项目聊天
         ↓                              ↓
     TypeScript / SQLite ← 回执、进度与验收证据
```

## 为什么使用它

同时维护多个项目聊天时，协调本身也会成为工作：记住任务交给了谁、判断超时后是否该重发、确认共享资源由谁占用，以及收集证据后再宣布完成。本项目为这些工作提供可重复的流程和持久记录。

它的价值在于保持已有聊天之间的工作连续性，让协调方能够回答：**任务归谁？是否已发送？验证了什么？现在能否释放资源？** 实际效果仍取决于协调方核实真实证据，以及参与聊天遵守流程。

- **保留项目上下文**：把任务交给已有聊天，无需为每次请求重新创建 worker。
- **减少重复派发**：按请求 key 去重，发送前记录 attempt，发送结果不明时先核实。
- **协调共享资源**：记录仓库、目标聊天、生产环境和浏览器的占用。
- **明确完成标准**：实现、验证、部署和线上验证分别登记，验收后释放占用。
- **运行状态留在本地**：TypeScript 实现，使用 Node 内置 SQLite，无第三方运行依赖。

## 适用人群

- **同时维护多个 Codex 项目的开发者**：各项目已有长期聊天，希望在一个入口统筹需求。
- **同时推进多条工作线的独立开发者**：需要分派本地修改并收集结果，减少重复交代项目背景。
- **需要协调共享仓库或环境的用户**：希望明确资源占用和安全交接，并留下记录。
- **经常续接中断任务的用户**：需要区分尚未发送、发送不明、正在实现和已经验证等状态。

### 一个使用场景

你用两个项目聊天分别维护应用和文档，在协调聊天中授权一次本地功能修复和一次文档更新。任务分别交给已有聊天，记录范围和验收标准。如果它们使用同一仓库，就配置相同资源名，使接入流程的任务串行执行。协调方收集报告、核实结果后结束任务；如果发送中断，则根据已保存的 attempt 核实情况，再决定是否重发。

### 什么情况下不需要它

如果只有一个聊天、偶尔处理任务，配置和台账可能增加额外负担。如果需要无人值守的 worker 集群、可视化会话管理或跨 agent 产品编排，当前项目尚不提供这些能力。安装前先确认下方宿主工具要求；缺少原生跨聊天工具时，完整流程无法使用。

## 环境要求

- Node.js 24.12+，使用内置 TypeScript 与 `node:sqlite`，运行无需构建或安装第三方包；CLI 面向 macOS/Linux，Windows 原生环境尚未验证。
- 开发检查先执行 `npm ci`；兼容性测试另需 Python 3.9+，用于对照原始实现。Node 24 可能在 stderr 输出 SQLite 实验性警告，JSON 结果输出到 stdout。
- 完整调度流程要求 Codex 宿主提供 `list_threads`、`read_thread`、`send_message_to_thread`、`wait_threads`。安装技能不会添加这些工具；缺少工具的环境只能使用台账和测试。

## 安装

### 让 Codex 帮你安装

把下面提示词复制到 Codex：

```text
请从 https://github.com/ZhangG0/codex-superagent-sample 安装 codex-superagent-sample。

先阅读 README.md 和 SKILL.md，检查当前宿主是否提供 list_threads、read_thread、
send_message_to_thread、wait_threads，并说明哪些能力可用。
将源码克隆到合适的本地目录，运行项目测试；把 SKILL.md 与 agents/openai.yaml
安装到当前宿主支持的个人技能目录，优先使用 ~/.agents/skills/codex-superagent-sample。
若已有安装，先备份并保留个人 policy、dependencies 等设置，避免重复安装。

本次仅安装：不要发送跨聊天消息、绑定真实聊天、修改已有路由或运行数据库。
完成后报告源码位置、技能位置、测试结果，以及工作区配置还需补充的信息。
```

### 手动安装

```sh
git clone https://github.com/ZhangG0/codex-superagent-sample.git
cd codex-superagent-sample
npm ci
npm test

# 首次安装；目标存在时停止，避免覆盖个人设置。
skill_dir="$HOME/.agents/skills/codex-superagent-sample"
test ! -e "$skill_dir" && mkdir -p "$skill_dir/agents" &&
  cp SKILL.md "$skill_dir/SKILL.md" &&
  cp agents/openai.yaml "$skill_dir/agents/openai.yaml"
```

也可安装到目标仓库的 `.agents/skills/codex-superagent-sample/`。已有宿主若使用 `~/.codex/skills/`，可沿用该安装根；选择一个能被发现的位置，避免同名副本。安装位置参考 [Codex 官方技能说明](https://learn.chatgpt.com/docs/build-skills)。技能未出现时重新打开 Codex。

升级前备份原技能，更新正文时保留个人元数据。旧 `chat-dispatcher` 或 `codex-chat-dispatcher` 用户需同步目录名、frontmatter 和默认提示中的调用名；无需重建数据库。

## 配置

下面建立一个独立临时工作区，验证示例，不接触真实项目：

```sh
workspace_dir="$(mktemp -d /tmp/codex-superagent-sample.XXXXXX)"
mkdir -p "$workspace_dir/config" "$workspace_dir/demo"
cp examples/dispatcher.example.json "$workspace_dir/config/dispatcher.json"
node dispatch.ts --config "$workspace_dir/config/dispatcher.json" check
```

`check` 只检查静态配置与目录，不核实聊天身份，也不创建数据库。

正式使用时，把示例复制到工作区 `config/dispatcher.json`，替换项目路径和聊天 ID：

| 字段 | 说明 |
| --- | --- |
| `workspace_root` | 相对配置文件目录的工作区路径 |
| `state_path` | 相对配置文件目录的数据库路径，须位于工作区内专用子目录 |
| `resources` | 所有资源名；共享仓库使用相同资源名 |
| `projects` | 工作区内已存在的项目子目录、目标 host/thread 和资源 |
| `entries` | 协调入口真实 host/thread 与允许的项目、scope、资源 |
| `expected_title` | 可选核对线索，不能替代真实身份检查 |

示例 ID 是占位值。通过原生工具核实当前协调聊天与目标聊天身份，确认绑定后再使用；caller 必须来自当前宿主上下文，不能从配置抄取身份冒充入口。示例默认仅允许讨论和本地任务。

在工作区 README 或 AGENTS.md 中记录源码说明、`dispatch.ts` 和配置的实际路径，让技能能够发现它们。真实配置和 `.local/` 状态目录应加入该工作区的 `.gitignore`。

## 使用

在已绑定的协调聊天中输入：

```text
使用 $codex-superagent-sample，把 demo 项目的登录校验修复交给已有项目聊天。
允许修改本地代码并运行测试，不部署。
验收标准：空密码不能提交，现有测试通过。
在当前聊天汇总改动、验证结果和未完成项。
```

流程：核实授权和目标 → 登记任务 → 领取资源 → 原生工具发送 → 记录回执 → 等待报告 → 核实证据 → 完成验收。

项目聊天正常最终回复即可。协调方读取报告，不要求项目聊天反向发送消息。完整命令、发送中断恢复、取消与游标合并见 [CLI 与运行维护](docs/usage.md)。

## 能力边界

CLI 是本地台账，不调用模型、不发送消息、不执行部署。当前没有常驻任务池、可视化控制台、自动重试或其他 agent 适配器；完整流程依赖 Codex 宿主。

入口检查和资源占用属于应用内防误操作，无法提供恶意用户隔离或锁住未接入的进程。发送超时不能作为重发或释放占用的依据；工具成功、聊天回合结束也不代表业务完成。配置不能代替用户授权。未绑定入口不得绕过台账直接发送。

## 开发与贡献

```sh
npm ci
npm run typecheck
npm test
```

测试使用临时目录与合成数据，覆盖去重、恢复、并发占用、入口白名单、阶段验收和游标合并，不发送真实消息。欢迎提交 Issue 或 PR；请描述复现步骤、预期行为和验证方式，分享日志前移除私人信息。

```text
SKILL.md                          调度技能
agents/openai.yaml                技能显示信息
dispatch.ts                      TypeScript 台账 CLI
tests/test_dispatch.py            行为与兼容性测试
examples/dispatcher.example.json  合成配置示例
docs/usage.md                     CLI 与维护说明
```

台账统一通过 `node dispatch.ts ...` 运行，原 Python 入口已移除。现有 SQLite 文件与配置格式兼容，无需迁移数据库。`tests/reference_dispatch.py` 是仅供兼容性验证使用的原始 Python 实现。

## 许可证

[MIT](LICENSE)。独立社区项目，与 OpenAI 无隶属关系。
