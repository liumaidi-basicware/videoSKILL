# 通用 Agent 启动入口

本文件是营销数字员工的宿主无关入口。Kilo、Codex、Hermes 或其他 Agent 都执行同一业务流程；宿主名称只用于诊断，不参与密钥、客户、审批、恢复或生成授权。

1. 读取 `AGENT_ENTRY_PROTOCOL.md`、`AGENTS.md` 和 `.codex/prompts/basicrouter-video.md`。后者虽保存在 Codex 命令目录，但内容是当前公共工作流；宿主适配器不得复制或改写业务规则。
2. 在包根执行 `python3 scripts/setup_env.py full-check`。缺环境时，macOS/Linux 执行 `AGENT_INLINE_BOOTSTRAP=1 bash deploy.sh`；Windows PowerShell 执行 `$env:AGENT_INLINE_BOOTSTRAP=1; .\deploy.ps1`。
3. 为本次对话取得稳定的公共会话 ID：执行 `python3 scripts/key_setup.py init --host-session-id <宿主稳定会话ID>`。宿主没有稳定 ID 时省略该参数并使用命令生成的新 ID。
4. 后续每次密钥命令都显式传 `--session-id <公共会话ID>`。保存密钥使用 `python3 scripts/key_setup.py save --stdin --session-id <ID>`，密钥只能从标准输入传入；不要依赖环境变量跨工具继承，也不要把 key 写进命令参数。
5. 确定 `CLIENT` 与稳定 `run-id`，再按 `AGENTS.md` 的阶段确认状态机推进。未知宿主安全记录为 `unknown`，核心流程必须继续可用。

若宿主支持 slash command，可把 `/basicrouter-video` 做成只读取本文件和 `AGENT_ENTRY_PROTOCOL.md` 的薄入口；若不支持，也必须在当前对话中执行同一工作流，不得要求客户先打开终端。新项目先执行：

```bash
python3 START_HERE_AGENT.py init --client <client> --run-id <run-id> \
  --out output/<client>/<run-id>/run_manifest.json
```

再按 `status` 输出推进，不得以 `--draft` 代替正式交付。
