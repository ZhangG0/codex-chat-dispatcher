# codex-chat-dispatcher

**一个聊天统筹需求，多个已有 Codex 聊天执行，任务进度有据可查。**

[安装](#安装) · [使用](#使用) · [配置](#配置) · [CLI 文档](docs/usage.md) · [MIT License](LICENSE)

面向同时维护多个项目的 Codex 用户。协调聊天负责派发和验收，项目聊天保留自己的上下文并完成实现；本地 SQLite 台账记录任务、发送尝试、共享资源占用和验收证据。

```text
你 → 协调聊天 → Codex 原生聊天工具 → 已有项目聊天
         ↓                              ↓
     Python / SQLite ← 回执、进度与验收证据
```

## 为什么使用它

- **保留项目上下文**：把任务交给已有聊天，无需为每次请求重新创建 worker。
- **减少重复派发**：按请求 key 去重，发送前记录 attempt，发送结果不明时先核实。
- **协调共享资源**：记录仓库、目标聊天、生产环境和浏览器的占用。
- **明确完成标准**：实现、验证、部署和线上验证分别登记，验收后释放占用。
- **运行状态留在本地**：Python 标准库实现，无需第三方运行依赖。

## 环境要求

- Python 3.9+，包含标准库 `sqlite3`；CLI 面向 macOS/Linux，Windows 原生环境尚未验证。
- 完整调度流程要求 Codex 宿主提供 `list_threads`、`read_thread`、`send_message_to_thread`、`wait_threads`。安装技能不会添加这些工具；缺少工具的环境只能使用台账和测试。

## 安装

### 让 Codex 帮你安装

把下面提示词复制到 Codex：

```text
请从 https://github.com/ZhangG0/codex-chat-dispatcher 安装 codex-chat-dispatcher。

先阅读 README.md 和 SKILL.md，检查当前宿主是否提供 list_threads、read_thread、
send_message_to_thread、wait_threads，并说明哪些能力可用。
将源码克隆到合适的本地目录，运行项目测试；把 SKILL.md 与 agents/openai.yaml
安装到当前宿主支持的个人技能目录，优先使用 ~/.agents/skills/codex-chat-dispatcher。
若已有安装，先备份并保留个人 policy、dependencies 等设置，避免重复安装。

本次仅安装：不要发送跨聊天消息、绑定真实聊天、修改已有路由或运行数据库。
完成后报告源码位置、技能位置、测试结果，以及工作区配置还需补充的信息。
```

### 手动安装

```sh
git clone https://github.com/ZhangG0/codex-chat-dispatcher.git
cd codex-chat-dispatcher
python3 -m unittest discover -s . -p 'test_*.py' -v

# 首次安装；目标存在时停止，避免覆盖个人设置。
skill_dir="$HOME/.agents/skills/codex-chat-dispatcher"
test ! -e "$skill_dir" && mkdir -p "$skill_dir/agents" &&
  cp SKILL.md "$skill_dir/SKILL.md" &&
  cp agents/openai.yaml "$skill_dir/agents/openai.yaml"
```

也可安装到目标仓库的 `.agents/skills/codex-chat-dispatcher/`。已有宿主若使用 `~/.codex/skills/`，可沿用该安装根；选择一个能被发现的位置，避免同名副本。安装位置参考 [Codex 官方技能说明](https://learn.chatgpt.com/docs/build-skills)。技能未出现时重新打开 Codex。

升级前备份原技能，更新正文时保留个人元数据。旧 `chat-dispatcher` 用户需同步目录名、frontmatter 和默认提示中的调用名；无需重建数据库。

## 配置

下面建立一个独立临时工作区，验证示例，不接触真实项目：

```sh
workspace_dir="$(mktemp -d /tmp/codex-chat-dispatcher.XXXXXX)"
mkdir -p "$workspace_dir/config" "$workspace_dir/demo"
cp examples/dispatcher.example.json "$workspace_dir/config/dispatcher.json"
python3 dispatch.py --config "$workspace_dir/config/dispatcher.json" check
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

在工作区 README 或 AGENTS.md 中记录源码说明、`dispatch.py` 和配置的实际路径，让技能能够发现它们。真实配置和 `.local/` 状态目录应加入该工作区的 `.gitignore`。

## 使用

在已绑定的协调聊天中输入：

```text
使用 $codex-chat-dispatcher，把 demo 项目的登录校验修复交给已有项目聊天。
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
python3 -m unittest discover -s . -p 'test_*.py' -v
```

测试使用临时目录与合成数据，覆盖去重、恢复、并发占用、入口白名单、阶段验收和游标合并，不发送真实消息。欢迎提交 Issue 或 PR；请描述复现步骤、预期行为和验证方式，分享日志前移除私人信息。

```text
SKILL.md                          调度技能
agents/openai.yaml                技能显示信息
dispatch.py                      台账 CLI
test_dispatch.py                  单元测试
examples/dispatcher.example.json  合成配置示例
docs/usage.md                     CLI 与维护说明
```

## 许可证

[MIT](LICENSE)。独立社区项目，与 OpenAI 无隶属关系。
