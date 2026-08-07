# Harness 工程思路审视报告 — AI 营销视频 Agent

> 分析日期：2026-08-04
> 方法论：借鉴 Harness 软件交付平台的工程哲学（Pipeline as Code、Policy-as-Code 治理、Continuous Verification、可观测性、成本治理、可靠性工程、Delegate 架构），逐条映射本项目现状并给出优化点。
> 与既有文档的关系：《项目深度分析报告.md》覆盖架构与正确性 Bug，《代码级修复与业务优化建议.md》覆盖 P0/P1/P2 修复与上帝文件拆分。本报告只做增量：**工程体系层**的审视。

---

## 一、结论先行

用 Harness 的成熟度坐标看，本项目呈现出罕见的"两极分化"：

| Harness 能力域 | 本项目成熟度 | 一句话评价 |
|---|---|---|
| Pipeline 编排（十二级流水线） | ★★★★☆ | `pipeline.py` STAGES + fail-closed 已是教科书级，但缺"声明式管线定义" |
| Gate 治理（确认闸门/审批失效） | ★★★★★ | 指纹绑定 + 审批失效 + waiver 登记，已超过多数商业系统 |
| Continuous Verification（持续验证） | ★★☆☆☆ | OCR/QC 只存在于生成后，无回归基准、无金丝雀、无全链路回放 |
| 可观测性（日志/指标/Tracing） | ★☆☆☆☆ | 零结构化日志、零指标、零 tracing，仅 print + cost_ledger |
| CI/CD（测试门禁/发布管线） | ★☆☆☆☆ | 无 CI 配置；`run_tests.sh all` 分支静默跑 0 个测试 |
| 成本治理（FinOps） | ★★★☆☆ | cost_ledger 记账完备，但无预算闸、无成本预估、无异常告警 |
| 可靠性工程（SLO/降级/恢复） | ★★★★☆ | 退避/锁/原子写/断点续跑成熟；缺 SLO 定义与错误预算 |
| Delegate/Agent 架构（多宿主） | ★★★★☆ | 薄适配层设计正确；缺宿主能力协商与健康检查协议 |
| Policy as Code（契约校验） | ★★☆☆☆ | 7 个 JSON Schema 全部"文档性"，运行时零校验 |
| 环境/制品治理（部署与依赖） | ★★★☆☆ | inline 自举 + SHA-256 树校验不错；在线路径裸 pip install 无校验 |

**核心判断：这个项目的"运行态治理"（gate/指纹/账本）已经达到 Harness 级别的严苛，但"工程态基础设施"（CI、可观测性、持续验证、策略即代码）几乎空白。** 前者保证单次交付正确，后者决定系统能否长期演进而不腐化。当前最危险的三个缺口：

1. **`run_tests.sh all` 静默失效** —— 全量测试实际跑 0 个用例，"700+ 测试"的承诺没有任何机制保证它真的在执行；
2. **零可观测性** —— 一旦客户现场出问题，没有日志级别、没有结构化事件、没有 trace，只能靠事后翻 manifest 文件考古；
3. **Schema 不校验** —— 7 份契约文档形同虚设，INC-005（旧计划复用）、INC-011（参考图静默丢失）本质上都是"契约未强制执行"导致的事故，而修复是点对点的，系统性防线没有建立。

---

## 二、逐域深度审视与优化点

### 1. Pipeline as Code —— 从"硬编码 STAGES"到"声明式管线"

**现状**：`pipeline.py` 的十二级流水线是代码内硬编码的阶段列表，阶段间依赖、重试策略、超时、产物契约都隐含在 Python 逻辑里。

**Harness 思路**：管线应该是**声明式的、可独立审查的、版本化的 YAML/JSON 定义**——阶段、闸门条件、失败策略、产物清单一目了然，非工程角色也能审阅。

**优化点**：
- 将 STAGES 提取为 `pipeline.yaml`（阶段名、前置闸门、产物契约 schema 引用、超时、失败策略 abort/retry/skip），`pipeline.py` 变成解释器。收益：管线变更可 diff、可 code review、可按 client 定制变体。
- 为每个阶段显式声明 `outputs` 与 `contract`（指向 schemas/ 里的对应 schema），进入下一阶段前强制校验产物——这同时解决第 4 节的 schema 空转问题。

### 2. Gate 治理 —— 已很强，补"策略集中化"最后一公里

**现状**：确认闸门+指纹失效+waiver 登记是本项目最强项（INC-011/012 的修复体现了"硬阻断优于静默降级"的正确价值观）。但闸门规则散布在 `run_manifest.py`、`asset_prep.py`、`video_engine.py`、`script_splitter.py` 多处，"严格执行规则"目前只写在 REAL_TEST_INCIDENTS.md 里。

**Harness 思路**：治理规则应当 **Policy as Code**（Harness 用 OPA/Rego），集中定义、统一执行点、可审计。

**优化点**：
- 把 REAL_TEST_INCIDENTS.md 末尾的 9 条"严格执行规则"落成机器可执行的策略文件（如 `policies/gates.yaml`），每条规定：触发点（submit_video / approve_asset / deliver）、条件、阻断消息。各脚本在统一入口调用 `policy_check(stage, context)`，而不是各自硬编码。
- 收益：规则变更不用改五个脚本；新增规则天然覆盖所有执行路径；审计时"策略版本 + 执行记录"即可回答"这次交付为什么被放行"。

### 3. Continuous Verification —— 最大的体系缺口

**现状**：质量验证是"单点事后型"——OCR 在出片后、QC 在交付前。没有回归基准：改了 prompt 模板、换了模型版本，没有任何机制告诉你"上一次能过的客户案例现在还能不能过"。INC-001~012 的修复都有回归测试，但全是单元级 mock，**没有一条真实链路的金丝雀回放**。

**Harness 思路**：Continuous Verification 要求部署/变更后自动对生产信号做验证，异常即回滚。

**优化点**：
- **金丝雀用例集**：把 AeroClip S1 这条真实跑通的 49 秒成片固化为金丝雀——冻结其 brief/脚本/plan/参考图指纹，每次引擎/prompt/模型映射变更后，以 `--dry-run`（构造请求但不下发真实 API）或低成本模型（wan2.7）回放全链路，断言各阶段产物契约通过。
- **生成质量基线**：对 OCR 通过率、best-of-N 首次通过率、单段平均重试次数建基线指标；变更后对比，劣化即告警。这些指标目前散落在 ledger 里没人聚合。
- **mock 网关**：为 br_client 录一套 VCR 式请求/响应 fixture（AeroClip 真实流量脱敏），让集成测试在无密钥环境下也能跑全链路——当前测试全是单脚本单测，缺"跨脚本接力"这一层。

### 4. Policy as Code / Schema —— 7 份契约零校验

**现状**：`schemas/` 有 clip-contract、reference-contract、continuity-state、take-review、generation-run、variant-matrix、performance 七份 JSON Schema，但 `requirements.txt` 没有 jsonschema 依赖，全仓 `import jsonschema` 零命中。INC-005（旧 plan 复用）和 INC-011（必需参考图被截断丢失）本质上都是**契约存在但未在执行点强制校验**。

**优化点**（性价比最高的单项改进）：
- 引入 `jsonschema`，在三个执行点强制校验：① plan 加载时（storyboard_plan.json → generation-run）；② 视频提交前（segment → clip-contract / reference-contract）；③ manifest 写入时（→ continuity-state）。
- 校验失败 fail-closed，错误信息直接引用 schema 字段路径。INC-011 式的"参考图静默丢失"将从"两层人工硬编码检查"升级为"契约自动拦截"。

### 5. 可观测性 —— 从 print 考古到结构化事件流

**现状**：全仓零 logging 配置，仅 video_engine.py 就有 32 处裸 print；无指标、无 trace、无运行健康视图。客户现场出问题时，排查手段 = 翻 output/ 目录 + 读 manifest JSON + 问客户"当时屏幕上显示了什么"。

**Harness 思路**：每个 pipeline execution 有结构化日志、阶段级指标、端到端 trace，可回放任意一次执行。

**优化点**：
- **结构化日志**：引入 `logging` + JSON formatter，统一字段（run_id、client、stage、segment_id、model、task_id、elapsed_ms、cost、result）。print 全部收口。日志文件按 run_id 落盘到 `output/<client>/<run_id>/run.log`。
- **阶段指标聚合**：cost_ledger/generation_ledger 已有 append-only 原始数据，补一个 `scripts/obs_report.py` 聚合出：每模型成功率、首次通过率、P50/P95 生成时长、单 run 总成本、OCR 拦截率。这是后续 FinOps 和 SLO 的数据源。
- **跨阶段 trace**：run_id 已贯穿 manifest，把它作为 trace_id 贯穿所有日志/账本/产物命名，一次 run 的完整故事可以用一条命令重建（`obs_report.py --run-id X` 输出时间线）。

### 6. CI/CD —— "700+ 测试"目前是一句没有强制执行的话

**现状**：无 `.github/workflows`、无 pre-commit；57 个测试文件靠手工跑；**`tests/run_tests.sh` 的 `all` 分支把 `tests/test_*.py` 当单个参数传入，`[ -f "tests/tests/test_*.py" ]` 永不命中——"全量测试"实际静默执行 0 个用例**。这是最危险的失效模式：不是报错，而是假绿。

**优化点**（按优先级）：
1. **P0 立即修**：修 `run_tests.sh` glob 展开 bug，并在全量模式结尾断言"实际执行用例数 > 阈值（如 500）"，防止未来再次静默空跑。
2. **接 CI**：仓库已有 GitHub remote（videoSKILL），加一个最小 workflow：push/PR 时跑 `python3 -m unittest discover tests` + 用例数断言 + `import` 冒烟（复用现有 `test_no_inline_imports.py` 的思路）。无密钥环境下跳过真实 API 测试即可。
3. **发布门禁**：版本号/CHANGELOG 与 runtime-manifest.json 的 SHA-256 树联动，改代码没更新 manifest 即 CI 失败——防止"离线资产与代码漂移"。

### 7. 成本治理（FinOps）—— 有账本，没预算

**现状**：`cost_ledger.py` 精确记账（已接线到 video_engine/pipeline），这是很好的底子。但 Harness CCM 视角下还缺三样：**事前预估、事中预算闸、事后异常检测**。

**优化点**：
- **事前预估**：确认闸门 UI 文案里加"本次渲染预计消耗 N 次生成 ≈ 估算成本"，数字从 ledger 历史均价推导。客户在确认前就知道要花多少钱——这与项目"每一分钱花在确认过的内容上"的价值观完全一致，是价值观的闭环。
- **预算闸**：支持 per-run / per-client 预算上限（`--budget-cap`），超过即阻断并提示。营销客户按项目结算是天然场景。
- **异常检测**：单段生成成本超过历史 P95 的 2 倍时打标（如模型降级失效导致全程走了贵的 kling），写进交付报告。

### 8. 可靠性工程 —— 补 SLO 与错误预算

**现状**：重试/退避/锁/原子写/断点续跑已成熟（br_client 的 Retry-After 处理、run_manifest 乐观锁、video_tasks 断点续跑都是正确实现）。缺的是**把"可靠"从工程习惯变成可度量承诺**。

**优化点**：
- 定义三个 SLO：① 单段生成成功率（含一次自动重试）≥ 95%；② 全链路（brief→成片）一次通过率 ≥ 80%；③ 断点恢复成功率 ≥ 99%。用第 5 节的指标聚合去度量。
- 错误预算驱动决策：当 seedance 首次通过率持续低于基线时，自动提高 best-of-N 的默认 N 或建议临时切换默认模型——把 INC-008/010 式的人工应对沉淀为自动策略。

### 9. Delegate 架构（多宿主 Agent）—— 协议正确，缺能力协商

**现状**：Agent 无关业务层 + 薄宿主适配的设计正确（AGENTS.md 铁律明确要求）。但宿主接入只靠文档约定：没有机制回答"当前宿主到底支不支持 slash command / 长任务后台轮询 / 文件预览"。

**优化点**：
- 定义 `AGENT_CAPABILITY` 握手：宿主入口上报能力集（slash_commands / background_tasks / file_preview / stdio_keys），`agent_entry.py` 据此选择交互降级路径（如不支持 slash command 就走问答式菜单）。把"无法识别宿主回退 unknown"从"保持可运行"升级为"按能力优雅降级"。
- 为 Kilo/Codex/Hermes 三个已声明宿主各留一个冒烟测试：模拟该宿主的调用方式跑通入口自检，防止薄适配层腐化。

### 10. 环境与制品治理 —— 双轨一致性

**现状**：deploy.sh 离线路径有 SHA-256 树校验（runtime-manifest.json），但在线路径是裸 `pip install -r requirements.txt` 无校验；node 侧 bundled node_modules 仅 darwin-arm64；assets/ 下有 3 个残留 .lock 文件；output/ 顶层混着前隔离时代的 271 个扁平遗留文件。

**优化点**：
- 在线路径装完依赖后同样做哈希/版本断言，离线与在线"殊途同验"。
- `setup_env.py check` 增加 stale lock 清理项（FileLock 已有 stale_after=300 检测，但没人定期扫）。
- output/ 遗留文件做一次归档迁移（`output/_legacy/`），不影响代码但消除"哪些是正式产物"的歧义——这也降低新接入 Agent 误读旧产物的风险（INC-005 的环境诱因）。

---

## 三、落地路线图建议

| 优先级 | 事项 | 投入 | 风险消除 |
|---|---|---|---|
| P0（本周） | 修 `run_tests.sh` glob 失效 + 用例数下限断言 | 0.5 天 | 测试假绿 |
| P0（本周） | 引入 jsonschema，三个执行点强制校验 7 份契约 | 1-2 天 | INC-005/011 同类事故 |
| P1（两周） | 结构化日志 + run_id 全链 trace + obs_report.py | 2-3 天 | 客户现场不可排查 |
| P1（两周） | 接最小 CI（unittest discover + 用例数断言） | 1 天 | 回归无门禁 |
| P1（两周） | 闸门规则集中为 policies/gates.yaml | 2 天 | 规则散落腐化 |
| P2（月度） | 金丝雀用例集 + 生成质量基线指标 | 3-5 天 | 变更无回归网 |
| P2（月度） | 成本预估 + 预算闸 | 1-2 天 | 费用不可预期 |
| P3（季度） | pipeline.yaml 声明式管线 + SLO/错误预算 | 5 天+ | 长期演进能力 |

**一句话总结**：这个项目已经修好了 Harness 最难修的部分——治理文化与 fail-closed 纪律；缺的是 Harness 最基建的部分——CI、可观测性、契约执行。先修测试假绿和 schema 空转这两件事，投入最小，堵的洞最大。

---

## 附录：修复落地记录（2026-08-04 全量修复）

| 报告建议 | 落地结果 |
|---|---|
| P0 修 run_tests.sh 假绿 | ✅ all 分支 nullglob 数组展开，实测 57 文件 / 515 用例真实执行；文件数 <40 或用例数 <400 即非零退出；另修复 test_prompt_review.py 缺 `unittest.main()` 的隐藏假绿 |
| P0 schema 运行时校验 | ✅ 新建 `scripts/schema_validate.py`（jsonschema 优先，未装时用内置 mini 校验器，零新增依赖）；五个执行点强制校验 fail-closed：generation_ledger.append_event（generation-run）、take_review.save_review（take-review）、continuity_state.save_state（continuity-state）、script_splitter 参考图/分镜契约创建点（reference-contract/clip-contract）、video_engine 提交前完整 typed reference 校验 |
| P1 结构化日志+trace | ✅ 新建 `scripts/obs_log.py`（JSON Lines，run_id 贯穿，落盘 run 目录 run.log，未 configure 时安全 no-op）；video_engine render_batch 接线 batch_cost_estimate/batch_start/batch_done/ocr_warning 事件 |
| P1 指标聚合 | ✅ 新建 `scripts/obs_report.py`：每模型成功率/首次通过率/平均重试/P50/P95 耗时/成本汇总/成本异常（P95×2 打标） |
| P1 最小 CI | ✅ `.github/workflows/ci.yml`：push/PR 触发，py3.11/3.13 矩阵，无密钥环境跑全量测试 |
| P1 闸门策略集中化 | ✅ `policies/gates.json`（GATE-001~010，含预算闸）+ `scripts/policy_check.py`；video_engine render_batch 提交前 `enforce("submit_video", ...)` |
| P2 成本预估+预算闸 | ✅ render_batch 新增 `budget_cap` 参数与 CLI `--budget-cap`；提交前打印预估成本，超支即 PolicyBlock 阻断 |
| P2 金丝雀回放 | ✅ `scripts/canary_replay.py --dry-run`：plan_load→segment→reference_contract→policy_gate→request_build 五阶段，零网络，实测 PASS（篡改用例正确 FAIL） |
| P2 stale lock 清理 | ✅ `setup_env.clean_stale_locks()` 接入 check()；首跑清理 18 个残留锁 |
| P2 在线 pip 无校验 | ✅ deploy.sh 在线路径装后逐模块 import + 版本下限断言（与离线 SHA-256 树校验殊途同验） |
| P2 output 遗留归档 | ✅ `scripts/cleanup_output.py`（dry-run 默认，--apply 只移动不删除）；18 个顶层遗留已归档 output/_legacy/20260804-170633/ |
| 契约 vocabulary 对齐 | ✅ reference-contract schema 的 type 枚举补齐生产实际词汇（character_board / product_board / product_usage_identity）——契约反映现实后才能强制 |

**回归结果**：57 文件 / 515 用例，56 通过；唯一失败 test_local_media_engines.py 为并行会话未提交的 hf_engine.py 改动（script 标签 nonce）所致，git stash 验证与本次修复无关。
