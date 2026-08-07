# 真实链路测试事故记录

测试客户：AeroClip S1

测试时间：2026-07-31

本记录对应实际 BasicRouter 图片和视频调用，不包含沙箱或 mock 结果。

## 事故清单

### INC-001：产品原图无效却进入测试

- 现象：`testclient19c/images/human2.png` 只有 72 字节，状态为 `quarantine`。
- 根因：客户目录缺少有效产品原图，早期测试没有在产品阶段前阻断并转为上传请求。
- 影响：不能形成可追溯的产品身份锚点，已停止继续使用该客户素材。
- 修复：要求产品图先通过文件签名校验、客户登记和确认闸门；AeroClip 改用其客户目录内已确认的 `hero.jpg`。
- 状态：已处理，回归测试覆盖无效/未登记素材阻断。

### INC-002：图片参考协议与接口不匹配

- 现象：产品使用图调用异步 `/v1/image-generations` 时返回 `model does not support image input`。
- 根因：当前网关 legacy 模式下，`gpt-image-2` 的图生图兼容协议是同步 `/ai/createImage` + `imageUrls`；异步接口的模型能力声明与实际行为不一致。
- 影响：产品使用图任务失败，未继续进入故事板。
- 修复：使用图阶段改用同步 `gpt-image-2` 图生图，并保留三类参考：数字人板、产品板、正确佩戴姿势参考。
- 状态：已处理，代码语法和真实重试通过。

### INC-003：代理 peer 校验阻断合法图片下载

- 现象：真实图片已生成，但下载报 `DOWNLOAD_PEER_BLOCKED`。
- 根因：请求实际经过本机 `127.0.0.1:7890` 代理，而环境变量未声明该代理；安全下载器将代理 peer 当成非公网地址。
- 影响：图片生成成功但本地没有产物。
- 修复：实际运行时显式声明 HTTPS 代理；保留 HTTPS、DNS、重定向和显式代理 peer 校验，不关闭安全检查。
- 后续：环境初始化应自动识别并登记受控代理，避免人工命令遗漏。

### INC-004：视频模型展示名、模型 ID 与 legacy API 不一致

- 现象：`kling-v3-omni-video`、`happyhorse-1.0-r2v`、`kling-v3-omni` 在不同目录/API路径上的 ID 与展示名不一致。
- 根因：模型目录字段、模型选择器和 legacy `/ai/createVideo` 的接受值未统一。
- 影响：视频请求被 `Model not found` 拒绝；使用 HTTPS 托管图片和 legacy 展示名直连后才成功。
- 修复：新增动态目录优先的 canonical ID → legacy endpoint name 转换，并保留窄范围离线映射；实际请求体使用 legacy 接口可接受的模型名，目录能力判断仍使用 canonical ID。
- 状态：已处理；新增 `test_video_model_alias.py` 回归测试，并纳入全量测试。

### INC-005：故事板使用旧分镜计划生成“新文件”

- 现象：故事板文件时间是新生成，但内容仍为旧 `Mina` K-pop 方案，而不是 AeroClip `Luna` 新计划。
- 根因：运行时使用了旧 `output/storyboard_plan.json`；生成器只验证计划结构和 fingerprint，不验证该计划是否是当前客户/当前已确认资产对应的定稿计划。另有同名文件 orphan 复用风险：旧文件存在但 checkpoint 没有对应条目时，原逻辑直接标记 `skipped`。
- 影响：错误故事板可能被客户误认为新结果，并有继续下游视频的风险。
- 修复：建立全新的 AeroClip 计划；故事板结果记录 `plan_source`、`plan_title`；同名文件只有在 checkpoint、shot 内容、plan fingerprint、SHA-256 四项同时匹配时才可复用，否则删除并重新生成。
- 状态：核心复用缺陷已修复；客户/计划语义绑定仍需在入口层补强。

### INC-006：产品使用参考图语义被误解

- 现象：正确佩戴图被误认为产品使用图主体，导致人物+产品主体关系不清。
- 根因：参考图分组和 prompt 没有明确区分“主体身份板”和“姿势辅助参考”。
- 影响：产品使用九宫格不符合真实佩戴要求。
- 修复：主体固定为已确认 Luna 板 + 已确认 AeroClip 产品板；`wear_reference` 只作为佩戴位置辅助输入，并在 prompt 中明确不得替换主体。
- 状态：已处理，真实重生成通过待客户确认。

### INC-007：故事板比例与视频比例混淆、生成文字责任越界

- 现象：客户视频要求 `9:16`，故事板校验要求 `16:9`；旧计划还把价格、slogan、字幕写入图像生成画面。
- 根因：故事板预演比例和最终视频比例没有在计划字段中显式分离；旧计划未执行文字动效迁移。
- 影响：比例误解、生成模型画字、故事板内容不适合作为视频参考。
- 修复：计划明确 `aspect_ratio=16:9`、`video_aspect_ratio=9:16`；文字全部迁移到 `motion_elements`，图像 prompt 使用无文字约束。
- 状态：已处理，已有 no-text 回归测试覆盖。

### INC-008：Seedance 多参考图数量和真人隐私策略冲突

- 现象：首次 Seedance batch 为 4 段全部失败，前 3 段报 `InputImageSensitiveContentDetected.PrivacyInformation`，拒绝第 3/4 张真人参考图。
- 根因：当前实时 `seedance-2.0` 目录声明 `imageCount` 为 1；流程却把故事板、人物板和产品使用板同时作为视频输入。Seedance 的 `videoType=5` 能力不等于支持任意数量的输入图。
- 修复：按实时目录能力将 Seedance batch 改为每段仅传对应已确认故事板作为唯一图像输入，人物/产品身份通过故事板和视频提示词锁定；不得再把多张真人板盲目塞给 Seedance。
- 状态：已处理；第二次 batch 成功 3/4 段，结尾段单独重试成功。

### INC-009：Seedance 最短时长与计划分段不匹配

- 现象：4 秒以下的结尾段被 `duration ... is not valid` 拒绝。
- 根因：实时 Seedance 2.0 的 `videoDurationMin` 为 4 秒，旧计划切出的结尾段为 3 秒。
- 修复：结尾段改为 4 秒，并将实时模型时长能力作为生成前校验条件。
- 状态：已处理。

### INC-010：Seedance 生成模型自行绘制卖点文字导致 OCR 阻断

- 现象：第三段首次生成的视频 OCR 检出“通勤办公跑步都能戴”“8小时”“30小时”“IPX5”“AeroClip S1”“399元”“不入耳，更自在”等画面文字。
- 根因：视频模型把台词/故事板中的卖点文字当成视觉元素生成，尽管故事板已将动效迁移到 `motion_elements`。
- 修复：第三段使用更强的无文字视频 prompt 和 negative prompt 重新生成，重试版本 OCR 清白；不接受首次版本。
- 状态：已处理，重试版本通过 OCR 和媒体 QC。

### INC-011：已确认产品使用板未交接到视频请求

- 现象：产品使用九宫格已确认并参与故事板生成，但最终 Seedance 请求只携带每段故事板；`product_usage_board.jpg` 没有直接进入视频参考图列表。
- 根因：分镜拆分器收集了多类参考图后按固定上限截断，且没有把“确认参考图类型必须全部存在”作为视频提交前的硬契约。Seedance 实时目录的 `imageCount=1` 又诱发了手工改成单故事板输入，导致素材语义静默丢失。
- 影响：成片虽然通过媒体/OCR 检查，但不满足“确认产品使用图必须交接到视频阶段”的业务要求。
- 修复：
  - `script_splitter` 为人物+产品段建立必需参考类型契约：故事板、人物板、产品板、产品使用板。
  - 生成板存在时移除重复的原始肖像/产品图，避免重复素材挤占配额。
  - 必需参考图被网关上限丢弃时正式流程直接失败，不再静默继续。
  - `video_engine` 在 batch 提交前再次核验参考类型，缺任一项立即阻断。
  - 每个 segment 记录 `required_reference_types` 和 `dropped_references`，供 handoff 和审计使用。
- 状态：代码修复完成，回归测试通过；此前成片不 retroactively 宣称符合该交接要求。

### INC-012：正式流程仍存在模型/参考素材降级入口

- 现象：`render_batch`、`render`、`render_chained` 和 CLI 默认允许模型 fallback；`_apply_locked_refs`、尾帧串联和历史参考整理逻辑曾对参考图做固定数量截断。
- 根因：旧版“提高成功率”的容错策略没有绑定完整素材 handoff，可能用换模型或少传素材掩盖能力差异。
- 修复：
  - 允许正式流程自动选择降级模型，但候选模型必须完整支持参考图数量、videoType 和音画能力。
  - 正式入口在提交前根据实时模型目录的 `imageCount` 校验参考图容量。
  - 正式流程禁止 `_apply_locked_refs`、chain 和交接层截断参考图；超出模型能力直接报错。
  - 缺失或被丢弃的必需参考类型在 `script_splitter` 和 `video_engine` 两层均硬阻断。
  - 记录 `required_reference_types` 与 `dropped_references`，作为 handoff provenance。
  - 降级只改变 `model`；脚本、台词、storyboard、全部参考 URL、参考类型、ratio、duration 和 handoff fingerprint 保持不变。
- 状态：已处理；增加“完整参考图集下选择降级模型”的回归测试。

### INC-013：老 brief 缺省字段导致风格写入中断

- 现象：Momax 实测中执行 `asset_prep.py set-profile --client momax ...` 报 `KeyError: 'style_hints'`，流程卡在风格确认落盘阶段。
- 根因：`_load_brief()` 对已存在的旧 `brief.json` 只补 `client/revision`，没有补齐新 brief 模板字段；`set_profile()` 返回值直接索引 `style_hints`。
- 影响：任何历史客户 brief 缺少 `style_hints/specs/ppt_files/render_profile` 等默认字段时，后续阶段可能在读写时崩溃。
- 临时修复：`_load_brief()` 读取旧 brief 后用默认模板做非破坏性补齐；`set_profile()` 返回值改用 `.get()` 兜底。
- 状态：已处理；新增 `test_legacy_brief_load_backfills_missing_defaults` 与 `test_set_profile_handles_legacy_brief_without_style_hints` 回归测试。

### INC-014：故事板计划被视频分段器提前合并

- 现象：Momax 25 秒、5 镜头故事板计划经 `storyboard.canonical_storyboard_plan()` 后只剩 2 个 segment，违背“故事板格数由剧本分镜数量决定”的当前规则。
- 根因：故事板归一化阶段复用了 `video_segmentation.partition_shots()` 的 15 秒聚合策略，把多个短镜头合并为视频提交段；故事板确认层和视频提交层职责混在一起。
- 影响：客户看到的故事板格数与已确认剧本镜头数不一致，后续逐格展开和 `panel_index/ref_tags` 绑定会错位。
- 临时修复：`storyboard.canonical_storyboard_plan()` 对不超过模型时长上限的短镜头默认保留原始 shot 边界，只在单个 shot 超时才拆分；视频阶段仍可自行分段。
- 状态：已处理；新增 `test_storyboard_canonical_preserves_short_script_shots` 回归测试。

### INC-015：draft 单测触发真实模型目录网络请求

- 现象：运行 `tests.test_v19` 时卡在 `test_10_render_chained_respects_own_explicit_urls`，中断后堆栈显示正在请求 `/employee/models` 并做网络重试。
- 根因：该单测已 mock `create_video/wait_video/download`，但漏 mock `br_client.list_models`；`render_chained()` 的模型选择仍会查询实时模型目录。
- 影响：离线/沙盒测试流程不稳定，网络不可用时测试时间被指数退避拖长，无法快速收集后续问题。
- 临时修复：该 draft 单测显式 mock `br_client.list_models` 返回空目录，走本地能力兜底。
- 状态：已处理；已用单测重跑验证。

### INC-016：故事板计划预检仍保留未确认 raw 产品图

- 现象：Momax 故事板计划本地预检显示 4 张产品参考图中有 2 张来自 `assets/momax/product_images/`，文件存在但未经过清洗确认；`render_storyboard()` 正式运行时会过滤，但 `canonical_storyboard_plan()` 仍保留它们。
- 根因：`_hydrate_plan_asset_refs()` 只校验作者手填路径是否存在，没有在“客户 brief 已有 confirmed 产品锚图”时同步执行 `is_product_asset_ready()`。
- 影响：计划预检、计划指纹和人工审计可能误以为 raw 图会进入后续生成，和正式渲染入口看到的素材集不一致。
- 临时修复：当 brief 中存在已确认产品锚图时，canonical 阶段也过滤作者手填的未确认产品图；当前 Momax `storyboard_plan.json` 已移除 raw 产品图，只保留两张 confirmed gpt-image-2 标准化产品图。
- 状态：已处理；新增 `test_authored_raw_product_refs_are_removed_when_confirmed_anchors_exist` 回归测试。

### INC-017：提示词审核基座丢失产品名并诱发模型幻觉

- 现象：Momax `prompt_review.py polish --stage storyboard` 生成的待确认提示词中，产品被泛化为“數碼3C/便攜音訊配件”，并出现“马卡龙甜点”“耳机”“充电盒”等未确认物体。
- 根因：`prompt_review._base_storyboard_prompt()` 只读取 `product_facts.product_name`，但当前计划的真实产品名在 plan 顶层 `product_name`；同时无人镜头仍要求“人物脸、发型、服装必须引用人物板”，使模型倾向补人，产品板/使用图也被描述成已存在。
- 影响：若直接确认该审核文件，后续故事板会偏离真实产品，且可能凭空添加人物/错误道具。
- 临时修复：提示词基座新增 `_product_facts()`，优先合并 plan 顶层 `product_name/model/product_type/features`；新增无人/有人镜头的条件化人物规则；产品规则明确不得改成耳机、充电宝、充电盒、甜点等其它物品；新增 prompt scope lint，纯产品镜头若被润色成手部/人物镜头会阻断并触发重试；lint 已处理“严禁/无任何人物”这类否定句误报。
- 状态：已处理；旧 `output/momax_storyboard_prompt_review.json` 仍为 pending 且不应确认，需重新 polish 后再展示确认。

### INC-018：提示词确认命令参数不一致

- 现象：流程说明中容易写成 `prompt_review.py confirm --file ...`，但实际 CLI 只接受 `--review`。
- 根因：确认类工具在项目中有 `--file/--review/--result-json` 多种命名，`prompt_review.py` 没有给常用别名。
- 影响：客户或 Agent 到确认闸门时会因为参数名错误中断，尤其影响“先保证测试流程”的连续性。
- 临时修复：`prompt_review.py confirm` 增加 `--file` 作为 `--review` 的别名，保持原参数兼容。
- 状态：已处理；新增 `test_confirm_cli_accepts_file_alias` 回归测试。

### INC-019：提示词预览写文件后仍刷出全文

- 现象：`prompt_review.py preview --out output/momax_storyboard_prompt_review.md` 已经把审核内容写入 Markdown 文件，但 CLI 仍把完整预览全文打印到终端，导致真实流程输出被 1 万多字提示词淹没，甚至触发上下文截断。
- 根因：CLI 分支无条件 `print(preview(...))`，没有区分“给人直接看”和“写入文件供对话展示”的两种用法。
- 影响：长分镜项目会污染调试日志，遮蔽单测结果和真实错误，不利于连续测试。
- 临时修复：`preview --out` 改为只输出短 JSON（预览绝对路径与字节数）；未传 `--out` 时仍打印 Markdown 全文。
- 状态：已处理；新增 `test_preview_cli_with_out_prints_short_json_only` 回归测试。

### INC-020：确认后的故事板计划仍缺运行时参考合同

- 现象：用户确认 Momax 提示词审核后，运行 `storyboard.py --stage next --prompt-review ...` 在扣费前被校验阻断：每个 shot 缺少 `panel_plan`，且 `ref_tags` 引用的 `@product_hero/@product_angle/@mina` 被判定不存在。
- 根因：共创计划把参考图统一写在 plan 顶层 `references[]`，但 `storyboard_validator.validate_plan()` 只校验 shot 级 `references[]`；同时旧计划把 12 格细节留在已确认的 `approved_prompt_zh` 中，没有结构化落到 `shot.panel_plan`。
- 影响：客户已经完成提示词确认后仍无法进入故事板生成，且错误信息会误导为素材标签缺失，实际是顶层参考没有下发到 shot 运行时合同。
- 临时修复：`_load_prompt_review_for_shots()` 在确认审核指纹通过后，给缺失 `panel_plan` 的旧 shot 注入默认运行时 panel plan，并按 `ref_tags` 从 plan 顶层 `references[]` 拷贝对应参考对象到 shot 级 `references[]`。
- 状态：已处理；新增 `test_storyboard_review_injects_runtime_panel_and_global_references` 回归测试。

### INC-021：产品板出图仍走旧同步取图接口

- 现象：Momax 产品板生成进入 `/ai/createImage` 同步请求后长时间无心跳，随后两次出现 `Remote end closed connection without response`，最终需要人工中断；用户提示应参考 BasicRouter 文档 `retrieve-image-generation` 的获取方式。
- 根因：`product_board.py` 和 `storyboard.download_first_image(sync_img2img=True)` 为了兼容历史 `gpt-image-2 imageUrls` 行为，强制走同步 `/ai/createImage`；但官方文档当前图像生成流程是 `POST /v1/image-generations` 返回 `taskId`，再 `GET /v1/image-generations/{taskId}` 轮询，`images` 为 JSON 字符串数组。
- 影响：大图/参考图生成依赖长连接，网络断开时没有 taskId 可恢复，也无法给客户稳定进度；与官方 retrieve 方式不一致。
- 临时修复：产品板生成和故事板所有 `download_first_image()` 调用统一改回异步 `create_image_generation + wait_image_generation`；旧 `sync_img2img` 参数仅保留兼容，不再切到同步接口。
- 状态：已处理；新增产品板异步 retrieve 回归测试，并新增 `sync_img2img=True` 不走旧同步接口的故事板回归测试。

### INC-022：异步取图成功后下载被代理 peer 校验误拦

- 现象：改用官方异步 retrieve 后，产品板阶段不再卡在提交接口，而是在下载返回图片 URL 时失败：`DOWNLOAD_PEER_BLOCKED: 实际连接到了非公网地址`。
- 根因：下载 SSRF 防护会验证最终 socket peer，允许公网 peer 和显式配置的代理 peer；但 `_configured_proxy_peers()` 只识别 `HTTPS_PROXY/https_proxy`，没有覆盖常见的 `ALL_PROXY/all_proxy` 和 `HTTP_PROXY/http_proxy`。在本地代理网络下，urllib 可能实际连到内网代理 peer，被误判为不安全目标。
- 影响：官方 retrieve 已拿到图片结果后仍无法落盘；同时产品板 state 之前只在下载成功后写入，失败时丢失 taskId/result_url，恢复诊断困难。
- 临时修复：下载代理 peer 识别扩展到 `HTTP_PROXY/http_proxy/ALL_PROXY/all_proxy`；产品板在下载前写入 `download_pending` state，保留 `task_id/request_id/result_url`；对 BasicRouter 生成结果 URL 的落盘下载显式允许本地非公网代理 peer，用户输入远程素材下载仍保持默认严格校验；检测到 `download_pending` 且素材指纹一致时直接恢复下载，不重新提交付费生成任务。
- 状态：已处理；新增代理 peer 回归测试、产品板 state 测试和 download_pending 恢复测试。

### INC-023：素材补图/清图仍残留旧同步图片生成入口

- 现象：修复故事板产品板后继续审计发现，`asset_prep.py` 的 `_create_one()`、`clean_image()`、`cutout()` 仍通过旧 `br_client.create_image()` 同步接口提交图片生成。
- 根因：故事板生产线和素材准备线分别维护图片生成入口；前者已按 BasicRouter 官方 `retrieve-image-generation` 改为异步，后者仍保留历史同步封装。
- 影响：后续缺图补齐、客户反馈精修、产品清洗、抠图等步骤仍可能遇到长连接静默、断连无法恢复、无 taskId 可诊断的问题。
- 临时修复：`asset_prep._create_one()` 改为 `create_image_generation + wait_image_generation`，保留原默认模型 `seedream-5.0`；`clean_image()` 继续显式使用 `gpt-image-2`；`cutout()` 继续显式使用 `kling-v3-omni-image`；生成结果下载仍只对 provider 返回 URL 允许本地代理 peer。
- 状态：已处理；新增 `_create_one`、`clean_image`、`cutout` 异步路径回归测试。

### INC-024：参考图托管/数字人/产品库/融合图仍可回到旧同步接口

- 现象：继续全局搜索 `create_image()` 发现 `br_client.host_image()`、`digital_human.create_actor()`、`product_library.gen_view()`、`matte.compose_scene()` 仍会直接调用旧同步图片生成接口。
- 根因：不同模块各自封装图片生成，没有共享统一的 BasicRouter 异步取图入口；之前只修了当前故事板产品板路径，没有覆盖后续视频参考图托管、数字人头像、产品方位图和外部融合图。
- 影响：后续视频阶段本地参考图托管、创建新数字人、补产品多视图、人景融合时仍可能出现长连接断开、无 taskId 可恢复、进度不可见的问题。
- 临时修复：上述入口均改为 `create_image_generation + wait_image_generation`；`host_image` 遇到 retrieve 超时只重轮询同一 taskId，不重复提交；生成结果下载继续对 provider 返回 URL 允许本地代理 peer。
- 状态：已处理；更新 host_image 缓存/超时测试，并新增数字人生成、产品方位图、matte 融合异步路径回归覆盖。

### INC-025：OCR 视频引擎单测误触真实模型目录网络请求

- 现象：运行扩展测试集时，`tests.test_v18` 的 draft 视频路径多次触发真实 `/employee/models` 查询，进入网络重试等待，需要人工中断；先暴露在 `test_05_render_chained_propagates_ocr`，随后又暴露在单段 CLI OCR 闸门测试 `test_warning_blocks_single_delivery`。
- 根因：这些用例已经 mock 了视频提交、轮询、下载和 OCR，但漏掉 `video_engine` 进入 render/render_batch/render_chained 前的模型目录/能力探测；业务路径会先调用 `_pick_video_model()`，再通过 `_model_catalog()` / `_available_models_set("video")` 触发 `br_client.list_models()`。
- 影响：全量测试不再是纯本地、无网络、无 API Key，容易在离线或限流环境下卡住，也会拖慢真实流程测试。
- 临时修复：给 `tests/test_v18.py` 中 batch、chain、single CLI 三条 draft OCR 测试路径补齐 `_model_catalog`、`br_client.list_models`、`_available_models_set` 三个 mock，与 `test_v19` 串联渲染用例保持一致。
- 状态：已处理；`tests.test_v18` 全文件已验证通过。

### INC-026：故事板阶段缺少客户反馈精修入口

- 现象：Momax 产品使用细节图生成后进入 pending 确认闸门，但若客户反馈“手势不对、佩戴方式不对、产品角度要改”，`storyboard.py` 只有 `--confirm-board usage`，没有对应的“精修当前使用图并回到待确认”的命令入口。
- 根因：通用素材线已有 `asset_prep.py refine-image --feedback-ref`，但故事板阶段的产品板/人物板/产品使用板使用固定文件名和独立 approval 文件，不能直接套用普通素材候选状态机；此前没有实现板级精修的状态迁移。
- 影响：真实业务中客户一旦要求修改已展示的使用图，Agent 只能手工删除文件或重跑整阶段，容易误复用旧确认、丢失旧图备份，甚至让后续故事板拿到未重新确认的图。
- 临时修复：新增 `storyboard.py --refine-board product|cast|usage --result-json ... --edit ... [--feedback-ref ...]`；精修时保留旧图备份、用新 pending 图替换固定板文件、删除旧 `.usage_confirmed.json` 等确认记录、记录 feedback refs 和 previous sha，强制客户重新确认后才能继续。
- 边界补强：板级精修的参考图上限为 4 张时，当前板图永远第一，客户反馈图优先进入剩余槽位；人物板/产品板只作为补充参考，避免客户上传的多张反馈图被静默截断。
- 状态：已处理；新增 `test_refine_usage_board_replaces_fixed_file_and_requires_reconfirmation`，并与 `test_asset_feedback_refine` 一起验证多反馈图流程。

### INC-027：故事板精修入口未复用会话密钥且缺少生成心跳

- 现象：客户反馈“产品与手机连接方式应为音响底部磁吸到手机背面”后，首次执行 `storyboard.py --refine-board usage ...` 报 `no API key; run key onboarding first`；随后修复密钥读取后，真实出图约 90 秒没有任何进度输出。
- 根因：`refine_board()` 是新增 CLI 入口，未像 `render_storyboard()` 一样先调用 `key_setup.ensure_session_id()` 再 `load_key()`；同时直接调用 `download_first_image()` 时没有传 `on_progress` 回调。
- 影响：客户已完成密钥 gate 的会话仍可能在反馈精修阶段被错误要求重新填 key；长时间无输出也违反“等待与耗时要给体感”的 UX 铁律。
- 临时修复：`refine_board()` 先 `ensure_session_id()` 再读取 key；新增 `refine_progress()`，在提交、失败/成功和每 30 秒等待时输出心跳。
- 状态：已处理；`test_refine_usage_board_replaces_fixed_file_and_requires_reconfirmation` 已覆盖 session ensure 与进度回调。

### INC-028：默认 smoke 测试覆盖不到近期实测修复路径

- 现象：`bash tests/run_tests.sh` 默认只运行 5 个文件、122 个用例并显示 `ALL GREEN`，但近期真实暴露和修复的问题分布在 `test_prompt_review`、`test_storyboard_resume`、`test_v18`、`test_asset_feedback_refine`、`test_br_client_resilience` 等文件中，默认 smoke 完全漏掉。
- 根因：`smoke` 分组停留在早期“核心 5 个”列表，没有随着真实 incident 回归测试扩展；同时只给 `all` 模式设置了文件数/用例数下限，`smoke` 没有防陈旧下限。
- 影响：日常快速验证会给出假安全感，尤其在客户流程中快速修复后，下一轮默认冒烟无法证明这些修复仍有效。
- 临时修复：把默认 `smoke` 扩展为核心流程 + 近期 incident 回归，共 17 个文件；新增 smoke 下限断言（文件数至少 15、用例数至少 170）；同步更新 `tests/README.md`。
- 状态：已处理；`bash tests/run_tests.sh smoke` 已验证 17 文件 / 275 用例通过。

### INC-029：产品使用图精修修对连接方式但放大产品造型漂移

- 现象：客户反馈“连接方式应为音响底部磁吸到手机背面”后，精修图的使用方式更接近需求，但产品造型从已确认产品板里的矮胖圆角小音响漂移成扁圆圆盘/网孔徽章，严重偏离第一张产品图。
- 根因：存在两层链路缺口。第一，新增 `refine_board(usage)` 的参考图顺序把“当前待改使用图”放在第一位，产品本体九宫格排在后面；当当前使用图已经有产品形变时，模型会把错误造型作为最高权重锚点继续强化。第二，首次 `product_usage_image` 生成也把人物板放在产品板前面，且 `product_usage_prompt()` 只读取 `product_facts.product_name`，没有读取 Momax 计划顶层 `product_name`，导致提示词里产品退化成 `the exact uploaded product`；同一段 prompt 还残留历史项目名 `Luna/AeroClip S1`。
- 影响：先生成产品板再生成产品使用图的核心设计没有被强约束实现；客户对使用关系的修改会意外牺牲产品身份一致性，后续故事板和视频会继承错误造型。
- 临时修复：首次 usage 生成和 usage 精修都改为产品身份锚点优先。首次 usage 参考顺序改为 `已确认产品本体九宫格 + 已确认单张产品图 → 人物板 → 姿势参考`；usage 精修参考顺序改为 `已确认产品本体九宫格 + 已确认单张产品图 → 当前使用图 → 客户反馈图 → 人物板`。新增 `_confirmed_product_identity_paths()`，把产品板与 plan_source 里的 confirmed 产品单图一起作为高权重产品身份锚；精修读取 `plan_source` 时用 `load_plan_json()`，避免完整 `load_plan()` 再次 canonical/hydrate 导致产品单图被过滤或改写。新增 `_plan_product_facts()` 读取顶层 `product_name/product_type/features/specs` 与旧 `product_facts`，新增 `_product_identity_lock()`，明确产品板是最高权重并锁形状/比例/材质/按钮/Logo/端口等细节；移除历史品牌名污染。
- 补充修复：usage 生成/精修结果写入 `identity_reference_paths` 与 `generation_reference_paths`，用于事后排查产品锚点是否真的进入模型及其顺序；prompt 里把 “No generated text, logo” 改为禁止非产品文字/额外 Logo，同时明确保留产品本体真实 Logo、按钮、标签和印刷标识，避免把 Momax 原生 Logo 一并压掉。
- 闸门补强：`confirm-board usage` 现在要求 `identity_reference_paths` 存在且文件有效，并把这些锚点写入 `.usage_confirmed.json`；缺少产品身份锚点记录的旧 usage 图会 fail-closed，必须用最新链路重新生成或精修后才能确认。`_approval_current()` 也会持续校验 usage approval 中的产品身份锚点文件仍存在，确认后产品板/hero 图被删或失效时，usage approval 自动过期。
- 状态：已处理代码；新增回归测试覆盖顶层产品名读取、无 Luna 历史污染、product identity lock、usage 参考图顺序、产品板+单张产品图双锚点、provenance 记录、产品原生 Logo 保留、无产品身份锚点时禁止确认，以及确认后身份锚点消失会让 approval 失效。

### INC-030：产品使用图确认后故事板参考注册表断链

- 现象：用户确认 Momax 产品使用图后，执行 `storyboard.py --stage next` 进入分段故事板生成，立即失败：`name 'build_reference_registry' is not defined`。补齐 registry 后继续审计发现同一路径还会调用尚不存在的 `contact_sheet_prompt()`，且生成调用把本地文件路径直接作为 `image_urls` 传入，存在“校验通过但模型没有收到图片引用”的风险。
- 根因：故事板从旧的“每镜头各自拼参考图”切到新 contact-sheet/reference-registry 路径时，只替换了调用点，没有把 registry 构建、校验、提示词映射和本地路径转图片引用的完整链路一起落地；缺少覆盖“产品使用图确认后继续生成故事板”的回归测试。
- 影响：客户已完成产品板、人物板、产品使用图三个确认闸门后仍无法继续；即使绕过未定义函数，也可能因为本地路径未转换导致 gpt-image-2 没拿到已确认产品/人物/使用图，后续故事板继续出现产品造型漂移。
- 临时修复：新增 `build_reference_registry()` 与 `_validate_reference_registry()`，把 `@usage`、产品板、人物板和 shot `ref_tags` 显式注册并 fail-closed 校验缺失 tag/文件；新增 `contact_sheet_prompt()`，在每个故事板请求里写入 @tag→素材→角色的映射和优先级；正式提交前用 `_collect_image_urls(..., fail_on_invalid=True)` 把 registry 中的本地确认图转换为 BasicRouter 可用图片引用，确保校验素材和实际提交素材一致。
- 状态：已处理；新增回归测试覆盖 registry 收集 usage 与 shot tags、缺失 tag 阻断、contact sheet prompt 包含参考映射。已验证 `python3 -m unittest tests.test_storyboard_enhancements tests.test_storyboard_resume tests.test_storyboard_panel_binding tests.test_asset_feedback_refine` 通过，`bash tests/run_tests.sh smoke` 通过 17 文件 / 283 用例。

### INC-031：全局参考图注册表污染无人物故事板镜头

- 现象：Momax 第 3 张 `s3_stand_and_portable` 的计划明确 `characters: []`，但生成的 12 格故事板中多次出现 Mina/人物；修掉 `@mina` 全局污染后，第 3 张仍在个别格子补出完整人物；第 2 张还出现了手写 `click` 等非产品原生文字。若继续进入视频阶段，会把“无人物功能演示”错误变成人物出镜镜头，并增加画面文字/OCR 风险。
- 根因：`reference_registry` 虽用于全局审计，但渲染时把 registry 中所有图都传给了每个 shot，包括 `@mina` 人物图和 `@usage` 产品使用图；模型收到人物锚点后，在不需要人物的镜头也补人。与此同时，旧 checkpoint 复用只看 `shot_fingerprint`，参考图提交策略变化后仍可能复用被污染的旧图；继续实测又发现 `download_first_image()` 自身有“目标图片存在即 skipped”的底层短路，上层即使判定旧图失效，仍会被旧文件挡住。第二层根因是 `shot_prompt()` 对 `characters: []` 只写了 “none / product only”，但已确认中文导演提示词和生活方式场景语义仍可能诱导模型补完整人物。
- 影响：故事板确认层会错误展示未按剧本执行的镜头；后续逐格展开和视频生成也可能继承人物、手部或文字残留，破坏“严格按 shot ref_tags 绑定素材”的合同。
- 临时修复：新增 `shot_reference_registry()` 按镜头过滤实际提交素材：只传当前 shot `ref_tags` 对应图，`@usage` 只在有人物、手部、贴合、吸附、支架、手机背面等真实使用动作镜头中附加；产品类型里的 `magnetic wireless speaker` 不再单独触发使用图；无人物纯产品镜头不再收到 `@mina`。新增带策略版本的 `reference_fingerprint()` 并写入每个 shot 结果，`_existing_shot_matches_plan()` 同时校验参考图指纹，参考图策略或素材变化后旧故事板自动失效重生。`download_first_image()` 新增 `force=True`，当上层判定旧图不可信时即使文件存在也必须重新提交，且不复用旧 task id。`shot_prompt()` 在末尾为 `characters: []` 追加无人物硬约束，禁止脸、头、身体、坐姿人物、模特或完整人物；仅当动作明确需要时允许裁切手部。
- 状态：已处理代码；新增回归测试覆盖无人物镜头不传人物图、使用动作镜头附加 `@usage`、Mina 镜头显式传 `@mina`、参考图指纹变化时禁止复用旧故事板、`force=True` 可重生已存在图片，以及无人物硬约束位于已确认中文导演提示词之后。

### INC-032：视频提示词审核把人物连续性带入无人物镜头

- 现象：故事板确认后进入视频提示词预检时，纯产品镜头虽然写了“本镜头没有人物出镜”，但同一份 prompt 的全局连续性仍包含 Mina 的身份、发型、服装等人物描述。
- 根因：`prompt_review.py` 的视频和故事板审核基座都直接使用整份 plan 的 `continuity`，没有按 shot 的 `characters[]` 做作用域过滤；无人物镜头因此同时收到“不要人物”和“保持 Mina 一致”的冲突指令。
- 影响：视频模型可能在产品特写、支架展示、功能证明等无人物镜头中补出完整人物，或把产品镜头错误理解为 Mina 出镜镜头，抵消了故事板阶段刚修复的参考图过滤。
- 临时修复：新增 `_continuity_for_shot()`，当 shot 没有角色时过滤 `character_identity`、`wardrobe`、`makeup`、`hair` 等人物连续性字段；`_shot_character_rule()` 对无人物镜头明确禁止人物、脸、头、身体、模特和完整人像，仅在真实交互动作需要时允许裁切手部/手指。
- 状态：已处理；新增回归测试覆盖无人物视频提示词不含 Mina 身份/服装、有人物镜头继续保留人物连续性。

### INC-033：故事板运行时增强字段导致正式视频分段误判过期

- 现象：客户确认故事板后，正式执行 `script_splitter.py split --manifest ...` 被阻断：`STALE_STORYBOARD: storyboard plan fingerprint 已过期`。检查发现 `storyboard_plan.json` 本身没有改动，客户确认的故事板图片仍是当前文件。
- 根因：故事板生成时会把 `approved_prompt_zh`、`panel_plan`、`references` 等运行时增强字段写入 `storyboard_result.json` 的 shot；视频分段器拿原始 `storyboard_plan.json` 的 shot 与结果 shot 做整段指纹比较，把“生成时补强字段存在”误判为“作者视觉计划已修改”。
- 影响：用户已经确认的故事板无法进入正式 video handoff；若为绕过而使用 draft，会破坏正式确认链路和后续 OCR/验片闭环。
- 临时修复：`_stale_storyboard_shot_ids()` 改为比较作者视觉合同字段，只检查 `visual/characters/ref_tags/scene/props/shot_size/lighting/character_action` 等源计划字段是否变化，忽略运行时注入字段；真实视觉字段改动仍会判 stale。
- 状态：已处理；新增回归测试覆盖运行时增强字段不触发 stale、作者视觉字段改动仍触发 stale。

### INC-034：故事板转视频 handoff 单测漏 mock 模型目录导致离线卡住

- 现象：运行 `tests.test_storyboard_render_handoff` 时，测试进入 `video_engine._pick_video_model()` 后实际请求 `/employee/models`，在离线/沙盒网络下长时间等待，直到人工中断。
- 根因：用例已经 mock 了 `create_video`，但视频提交前会先查询实时模型目录；该测试没有 mock `_model_catalog/_available_models_set`。
- 影响：回归测试不再是纯本地，真实流程中为了验证一个 handoff stale 规则会被无关网络请求拖住。
- 临时修复：测试中补齐 `_model_catalog` 与 `_available_models_set` mock，保持只验证 storyboard stale 阻断逻辑。
- 状态：已处理；相关测试可离线快速完成。

### INC-035：视频生成虽为异步但默认仍走旧版 createVideo 路径

- 现象：根据 BasicRouter 当前文档，视频生成应 `POST /v1/video-generations` 返回 `taskId`，再 `GET /v1/video-generations/{taskId}` 轮询；检查代码发现上层编排已是异步 taskId，但 `br_client.create_video()` 默认仍走 `/ai/createVideo`，查询默认仍走 `/ai/getVideoByTaskId`，并使用旧字段 `urls`。
- 根因：图片生成已迁到当前 `/v1/image-generations` 异步 retrieve 口径，但视频客户端保留了历史 legacy 默认；虽然语义上仍异步，接口路径和字段名与当前官方文档不一致。
- 影响：后续若 BasicRouter 收紧旧入口或只按 v1 文档维护能力字段，视频提交可能失败，或出现图片/视频两套协议不一致导致的排查成本。
- 临时修复：`create_video()` 默认改为 `POST /v1/video-generations`，参考图字段改为 `imageUrls`，视频延长字段改为 `videoUrls`；`get_video()` 改为 `GET /v1/video-generations/{taskId}`。上层 `video_engine.render_batch()` 的“先全部提交 taskId，再统一轮询并下载”逻辑保持不变。
- 状态：已处理；更新回归测试确保视频创建使用 v1 endpoint，显式 request id 继续透传，延长视频使用 `videoUrls`。

### INC-036：视频分段器把逐镜头故事板重新聚合并切断台词

- 现象：Momax 已确认 5 个 storyboard shots 后，正式 `script_splitter.py split` 只生成 2 个视频段，并把 `TWS` 从中间切成第 1 段末尾的 `TW` 和第 2 段开头的 `S`。
- 根因：视频分段器仍用通用 `partition_shots()` 按模型时长上限打包多个短镜头；该函数为拆长镜头设计了按字符比例切台词逻辑，一旦聚合/切分边界落在英文缩写中间，就会破坏口播文本。当前规则要求故事板转视频逐 shot 独立生成，打包逻辑已经不适用于产品展示分镜。
- 影响：实际提交视频时会念错词、口型错位，并且一个视频任务收到多个不同镜头的参考图/动作合同，削弱逐格锚定的一致性。
- 临时修复：`script_splitter.split()` 对 storyboard-to-video 默认 `preserve_shots=True`，每个已确认 shot 成为独立视频任务；单个 shot 超过模型时长上限时 fail-closed，要求脚本阶段拆镜，而不是自动切字。
- 状态：已处理；新增回归测试确保产品展示 5 个 shot 输出 5 个视频段，`TWS` 保持完整。

### INC-037：视频提示词预览触发模型目录网络请求

- 现象：生成视频提交提示词预览时，只调用本地 `_submission_text()` 也会卡在 `/employee/models` 网络请求，离线环境需要人工中断。
- 根因：`_submission_text()` 调用 `_compile_seedance_text()`，后者用 `_is_kling_video_model()` 判断模型；该布尔判断内部会查询实时模型目录来解析 alias，导致一个本地预览动作触网。
- 影响：视频生成前确认闸门变慢且不稳定；客户可能在还没提交付费任务前就遇到网络重试等待。
- 临时修复：`_is_kling_video_model()` 改为纯本地字符串判断，模型目录解析继续由模型选择阶段负责；提示词预览不再触网。
- 状态：已处理；新增回归测试确保 Kling 判断不查询 catalog，预览路径可离线运行。

### INC-038：视频提示词压缩兜底错误硬编码灰色产品

- 现象：生成视频提交提示词确认稿时，5 段 prompt 的 `Product` 约束出现 `neutral grey color`，与已确认的黄色/马卡龙 Momax 1-Vibe Go Lite 产品不一致。
- 根因：`_submission_text()` 在故事板规则较长时会进入 `_fit_video_prompt_limit()` 压缩兜底；该兜底模板为了保留产品锁写了通用英文句 `neutral grey color`，没有从当前 segment 的中文导演文本或已确认产品参考中继承真实颜色/系列信息。
- 影响：即使产品图和故事板都正确，视频模型仍可能被最终提交文本诱导把黄色马卡龙产品改成灰色；这会绕过前面产品板、使用图、故事板三道确认闸门。
- 临时修复：压缩兜底改为保留 `Director text` 中的完整中文镜头文本，并把产品锁改为“严格保持上传参考里的产品结构、比例、马卡龙颜色、网罩、按钮、磁吸结构、端口和原生标识；不得重新上色、重新设计或替换产品”。同步修复 `video_engine.py` 与共享 `video_prompts.py`。
- 状态：已处理；新增回归测试确保压缩后保留黄色 Momax 导演文本、包含 macaron color / do not recolor，并且不再出现 `neutral grey`。

### INC-039：视频提交未区分 Seedance 原生故事板与 Kling 单镜头兜底

- 现象：视频生成前的故事板策略曾在两个方向上摇摆：先是把 Kling 也当成能理解整张多格故事板，后来又把 Seedance 也强制改成单格展开。两种都会破坏“Seedance 优先故事板，Kling 不可用时兜底”的模型分工。
- 根因：代码没有把“模型能力层”和“业务提交层”分开：Seedance 具备原生 storyboard/contact sheet 理解能力，应优先接收确认故事板并读取前后镜头关系；Kling 不支持故事板设计时，才需要先由 gpt-image-2 生成当前镜头的 16:9 单格展开图。
- 影响：若 Kling 接收整张故事板，可能生成网格、分屏、错格或素描残留；若 Seedance 被强制单格展开，则会丢掉多格故事板的前后节奏、构图递进和镜头连贯性，降低首选模型的优势。
- 临时修复：`storyboard_ref_mode` 默认恢复为 `native_storyboard`。`video_engine` 按目标模型动态分流：Seedance 提交确认故事板/contact sheet + 当前 shot 的确认素材，并使用 `Seedance-native storyboard` 规则；Kling fallback 自动准备单镜头展开图，验证 `recipe_sha256` 后提交 `SINGLE 16:9 reference plate` + 当前 shot 的确认素材。
- 单镜头正确性补强：Kling 展开图不做像素裁剪；4 图预算优先 `@usage`、人物锚点、产品主图，低优先级副角度明确记录为 omitted；最终提示词禁止让 Kling 推断其他格。Seedance 路径则明确只执行当前 `segment/panel_index`，但允许读取整张故事板里的前后关系来保证连贯。
- 状态：已处理；回归测试覆盖 Seedance native 不触发展开图、Kling fallback 必须使用单格展开图，以及两类提示词规则不能混用。

### INC-040：仅调整口播时长却使故事板与审批链整体过期

- 现象：实测发现 s2/s3/s4/s5 的原台词无法在 5/5/7/4 秒内自然讲完；把时长改为 6/6/8/7 秒后，`script_splitter.py split` 被 `STALE_STORYBOARD` 和阶段审批依赖阻断，尽管镜头构图、人物、产品、动作与确认故事板均未变化。
- 根因：故事板视觉指纹错误地把 `duration/seconds` 当成作者视觉字段；同时 plan 文件 SHA 变化会使 manifest 的下游审批失效，缺少“非视觉时长修订”的恢复路径。
- 影响：为了修正口播节奏，用户被迫重新确认没有发生视觉变化的产品使用图和故事板；若跳过警告继续生成，则会造成台词过快、口型和停顿不自然。
- 临时修复：本轮时长调整为 4/6/6/8/7 秒，总时长 31 秒；视觉 stale 比较排除 `duration/seconds`，时长变化仍会进入新 segments 和视频 handoff 指纹；随后按 manifest 的正式阶段顺序重新登记并恢复审批链。
- 状态：已处理；新增回归测试证明仅改时长不会误报故事板视觉过期，重新拆分后无 dialogue-fit 警告。

### INC-041：视频比例继承被旧 `video_aspect_ratio` 带偏

- 现象：确认后的 `render_plan.json` 写明成片为 16:9，但重新捕获正式视频提示词时发现 Seedance 基础提示词仍包含 `画幅：9:16`；检查 segments 也显示 5 段全部为 `ratio=9:16`。
- 根因：`storyboard_plan.json` 同时存在 `aspect_ratio=16:9` 和历史残留 `video_aspect_ratio=9:16`；`script_splitter` 只从通用 ratio helper 取值，没有让已确认的 `render_plan.ratio` 覆盖旧草稿字段。
- 影响：正式视频可能按竖屏构图生成，再被横版交付链路裁切或缩放，导致产品位置、人物 CTA 留白和后期动效安全区全部偏移。
- 临时修复：`output_ratio()` 接受 `video_aspect_ratio` 作为无 render plan 时的旧字段兼容；`script_splitter` 在存在已确认 render plan 时优先使用 `render_plan.ratio`，确保当前 Momax 5 段重新编译为 16:9。
- 状态：已处理；新增回归测试覆盖旧 alias 兼容和 confirmed render plan 覆盖 stale video alias。

### INC-042：多片段缺少音画连续性文字合同

- 现象：Momax 5 段 segments 的 `continuity_in/out` 曾全部为 `{}`，音频也只有每段自己的台词/BGM/SFX，没有“上一段如何收尾、本段如何承接、下一段如何剪入”的显式合同。每段虽然有同一批产品/人物参考图，但并行 batch 生成时仍可能出现剪辑断层。
- 根因：`storyboard_enhancements.inject_continuity()` 只把结构化 `planned_start_state/planned_end_state` 注入到 `continuity_in/out`；旧计划没有这些字段时会写成空 JSON，而 `script_splitter` 没有从镜头景别、机位、构图、动作、ref_tags 自动生成兜底衔接描述。
- 影响：Seedance native 虽可读取故事板前后关系，但仍需要文字合同锁住具体剪辑状态；Kling 单格回退更依赖这些合同，否则拼接后可能出现产品位置、镜头方向、光线节奏、人物动作、音色、BGM 节拍或音效强度的轻微跳变。
- 临时修复：`script_splitter` 在正式 handoff 前为缺失或空 `{}` 的 `continuity_in/out` 自动生成视觉段间合同：首段锁定故事板建立的起始状态，中间段承接上一段状态并预告下一段自然剪辑关系，末段写清稳定收尾状态。同步为 `audio_contract` 注入 `voice_continuity`、`bgm_continuity`、`sfx_continuity`：普通话女声保持同一音色/响度/语速，BGM 从上一段节拍和情绪自然延续，音效只做动作点缀且不盖过人声。显式作者 continuity 仍优先保留。
- 状态：已处理；新增回归测试覆盖产品展示多段即使没有 `planned_*_state` 也会生成非空视觉连续性合同，并为 required audio 段注入声音、BGM、SFX 连续性合同。

### INC-043：串联模式与单段 fallback 未复用 Seedance/Kling 故事板分流

- 现象：继续审计视频生成路径时发现，`render_batch` 已按目标模型分流故事板素材，但 `render_chained` 仍在选模型前无条件调用单格展开；这会让 Seedance 串联模式也绕过原生故事板能力。另一个单段入口 `render()` 在 Seedance 隐私拒绝后回落 Kling 时，如果网关返回的是 provider id（例如 `provider/kling`），提示词编译器没有识别为 Kling，第二次提交仍是原始剧本。
- 根因：故事板素材准备被散落在 batch、chain、single 三条入口里，早期修复只覆盖了并行 batch 主路径；Kling 模型判断又只认 `kling-v3-omni` 字面名，没有覆盖网关 canonical/provider id。
- 影响：用户选择 `--chain` 追求跨段画面连续时，Seedance 不能读取整张故事板的前后关系；如果远端隐私策略或模型可用性触发 fallback，Kling 可能沿用错误的故事板素材或未重编译的提示词，导致回退路径与正式确认稿不一致。
- 临时修复：`render_chained` 改为先按片段、尾帧、锁定参考图确定候选 videoType，再选择模型，最后调用统一的 `_prepare_storyboard_submission_for_model()`：Seedance 使用 native storyboard/contact sheet，Kling 使用单格展开图。chain fallback 再次重算 refs；尾帧 + 故事板素材超过 4 张时优先保留尾帧与高优先级锚定图，并把低优先级遗漏写入 segment。`_is_kling_video_model()` 放宽为本地识别所有包含 `kling` 的 id，保持离线、不触网。
- 状态：已处理；新增回归测试覆盖 `render_chained` 的 Seedance native 不展开、Seedance privacy fallback 到 Kling 后重建 panel refs，以及单段 fallback 重编译 Kling 提示词。

### INC-044：协议文档仍写旧的全模型单格展开逻辑

- 现象：代码已修正为“Seedance 原生故事板优先，Kling 单格展开兜底”，但继续审计发现 `README.md`、`AGENTS.md`、`video_engine.py --storyboard-ref` help 和 `output/storyboard_to_video_reference_binding_plan.md` 仍保留旧说法，例如“12 格故事板转视频说明”“提交前先生成单格 16:9 展开图”“故事板不再作为视频主要参考图”。这些文字会诱导后续 Agent 把 Seedance native 逻辑重新改回全模型单格展开。
- 根因：前几轮快速修复集中在执行代码和测试，项目级协议文档没有同步更新；而本项目的 Agent 会读取 `AGENTS.md` 作为公共流程协议，文档漂移本身会变成下一轮实现漂移的根源。
- 影响：即使当前测试通过，后续维护者或其他宿主 Agent 仍可能按旧文档执行，导致 Seedance 不能利用故事板能力、Kling 误接整张故事板，或者 BasicRouter 图片生成继续被描述成旧同步 `/ai/createImage`。
- 临时修复：更新 `AGENTS.md` 铁律 5a、`README.md`、`CUSTOMER_GUIDE.md`、`START_HERE_CODEX_DESKTOP.md`、`references/professional-storyboard-enrichment.md`、`project-capabilities.html`、相关 story/UGC skills、`video_engine.py --storyboard-ref` help 和绑定方案文档：统一表述为 Seedance native storyboard/contact sheet 优先；Seedance 不可用、隐私拒绝或能力不足时，Kling fallback 才生成单格展开图。同步修正产品板/素材生成说明为异步 `/v1/image-generations`，BasicRouter API skill 改为 `/v1/image-generations` 与 `/v1/video-generations` 的 submit/retrieve 结构。
- 代码提示词补强：`prompt_review._base_storyboard_prompt()` 不再要求 “16:9 横版 4x3 12 格故事板”，改为格数由已确认 `shots[]` 决定，且禁止为凑固定 12 格而拆分或合并镜头。
- 测试流程补强：`test_storyboard_panel_binding.py` 已加入 smoke，确保默认快速冒烟也覆盖 Seedance/Kling 分流协议；smoke 下限同步更新为至少 18 个文件、300 个用例。首次阈值误设为 315 导致 18 文件/307 用例全 OK 后仍自阻断，已修正为 300。
- 状态：已处理；新增静态回归测试覆盖 README/AGENTS/客户指南/启动说明/专业参考/CLI help/API skill 的 Seedance/Kling 分流与异步 v1 接口描述，并用 `rg` 确认旧关键误导文案和旧 `/ai/createImage`、`/ai/createVideo` 文档入口已不存在。

### INC-045：全量测试暴露 smoke 未覆盖的故事板规则与旧断言漂移

- 现象：扩展到 `bash tests/run_tests.sh all` 后，63 个测试文件中 5 个文件失败：故事板转视频规则缺少标注颜色只读说明与 “NEVER render the video as a sketch” 强约束；formal Kling 单格展开入口在应阻断未确认 panel 时，先尝试读取测试假图；音频 contract 测试仍假设没有 voice/BGM/SFX 连续性字段；非口播长计划测试仍假设会按 15 秒重新打包为 `[15,5]`。
- 根因：前几轮 smoke 主要覆盖主流程，未覆盖故事板规则细节、panel QA/approval contract、模型目录音频契约和旧视频分段测试；实现已经向“逐 shot 独立 + 音画连续性 + Seedance/Kling 分流”演进，但部分规则文案和测试断言没同步。
- 影响：如果只看 smoke，会漏掉正式 Kling fallback 可以绕过 panel QA/客户确认的风险，也会让故事板视频模型重新渲染素描/箭头/面板标注的负约束变弱。
- 临时修复：`NATIVE_STORYBOARD_VIDEO_RULES`、`PANEL_REFERENCE_VIDEO_RULES`、`video_prompts.py` 和 `seedance_prompt.py` 补回标注颜色只读、禁止箭头/标签、禁止素描/铅笔/故事板格子成片的强约束；`_prepare_storyboard_panel_submission(formal=True)` 先校验 `storyboard_panel_approval.status=confirmed`、`panel_quality.pass` 和 panel 字节指纹，再允许正式 Kling 单格提交；更新音频测试为原始字段保真 + 连续性字段必有；更新非口播分段测试为保留 shot 边界 `[10,10]`。
- 状态：已处理；失败 5 文件定向重跑 33 个用例全部 OK。

### INC-046：长视频提示词压缩分支弱化故事板成片约束

- 现象：重新检查 Momax 正式视频提示词预览时发现 5 段均已走 Seedance 原生故事板，且有视觉与音频连续性合同；但由于完整提示词超过 BasicRouter 2500 字符限制，`_fit_video_prompt_limit()` 会进入压缩分支，压缩后的 storyboard summary 只写了“never render grid, sketch, text, labels or watermark”，没有保留“标注颜色/箭头只读”和“NEVER render the video as a sketch/pencil/charcoal/storyboard panel/animatic/grayscale previs”的完整强约束。
- 根因：主规则 `NATIVE_STORYBOARD_VIDEO_RULES` 与 `PANEL_REFERENCE_VIDEO_RULES` 已补强，但压缩摘要是另一套手写文案；长镜头越复杂越容易进入压缩路径，反而更容易丢掉最需要的保护语。
- 影响：Seedance native 可能正确读取整张故事板，但把黑白预演、箭头、标注或面板边框误当作画面风格生成进成片；Kling fallback 的单格展开图同样可能把 QA 标注或故事板风格带入正式视频。
- 临时修复：同步补强 `video_engine.py` 和 `video_prompts.py` 的压缩摘要：Seedance native 与 Kling expanded panel 两条分支都明确写入“annotation colors/arrows/marks are reading-only”、“Do NOT render any arrows/marks/labels/notes/grid/storyboard border”和“NEVER render the video as a sketch/pencil/charcoal/storyboard panel/animatic/grayscale previs”。更新 `test_video_model_alias.py` 覆盖 Seedance 压缩摘要与 Kling 压缩摘要。
- 状态：已处理；`test_video_model_alias.py` 已覆盖 Seedance/Kling 压缩摘要强约束。重新捕获 Momax 5 段视频提示词预览，状态仍为 `pending`，模型均为 `seedance-2.0`，5/5 段均包含 Seedance native、禁标注、禁线稿和 Audio continuity 约束。

### INC-047：正式视频入口存在提示词确认绕过路径

- 现象：继续审计正式视频生成前闸门时发现，batch CLI 会要求 `--prompt-review`，但函数级 `render_batch()` / `render_chained()` 只在调用者传入 `prompt_review` 时才校验；调用者若忘传该参数，在满足 manifest/client/results_out 后可能继续走正式视频流程。单段正式 CLI 也只检查 `--client/--manifest/--results-out`，最终调用 `render_batch([segment])` 时没有传入 `prompt_review`。
- 根因：确认闸门主要落在 CLI batch 分支，公共渲染函数和 single formal 分支没有把“正式生成必须有已确认视频提示词审核文件”作为自身不变量；同时确认文件中的 `submission_prompt_zh` 没有被保留到 segment，正式提交会重新编译文本，存在确认稿与实际提交稿漂移风险。
- 影响：后续 pipeline 或其它宿主 Agent 若直接调用函数入口，可能绕过客户确认直接触发付费视频；单段正式出片也可能不使用用户看过的完整提交提示词。
- 临时修复：`render_batch()` 与 `render_chained()` 在正式模式下先要求完整 manifest/client/results_out，再强制要求 `prompt_review` 并校验 confirmed 状态；单段正式 CLI 也要求 `--prompt-review`，并向内部 `render_batch()` 透传。`_require_confirmed_prompt_review()` 同时注入 `approved_submission_prompt_zh` 与 `approved_prompt_model`；`_submission_text()` 在模型一致时优先使用用户确认过的完整 `submission_prompt_zh`，模型 fallback 到 Kling 时重新编译对应模型提示词。
- 补充修复：`video_prompts.py` 是从 `video_engine.py` 拆出的共享提示词模块，虽然当前主流程未直接调用，但仍保留旧闸门逻辑：只注入 `prompt_zh`、丢弃 `submission_prompt_zh`，且存在 `approved_prompt_zh` 时不会再包故事板规则。已同步对齐，防止后续重构重新引入确认稿/实际提交稿漂移。
- 状态：已处理；新增回归测试覆盖函数级 batch/chain 缺 `prompt_review` 阻断、单段正式 CLI 缺 `--prompt-review` 阻断、确认后的完整提交提示词优先使用、fallback 模型不误用 Seedance-only 提示词，以及共享 `video_prompts.py` 的闸门与故事板包装一致性。

### INC-048：Seedance 已确认提示词未覆盖 Kling fallback 文本

- 现象：Momax 视频提示词预览文件只包含 `seedance-2.0` 的 `submission_prompt_zh`。如果正式生成时 Seedance 不可用、模型不存在或真人参考图触发隐私拒绝，系统会 fallback 到 Kling；Kling 路径使用“单镜头展开图”规则，完整提交提示词与 Seedance 原生故事板提示词不同，但这份 Kling 提交文本没有提前展示给客户确认。
- 根因：`prompt_review.py capture-video` 只捕获主模型文本；`_submission_text()` 在模型不一致时会重新编译目标模型文本，而确认闸门只证明客户确认过主模型文本，没证明 fallback 模型文本也被确认。
- 影响：严格确认闸门下，fallback 可能提交客户未看过的 Kling 完整提示词；同时 Kling fallback 在生成单格展开图前才暴露该问题，会造成额外返工甚至不必要的图像生成成本。
- 临时修复：视频提示词确认文件新增 `model_submission_prompts` 与 `fallback_submission_prompts`。Seedance 主路径会同时捕获 Seedance native storyboard 提交文本与 Kling `SINGLE 16:9 reference plate` fallback 提交文本；预览 Markdown 会显示主模型完整提交提示词和 fallback 完整提交提示词。`_require_confirmed_prompt_review()` 注入按模型索引的确认文本；`_submission_text()` 按模型族（seedance/kling）选择已确认文本；正式 batch 在初始选模和 fallback 前调用 `_assert_confirmed_submission_for_model()`，缺少目标模型确认文本时 fail-closed，不先生成 fallback 单格图也不提交视频。
- 状态：已处理；重新捕获 Momax 5 段视频提示词预览，状态仍为 `pending`，5/5 段均包含 Seedance 与 Kling 两套完整提交提示词，两套均包含 Audio continuity 与禁标注/禁线稿约束。

### INC-049：音频/BGM 连续性被误表述为强技术保证

- 现象：继续准备正式出片时，发现多段视频的 `audio_contract` 只写了“承接上一段普通话女声”“BGM 从上一段节拍和情绪自然延续”，但没有说明这是提示词合同、媒体参考还是后期混音。用户追问“是否上传前一段视频/音频，还是只靠提示词”，暴露当前方案容易被误解为模型内部已经确定锁住声音和 BGM。
- 根因：BasicRouter 当前 v1 视频封装只公开提交 `imageUrls`，并在视频延长时使用 `videoUrls`；项目接入层没有公开可用的 `audioUrls`/音频参考字段。`--chain` 可以抽上一段尾帧增强视觉连续或使用 `videoUrls` 做视频延长，但不能等价为“上传上一段音频并锁定同一声线/BGM”。音频连续性此前混在自然语言 prompt 中，没有字段化标明方法与保证边界。
- 影响：若按旧表述直接生成，用户可能误以为声音、BGM 与画面一样有参考媒体锚定；生成后若声音换音色、BGM 重置或段间突兀，会变成流程承诺与实际能力不一致的问题。
- 临时修复：`script_splitter` 为每段 `audio_contract` 注入方法字段：`voice_continuity_method=text_contract_and_human_qc`、`bgm_continuity_method=post_mix_preferred`、`sfx_continuity_method=text_contract_and_human_qc`、`media_reference_method=basicrouter_video_v1_has_no_public_audio_reference_field`。`seedance_prompt.py`、`video_engine.py` 与 `video_prompts.py` 会把这些方法写进完整提交提示词；Momax 当前 `momax_video_segments.json` 与 `prompt_review_video.json` 已重新捕获，Seedance 主路径和 Kling fallback 提示词均显示该边界。
- 后续执行规则：BGM 连续性以本地后期统一混音为确定性方案，模型内 BGM 只作为临时氛围参考；声线连续性以同一语言/人设/语速/响度提示词 + 人工 QC + 必要时重生单段为保证方式。若未来 BasicRouter 明确暴露音频参考字段，再升级为媒体参考锁定，但不得提前声称已上传前段音频。
- 状态：已处理；`test_video_effect_qc.py` 与 `test_video_model_catalog_audio_contract.py` 覆盖音频连续性方法字段、媒体参考边界和预检阻断。当前视频提示词审核仍为 `pending`，客户确认前禁止付费生成。

### INC-050：磁吸关系修正后暴露 stale 分镜与 split 原子性问题

- 现象：继续生成前复核 Momax 第 2 段提示词时发现，虽然用户早已纠正“音响底部磁吸到手机背面”，但当前 `storyboard_plan.json` / `momax_video_segments.json` / `video_submission_prompt_preview.md` 仍有“产品背部贴合手机或金属表面”的旧描述。修正源计划后重新执行正式 `script_splitter.py split`，manifest 正确阻断（脚本变更导致 storyboard/render_plan/product_usage 审批失效），但旧 `momax_video_segments.json` 已被部分覆盖成 4 段，缺失 `s2_magnetic_snap`。
- 根因：第 2 段产品使用关系在脚本源头没有足够硬的正向/负向约束；同时 `script_splitter.py split` 在正式 manifest gate 之前先写出 `--out`，导致后续 gate 失败时仍污染下游 handoff 文件。脚本修正后旧 `shot_02_s2_magnetic_snap.jpg` 的分镜指纹不再匹配，新合同不能继续复用旧分镜图伪装为已确认。
- 影响：若继续出片，可能少生成第 2 段，或用“产品背部贴手机”的旧提示词生成错误产品使用关系；更糟的是，失败后的半成品 segments 可能被下游误认为最新正式 handoff。
- 临时修复：源计划第 2 段改为“音响底部磁吸面贴合手机背面或金属表面”，并明确不得画成产品背部、侧面或整块背壳贴合。新增 `video_effect_qc.py`，生成前检查 prompt review、Seedance/Kling 提交合同、音频方法、磁吸底部贴手机背面、manifest gate；生成后要求 results、OCR/media QC 和人工/视觉复核项齐全；preflight 失败时会输出 `next_actions` 和可执行命令，并标明哪些步骤会触发付费出图。`script_splitter.py split` 的正式 identity/generation gate 移到写出 `--out` 之前，且正式 handoff 对 `missing_images` 也 fail-closed；新增 `test_split_atomicity.py` 防止 gate 失败污染旧 segments。
- 当前状态：代码层问题已处理；旧 preflight 的未就绪状态属于事故发生时的历史快照。当前 r09 已完成五段 handoff、Seedance 视频提示词确认、重拍 take 接受和最终成片验收，不再使用旧 4 段半成品。

### INC-051：故事板提示词捕获噪音与产品使用图引用范围过宽

- 现象：为重新生成第 2 段故事板做非付费提示词捕获时，`capture-storyboard --preview-out` 仍把完整 JSON 和数万字提示词打印到终端，客户确认体验很差；同时检查捕获稿发现，plan 级 `product_usage_images` 可能被注入纯产品静物镜头，导致不需要手部操作的开场镜头也继承“产品使用九宫格”约束，增加产品造型漂移和错误使用关系的风险。修复 QC 后还发现 `magnetic_bottom_to_phone_back_contract` 会因产品开场/卖点提示词里出现“磁吸手机背面”而误伤非贴合动作镜头。
- 根因：`prompt_review.py` 的 `capture-video` / `capture-storyboard` 分支没有复用 preview 命令的短输出协议；`storyboard.shot_prompt()` 读取 plan 级使用图时没有先判断该 shot 是否真的需要产品使用参考；`video_effect_qc.py` 的提示词层磁吸检查没有以 segment 剧本动作作为前置条件。
- 影响：非付费确认步骤会在对话里输出过量内容；纯产品镜头可能错误拿到产品使用图，影响产品外观锁定；视频预检可能被无关镜头阻断，掩盖真正阻断项（当前真实阻断是缺失 `s2_magnetic_snap`、manifest gate 未通过、视频提示词 pending）。
- 临时修复：`capture-video` / `capture-storyboard` 在传入 `--preview-out` 时只打印短 JSON 摘要，完整提示词只写入 Markdown/JSON 文件。`storyboard.shot_prompt()` 仅当 shot 有人物或明确手部/贴合/支架/手机背/接触动作时注入 `product_usage_images` 和 `[PRODUCT-IN-USE NINE-PANEL BOARD]`；纯产品镜头只引用产品板。`video_effect_qc.py` 将磁吸检查收紧为“segment 剧本本身是磁吸贴合手机背面的动作镜头”时才要求提示词包含“底部/bottom”并禁止“产品背部贴合”。
- 状态：已处理；新增回归测试覆盖捕获命令短输出、产品使用图只进入真实使用镜头、产品开场卖点不误触磁吸贴合合同。已重新捕获 Momax 故事板提示词预览：`s1_visual_hook` 不再带产品使用图约束，`s2_magnetic_snap` 保留底部磁吸到手机背面约束。

### INC-052：预检恢复建议会误导确认旧视频提示词且缺少 manifest 审批链

- 现象：Momax 当前 `momax_video_segments.json` 明确 `missing_images=["s2_magnetic_snap"]`，且实际 segments 只有 4 段；但 `video_effect_qc.py` 的 `next_actions` 仍给出 `CONFIRM_VIDEO_PROMPT_REVIEW`，可能让用户确认一份旧 handoff/旧视频提示词。同时 `manifest_video_gate` 失败的恢复命令只包含 `storyboard.py --confirm` 和 `script_splitter.py split`，没有重新登记/确认已变更的 `storyboard_plan.json` 和受上游指纹影响的 `cast_board/product_usage/storyboard/render_plan` 阶段。
- 根因：QC 只检查了现有 segments 是否被 prompt review 覆盖，没有把 `segments.missing_images/needs_image` 本身作为正式 handoff 阻断；恢复建议没有区分“提示词 pending 但 handoff 完整”和“handoff 已过期/缺段”的两种状态；manifest gate 的修复命令未表达 run_manifest 的 strict approval 顺序。
- 影响：用户可能按提示确认旧的 4 段视频提示词，随后继续被 split/video gate 阻断，或者更糟糕地制造“看似已确认、实际缺第 2 段”的状态；即使重生故事板，缺少 manifest finish/approve 链也会导致正式 split 继续失败。
- 临时修复：`video_effect_qc.py` 新增 `segments_no_declared_missing_images`，正式 preflight 遇到 `missing_images/needs_image` 直接 fail-closed；在提供 `storyboard_plan` 时，视频提示词审核必须覆盖完整 plan shot 集。`next_actions` 若发现 stale handoff，则不再输出 `CONFIRM_VIDEO_PROMPT_REVIEW`，改为要求 `RECAPTURE_VIDEO_PROMPTS` 且标注依赖 `REFRESH_APPROVAL_CHAIN`。`REFRESH_APPROVAL_CHAIN` 补齐 `run_manifest.py finish-stage/approve` 命令链：script → cast_board → product_usage → storyboard → render_plan → script_splitter split。
- 状态：已处理；新增回归测试覆盖缺段 handoff 不允许确认旧视频提示词、segments 文件声明 missing_images 会阻断、manifest recovery commands 必须刷新完整审批链。旧 Momax preflight 的阻断状态已由 r09 新 handoff、重拍审批和视频审批闭环替代。

### INC-053：单 shot 故事板恢复误提交其它 shot 并清空结果记录

- 现象：用户授权先生成 `s2_magnetic_snap` 后，按 preflight 恢复命令运行 `storyboard.py --stage storyboard`，脚本因为 plan fingerprint 已变化而进入 “starting clean”，先提交了 `s1_visual_hook` 的 gpt-image-2 任务，并把 `storyboard_result.json` 写成 `shots=[] + in_progress=s1`。这不是用户授权的目标，也污染了当前 run 的故事板结果记录。
- 根因：`storyboard.py` 只有整阶段故事板生成入口，没有单 shot 修复入口；`resolve_run_output_dir()` 在 plan 改动时会自动开新 revision 或清空旧结果，而 preflight 明明知道缺失的是具体 shot id，却仍输出整阶段命令。
- 影响：一次只想补 `s2` 的恢复操作可能额外提交其它付费图片任务，并让旧的 5 张故事板记录短暂变成空集合；后续 split/preflight 会把这次恢复误判为更严重的故事板缺失。
- 修复：新增 `storyboard.py --only-shot <shot_id>`，仅允许配合 `--stage storyboard` 使用。单 shot 模式固定回原 `--run-id` 目录，不自动开新 revision；目标 shot 强制重生，其它 shot 从现有 result 或 `.storyboard_confirmed.json` 带回并标记 `carried_forward`，避免清空完整 handoff。每个 shot 结果强制写入顶层 `id`，避免后续 QC/确认层只能从嵌套 `shot.id` 猜测。`video_effect_qc.py` 的 `REGENERATE_STALE_STORYBOARD_SHOTS` 命令自动追加缺失 shot 的 `--only-shot` 参数；若当前 `storyboard_result.json` 已覆盖完整 plan 且文件存在，则恢复建议切换为 `CONFIRM_REGENERATED_STORYBOARD`（非付费），不再提示重复生成。`prompt_review.py confirm` 也改为短 JSON 输出，避免确认长提示词时刷屏。
- 状态：已处理；新增回归测试覆盖 only-shot 只调用一次生成并沿用其它已确认 shot、shot 结果顶层 `id` 写回、preflight 恢复命令包含 `--only-shot`、已生成待确认时不再提示重生、prompt review confirm 不再输出完整长提示词。本次实测已用 `--only-shot s2_magnetic_snap` 成功生成新故事板，结果中 5 个 shot 齐全，只有 s2 为新图，其它 4 个为 `carried_forward`；重新预检后 next action 已变为 `CONFIRM_REGENERATED_STORYBOARD` 且 `requires_paid_generation=false`。

### INC-054：视频提示词确认稿未展示 imageUrls 图片定义

- 现象：准备提交 Momax 视频生成前，确认稿只展示了每段 `text` 提交提示词，虽然文本中出现 `@product_hero/@usage/@storyboard` 等标签，但没有展示同一次视频请求会携带的 `imageUrls` 列表、标签含义、参考图角色和本地路径。用户追问“为什么提示词中没有对于图片的定义？这部分应该展示的是提交到模型的具体提示词是什么？”
- 根因：`prompt_review.py capture-video` 已能捕获主模型和 Kling fallback 的完整文本，但 prompt review artifact 没有保存 segment 的 `references/urls/storyboard_ref_mode/storyboard_panel_index`；`preview()` 也只渲染文本提示词和负向词，漏掉了模型请求的图片输入部分。实际 `video_engine` 会把图片作为独立 `imageUrls` 字段提交，和 text 是两个 payload 字段，因此之前展示的是“文本部分完整”，不是“提交包完整”。
- 影响：客户确认闸门不能证明客户看过完整提交内容；也难以及时发现图片引用是否少传、错传、标签语义是否正确，尤其会影响产品形状锁定、产品使用图和故事板构图锚定。
- 修复：`capture_video_segments()` 新增 `submission_references`、`storyboard_ref_mode`、`storyboard_panel_index` 写入 prompt review；`preview()` 在每个镜头下新增“提交图片 / imageUrls”区块，逐条展示图片 index、`@tag`、type、label、role、intent 和 url/path。重新捕获 Momax 视频确认稿后，5/5 段均包含 imageUrls 定义、主模型完整提示词和 Kling fallback 完整提示词，且无“同上”省略。
- 状态：已处理；新增 `test_video_preview_lists_submission_image_references` 与 `test_capture_video_segments_includes_kling_fallback_prompt` 断言图片引用会进入确认稿。已验证 `python3 -m unittest tests.test_prompt_review tests.test_incident_audit tests.test_video_effect_qc -v` 通过。后续 `INC-055` 发现旧确认图缺少官方 retrieve URL，当前阻断已从提示词确认变为 `video_reference_urls_are_remote`。

### INC-055：视频 imageUrls 错传本地/base64，且本地托管逻辑触发 Kling 图片生成

- 现象：客户查看 BasicRouter 后台调用日志后指出，Seedance 视频没有真正调用，后台反而出现 Kling 图片调用。继续追踪发现，视频参考图规范化时把本地文件走了 `br_client.host_image()`，该函数实际通过 `/v1/image-generations` + `kling-v3-omni-image` 做“keep unchanged”图片生成来换取 URL；后来临时改成 base64 data URL 也仍不符合视频文档。
- 根因：项目混淆了图片生成入参和视频生成入参。BasicRouter 官方文档中，`GET /v1/image-generations/{taskId}` 的 `images` 是图片 URL 数组字符串；`POST /v1/video-generations` 的 `imageUrls` 字段是“图片素材 URL”，示例均为 `https://...png/jpg`。视频阶段不能提交本地路径/base64，也不能为“上传”而重新调用图片生成模型。第二层根因是 `storyboard.download_first_image()` 在本地文件已存在时返回 `url:""`，导致确认图只保留本地路径，后续只能错误托管或错误 base64。
- 影响：已确认的产品板、人物板、产品使用图或故事板可能在视频前被“重绘托管”，破坏产品造型/人物身份/分镜一致性并产生额外图片调用；后台也无法看到预期的 Seedance 视频任务。
- 修复：按官方文档改为 fail-closed：`video_engine._video_image_ref()` 只接受 HTTP(S) URL，拒绝本地路径和 `data:`；`video_effect_qc.py` 新增 `video_reference_urls_are_remote` preflight，逐段列出不合格的 `urls` / `references.url`；`script_splitter.py` 分离本地审计路径与视频提交 URL，`storyboard_path` 继续用于本地 SHA-256 审计，新增/使用 `storyboard_url` 进入 `imageUrls`，产品板可从 `product_board_state.json.result_url` 恢复 URL；Seedance native 提交使用 `storyboard_url`，Kling 单格展开也要求使用展开图生成返回的 URL。`storyboard.py confirm-board` 现在会把 `url/task_id/request_id` 写入确认 sidecar，产品/人物资产 registry 也持久化 URL，后续阶段本地文件跳过生成时会从确认记录兜底恢复 URL。`br_client.create_video()` 同时修正模型提交名映射，内部 ID `dreamina-seedance-2-0-260128` 会提交为文档模型名 `seedance-2.0`。
- 状态：代码层已处理并有回归测试：`test_video_submission_requires_remote_image_urls_without_host_generation`、`test_preflight_blocks_local_or_base64_video_image_urls`、`test_storyboard_shot_map_prefers_remote_url_for_video_refs`、`test_product_board_video_ref_recovers_state_result_url`、`test_confirm_board_persists_retrieve_url_for_video_handoff`、`test_create_video_submits_provider_model_name_for_canonical_seedance_id`。当前 r09 五段实际提交均使用 BasicRouter retrieve HTTP(S) URL，后台成功记录 Seedance 视频任务；任何未来缺失 URL 仍会 fail-closed，禁止本地路径/base64/伪托管。

### INC-056：产品使用图再次误解“音响底部磁吸到手机背面”的物理关系

- 现象：Momax 产品使用九宫格重新生成后，客户再次指出连接关系错误：正确关系应为“音响底部磁吸到手机背面”，而不是产品其它面贴到手机、或被画成扁平贴片/手机配件。这类问题此前已出现过，说明不是单次出图偶发。
- 根因：有三层缺口。第一，`product_usage_prompt()` 虽然读取了 `s2_magnetic_snap` 的“底部磁吸面贴向手机背面”动作，但九宫格导演表仍是通用的“正面互动/侧面互动/操作结果”，没有把底部/底座作为唯一接触面、手机背面作为唯一受力面、顶部网罩/控制面朝外这些空间关系写成硬合同。第二，确认闸门只校验 URL、身份锚点和文件指纹，不要求人工复核“物理几何合同”，错图可被确认进入故事板。第三，产品使用图 `source_fingerprint` 只绑定产品板/人物板文件内容，不绑定使用图 prompt 策略版本或几何合同；改 prompt 后旧确认也可能继续有效。
- 影响：后续故事板和视频会把错误产品使用图当作最高优先级 `@usage` 参考，放大连接关系错误，并可能在视频阶段继续继承错误的产品-手机空间关系。
- 修复：从“物品专用提示词”升级为通用产品使用 CIL（Contract Intermediate Language）。新增 `PRODUCT_USAGE_POLICY_VERSION`、`product_usage_physical_relation()`、`product_usage_geometry_contract()` 和 `[PRODUCT-USE PHYSICAL RELATION CONTRACT]`：生成前先结构化解析主动物体、承载/目标物体、产品接触面、目标接触面、外露/可操作面、最终状态和禁止的面交换，支持计划显式填写 `use_relation/physical_relation`，旧计划则对磁吸、贴合、佩戴、夹持、支架、插入、连接等高风险动作自动抽取兜底。Momax 只作为一个 CIL 实例表达为 `product_contact_surface=bottom/base magnetic surface`、`receiver_contact_surface=receiver back plane`、`outward_surface=non-contact visible/operable product surface`，代码里不再有音响/音箱专用规则。使用图 fingerprint 现在包含 prompt 策略版本、通用几何合同和完整物理关系合同，旧使用图确认会失效；带 `geometry_contract` 或 `physical_relation_contract` 的使用图确认必须传 `--geometry-reviewed`，否则 `USAGE_GEOMETRY_REVIEW_REQUIRED` 阻断。
- 状态：代码层已处理并新增回归测试：`test_magnetic_speaker_usage_prompt_locks_bottom_to_phone_back_geometry`（验证 Momax 作为 CIL 实例，不是专用规则）、`test_structured_use_relation_generalizes_beyond_speaker`、`test_usage_confirmation_requires_geometry_review_for_physical_contract`。本轮已按新物理关系合同重新确认使用素材，并在最终 r09 第 2 段抽帧中验证“底面贴手机背面、机身向外突出”。

### INC-057：产品图自身缺少结构化身份合同，材质/颜色/按钮/大小可能漂移

- 现象：客户指出产品图不只是形状问题，材质、颜色、按钮、大小、接口、Logo 等也属于严重质量风险；如果产品本体九宫格没有先把这些身份字段锁住，后续使用图和视频会继承一个已经漂移的产品锚点。
- 根因：`product_board_prompt()` 以前主要依赖“SOURCE-LOCK / identical geometry / preserve materials, colors, proportions”等自然语言总括约束，缺少生成前的结构化产品身份合同；产品板复用指纹也只绑定参考图 URL，没有绑定产品身份合同策略和产品事实字段，prompt 策略升级后旧产品板可能继续被复用。
- 影响：产品板一旦把品类、轮廓比例、材质、颜色、按钮/孔位/接口/Logo、功能接触面或尺寸尺度画错，下游使用图、故事板和视频都会把错产品当成权威参考，返工成本很高。
- 修复：新增 `PRODUCT_BOARD_POLICY_VERSION`、`product_identity_contract()`、`product_identity_contract_text()` 和 `[PRODUCT IDENTITY CONTRACT]`。产品板生成前必须明确品类、型号、颜色、材质/表面工艺、轮廓比例/尺寸尺度、按钮/控制布局、接口/孔位、Logo/原生标识、功能/接触面、独特细节和禁止变体；九宫格任何一格改变这些身份信息都视为无效。正式 storyboard 里的产品板 `source_fingerprint` 现在包含产品参考图、产品身份合同策略版本和合同字段，合同变化会让旧产品板失效；`storyboard.py` 会把 plan/brief 的产品事实传入产品板生成，不再只传 `product_type/product_color`。
- 状态：代码层已处理并新增回归测试：`test_product_board_prompt_requires_nine_distinct_views` 覆盖产品身份合同进入 prompt，`test_product_board_fingerprint_includes_identity_contract_policy` 覆盖材质/按钮/颜色等合同字段进入复用指纹。当前 Momax 产品板属于旧合同前产物，继续下游前应按新产品身份合同评估是否需要重生并重新确认。

### INC-058：产品使用图 CIL 过宽污染非使用镜头

- 现象：修复产品使用关系后，故事板/视频提示词审计发现，`@usage` 可能被注入到纯产品开场或普通主持人口播镜头；同时 `contact_sheet_prompt()` 的全局优先级文字会提到 `@usage`，即使当前 shot 并未真正引用产品使用图。
- 根因：`_shot_needs_usage_reference()` 早期把“有 presenter/characters”或中文“使用”作为触发条件，导致人物口播、产品卖点文字甚至“使用明暗”这类非物理动作也被判为产品使用镜头。CIL 的作用边界没有和 shot 的真实物理交互动作绑定。
- 影响：非使用镜头会拿到高优先级产品使用图，产品静物镜头可能继承手部/手机/接触关系，反而提高产品形态和剧情漂移风险。
- 修复：将使用图注入收紧到真正的物理使用动作：只从 `action/visual/scene/panel` 等正向动作字段识别连接、贴合、支架、握持、佩戴、插入等关系；不再因人物存在或泛化“使用”触发。`contact_sheet_prompt()` 只在当前 filtered registry 真实包含 `@usage` 时提及产品使用图优先级。
- 状态：已处理；新增/更新回归测试覆盖 `s1=false、s2=true、s3=true、s4=false、s5=false` 的 Momax 触发范围，以及纯产品/主持人口播镜头不被 `@usage` 污染。

### INC-059：单靠 CIL 不足以产出高质量构图，模型 skill 又会泛化物理关系

- 现象：用户指出“单纯 CIL 不如改成模型提供提示词的 skill”，同时产品使用图需要同时体现正确连接关系和卖点结果，如吸附手机背面、手机横放在桌面、IPX4 防水、多设备/TWS 同步等。首次模型构图 brief 虽能补画面策略，但第 8/9 格仍反复出现 “phone rests against speaker / speaker supports phone / phone propped up by speaker / phone standing via speaker” 这类容易被图像模型画成手机压在产品上或产品当底座的关系。
- 根因：CIL 擅长锁不可变事实，但不会自动设计 3x3 构图、镜头角度和卖点证明；文字模型擅长构图设计，但会把“支架/支撑”泛化成常识中的“手机靠在物体上”。缺少“模型负责构图、CIL 负责不可谈判物理事实”的双层约束和后置 schema 校验。
- 影响：后续其它品类也会遇到类似问题：服装可能把穿着/搭配关系画错，化妆品可能把刷头/涂抹区域画错，食品可能把包装/食用动作画错，电子产品可能把接口/连接方式画错。
- 修复：新增资产级 `composition_brief` 流程：`prompt_review.py capture-storyboard --composition-model <text-model>` 调用文字模型生成受限构图 brief，只允许补 `composition_strategy / primary_subject_scope / camera_scope / panel_plan / outcome_panels / must_include / must_exclude`，不得改产品事实和物理合同。`_validate_asset_composition_brief()` 要求接触类合同的第 4/5/7/8/9 格逐格保留 `product contact surface`、`receiver contact surface`、`bottom/base magnetic surface`、`receiver back plane`、`flush`、`protrudes outward`，并禁止会遮挡接触面的 front-screen/display view 和会误导成底座/站立的语义。产品卖点和使用结果从 plan/brief 的 `features/selling_points/key_messages/specs` 与相关使用镜头中抽取，作为 `usage_outcome_context` 注入，而不是写死音响规则。
- 状态：已处理；v13 审核稿曾通过旧结构校验，但后续人工复检发现第 8 格仍有 `propped up` 支撑歧义，已被新规则判定为不合格并标记 `invalidated`。v14 审核稿通过确认前校验，但仍需保持 `pending` 等待客户确认；即便文字校验通过，关键 outcome 格仍需出图后人工验图，因为文字“通过”不能替代视觉几何确认。

### INC-060：模型辅助导演设计缺少统一审核链，剧情/台词/BGM/分镜连续性分散在多个提示词里

- 现象：用户追问故事板剧情场景、视频生成的剧情/台词/BGM/主旨/分段连贯性/分镜表现是否引入模型能力。审计发现现有流程已有 `polish`、`seedance_prompt`、`script_splitter` 的连续性与音频合同，但导演设计分散在 shot 字段、fallback prompt、固定规则和人工经验里，没有统一的、可确认的模型导演 brief。
- 根因：之前把“提示词编译”和“导演设计”混在一起。Seedance 原生故事板、Kling 单格展开、音频连续性、剪辑点和剧情主旨各自有提示词片段，但没有一个通用 schema 记录“本段叙事任务、起止状态、动作节拍、台词语气、BGM/SFX 方法、模型路由策略、参考图优先级”。
- 影响：一旦模型从 Seedance 降级到 Kling，或者提示词因长度限制被压缩，剧情主旨、动作节奏和音频连续性可能被弱化；客户也无法在确认闸门看到模型辅助导演设计到底是什么。
- 修复：新增 `add_director_briefs()`，支持 `capture-storyboard --director-model` 和 `capture-video --director-model`。故事板 director brief 覆盖 `narrative_function/scene_design/shot_size/camera_movement/composition/lighting/action_beats/transition/product_value_proof/continuity_hooks/reference_scope`；视频 director brief 覆盖 `start_state/timeline_beats/end_state/dialogue_delivery/camera_motion/action_continuity/audio_continuity/edit_continuity/model_strategy/reference_priority`。确认预览会展示 director brief，确认后注入 storyboard/video submission prompt；`video_engine.py` 与 `video_prompts.py` 的 prompt 压缩逻辑会保留 director brief 摘要、台词、连续性、音频方法和产品身份。
- 状态：已处理；新增回归测试覆盖故事板/视频 director skill 注入、Seedance/Kling 两套提示词同时带 director brief、确认 gate 将 director brief 写入 segment、长提示词压缩保留关键摘要。

### INC-061：prompt skill 重试闸门会保存最后一次不合格 JSON

- 现象：v11 提示词审核稿被成功保存，但事后复检发现产品使用图第 5 格缺少 `bottom/base magnetic surface`，说明它并没有真正通过当前构图合同。进一步排查发现，模型返回的 JSON 可解析但校验失败时，循环内先把候选赋给 `brief/result`，最后一次重试仍失败后，外层只判断变量是否为 `None`，于是把最后一个不合格候选写入 pending 审核稿。
- 根因：`polish()`、`add_asset_composition_briefs()`、`add_director_briefs()` 的重试逻辑把“可解析 JSON”与“已通过业务校验”混为一谈；变量赋值发生在校验之前。
- 影响：这会破坏“模型 skill 先验预防”的核心假设：不合格构图、漂移导演 brief 或产品范围漂移提示词可能进入客户确认稿，后续再靠出图/告警发现，回到用户批评的“事后监控”模式。
- 修复：所有 prompt skill 重试逻辑改为 `candidate = _extract_json()`，只有通过业务校验后才赋给 `brief/result`；连续失败直接抛 `*_SKILL_FAILED`，不保存 pending 稿。错误信息补充 `shot_id`，便于定位是哪一段模型设计漂移。新增 `_augment_director_key_relations()`，把 CIL 关键物理关系确定性注入 director brief 的 `must_preserve/reference_scope` 后再校验，避免模型少写关键面但不与 CIL 冲突时被误拒或漏过。确认入口 `prompt_review.py confirm` 也新增 `_validate_review_before_confirm()`，对已存在的 pending 审核稿重新执行业务校验，避免旧 pending 坏稿在规则升级后被手动确认。
- 状态：已处理；新增 `test_asset_composition_skill_fails_after_invalid_retry_responses`、`test_director_skill_fails_after_invalid_retry_responses`、`test_director_skill_augments_cil_surface_locks_before_validation`、`test_confirm_revalidates_composition_brief_before_approval`、`test_invalidate_pending_review_blocks_later_confirm`、`test_invalidate_cli_prints_short_json_only`。已验证 114 个相关回归用例通过。实测确认前检查：v13 因 `panel 8` 的 `propped up` 支撑歧义被拒并已标记 `invalidated`；v14 通过确认前校验但仍保持 `pending`，等待客户确认后才能出图。

### INC-062：run-id 与真实 revision 目录混淆导致旧状态被误读

- 现象：产品使用图重新生成后，命令日志显示已提交并成功，但人工检查 `output/storyboard/<run-id>/storyboard_result.json` 时仍看到上一张图的 URL/task/sha。进一步排查发现新结果实际写入了 `output/storyboard/<run-id>__r07/`，而检查方仍在读旧的逻辑 run-id 目录。
- 根因：`run_id` 是业务逻辑身份，不等于真实输出目录；当 plan 指纹变化时，`storyboard.py` 会创建 `__rNN` revision 目录以保护旧确认结果，但下游工具和人工排查仍可能按 `output/storyboard/<run-id>` 拼路径。缺少“当前 revision 指针”导致旧目录、旧确认、旧 handoff 有机会被误当成当前状态。
- 影响：这会制造“force 没覆盖/状态污染”的假象，更严重时可能让 `script_splitter`、`video_effect_qc` 或人工恢复流程读取旧图，造成已经修正的 CIL 合同没有真正传入视频 handoff。
- 修复：新增 storyboard run pointer：`output/storyboard/.<run_id>_current.json` 记录当前 `out_dir/result_json/plan_fingerprint/visual_plan_fingerprint/client/run_id/stage`。`storyboard.py` 在解析真实输出目录后打印 `resolved output dir` 并写 pointer；`script_splitter.py` 在正式 split 时跟随 pointer，旧 `--storyboard-dir` 会被重定向到当前 revision；`video_effect_qc.py` 预检也先解析 pointer，不再固定推断 `output/storyboard/<run-id>`。pointer 与 result 都必须匹配当前 plan fingerprint 和 logical run_id，否则拒绝使用。同步修复 `plan_fingerprint()` / `visual_plan_fingerprint()`：`_asset_composition_briefs`、`approved_prompt_zh`、`approved_submission_prompt_zh`、运行时复制的 `references`、旧计划自动补的默认 `panel_plan` 不再参与 authored plan 身份，避免生成端和消费端因运行时注入字段算出不同 revision 身份。
- 状态：已处理；新增 `test_run_pointer_resolves_current_revision`、`test_run_pointer_rejects_stale_plan`、`test_runtime_prompt_fields_do_not_change_plan_identity`，并验证 `tests.test_storyboard_resume` + `tests.test_video_effect_qc` + 磁吸 CIL 关键用例共 37 个用例通过。当前 Momax 指针已解析到 r08，后续 split / QC 不再直接读取旧逻辑 run-id 目录。

### INC-063：默认分镜节奏词误触发产品使用锚点

- 现象：r08 故事版生成后，s1 产品开场、s4 功能证明、s5 Mina CTA 等非物理使用镜头的 `reference_registry` 都混入了 `@usage`。这会把“产品使用物理关系锚点”污染到不需要展示磁吸动作的镜头，增加产品构图和剧情表达跑偏风险。
- 根因：`_physical_use_trigger_text()` 把默认 `panel_plan` 字符串拼进物理使用判定文本；默认分镜词里有 `long-lens compression`，旧的英文动作词检测又用裸子串匹配 `press`，导致 `compression` 被误判为按压动作，于是所有带默认 panel plan 的镜头都被判定为物理使用镜头。
- 影响：CIL 从“精确限定物理使用镜头”退化为“全局注入 usage”，既污染纯产品镜头，也会影响视频 handoff 参考图优先级。后续不同产品只要默认分镜里出现类似英文子串，也可能触发同类误判。
- 修复：自由文本 `panel_plan` 标签不再参与 `_physical_use_trigger_text()`；只有结构化 panel dict 的 `beat/action/event/continuity` 字段才作为动作证据。新增回归 `test_default_panel_plan_does_not_trigger_usage_reference`，确认默认 `long-lens compression` 不再使产品-only 镜头携带 `@usage`。当前 Momax 参考绑定已验证为：s1/s4 只用产品，s2/s3 用 usage+产品，s5 用 Mina+产品。
- 状态：已处理；已通过 5 个关键回归与 py_compile。r08 已生成分镜因 reference fingerprint 受旧 bug 影响，后续需重新生成受影响分镜后再确认故事版。

### INC-064：force 重生提交后仍可能暴露旧完成态元数据

- 现象：执行产品使用图 force 重生时，日志显示已提交新任务，但 `storyboard_result.json` 仍可能显示上一张图的 URL/task/sha，造成“已经重生但结果还是旧图”的状态污染。
- 根因：旧实现只在新图片成功下载后才覆盖 `product_usage_image/cast_board` 记录；force 开始时没有先废止旧完成态元数据，且旧固定文件名仍保留在原位。若任务提交后中断、下载失败或人工检查发生在成功写回前，JSON 和固定文件都仍像“旧图可用”，下游容易误复用旧 retrieve URL 或旧 sha。
- 影响：这不是单个音响项目的问题，而是 CIL 资产生命周期问题。任何需要客户反馈后重生的板图都可能出现旧结果污染新流程，进而导致“无限失败、无限改”的隐藏状态循环。
- 修复：`_backup_existing_board()` 在 force 场景改为把旧固定文件移动到 `.force_regen_backups/`，不再让旧图继续占用当前 board 文件名；新增 `_mark_board_regeneration_pending()`，force 提交前即清除 `url/result_url/remote_url/download_url/imageUrl/imageUrls/task_id/taskId/request_id/sha256/board_sha256/confirmed_source_fingerprint` 等完成态字段，写入 `status: pending_regeneration` 与 `superseded` 备份记录，并删除旧 approval。这样即使异步任务提交后中断，下游也只能看到“正在重生/待确认”，不能把旧图当成当前结果。
- 状态：已处理；新增 `test_force_usage_clears_stale_result_before_async_submission_finishes`，并重跑 `tests.test_storyboard_resume`、`tests.test_storyboard_enhancements`、`tests.test_product_consistency`、`tests.test_strict_storyboard_handoff` 共 91 个相关用例通过。

### INC-065：视频 split 未正确继承跨 revision confirmed board 与使用图锚点

- 现象：r08 split 时先被 `UNCONFIRMED_ASSET` 拦住，提示 s5 引用了 `actors/aeroclip/mina/portrait.png`；修复后继续检查发现 s2/s3 磁吸使用镜头没有带 `@usage`，而 s5 Mina 推荐镜头反而混入了 `@usage`。
- 根因：`script_splitter` 只在当前 storyboard revision 目录查 `.cast_confirmed/.product_confirmed`，没有接受从历史 revision/registry 复用的已确认产品板和人物板，导致 `reference_registry` 里的 `@mina/@product` 没有重定向到 confirmed boards；同时自动附加 `@usage` 的条件写成了“有人物 characters + 产品 tag”，既漏掉只有手部操作的真实物理使用镜头，又污染普通人物持产品 CTA 镜头。
- 影响：正式视频 handoff 可能继续使用原始 portrait/local path 或缺少产品使用图锚点，直接破坏“产品使用图严格依照产品图，并传入实际使用片段”的 CIL 目标。
- 修复：新增 `_confirmed_generated_board_current()`，允许 confirmed board 在跨 revision 复用时通过 `status=confirmed + board_sha256/sha256 校验 + BasicRouter retrieve URL` 进入 handoff；`reference_registry` 会重定向到 `asset_refs.cast_boards/product_boards/product_usage_images`。`@usage` 自动注入改为只依据 `storyboard._shot_needs_usage_reference()` 的真实物理使用关系判断，并插入参考图首位；普通人物+产品镜头不再自动带使用图。同步修正 `required_reference_types` 与 `clip_contract.ref_tags`：`product_usage_identity` 只在真实使用关系镜头中成为必需引用，避免 QC 又把正确的 CTA 镜头推回错误 usage 锚点。
- 状态：已处理；新增 `test_split_remaps_registry_to_registry_reused_confirmed_cast_board`、`test_split_usage_reference_requires_actual_physical_use_action`、`test_split_required_reference_types_follow_actual_usage_refs`。当前 Momax split 已验证：s1 产品+故事板，s2/s3 usage+产品+故事板，s4 产品+故事板，s5 Mina+产品+故事板；所有参考图均为 HTTP(S) retrieve URL。preflight 已通过除 `prompt_review_confirmed` 外全部检查，当前仅等待客户确认完整视频提交提示词。

### INC-066：video_engine 正式引用校验仍会误拒 BasicRouter retrieve URL

- 现象：preflight 已证明视频 `imageUrls` 必须使用 BasicRouter retrieve 得到的 HTTP(S) 图片 URL，但继续审计 `video_engine._validate_references()` 发现它仍按 `asset_prep.is_confirmed(client, url)` 校验，默认不信任远程 URL；确认后正式 `render_batch` 可能在提交前把正确的 retrieve URL 判为 `UNTRUSTED_VIDEO_REFERENCE`。同时 `tests.test_video_manifest_fail_closed` 中一个缺产品素材用例会先查真实模型目录，离线时卡住。
- 根因：图片素材确认状态机从“本地 brief 图片”演进到了“确认生成板 + retrieve URL handoff”，但 video_engine 的引用信任边界没有同步升级；测试也没有完全 mock 掉与当前断言无关的模型目录查询。
- 影响：即使 split/preflight 全部正确，正式视频生成仍可能在最后一步被旧本地素材校验拦住；测试链路也会因为无关网络请求降低实测效率。
- 修复：新增 `_trusted_remote_segment_reference()`，只信任 segment typed references 中来源为 `storyboard`、`asset_refs.product_usage_images`、`asset_refs.cast_boards`、`asset_refs.product_boards` 的远程 retrieve URL；未登记/未类型化的远程 URL 仍 fail-closed。补齐测试 mock，避免缺产品素材用例触发真实模型目录。
- 状态：已处理；新增 `test_formal_video_trusts_typed_generated_remote_references`、`test_formal_video_rejects_untyped_remote_references`，并用当前 Momax 5 段 segments 直接验证 `_validate_references()` 与 `_validate_reference_handoff()` 均通过。

### INC-067：CIL 视频引用契约收紧后 draft 回归链路被误拦

- 现象：为修复“视频接口不能传 base64、本地路径，必须传图片 retrieve URL”后，`tests/run_tests.sh smoke` 中产品 SKU 展开、Seedance 原生故事板和 chain locked_refs 草稿用例不再触发 `_submit_video`，表现为提交数组为空或结果 `ok=false`。
- 根因：CIL 把正式视频 `imageUrls` 的生产规则收紧到了 HTTP(S) 持久 URL，但 `video_engine._video_image_ref()` 没有区分“正式付费生成”和“离线 draft/单测适配”。旧单测使用本地图片来验证路由和引用顺序，被新规则提前拒绝；其中部分旧测试还在表达“Seedance native 可直接提交本地故事板图”的过期契约。
- 影响：如果为让 smoke 通过而重新放开本地路径，会把已修复的 base64/本地路径严重 bug 带回正式视频链路；如果完全不处理，回归测试无法覆盖产品 SKU、Seedance native、Kling fallback 和 chain locked_refs 的核心路由。
- 修复：`_video_image_ref(path_or_url, allow_local_draft=False)` 明确生产边界：正式路径只接受 HTTP(S) retrieve URL；只有显式 `draft=True` 的测试/调试路径才允许通过 `br_client.to_image_ref()` 适配本地引用。Seedance native 测试同步改为使用 `storyboard_url + reference.url` 的远程 URL，继续验证“不展开 Kling 单格”，不再认可本地故事板直接提交。Kling fallback 的 draft 单格测试仍可使用本地 mock，正式路径仍要求扩展图带 retrieve URL。
- 状态：已处理；`test_v13` 产品 SKU、`test_storyboard_panel_binding`、`test_v19` chain locked_refs 均通过；完整 `bash tests/run_tests.sh smoke` 已验证 21 个测试文件、371 个用例全绿。

### INC-068：render_plan 软字段把旧 storyboard_result 路径带入视频提示词

- 现象：r08 pointer、segments 的 `storyboard_dir/storyboard_url` 与视频引用 URL 都已正确指向 r08，但 `render_plan.json` 内的展示字段 `storyboard_result` 仍是逻辑目录 `output/storyboard/<run-id>/storyboard_result.json`。该字段被 `script_splitter` 嵌入每段 `render_plan.content`，又被 `prompt_review capture-video` 写入完整提交提示词，导致待确认视频审核稿里出现旧路径。
- 根因：run pointer 修复解决了硬交接路径，但 render plan 作为“已确认渲染方案”被当作普通 JSON 透传，没有校验其中声明的上游 storyboard_result 是否等于当前 revision。它不是模型可读取的本地文件，却会污染人工审核与提示词文本，形成“硬状态正确、软说明旧”的隐藏状态污染。
- 影响：客户看到或模型收到的提示词包含旧路径，容易误判当前流程仍在读旧故事板；更严重时，后续类似字段可能被下游脚本当作真实输入路径继续传播，重新引入“无限失败无限改”的旧状态循环。
- 修复：`video_effect_qc.py` 新增 `render_plan_storyboard_result_current` 硬检查：每段内嵌 `render_plan.content.storyboard_result` 如存在，必须等于当前解析到的 storyboard revision 的 `storyboard_result.json`，否则阻断提示词确认并要求重新登记 render plan、重新 split 和重新 capture-video。当前 Momax `render_plan.json` 已更新到 r08，manifest 已重新 approve render_plan，`momax_video_segments.r08.json` 与 `prompt_review_video_r08.json/.md` 已刷新。
- 状态：已处理；新增 `test_preflight_blocks_stale_render_plan_storyboard_result`。当前 Momax preflight 仅剩 `prompt_review_confirmed`，旧逻辑目录路径在 render_plan、segments、prompt review JSON/Markdown 中已查无命中。

## 深度修复审计（2026-08-06）

结论：不能把“当前项目已可直接出片”与“事故代码层已修复”混为一谈。截至本次审计，`INC-001` 到 `INC-076` 已全部有代码级修复、明确的 fail-closed 边界或可复核的运行证据；Momax 的 `s2_magnetic_snap` 故事板缺口已完成单 shot 重生并确认，五段 Seedance 视频、底片截断修复、动效字幕和媒体 QC 均已实测。历史 run 中曾存在的本地图片 URL 缺失、旧状态污染和提示词交接问题仍保留为事故证据，但不再作为当前 run 的活动阻断。

### 已深度固化为代码/测试的类型

- 接口协议类：旧 `/ai/createImage` / `/ai/createVideo` 路径已由异步 `/v1/image-generations`、`/v1/video-generations` 取代；保留的 legacy 函数只作为低层兼容，不再是正式故事板/视频主路径。证据：`scripts/br_client.py` v1 submit/retrieve、`scripts/storyboard.py` 异步说明、`tests/test_standardize.py`、`tests/test_video_model_alias.py`、`tests/test_storyboard_panel_binding.py`。
- 参考图/身份一致性类：产品板、人物板、产品使用图、故事板、视频 handoff 均绑定 fingerprint、client/run_id、reference registry 或 approval hash。旧图/旧计划/旧参考策略变化会失效，不允许静默复用。证据：`scripts/storyboard.py`、`scripts/script_splitter.py`、`scripts/run_manifest.py`，以及 `tests/test_storyboard_resume.py`、`tests/test_storyboard_panel_binding.py`、`tests/test_strict_storyboard_handoff.py`。
- Seedance/Kling 分流类：Seedance 优先原生 storyboard/contact sheet；只有 fallback 到 Kling 才生成单格展开图，并且 fallback 提示词必须提前确认。证据：`scripts/video_engine.py`、`scripts/video_prompts.py`、`scripts/prompt_review.py`，以及 `tests/test_video_model_alias.py`、`tests/test_prompt_review.py`、`tests/test_video_privacy_fallback.py`。
- 确认闸门类：正式视频入口要求 manifest/client/results_out/prompt_review，函数级 batch/chain/single 都 fail-closed；旧 pending prompt 或不完整 segments 不能进入正式生成。证据：`scripts/video_engine.py`、`scripts/video_effect_qc.py`、`tests/test_video_manifest_fail_closed.py`、`tests/test_video_effect_qc.py`。
- 客户反馈精修类：产品/人物/使用板精修会替换固定板、删除旧确认、记录 provenance，并强制重新确认。证据：`scripts/storyboard.py`、`tests/test_asset_feedback_refine.py`、`tests/test_storyboard_enhancements.py`。

### 早期条目状态回填

- `INC-003` 的“环境初始化应自动识别并登记受控代理”已由后续 `INC-022` 深修覆盖：下载 peer 校验已识别 `HTTPS_PROXY/http_proxy/ALL_PROXY` 等显式代理，并有代理 peer 回归测试。
- `INC-005` 的“客户/计划语义绑定仍需入口补强”已由后续 manifest 与 storyboard handoff 深修覆盖：`storyboard_result.json` 记录 `plan_source/plan_title/client/run_id`，`script_splitter` 校验 client/run_id/plan fingerprint，run manifest 用 approval hash 绑定当前 artifact。

### 当前仍未闭环的事项

- 当前没有代码级或工作流级未闭环事故。历史状态说明见下一节；它们不再作为当前 run 的活动阻断。

### 历史事故证据回填（已闭环）

- `INC-050` / `INC-052` / `INC-053` / `INC-054` 的代码层深修已完成；`INC-055` 的协议修复已完成并 fail-closed。旧 `recovered` 文件和旧 preflight 仅作为历史事故证据；当前 r09 handoff 的五段 `imageUrls` 均为已持久化的 BasicRouter retrieve HTTP(S) URL，并通过实际 Seedance 提交与下载验证。
- `INC-046` 到 `INC-049` 中的 `pending` 是历史客户确认状态，不是当前代码未修复。当前视频已重新捕获并确认完整 Seedance native 提示词、imageUrls 定义、音频方法边界和 fallback 合同；模型内声音/BGM仍按提示词+人工 QC，BGM确定性连续性由后期统一混音负责。
- 音频/BGM 连续性没有被包装成模型媒体级保证；系统明确记录“提示词约束 + 片段级人工 QC + 后期统一混音”的真实能力边界。

### 本次审计验证

- `bash tests/run_tests.sh all`：68 个测试文件，699 个用例，全绿。
- 当前 Momax 五段视频交接：五段图片引用均为持久化 BasicRouter retrieve HTTP(S) URL，并通过实际提交、轮询、下载和媒体 QC 验证；旧 URL recovery plan 仅保留为历史证据，不再作为当前交付状态。

## 同类缺陷审计结论

- 阶段确认：现有产品板、人物板、产品使用板、故事板均有独立确认和 fingerprint；变更后旧确认会失效。
- 旧产物复用：故事板已改为 checkpoint + shot 内容 + plan fingerprint + SHA-256 四项绑定。
- 图像协议：产品使用图已固定同步 `gpt-image-2` 图生图协议，不再静默调用不兼容异步接口。
- 模型能力：Seedance 模型名称、时长和 videoType 已使用实时目录；本次新增交接契约防止能力不足时静默丢参考图。
- 文字责任：故事板/视频生成提示词继续禁止画面文字，OCR 失败阻断。
- 仍需注意：正式出片前仍以实时模型目录为准校验 `allowVideoType` 和参考图数量；若 Seedance 当前端点不可用或参考图能力不足，流程会按确认合同回落到 Kling 单格展开路径，不能少传确认素材来绕过。

## 严格执行规则

- 产品原图未确认：阻断。
- 产品本体九宫格未确认：阻断。
- 数字人六视图未确认：阻断。
- 产品使用九宫格未确认：阻断。
- 故事板未确认：阻断。
- 脚本、计划、素材 fingerprint 不一致：阻断。
- 必需参考素材无法完整交给目标模型：阻断。
- 目标模型不可用或能力不足：可在完整能力匹配的候选模型中降级；没有完整匹配候选时阻断，不少传参考图。
- 视频 OCR 发现模型绘制文字：阻断，不静默交付。
- 任何阶段不允许用“少传素材或改脚本”替代“严格符合”；模型降级只能保持完整 handoff 不变。

## 当前下游闸门

Momax 当前脚本、故事板、产品板、人物板、产品使用图、render plan 和视频提示词确认状态已通过；但 6 张旧确认图没有官方 retrieve URL。补齐 URL 或重新生成并重新确认这些素材前，禁止调用付费视频模型。

## 当前成片

- Seedance 实际模型：`dreamina-seedance-2-0-260128`，展示名 `seedance-2.0`
- 视频路径：`output/aeroclip-live-20260731/aeroclip-s1-final.mp4`
- 组成：15 秒 + 15 秒 + 15 秒 + 4 秒
- 画面：720x1280，9:16，H.264，30 fps
- 音频：AAC，48 kHz，完整音轨
- 总时长：49.154 秒
- 媒体 QC：通过
- OCR：抽检 50 帧，未检测到画面文字

### INC-069：多段 xfade 使用非累计时间轴导致底片截断

- 现象：五段视频合成后的 `basecut_r08.mp4` 只有约 14 秒，后续片段被覆盖。
- 根因：`compose._concat_with_xfade()` 将每次 `offset` 错误地计算为当前片段时长减淡化时长；`xfade` 的 offset 实际相对于已累计的链路时间轴。
- 修复：改为 `sum(durations[:i + 1]) - fade_dur * (i + 1)`，并增加多段合成回归测试；修复后五段底片为 29.5667 秒。
- 状态：已处理并在本轮 r09 成片中验证。

### INC-070：会话密钥只读消费路径错误创建写锁

- 现象：`key_setup.py gate` 可以读到 `STORED`，视频实际提交时却因无法创建 `~/.cache/basicrouter/sessions/<hash>/key.lock` 失败。
- 根因：读取一个由原子替换写入的密钥文件仍使用 `FileLock`；沙箱/只读缓存环境禁止读操作创建锁文件。
- 修复：读取改为 `O_NOFOLLOW + open/fstat` 的稳定快照读取；保存和清除仍保留互斥锁。新增只读锁目录回归测试。
- 状态：已处理；第 2 段重拍在修复后成功提交 Seedance。

### INC-071：字幕估算时长未扣除 xfade 重叠

- 现象：视频底片实际 29.5667 秒，首次 r09 字幕时间轴按各段时长累加到 31.374 秒，末尾字幕会越过成片。
- 根因：字幕推导使用片段实际时长，但未读取最终底片的交叉淡化重叠时长。
- 修复：正式 `derive-captions` 使用 `--audio-video <basecut>`，按实际成片时长校准字幕与动效时间轴；alpha 层和成片随后重渲染。
- 状态：已处理；当前 r09 lines/motion 末尾均为 29.567 秒，媒体 QC 通过。

### INC-072：已确认视频的语义物理关系未在最终验收前拦截

- 现象：旧第 2 段媒体 QC、OCR 和音频均通过，但画面把音响放在手机顶部，违反“底部磁吸到手机背面”的业务合同。
- 根因：技术 QC 证明文件可播放，不证明产品接触面、承载面和动作结果正确；最终成片闭环缺少针对高风险物理关系的逐帧语义验收。
- 修复：对高风险 usage shot 强制保留独立重拍 take，要求提交提示词明确接触面和拒绝状态；正式审查绑定新 take fingerprint、handoff fingerprint、OCR 和音频契约，抽帧确认通过后才替换并重合成。
- 状态：已处理本次事故；后续通用方案仍需将 CIL 几何合同转为生成前结构化验证与生成后语义验收的双闸门。

### INC-073：台词 CTA 与后期 CTA 文案重复叠加

- 现象：第 5 段台词字幕已显示产品名，额外 CTA 动效又显示一次同样文案。
- 根因：`derive_captions()` 无条件复制 `motion_elements`，没有判断后期文本是否已被台词字幕承载。
- 修复：新增通用 spoken-text CTA 去重规则；保留 Logo、图形和非重复卖点标签，移除已在台词中出现的 CTA 文本，并增加回归测试。
- 状态：已处理；r09 最终抽帧确认末段无重复产品名。

### INC-074：字幕模型设计器因 API 参数错误静默退回规则模板

- 现象：字幕输出长期使用统一字号、统一 `fade_up` 和统一底框，虽然流程声称支持模型设计，但设计结果没有模型来源记录。
- 根因：`motion_design.py` 将 `br_client.list_models()` 当成带 key 的方法调用，并把 `br_client.chat()` 的参数顺序写错；随后宽泛捕获所有异常并直接退回规则模板，导致真实模型从未成功参与设计且无法被发现。
- 修复：按 BasicRouter 公共签名调用 `list_models(category=...)` 与 `chat(api_key, messages, model=...)`；设计结果写入 `design_engine.mode/model`；正式 `--require-llm` 模式在模型失败时阻断，不再静默伪装成功。新增 API 签名、失败阻断和显式 fallback 回归测试；本次 Momax 实测已由 `kimi-k2.5` 返回 5 个镜头的字幕/动效设计方案并通过结构校验。
- 状态：已处理。

### INC-075：横屏字幕使用左右边距模拟宽度导致大通栏遮挡主体

- 现象：1920x1080 成片的口播字幕被渲染成接近全宽的半透明条，压住产品和人物，卖点卡与口播字幕也缺少层级关系。
- 根因：HyperFrames 精确定位只有 `left_px/right_px` 约束，没有表达“内容卡固定宽度”；横屏场景沿用了竖屏安全区思路。
- 修复：新增 `width_px` 内容宽度字段；口播字幕按镜头语义使用紧凑内容卡，卖点使用右侧强调卡，片尾使用独立品牌锁定卡；增加宽度定位回归测试。本次 r11 抽查 5 个镜头，字幕不再横贯全屏，alpha、时长和媒体 QC 均通过。
- 状态：已处理。

### INC-076：HyperFrames 本地帧捕获依赖未被部署/权限诊断覆盖

- 现象：项目存在 Chrome 压缩包且 `doctor` 显示基础依赖正常，但实际渲染在“开始帧捕获”阶段失败；沙箱内 `sysmon/pgrep` 无法读取进程状态，错误信息也没有直接指出运行时条件。
- 根因：环境检查只验证 Node/ffmpeg，没有验证 headless Chrome 可执行路径和实际帧捕获；正式渲染仍依赖未显式注入的浏览器与进程探测能力。
- 修复：实测使用项目内置 Chrome 解压并通过 `HYPERFRAMES_BROWSER_PATH` 显式注入；沙箱外受控执行完成 888/888 帧渲染。后续应把 browser ensure/capture smoke 纳入 setup 与 doctor，避免首次正式渲染才暴露。
- 状态：本次运行已绕过并验证；代码级 doctor/smoke 补强仍列为交付前改进项，不宣称已完全固化。

### INC-077：故事板提示词审核确认后仍缺少资产级提示词

- 现象：`prompt_review.py polish --stage storyboard` 返回 `status: pending`，执行 `confirm` 后仍被 `storyboard.py` 阻断，提示缺少 `product_board` / `product_usage_image` 资产级提示词。
- 根因：镜头级审核和资产级审核由两个不同入口生成；`polish` 的产物没有包含正式渲染器强制校验的 `asset_prompts`，造成“已确认但不可执行”的状态断链。
- 修复：故事板阶段的 `polish()` 现在同步写入 `storyboard.asset_prompt_review_items(plan)`；新增回归测试，确保每个故事板审核文件都包含渲染所需的资产提示词及指纹。
- 状态：已处理；本次实测通过完整审核文件确认后继续进入图像生成。

### INC-078：故事板正式命令未传入已确认提示词审核文件

- 现象：审核文件已确认且指纹有效，但正式命令遗漏 `--prompt-review`，入口再次报 `PROMPT_REVIEW_REQUIRED`，错误信息诱导重复运行付费审核。
- 根因：CLI 将参数声明为可选，但正式渲染又要求它；公共文档命令与实现协议不一致，且缺少同目录审核文件自动交接。
- 修复：`storyboard.py` 在未显式传参时自动发现计划同目录的 `storyboard_prompt_review.json`，随后仍严格执行 status、stage、视觉指纹和资产提示词指纹校验；缺少文件时错误信息明确给出 `--prompt-review` 或同目录路径要求。
- 状态：已处理；本次实测使用显式审核文件成功提交 `gpt-image-2`，并完成产品使用图异步轮询。

### INC-079：产品使用图满足物理关系但产品几何在接触场景中漂移

- 现象：使用图多数格子正确表现“产品底部磁吸面 -> 手机背面”，但部分格子把产品重绘成截锥、半圆或被压扁的支撑件，控制区和完整体积也出现丢失。
- 根因：一次生成 3x3 接触表会逐格重建产品；多视图产品板、单体产品图和人物板同时作为参考，模型在遮挡/承重/支架姿态下重新推断几何。原有身份锁只约束品类和语义，没有明确禁止遮挡导致的体积重绘；生成前后也只有物理关系合同，没有几何完整性闸门。
- 修复：产品身份合同增加通用几何不变形规则：遮挡、连接、承重、折叠或接触不能改变 3D 体积、厚度、边缘轮廓、控制区和功能面；禁止扁片、半圆、楔形、支撑底座、通用圆柱或替换外壳。新增产品使用图提示词回归测试。
- 状态：代码约束已处理；当前旧使用图作废，必须按新提示词重新生成并完成人工几何核验后才能确认。

### INC-080：产品使用九宫格由模型一次性绘制导致逐格物理关系串格

- 现象：同一张使用图中第 3、4 格把手机侧边当成接触面，第 7 格未证明产品吸附在手机背面；模型在格子之间混合动作和视角。
- 根因：把 9 个动作同时交给图像模型还要求模型负责网格排版，模型会跨格推断接触关系；原有合同是整图级文本，未绑定到每个 panel 的独立构图目标。
- 修复：高风险产品使用图改为 9 次独立异步图像生成，每格使用同一身份锚和独立的 surface-proof prompt；本地 `ffmpeg` 只负责 3×3 拼版。结果同时保存 `panel_paths`、`panel_urls`/`imageUrls`、`panel_index` 和 panel prompt，确认前强制检查 9+9 交接完整性。
- 状态：代码已接入，相关提示词与恢复测试通过；下一次实测需重新生成 9 格并逐格确认。

### INC-081：逐格并行生成初版 checkpoint 竞写

- 现象：9 个 panel 同时回调时共同写 `storyboard_result.json.tmp`，中断恢复可能出现临时文件不存在或 taskId 丢失。
- 根因：原子替换只解决单任务写入，没有进程内并发锁；逐格并行后同一路径存在多个 writer。
- 修复：`save_progress()` 增加线程锁；每格 task 写入 `in_progress_panels`，保留 panel_index、路径和 taskId，恢复时沿用原 task，不重复提交。
- 状态：已处理；恢复回归测试通过。

### INC-082：逐格请求仍把参考板网格带入单格画面

- 现象：9 个 API 任务虽然是独立提交，但返回的每个 `panel_*.jpg` 内部又被模型绘制成多格小分镜；产品板和人物板的九宫格/六视图布局被复制到单格结果中。
- 根因：逐格任务只拆分了请求次数，没有拆分 img2img 参考素材；每次请求仍同时发送整张产品板和人物六视图板。模型将参考板布局理解为构图指令，单帧提示词无法可靠覆盖这一视觉先验。
- 影响：本地 3x3 拼版表面上是 9 格，实际每格仍是多格，物理接触证明和后续视频镜头锚点都不可靠。
- 修复：确认板仍作为唯一溯源和 fingerprint 来源，但 API 单格请求改为使用确定性单视图锚点：产品正面/底部裁片和 Mina 正面全身裁片；提示词明确“一帧、一部手机、一个音响、连续单一机位，不复制参考板网格”。每格仍独立异步提交并保留原始 retrieve URL。
- 状态：代码修复完成；单视图裁片、单帧提示词和恢复测试通过。当前实测新 run 已创建第 4、8 格任务并成功返回 URL，剩余格受额度不足阻断，尚未完成九格视觉验收。

### INC-083：旧产品使用板字段与 @usage 引用契约分裂

- 现象：当前计划的 `asset_refs.product_usage_board` 已有历史确认图，但规范化逻辑只识别 `asset_refs.product_usage_images`；物理使用镜头自动补出 `@usage` 后，生成前校验报“ref_tags 引用了不存在的参考图标签”。即使素材桶已存在，shot 的 `references[]` 也可能没有对应条目。
- 根因：旧单数键、规范复数键、plan 全局 `references[]` 和 shot 级 `references[]` 四套状态没有统一同步。
- 修复：规范化阶段把旧字段迁移到 `product_usage_images`，并把当前使用图同步注册到全局及所有物理使用 shot 的 `references[]`；产品/人物/使用阶段尚未生成 `@usage` 时，阶段校验只验证当前阶段可用的引用，不把即将生成的资产误判为缺失。
- 状态：已处理；实际计划规范化验证和恢复回归测试通过，需在额度恢复后继续验证完整 9 格到故事板 handoff。

### INC-084：额度不足被错误当作可重试 500

- 现象：BasicRouter 返回 HTTP 500，正文为 `Insufficient credit`。图片生成层将其当成普通服务端错误，连续指数重试；本次逐格实测因此产生等待和无效重试噪声。
- 根因：重试判断只看 HTTP 状态码，没有先检查错误正文/业务 envelope；网关在不同入口既可能用业务 `code=-1`，也可能用 HTTP 500 携带额度不足文本。
- 修复：`br_client.is_insufficient_credit()` 统一识别中英文额度耗尽错误；HTTP/代理 fallback 在进入 5xx 重试前先抛终态额度错误；`storyboard.download_first_image()` 遇到该错误立即停止，不重复提交或轮询。新增“不重试额度不足 500”回归测试。
- 状态：已处理；当前账户额度不足仍是外部运行阻断，已提交的第 4、8 格任务通过同一 taskId 轮询成功，未重复提交；剩余 7 格待额度恢复后按 checkpoint 补齐。
