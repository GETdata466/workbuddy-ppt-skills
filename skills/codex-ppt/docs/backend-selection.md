# Backend & Quota Selection (WorkBuddy edition)

Read this before generating any slide image. There are two consecutive gates and
they must be resolved in order:

1. **Quota source** — whose budget pays for the pixels. Ask the user, always.
2. **Image backend** — which tool executes it. Decide from runtime capability.

Skipping gate 1 and letting the `.env` default decide is a defect: the user may
be saving subscription quota, or may be out of API credit, and either mistake
costs real money or a stalled deck.

---

## Gate 1 — Quota source (ask before the first image)

### What you must do

1. Probe whether the WorkBuddy `ImageGen` tool is callable in this session.
2. Ask the question below, with the probe result reflected in the options.
3. Wait for the answer. Do not generate a sample, a style probe, or a placeholder
   in the meantime.
4. Pass the answer down as `--provider` on **every** `scripts/image_gen.py` call
   in this deck, including slide subagents.
5. Record it in `deck_spec.json` (fields at the bottom of this section).

### Ask text (ImageGen callable)

```text
开始生图前先确认额度来源，三选一（整套 PPT 会固定用同一个来源）：

1. ImageGen 工具 — 当前会话可调用，走 WorkBuddy 平台额度，无需配置
2. Codex 本地额度 — 用本机 ChatGPT 订阅额度，零 API 费用，单页约 1–2 分钟
3. 中转站 API — 按量计费，直连速度快，可高并发

选哪个？
```

### Ask text (ImageGen not callable)

```text
开始生图前先确认额度来源，二选一（整套 PPT 会固定用同一个来源）：

1. Codex 本地额度 — 用本机 ChatGPT 订阅额度，零 API 费用，单页约 1–2 分钟，并发上限 3
2. 中转站 API — 按量计费，直连速度快，可高并发

选 1 的话我不会自动回落到中转站：本地额度失败就直接报错停下，避免偷偷扣费。
选哪个？
```

### Mapping the answer to commands

| User answer | Enforcement | Effect |
|---|---|---|
| Codex 本地额度 | `--provider codex-oauth` | Forces `codex exec`; paid-API fallback **disabled** |
| 中转站 API | `--provider api` | Uses `CODEX_PPT_API_BASE_URL` + `OPENAI_API_KEY` |
| ImageGen 工具 | Do not call the CLI | Generate through the `ImageGen` tool |
| No answer / ambiguous | Stop | Ask again; never fall back to a default silently |

`--provider auto` exists for unattended reruns and keeps the legacy behaviour
(follow `OPENAI_BASE_URL`, allow the fallback). Never use it as a substitute for
asking, and never use it when the user said they are short on API credit.

### Why the fallback switch matters

The default CLI path (`codex-oauth`) can drop to a metered API when the Codex
call fails. That is a reasonable safety net for an unattended pipeline, and a
bug when the user just said "use my subscription, I am low on API credit".
`--provider codex-oauth` therefore sets `allow_fallback=False`: a failure surfaces
as an error with the deck blocked, not as an unexpected invoice.

### Recording the decision

In `deck_spec.json`:

```json
{
  "quota_source": "codex-oauth | api | imagegen",
  "provider_flag": "--provider codex-oauth | --provider api | null",
  "quota_source_confirmed_at": "<ISO 8601 timestamp>"
}
```

Every slide result recorded through `record_slide_result.py` must report the same
source. If a later slide reports a different one, treat it as a blocker and
investigate before assembling the deck.

### Re-asking rules

- New deck → ask again. A choice is per-deck, not per-machine.
- Same deck, already answered → reuse it. Restate it in one line before the next
  batch ("继续用 Codex 本地额度，不再询问") instead of re-asking.
- User changes their mind mid-deck → allowed, but ask whether to regenerate the
  slides that used the previous source.
- User pre-declares before the question (for example "全程用中转站，别问") →
  honour it, state it back once, and record it.

---

## Gate 2 — Image backend

Two backends:

1. **WorkBuddy `ImageGen` tool** — the native surface. Use it when gate 1 selected
   it and the tool is callable.
2. **Local CLI** — `scripts/image_gen.py`. Provider is chosen by `--provider` as
   decided in gate 1.

### Decision rules

- Verify callability actively; do not infer it from the agent name, the
  subscription, or a previous session.
- Resolution, quality, aspect ratio, or slide-edit requests are not reasons to
  switch backend. First check which parameters the tool exposes.
- Switch to the CLI only when one of these holds:
  - `ImageGen` is unavailable in this session.
  - `ImageGen` failed for a capability this deck requires (exact pixel size,
    reference-image editing, batch output to explicit paths).
  - Gate 1 selected a CLI quota source, or the user explicitly asked for it.
- Do not switch merely because the CLI has `--out` paths or simpler automation.
- Do not name a specific GPT Image model for `ImageGen` unless the environment
  provides evidence. If exact model, size, or quality is required and the tool
  does not expose it, say so and agree on the CLI path.
- The CLI loads its runtime `.env` automatically (`${CODEX_PPT_HOME:-~/.workbuddy/ppt-skill}/.env`,
  falling back to `~/.codex-ppt-skill/.env` when the new home has no config). Run
  it normally; do not parse `.env` by hand or ask for configuration pre-emptively.
- Ask for `OPENAI_API_KEY` only after gate 1 selected the API source and the CLI
  reports missing or invalid configuration. Never raise it while `ImageGen` is
  usable, and never raise it when the user chose the subscription quota.
- Report which provider actually produced the image. The CLI prints
  `Quota source: --provider <choice> -> codex-oauth (…)` or `api (…)` on every run.
  Record that provenance rather than assuming.

### Confirmation text

`ImageGen` backend:

```text
我确认当前会话可以调用 ImageGen 工具，因此用 ImageGen 生成样张，不切到本地 CLI。可以开始生成 1 页样张吗？
```

Local CLI backend:

```text
额度来源已确认：<Codex 本地额度 / 中转站 API>。
用本地 CLI scripts/image_gen.py --provider <codex-oauth / api> 生成样张，整套 PPT 都固定用这个来源。
<若 codex-oauth：失败会直接报错停止，不会自动回落到中转站。>
可以开始生成 1 页样张吗？
```

Wait for confirmation before generating the sample slide. If the user questions
the backend, resolve that first.

---

## Provider notes (CLI path)

- `codex-oauth`：调用本机 `codex exec` 的内置生图能力，消耗 ChatGPT 订阅额度。
  需要本机能访问 `chatgpt.com`；代理端口会被自动探测（`7897`、`7890`、`7891`、
  `7892`、`10808`、`1080`），也可用 `CODEX_PROXY` 强制指定。
- `api`：任意 OpenAI 兼容的 Images API。端点取 `CODEX_PPT_API_BASE_URL`，未配置时
  退回 `CODEX_PPT_FALLBACK_BASE_URL`；key 取 `OPENAI_API_KEY`。
- 单页耗时：订阅额度路径通常 60–120 秒，比直连 API 慢，但边际成本为零。排期时按
  每页 1–2 分钟估算，不要把超时误判为失败。
- 并发：CLI 内部默认最多 3 个并行 Codex 进程（`CODEX_PPT_MAX_PARALLEL`）；API 路径
  用 `--concurrency`。幻灯片较多时先确认并发设置，再一次性派发。
- 首次配置成套参数（可交给用户自己跑，别让代理猜 key）：

```bash
python {skill_root}/scripts/codex_ppt_runtime.py config \
  --provider codex-oauth \
  --api-base-url https://your-relay.example.com/v1 \
  --api-key "<relay key>"
```

`config` 只改你点名的键，`.env` 里的注释、代理端口、超时设置都会原地保留。
