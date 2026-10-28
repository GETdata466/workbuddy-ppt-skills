"""Offline test suite for the quota-source gate (--provider).

Run: python tests/test_provider_selection.py

Everything here is offline: no Codex process, no network call, no image spend.
Covers flag parsing, base-URL rewriting, the paid-API fallback switch, env-file
upsert safety, and the CLI's dry-run provenance line.
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "codex-ppt" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import image_gen as ig  # noqa: E402
import image_providers.factory as factory  # noqa: E402

RESULTS = []
SCRATCH = Path(tempfile.mkdtemp(prefix="codex-ppt-provider-test-"))

PROVIDER_ENV_KEYS = (
    "OPENAI_BASE_URL",
    "OPENAI_API_KEY",
    "CODEX_PPT_API_BASE_URL",
    "CODEX_PPT_FALLBACK_BASE_URL",
    "CODEX_PPT_DEFAULT_PROVIDER",
    "CODEX_PPT_HOME",
)


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(("PASS  " if ok else "FAIL  ") + name + (f"   [{detail}]" if detail else ""))


class EnvSandbox:
    """Apply and restore the provider-related environment variables."""

    def __init__(self, **values):
        self.values = values
        self.saved = {}

    def __enter__(self):
        for key in PROVIDER_ENV_KEYS:
            self.saved[key] = os.environ.get(key)
            os.environ.pop(key, None)
        os.environ.update({k: v for k, v in self.values.items() if v is not None})
        return self

    def __exit__(self, *exc):
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        return False


def capture_die(fn, *args, **kwargs):
    """Return (exit_code, stderr_text) for a function that may call _die."""
    import io
    import contextlib

    buf = io.StringIO()
    code = 0
    with contextlib.redirect_stderr(buf):
        try:
            fn(*args, **kwargs)
        except SystemExit as exc:
            code = int(exc.code or 0)
    return code, buf.getvalue()


def main():
    # --------------------------------------------------------------- 1 ------
    # Explicit codex-oauth: base URL is forced and the paid fallback is off.
    with EnvSandbox(OPENAI_API_KEY="sk-test", CODEX_PPT_FALLBACK_BASE_URL="https://relay.example/v1"):
        resolved = ig.resolve_provider_choice("codex-oauth")
        check("codex-oauth resolves to codex-oauth", resolved == ig.PROVIDER_CODEX_OAUTH, resolved)
        check(
            "codex-oauth rewrites base url",
            os.environ.get("OPENAI_BASE_URL") == "codex-oauth://local",
            str(os.environ.get("OPENAI_BASE_URL")),
        )
        check("codex-oauth disables fallback", ig._allow_fallback("codex-oauth") is False)

    # --------------------------------------------------------------- 2 ------
    # Explicit api: the relay endpoint wins over the codex-oauth scheme.
    with EnvSandbox(
        OPENAI_API_KEY="sk-test",
        OPENAI_BASE_URL="codex-oauth://local",
        CODEX_PPT_API_BASE_URL="https://relay.example/v1",
        CODEX_PPT_FALLBACK_BASE_URL="https://fallback.example/v1",
    ):
        resolved = ig.resolve_provider_choice("api")
        check("api resolves to api", resolved == ig.PROVIDER_API, resolved)
        check(
            "api prefers CODEX_PPT_API_BASE_URL",
            os.environ.get("OPENAI_BASE_URL") == "https://relay.example/v1",
            str(os.environ.get("OPENAI_BASE_URL")),
        )
        check("api keeps fallback flag true", ig._allow_fallback("api") is True)

    # --------------------------------------------------------------- 3 ------
    # api without any endpoint must fail loudly instead of silently spending
    # the subscription quota.
    with EnvSandbox(OPENAI_API_KEY="sk-test", OPENAI_BASE_URL="codex-oauth://local"):
        code, err = capture_die(ig.resolve_provider_choice, "api")
        check("api without endpoint exits non-zero", code != 0, str(code))
        check(
            "api without endpoint explains the fix",
            "CODEX_PPT_API_BASE_URL" in err,
            err.strip().splitlines()[-1][:60] if err.strip() else "",
        )

    # --------------------------------------------------------------- 4 ------
    # auto keeps the legacy behaviour: follow .env, fallback still available.
    with EnvSandbox(OPENAI_API_KEY="sk-test", OPENAI_BASE_URL="codex-oauth://local"):
        resolved = ig.resolve_provider_choice("auto")
        check("auto follows env codex-oauth", resolved == ig.PROVIDER_CODEX_OAUTH, resolved)
        check("auto keeps fallback flag true", ig._allow_fallback("auto") is True)

    with EnvSandbox(OPENAI_API_KEY="sk-test", OPENAI_BASE_URL="https://relay.example/v1"):
        resolved = ig.resolve_provider_choice("auto")
        check("auto follows env api", resolved == ig.PROVIDER_API, resolved)

    # --------------------------------------------------------------- 5 ------
    # Unknown values are rejected rather than guessed.
    with EnvSandbox(OPENAI_BASE_URL="codex-oauth://local"):
        code, _ = capture_die(ig.resolve_provider_choice, "chatgpt")
        check("invalid provider rejected", code != 0, str(code))

    # --------------------------------------------------------------- 6 ------
    # The factory must honour allow_fallback=False.
    with EnvSandbox(OPENAI_API_KEY="sk-test", CODEX_PPT_FALLBACK_BASE_URL="https://relay.example/v1"):
        p_off = factory.create_image_provider(
            api_key="sk-test", base_url="codex-oauth://local", allow_fallback=False
        )
        check("factory disables fallback key", p_off._fallback_key is None)
        check("factory disables fallback url", p_off._fallback_base_url is None)

        p_on = factory.create_image_provider(
            api_key="sk-test", base_url="codex-oauth://local", allow_fallback=True
        )
        check("factory keeps fallback url", p_on._fallback_base_url == "https://relay.example/v1")

    # --------------------------------------------------------------- 7 ------
    # A non-codex base URL yields the compatible provider regardless of flag.
    provider_api = factory.create_image_provider(
        api_key="sk-test", base_url="https://relay.example/v1", allow_fallback=False
    )
    check(
        "api base url builds compatible provider",
        provider_api.__class__.__name__ == "OpenAICompatibleImageProvider",
        provider_api.__class__.__name__,
    )

    # --------------------------------------------------------------- 8 ------
    # Env-file upsert must not destroy comments or unrelated settings.
    import codex_ppt_runtime as rt

    env_path = SCRATCH / "upsert" / ".env"
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text(
        "\n".join(
            [
                "# 手动注释：切回按量计费就改下面一行",
                "OPENAI_BASE_URL=codex-oauth://local",
                "CODEX_PPT_FALLBACK_BASE_URL=https://relay.example/v1",
                "CODEX_PPT_TIMEOUT=600",
                "",
            ]
        ),
        encoding="utf-8",
    )
    rt._upsert_env_file(
        env_path,
        {"CODEX_PPT_DEFAULT_PROVIDER": "codex-oauth", "CODEX_PPT_API_BASE_URL": "https://relay.example/v1"},
    )
    text = env_path.read_text(encoding="utf-8")
    check("upsert keeps comments", "手动注释" in text)
    check("upsert keeps unrelated keys", "CODEX_PPT_TIMEOUT=600" in text)
    check("upsert keeps fallback endpoint", "CODEX_PPT_FALLBACK_BASE_URL=https://relay.example/v1" in text)
    check("upsert writes new provider key", "CODEX_PPT_DEFAULT_PROVIDER=codex-oauth" in text)
    check("upsert writes api base", "CODEX_PPT_API_BASE_URL=https://relay.example/v1" in text)
    check("upsert does not duplicate keys", text.count("OPENAI_BASE_URL=") == 1, str(text.count("OPENAI_BASE_URL=")))

    # Removal path (config --clear-base-url).
    rt._upsert_env_file(env_path, {}, ["OPENAI_BASE_URL"])
    text_removed = env_path.read_text(encoding="utf-8")
    check("upsert removal drops the key", "OPENAI_BASE_URL=" not in text_removed)
    check("upsert removal keeps api base", "CODEX_PPT_API_BASE_URL=" in text_removed)

    # --------------------------------------------------------------- 9 ------
    # The CLI must expose the flag and echo the resolved source on dry-run.
    env = dict(os.environ)
    env["CODEX_PPT_HOME"] = str(SCRATCH / "home")
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "image_gen.py"),
            "generate",
            "--provider",
            "codex-oauth",
            "--prompt",
            "sample",
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    check("cli dry-run exit 0", proc.returncode == 0, str(proc.returncode))
    check(
        "cli echoes quota source",
        "Quota source: --provider codex-oauth -> codex-oauth" in proc.stderr,
        proc.stderr.strip().splitlines()[0][:70] if proc.stderr.strip() else "",
    )
    check(
        "cli reports fallback disabled",
        "fallback disabled" in proc.stderr,
        "",
    )
    check(
        "cli dry-run preview uses codex exec",
        "codex exec" in proc.stdout,
        "",
    )

    # --provider api with a relay endpoint configured in the runtime home.
    home = SCRATCH / "home"
    home.mkdir(parents=True, exist_ok=True)
    (home / ".env").write_text(
        "\n".join(
            [
                "OPENAI_API_KEY=sk-test",
                "CODEX_PPT_API_BASE_URL=https://relay.example/v1",
                "",
            ]
        ),
        encoding="utf-8",
    )
    proc_api = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "image_gen.py"),
            "generate",
            "--provider",
            "api",
            "--prompt",
            "sample",
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    check("cli api dry-run exit 0", proc_api.returncode == 0, str(proc_api.returncode))
    check(
        "cli api preview targets images endpoint",
        "/v1/images/generations" in proc_api.stdout,
        "",
    )
    check(
        "cli api uses relay base url",
        "relay.example" in proc_api.stderr,
        proc_api.stderr.strip().splitlines()[0][:70] if proc_api.stderr.strip() else "",
    )

    # --------------------------------------------------------------- 10 -----
    # Default value comes from .env when the flag is omitted.
    proc_default = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "image_gen.py"),
            "generate",
            "--prompt",
            "sample",
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        env={**env, "CODEX_PPT_DEFAULT_PROVIDER": "codex-oauth", "OPENAI_BASE_URL": "https://relay.example/v1"},
        timeout=120,
    )
    check("env default provider honoured", proc_default.returncode == 0, str(proc_default.returncode))
    check(
        "env default overrides base url",
        "codex exec" in proc_default.stdout,
        proc_default.stdout[:40],
    )

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n{passed}/{len(RESULTS)} passed")
    shutil.rmtree(SCRATCH, ignore_errors=True)
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
