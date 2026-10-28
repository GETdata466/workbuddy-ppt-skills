# WorkBuddy PPT Skills

把两个成熟的 PPT 技能迁移到 **WorkBuddy** 运行时：

| 技能 | 做什么 | 什么时候用 |
|---|---|---|
| `codex-ppt` | 从文章、报告、论文、笔记或大纲生成**整页图片式** PPT：每页是一张完整 16:9 图像，最后组装成 `.pptx` | 想要视觉统一、版式自由、看图胜过看文字的演示 |
| `image-to-editable-ppt` | 把图片版幻灯片、扫描件、图片式 PPT/PPTX 或 PDF 重建为**对象级可编辑** PPTX | 需要事后逐字修改、替换图表、复用模板 |

两者互补：`codex-ppt` 出图，`image-to-editable-ppt` 把图变回可编辑对象。

---

## 快速开始

### 前置条件

- WorkBuddy（技能目录 `~/.workbuddy/skills/`）
- Python 3.10+
- 生图能力，三选一：
  - 当前会话可调用 WorkBuddy 的 `ImageGen` 工具（零配置）
  - 本机装有 Codex CLI 并已用 ChatGPT 账号登录（消耗订阅额度，零 API 费用）
  - 任意 OpenAI 兼容的 Images API（key + base URL）

### 安装（macOS / Linux / Git Bash）

```bash
git clone <this-repo> workbuddy-ppt-skills
cd workbuddy-ppt-skills
bash install.sh
```

### 安装（Windows PowerShell）

```powershell
git clone <this-repo> workbuddy-ppt-skills
cd workbuddy-ppt-skills
powershell -ExecutionPolicy Bypass -File install.ps1
```

安装脚本会做三件事：复制技能到技能目录、安装 Python 依赖、以 editable 模式安装 `editppt` CLI。完成后**重启会话**，技能才会出现在可用列表里。

### 手动安装

```bash
cp -r skills/codex-ppt skills/image-to-editable-ppt ~/.workbuddy/skills/

python -m pip install -r ~/.workbuddy/skills/codex-ppt/requirements.txt
python -m pip install --editable ~/.workbuddy/skills/image-to-editable-ppt/cli

editppt doctor    # 自检：依赖、OCR token、生图后端
```

---

## 配置生图后端

`codex-ppt` 与 `image-to-editable-ppt` 的 CLI 共用同一套后端策略，优先级从高到低：

1. **WorkBuddy `ImageGen` 工具** — 会话内可调用就用它，无需任何配置。
2. **本地 Codex CLI（订阅额度）** — `OPENAI_BASE_URL=codex-oauth://local`。调用本机 `codex exec` 的内置生图能力，消耗 ChatGPT 订阅额度，**不产生 API 费用**。
3. **OpenAI 兼容 API** — 仅在 2 失败且配置了回落地址时自动使用。

### 配置文件

`codex-ppt` 的配置在 `~/.workbuddy/ppt-skill/.env`（若该目录没有 `.env` 而旧的 `~/.codex-ppt-skill/.env` 存在，会自动沿用旧配置，无需手工迁移）：

```dotenv
# 默认：本地 Codex CLI，走订阅额度
OPENAI_BASE_URL=codex-oauth://local
CODEX_PPT_IMAGE_MODEL=gpt-image-2.5-flare

# 仅回落用；订阅额度路径不需要 key
OPENAI_API_KEY=sk-...
CODEX_PPT_FALLBACK_BASE_URL=https://api.openai.com/v1

# 可选
# CODEX_PPT_PROXY=http://127.0.0.1:7897
CODEX_PPT_TIMEOUT=600
CODEX_PPT_MAX_PARALLEL=3
```

改成纯 API 模式只需把 `OPENAI_BASE_URL` 换回 `https://api.openai.com/v1`（或你的中转地址）。

### 代理

订阅额度路径需要访问 `chatgpt.com`。脚本会依次探测 `7897`、`7890`、`7891`、`7892`、`10808`、`1080`，也可用 `CODEX_PROXY` 指定。注意 Python 只认小写代理变量（`https_proxy`），两个技能内部都已同时设置大小写，避免被环境里的其他代理截走。

### OCR（仅 `image-to-editable-ppt`）

文字位置与字号依赖 PaddleOCR 的免费个人额度。在 <https://aistudio.baidu.com/account/accessToken> 申请后：

```bash
editppt config --paddle-ocr-token <token>
```

不配置也能跑，但会退化为本地文字测量，重建后的字号稳定性明显下降。

---

## 与上游的差异

| 项目 | 上游 | 本仓库 |
|---|---|---|
| 生图后端 | 内置 `image_gen` / `image_generate` | `ImageGen` 工具；CLI 默认走本地 Codex CLI 订阅额度 |
| 逐页子代理 | Codex subagent 机制 | WorkBuddy `Agent` 工具（`general-purpose`），交接模板不变 |
| CLI 安装 | `pipx install --editable` | `pip install --editable`（WorkBuddy 环境通常没有 pipx） |
| 运行目录 | `~/.codex-ppt-skill` | `~/.workbuddy/ppt-skill`，无配置时回退旧目录 |
| 后端工具名 | `image_gen.imagegen` | `ImageGen`（`--image-backend builtin-imagegen` 取值不变） |
| 新增能力 | — | `image_providers/codex_oauth.py`：用订阅额度生图，独立临时目录并发隔离、比例自适应、读锁重试、API 回落 |
| 文档语言 | 英文 | 中文导读 + 英文正文 |

工作流本身没有改动：审批门、状态脚本、验收标准、页面决策树均保持上游设计。

---

## 已知限制

- **`codex-ppt` 产出的页面是整张图片**，文字不可直接编辑。需要可编辑时，再用 `image-to-editable-ppt` 跑一遍。
- **订阅额度路径较慢**：每页约 60–120 秒（含 Codex 启动与多轮工具调用）。10 页并发 3 约需 5–8 分钟，别把超时当失败。
- **`codex exec` 必须重定向 stdin**。非交互环境下它会一直等待输入，表现为“卡住”。两个技能内部已用 `stdin=DEVNULL` 处理。
- **Windows 临时目录**：若系统 `TEMP` 指向受限位置，读到刚写入的 PNG 可能报 `Permission denied`。脚本已把工作目录放到 `${CODEX_PPT_HOME:-~/.workbuddy/ppt-skill}/scratch` 并带重试；仍失败时可显式设置 `CODEX_PPT_SCRATCH`。
- **订阅额度的可用性取决于账号**：额度耗尽或 `chatgpt.com` 不可达时，Codex 路径会失败并（若已配置）回落到 API。技能会如实报告实际使用的后端。

---

## 测试

订阅额度后端带一套离线测试，用注入式替身替代 `subprocess`，**不启动 Codex、不消耗额度**：

```bash
python tests/test_codex_oauth_provider.py
```

覆盖 32 项断言：命令参数拼装、并发任务工作目录隔离、16:9 比例自适应、PNG/JPEG 转换、文件被占用时的读取重试、Codex 失败后的 API 回落、找不到 Codex 时的报错、参考图编辑、代理变量传递等。

## 目录结构

```text
workbuddy-ppt-skills/
├── README.md
├── LICENSE
├── install.sh
├── install.ps1
├── tests/
│   └── test_codex_oauth_provider.py
└── skills/
    ├── codex-ppt/
    │   ├── SKILL.md
    │   ├── docs/            # 工作流、后端、装配、风格库等规则
    │   ├── prompts/         # 逐页子代理交接模板
    │   ├── references/      # 内置视觉风格
    │   └── scripts/
    │       ├── image_gen.py
    │       ├── assemble_ppt.py
    │       └── image_providers/
    │           └── codex_oauth.py   # 新增：订阅额度后端
    └── image-to-editable-ppt/
        ├── SKILL.md
        ├── cli/editppt/     # editppt CLI
        ├── prompts/ references/ scripts/
```

## 许可

MIT，见 [LICENSE](LICENSE)。上游版权归 [ningzimu](https://github.com/ningzimu)，本仓库的 WorkBuddy 适配部分以同一许可发布。
