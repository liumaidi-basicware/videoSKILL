# AI 营销视频工作室

> **你出创意，我出成片。** 从一句话需求到可发布的营销视频，全程引导式完成。

## 文档导航

| 你是谁 | 看这个 |
|--------|--------|
| **我是客户，想做视频** | [CUSTOMER_GUIDE.md](CUSTOMER_GUIDE.md) — 客户使用指南 |
| **我是交付人员** | [DELIVERY_CHECKLIST.md](DELIVERY_CHECKLIST.md) — 交付检查清单 |
| **我是技术负责人** | [PROJECT_OVERVIEW.md](PROJECT_OVERVIEW.md) — 项目概览 |
| **我是开发者** | 继续往下读本文档 |

---

## 开发者文档

> **使用前先解压：** 不要直接把 zip 当作工作目录。请先解压，再用 Kilo、Codex、Hermes 或其他兼容 Agent 打开 `marketing-video-agent/` 文件夹。Codex Desktop 的专用操作见 `START_HERE_CODEX_DESKTOP.md`，该文档只是宿主适配说明，不代表公共流程依赖 Codex。
>
> **分镜图片说明：** 分镜图片只有在实际运行 `python3 scripts/storyboard.py --plan output/storyboard_plan.json --out-dir output/storyboard --model gpt-image-2 --json` 后才会生成。成功日志必须看到 `[gpt-image-2] rendering ...` 和 `"ok": true`。默认会写入 `output/storyboard/<run-id>/`，每次会话独立保存，避免覆盖旧的人物板/故事板。如果当前 Agent 没有这些日志，说明还没有调用 gpt-image-2。若宿主界面不显示本地路径图片，请改看返回 JSON 里的 `preview_html` 或 `embedded_md`。

这是一套**客户无关的 AI 营销数字员工通用包**。它可以服务任意品牌/客户：先把客户一句话需求，通过**引导式顾问对话**共创成高质量剧本和逐段分镜；剧本确认后，用 **gpt-image-2** 生成**人物六视图人物板 + 电影级 16:9、格数由剧本分镜数量决定的故事板**让客户看图确认；最后再生成**真人数字人短视频 / 动态文字视频 / 产品展示视频**。

> **故事板转视频说明：** 客户确认 `shot_*.jpg` 后，出视频必须开启 `video_engine.py --storyboard-ref` 或在 `segments.json` 写 `"storyboard_ref": true`。默认优先使用 Seedance 的原生故事板能力：提交已确认故事板/contact sheet + 当前镜头确认素材，并通过 `segment/panel_index` 明确只执行当前镜头，同时利用前后镜头关系保证连贯。只有 Seedance 不可用或回落 Kling 时，才自动生成当前镜头的 16:9 单格展开图并提交给 Kling。硬性要求：逐 shot 独立生成；严格保持角色、产品、场景、光线、声音人设、BGM 氛围和故事顺序一致；不要添加额外角色，不要改变剧情、服装、道具、产品外观和场景关系。

> **专业分镜补强说明：** 本包保留自己的 0 基础顾问式引导，不会改成复杂填表；但在脚本定稿后，会参考 `references/professional-storyboard-enrichment.md` 补齐电影级运镜、构图、角色/场景/道具资产提示词、微表情和 BGM/SFX 连续性，再生成 16:9 动态格数故事板。

> 包内自带 `assets/<client>/`、`brand/<client>/`、`actors/<client>/` 只是演示样例，不是业务绑定。正式客户使用时，当前 Agent 会使用 `--client <客户英文代号>` 在 `assets/<client>/`、`brand/<client>/`、`actors/<client>/` 下生成独立资料。

---

## 一、开始使用（无需先装任何东西）

**你不需要先在终端跑安装脚本。** 直接：

1. 解压本包。
2. 用你选择的任意兼容 Agent 打开解压后的文件夹，并让它读取 `AGENT_ENTRY_PROTOCOL.md`、项目级 `AGENTS.md` 和该宿主的薄适配入口。
3. 在对话框输入 **`/basicrouter-video`**；若宿主不支持 slash command，则让它执行同名入口协议。这是唯一要记的总入口。

它会自动完成首次准备：
- 检测运行环境，**如果缺东西就当场自动装好**（Python 依赖 + Node.js + 字幕/动效引擎，首次约一两分钟，需联网）。
- 引导你**粘贴一次 API Key**（`sk-` 开头，之后自动记住，全程只填这一次）。
- 询问或识别客户/品牌代号，例如 `acme`、`hotelhk`、`<client>`，之后所有资料都按该 client 隔离保存。
- 用一张菜单帮你选想做的视频类型，然后一步步带你共创脚本、分镜、故事板和成片。

不知道敲什么时，永远先敲 `/basicrouter-video`，它会带你往下。

---

## 二、标准视频生成流程

```text
/basicrouter-video
  → 环境/API Key 检查
  → 确定 client 代号
  → 导入产品/品牌/人物素材
  → 读取 assets/<client>/brief.json，脚本接地
  → 顾问式共创剧本
  → 每段生成完整分镜：角度、景别、构图、动作、镜头运动、锚定图
  → 确认剧本 + 逐段分镜
  → 确认出场人物/数字人/音色
  → gpt-image-2 生成人物六视图人物板 + 故事板
  → 客户确认故事板
  → 确认渲染方案
  → video_engine.py 生成底片
  → 每段 formal take review + accept-take/retry
  → OCR clear 或精确 waiver
  → assemble 并审批 video
  → derive-captions + 客户确认 caption artifact
  → HyperFrames/subtitle overlay 生成 captioned basecut
  → final_edit.py + formal media QC + 客户审批 final
  → pipeline.py delivery create/verify
```

关键原则：

- **脚本没定稿，不出片。**
- **故事板没确认，不出片。**
- **每段分镜必须有剪辑跨度和专业画面设计**：相邻镜头要有 30°–50° 机位偏移，或远景/中景/近景/特写变化，避免连续正面半身口播；每段 `storyboard_plan.json` 尽量补齐景别、运镜、构图、光线、微表情、角色动作、场景/道具资产提示词和音频连续性。
- **素材图 + 文字台词 → 图生视频** 是默认路径，避免纯文生视频胡编产品。
- 正式流程缺 reviews、accepted take、OCR 证据、caption artifact 或 formal QC 时不会交付。
- `--draft` 仅用于内部预览，不是正式客户成片。

---

## 三、你会用到的几个说法

| 你想做的事 | 对当前 Agent 说 |
|---|---|
| 开始 / 不知道敲什么 | `/basicrouter-video` |
| 首次初始化 / 配置密钥 | `/setup` |
| 导入产品图/PPT/PDF/Word | `导入产品资料` |
| 设置品牌 Logo 和风格 | `配置品牌` |
| 生成数字人形象 | `做一个数字人形象` |
| 普通口播（粤语/普通话） | `做一条口播` |
| 访谈形式 | `做一条访谈` |
| 实景 + 服务介绍 | `做一条服务介绍` |
| 动态文字/字幕动效 | `做一条动态文字视频` |
| 整合营销方案（含排期预算） | `做一份营销方案` |
| 社交快剪（5-10秒） | `做一条社交快剪` |
| 产品实拍演示（30秒-1分钟） | `做一条产品演示` |
| 品牌广告片 TVC（15-20秒） | `做一条品牌TVC` |

---

## 四、可选：独立终端部署

如果你更习惯先在终端一次性装好环境，也可以在包目录跑：

macOS / Linux：

```bash
./deploy.sh
```

Windows：

```powershell
powershell -ExecutionPolicy Bypass -File .\deploy.ps1
```

但这一步不是必须的；支持 inline 自举的 Agent 可直接运行 `/basicrouter-video` 自动准备环境。

---

## 五、常见问题

- **这个包是否只适用于 客户品牌？** 不是。客户品牌 只是内置演示客户。正式客户会使用自己的 `--client <客户代号>`、品牌包、演员库和素材 brief。
- **成片在哪？** 生成的视频都在 `output/` 文件夹，当前 Agent 也会把可播放的绝对路径发给你。
- **装依赖失败？** 多为网络问题。确认能访问 PyPI / npm；网络受限时可让当前 Agent 换镜像源重试。
- **没有某个特定 Agent / 没有 Node？** 本项目不要求指定 Agent；任选兼容宿主即可。deploy 脚本会处理业务运行依赖，但不会擅自安装或切换宿主 Agent。
- **视频生成失败？** 可能是 API Key 无效、余额不足、限流或网络超时。当前 Agent 应用客户能理解的语言说明原因和下一步。
- **想看流程画布？** 可用 `python3 scripts/workflow_canvas.py generate ...` 一次性导出；或用 `python3 scripts/workflow_canvas.py serve ...` 起一个会自动刷新、能记录评论和历史轨迹的本地工作台。它会自动从当前 workspace 里反推 `client / run-id`，把 `manifest / brief / storyboard_result / segments / run.log` 汇成一页画布，显示当前步骤、素材引用、修改回路和事件流。

生成的成片、口型、Logo 稳定性以实际结果为准；建议人工过目一遍再发布。
