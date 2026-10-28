# Image Model Configuration

Use this reference only when the local API/CLI fallback is needed and the runtime config is missing or must be changed.

Do not manually parse `.env`. The fallback CLI loads the shared config automatically. Run the fallback command first, then use this document only if the CLI reports missing or invalid configuration.

Ask the user to configure or update settings only when:

- The fallback CLI reports missing `OPENAI_API_KEY`.
- The user explicitly wants to change API key, base URL, or model.
- A real API call fails with authentication, permission, base URL, or model-not-found errors.

## When Configuration Is Needed

Configure image API access only for API/CLI fallback image generation.

Typical cases:

- The user wants a third-party API or an OpenAI-compatible proxy for image generation.
- The WorkBuddy `ImageGen` tool is unavailable and the local Codex CLI (subscription quota) also fails, so the CLI falls back to an API.

The default CLI provider is `codex-oauth`, which consumes the local Codex ChatGPT subscription and needs **no** `OPENAI_API_KEY`.
Configure an API only when that path is unavailable or the user asks for a specific provider — do not ask for a key while the subscription path works.

## Required And Optional Values

- `OPENAI_API_KEY` is required only for the API provider. The default `codex-oauth` provider does not use it.
- `OPENAI_BASE_URL` is optional. When it is unset, the CLI uses the official OpenAI API. When it is set, the CLI uses the configured third-party provider base URL.
- `CODEX_PPT_IMAGE_MODEL` is optional. The default is `gpt-image-2.5-flare`. Use `gpt-image-2.5-sunburst` to select Sunburst, or a model name supported by the provider.

Configure provided API settings with `scripts/codex_ppt_runtime.py config --api-key`. The config command writes `~/.workbuddy/ppt-skill/.env`.

## Official OpenAI Example

```bash
python {skill_root}/scripts/codex_ppt_runtime.py config \
  --api-key "your-api-key" \
  --model gpt-image-2.5-flare
```

## OpenAI-Compatible Provider Example

Use this shape for providers that implement the OpenAI Images API paths used by the fallback CLI.

```bash
python {skill_root}/scripts/codex_ppt_runtime.py config \
  --api-key "your-provider-api-key" \
  --base-url "https://xxxx.example.com/v1" \
  --model gpt-image-2.5-flare
```

This produces the same effective runtime config as:

```env
OPENAI_API_KEY=your-provider-api-key
OPENAI_BASE_URL=https://xxxx.example.com/v1
CODEX_PPT_IMAGE_MODEL=gpt-image-2.5-flare
```

For OpenAI-compatible providers, `OPENAI_BASE_URL` should normally end at the provider's `/v1` root. Do not set it to `/images/generations`, `/images/edits`, or another terminal endpoint. The fallback CLI appends the image-generation or image-edit path through the OpenAI SDK.

Use the provider's model name only when the provider documents a custom name. Otherwise prefer `gpt-image-2.5-flare`.

## AtlasCloud Example

For AtlasCloud, set `--model` to the base model name. The CLI chooses the matching generation or editing model route internally. This example retains the known `gpt-image-2` route; check provider documentation before selecting a 2.5 model.

```bash
python {skill_root}/scripts/codex_ppt_runtime.py config \
  --api-key "your-atlascloud-api-key" \
  --base-url "https://api.atlascloud.ai/api/v1/model" \
  --model openai/gpt-image-2
```

## Runtime Config

The config is written to:

```text
~/.workbuddy/ppt-skill/.env
```

The file is created with mode `0600`. It is shared by every agent that runs this skill's CLI on this machine, including WorkBuddy sessions.

Process environment variables override `.env` values. A command-line `--model` overrides `CODEX_PPT_IMAGE_MODEL` for that single command.
