# Backend Selection (WorkBuddy edition)

Read this before confirming the image backend or generating the first sample slide.

This skill ships two image backends:

1. **WorkBuddy `ImageGen` tool** — preferred whenever it is callable in the current session. It is the native image generation surface of the WorkBuddy runtime.
2. **Local CLI** — `scripts/image_gen.py`. Its default provider is the local Codex CLI using the ChatGPT subscription quota (`OPENAI_BASE_URL=codex-oauth://local`), with automatic fallback to a configured OpenAI-compatible API.

## Decision Rules

- Before recommending the CLI, actively verify whether the `ImageGen` tool is callable in this session. Do not infer availability from the agent name, the subscription, or the fact that a previous session could call it.
- Prefer `ImageGen` when it is available. Resolution, quality, aspect ratio, or slide-edit requests alone are not sufficient reasons to switch to the CLI — first check which parameters the tool actually exposes.
- Switch to the CLI only when one of these holds:
  - `ImageGen` is unavailable in this session.
  - `ImageGen` failed for a capability this deck requires (exact pixel size, reference-image editing, batch output to explicit paths).
  - The user explicitly asks for the CLI, a third-party image API, or provider adapters.
- Do not switch merely because the CLI offers direct `--out` paths, easier local file management, or simpler automation.
- Do not name a specific GPT Image model for `ImageGen` output unless the environment provides evidence. If exact model, size, or quality is required and the tool does not expose it, say so and agree with the user on the CLI path.
- The CLI loads its runtime `.env` automatically (`${CODEX_PPT_HOME:-~/.workbuddy/ppt-skill}/.env`, falling back to `~/.workbuddy/ppt-skill/.env` when the new home has no config). Run the CLI normally; do not parse `.env` by hand or ask for configuration pre-emptively.
- Ask for `OPENAI_API_KEY` only after you have deliberately selected the CLI and it reports missing or invalid configuration, or when the user wants to change API settings. Never raise `OPENAI_API_KEY` while `ImageGen` is usable — the default CLI provider consumes the subscription quota and needs no key.
- Report which provider actually produced the image. When the CLI runs on the Codex OAuth provider it prints `Codex CLI backend using the ChatGPT subscription quota`; when it falls back it prints the API base URL. Record that provenance in `deck_spec.json` / `slide_jobs.json` rather than assuming.

## Confirmation Text

`ImageGen` backend:

```text
我确认当前会话可以调用 ImageGen 工具，因此用 ImageGen 生成样张，不切到本地 CLI。可以开始生成 1 页样张吗？
```

Local CLI backend:

```text
当前会话无法调用 ImageGen（或它缺少本页必需的参数），因此改用本地 CLI scripts/image_gen.py 生成样张。
该 CLI 默认使用本机 Codex 的 ChatGPT 订阅额度，不需要 API key；失败时会自动回落到已配置的 OpenAI 兼容接口。
可以开始生成 1 页样张吗？
```

Wait for confirmation before generating the sample slide. If the user questions the backend, resolve that first.

## Provider Notes (CLI path)

- `codex-oauth`（默认）：调用本机 `codex exec` 的内置生图能力，消耗 ChatGPT 订阅额度。需要本机能访问 `chatgpt.com`；代理端口会被自动探测（`7897`、`7890`、`7891`、`7892`、`10808`、`1080`），也可用 `CODEX_PROXY` 强制指定。
- `api`：任意 OpenAI 兼容的 Images API，由 `OPENAI_BASE_URL` + `OPENAI_API_KEY` 配置。仅在 `codex-oauth` 失败且配置了 `CODEX_PPT_FALLBACK_BASE_URL` 时自动使用。
- 单页耗时：订阅额度路径通常 60–120 秒，比直连 API 慢，但边际成本为零。排期时按每页 1–2 分钟估算，不要把超时误判为失败。
- 并发：CLI 内部默认最多 3 个并行 Codex 进程（`CODEX_PPT_MAX_PARALLEL`）。幻灯片较多时先确认并发设置，再一次性派发。
