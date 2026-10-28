#!/usr/bin/env python3
"""Manage the cross-agent runtime for codex-ppt.

The runtime lives outside any specific agent installation so Codex, Claude Code,
OpenClaw, Hermes Agent, and other local agents can share one virtual
environment and one API configuration.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from typing import Dict, Iterable, Optional
import urllib.error
import urllib.parse
import urllib.request
import venv


DEFAULT_RUNTIME_HOME = "~/.workbuddy/ppt-skill"
LEGACY_RUNTIME_HOME = "~/.codex-ppt-skill"


def _resolve_runtime_home() -> Path:
    """Prefer the WorkBuddy home, but keep using a populated legacy home.

    Users migrating from the upstream skill already have credentials and custom
    styles under ~/.codex-ppt-skill. When the new home has no .env yet, fall back
    to the legacy one so nothing has to be reconfigured by hand.
    """
    explicit = os.getenv("CODEX_PPT_HOME")
    if explicit:
        return Path(explicit).expanduser()
    new_home = Path(DEFAULT_RUNTIME_HOME).expanduser()
    legacy_home = Path(LEGACY_RUNTIME_HOME).expanduser()
    if not (new_home / ".env").exists() and (legacy_home / ".env").exists():
        return legacy_home
    return new_home

DEFAULT_MODEL = "gpt-image-2.5-flare"
ENV_FIELDS = (
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "CODEX_PPT_IMAGE_MODEL",
    "CODEX_PPT_DEFAULT_PROVIDER",
    "CODEX_PPT_API_BASE_URL",
)



def _runtime_home() -> Path:
    return _resolve_runtime_home()


def _skill_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _venv_python(home: Path) -> Path:
    if os.name == "nt":
        return home / ".venv" / "Scripts" / "python.exe"
    return home / ".venv" / "bin" / "python"


def _env_path(home: Path) -> Path:
    return home / ".env"


def _requirements_path() -> Path:
    return _skill_root() / "requirements.txt"


def _die(message: str, code: int = 1) -> None:
    print(f"Error: {message}", file=sys.stderr)
    raise SystemExit(code)


def _mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "****"
    return f"{value[:4]}...{value[-4:]}"


def _parse_env_file(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {}
    values: Dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def _quote_env_value(value: str) -> str:
    if not value:
        return ""
    if any(ch.isspace() for ch in value) or "#" in value or '"' in value:
        return json.dumps(value)
    return value


def _write_env_file(path: Path, values: Dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# codex-ppt shared runtime configuration",
        "# Used by Codex, Claude Code, OpenClaw, Hermes Agent, and other local agents.",
    ]
    for key in ENV_FIELDS:
        value = values.get(key, "")
        if value:
            lines.append(f"{key}={_quote_env_value(value)}")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def _upsert_env_file(
    path: Path,
    updates: Dict[str, str],
    removals: Iterable[str] = (),
) -> None:
    """Rewrite only the keys being changed, so comments and extra settings survive.

    Users hand-edit this file (proxy ports, timeouts, fallback endpoints); a full
    rewrite would silently drop those lines.
    """
    existing: List[str] = []
    if path.exists():
        existing = path.read_text(encoding="utf-8").splitlines(keepends=False)
        while existing and not existing[-1].strip():
            existing.pop()

    pending = {k: v for k, v in updates.items() if v is not None}
    drop = set(removals)
    written: set = set()
    out: List[str] = []
    for raw in existing:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            out.append(raw)
            continue
        key = line.split("=", 1)[0].strip()
        if key in drop:
            continue
        if key in pending:
            written.add(key)
            value = pending[key]
            if value:
                out.append(f"{key}={_quote_env_value(value)}")
            continue
        out.append(raw)

    if not out:
        out = [
            "# codex-ppt shared runtime configuration",
            "# Used by Codex, Claude Code, OpenClaw, Hermes Agent, and other local agents.",
        ]
    for key, value in pending.items():
        if key in written or not value or key in drop:
            continue
        out.append(f"{key}={_quote_env_value(value)}")

    out.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out), encoding="utf-8")
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def _load_env_values(home: Path) -> Dict[str, str]:
    values = _parse_env_file(_env_path(home))
    for key in ENV_FIELDS:
        if os.getenv(key):
            values[key] = os.environ[key]
    return values


def _ensure_dirs(home: Path) -> None:
    for child in (home, home / "cache", home / "logs"):
        child.mkdir(parents=True, exist_ok=True)


def _bootstrap(args: argparse.Namespace) -> int:
    home = _runtime_home()
    _ensure_dirs(home)
    python = _venv_python(home)
    if not python.exists():
        print(f"Creating virtual environment: {home / '.venv'}")
        venv.EnvBuilder(with_pip=True, clear=False).create(home / ".venv")
    else:
        print(f"Virtual environment already exists: {home / '.venv'}")

    requirements = _requirements_path()
    if not requirements.exists():
        _die(f"requirements.txt not found: {requirements}")

    cmd = [str(python), "-m", "pip", "install", "-r", str(requirements)]
    if args.upgrade:
        cmd.insert(4, "-U")
    print(f"Installing dependencies from: {requirements}")
    subprocess.run(cmd, check=True)
    print(f"Runtime ready: {home}")
    return 0


def _config(args: argparse.Namespace) -> int:
    home = _runtime_home()
    _ensure_dirs(home)
    env_file = _env_path(home)

    updates: Dict[str, str] = {}
    removals: List[str] = []
    if args.api_key:
        updates["OPENAI_API_KEY"] = args.api_key
    if args.base_url is not None:
        updates["OPENAI_BASE_URL"] = args.base_url.strip()
    if args.model is not None:
        updates["CODEX_PPT_IMAGE_MODEL"] = args.model.strip()
    if getattr(args, "api_base_url", None) is not None:
        updates["CODEX_PPT_API_BASE_URL"] = args.api_base_url.strip()
    if getattr(args, "provider", None) is not None:
        updates["CODEX_PPT_DEFAULT_PROVIDER"] = args.provider.strip()

    if args.clear_base_url:
        removals.append("OPENAI_BASE_URL")

    _upsert_env_file(env_file, updates, removals)
    print(f"Wrote {env_file}")
    values = _parse_env_file(env_file)
    for key in ENV_FIELDS:
        value = values.get(key, "")
        if key == "OPENAI_API_KEY":
            value = _mask_secret(value)
        print(f"{key}={value or '<unset>'}")
    print(f"default quota source: {_effective_provider_label(values)}")
    return 0


def _effective_provider_label(values: Dict[str, str]) -> str:
    base_url = (values.get("OPENAI_BASE_URL") or "").strip()
    if base_url.lower().startswith("codex-oauth:"):
        return "codex-oauth (local ChatGPT subscription quota)"
    return f"api ({base_url or 'https://api.openai.com/v1'})"


def _check_python_imports(python: Path) -> bool:
    if not python.exists():
        print(f"venv: missing ({python})")
        return False
    code = "import openai, PIL, pptx; print('imports ok')"
    proc = subprocess.run(
        [str(python), "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if proc.returncode != 0:
        print("dependencies: missing or broken")
        if proc.stderr.strip():
            print(proc.stderr.strip())
        return False
    print("dependencies: ok")
    return True


def _models_request(base_url: str, api_key: str, timeout: int) -> bool:
    endpoint = base_url.rstrip("/") + "/models"
    req = urllib.request.Request(
        endpoint,
        headers={"Authorization": f"Bearer {api_key}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            print(f"models endpoint: HTTP {resp.status}")
            return 200 <= resp.status < 300
    except urllib.error.HTTPError as exc:
        body = exc.read(800).decode("utf-8", "replace")
        print(f"models endpoint: HTTP {exc.code}")
        if body:
            print(body)
        return False
    except Exception as exc:
        print(f"models endpoint: {exc.__class__.__name__}: {exc}")
        return False


def _is_atlascloud_base_url(base_url: str) -> bool:
    hostname = urllib.parse.urlparse(base_url).hostname or ""
    return "atlascloud.ai" in hostname.lower()


def _doctor(args: argparse.Namespace) -> int:
    home = _runtime_home()
    env_file = _env_path(home)
    print(f"runtime home: {home}")
    print(f"env file: {env_file} ({'exists' if env_file.exists() else 'missing'})")
    print(f"venv python: {_venv_python(home)}")

    ok = True
    ok = _check_python_imports(_venv_python(home)) and ok

    values = _load_env_values(home)
    api_key = values.get("OPENAI_API_KEY", "")
    base_url = values.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    model = values.get("CODEX_PPT_IMAGE_MODEL", DEFAULT_MODEL)

    print(f"OPENAI_API_KEY={'set (' + _mask_secret(api_key) + ')' if api_key else '<unset>'}")
    print(f"OPENAI_BASE_URL={base_url}")
    print(f"CODEX_PPT_IMAGE_MODEL={model}")
    print(f"CODEX_PPT_DEFAULT_PROVIDER={values.get('CODEX_PPT_DEFAULT_PROVIDER') or 'auto'}")
    print(f"CODEX_PPT_API_BASE_URL={values.get('CODEX_PPT_API_BASE_URL') or '<unset>'}")
    print(f"active quota source: {_effective_provider_label(values)}")

    if "gpt-image-" not in model:
        print("model check: warning, model name should contain 'gpt-image-'")
        ok = False
    else:
        print("model check: ok")

    if args.check_api:
        if not api_key:
            print("api check: skipped, OPENAI_API_KEY is unset")
            ok = False
        elif _is_atlascloud_base_url(base_url):
            print("api check: AtlasCloud provider detected; skipping /models probe")
        else:
            ok = _models_request(base_url, api_key, args.timeout) and ok

    return 0 if ok else 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage the codex-ppt shared runtime")
    subparsers = parser.add_subparsers(dest="command", required=True)

    bootstrap = subparsers.add_parser("bootstrap", help="Create shared venv and install deps")
    bootstrap.add_argument("--upgrade", action="store_true")
    bootstrap.set_defaults(func=_bootstrap)

    config = subparsers.add_parser("config", help="Write or update shared .env")
    config.add_argument("--api-key")
    config.add_argument("--base-url")
    config.add_argument("--clear-base-url", action="store_true")
    config.add_argument("--model")
    config.add_argument(
        "--provider",
        choices=("auto", "codex-oauth", "api"),
        help="Default quota source used when image_gen.py is called without --provider",
    )
    config.add_argument(
        "--api-base-url",
        help="Relay / OpenAI-compatible endpoint used when the user picks the API budget",
    )
    config.set_defaults(func=_config)

    doctor = subparsers.add_parser("doctor", help="Check runtime and optional API access")
    doctor.add_argument("--check-api", action="store_true")
    doctor.add_argument("--timeout", type=int, default=30)
    doctor.set_defaults(func=_doctor)

    return parser


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
