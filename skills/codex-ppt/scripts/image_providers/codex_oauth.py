"""Image provider backed by the local Codex CLI (ChatGPT subscription quota).

This adapter lets codex-ppt use the `codex exec` agent and its built-in
`image_gen` tool instead of a paid Images API. It is selected when
``OPENAI_BASE_URL`` uses the ``codex-oauth://`` scheme, for example::

    OPENAI_BASE_URL=codex-oauth://local

Design notes
------------
* Isolation: every request runs in its own temporary working directory and the
  agent is asked to copy the result to ``out.png``. Parallel jobs therefore
  never race for files in the shared ``~/.codex/generated_images`` tree.
* Fallback: if the agent does not honour the copy instruction, the newest file
  under ``~/.codex/generated_images`` created during the call is used instead.
* Aspect ratio: codex-ppt stretches slide images to the full 16:9 placeholder,
  so results are center-cropped/rescaled to the requested ``size`` here.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import glob
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .base import ImageProvider


SCHEME = "codex-oauth"
DEFAULT_TIMEOUT = 600.0
DEFAULT_PROXY_PORTS = (7897, 7890, 7891, 7892, 10808, 1080)
CODEX_BIN_GLOBS = (
    "~/AppData/Local/OpenAI/Codex/bin/*/codex.exe",
    "~/AppData/Local/Programs/codex/codex.exe",
)
RUNTIME_HOME = "~/.workbuddy/ppt-skill"
LEGACY_RUNTIME_HOME = "~/.codex-ppt-skill"


def is_codex_oauth_base_url(base_url: Optional[str]) -> bool:
    if not base_url:
        return False
    return base_url.strip().lower().startswith(f"{SCHEME}:")


# --------------------------------------------------------------------------- #
# environment helpers
# --------------------------------------------------------------------------- #
def _resolve_runtime_home() -> Path:
    """Prefer the WorkBuddy home, falling back to a populated legacy home."""
    explicit = os.getenv("CODEX_PPT_HOME")
    if explicit:
        return Path(explicit).expanduser()
    new_home = Path(RUNTIME_HOME).expanduser()
    legacy_home = Path(LEGACY_RUNTIME_HOME).expanduser()
    if not (new_home / ".env").exists() and (legacy_home / ".env").exists():
        return legacy_home
    return new_home


def _runtime_env() -> Dict[str, str]:
    """Parse the runtime .env without the caller's environment whitelist."""
    path = _resolve_runtime_home() / ".env"
    values: Dict[str, str] = {}
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        return {}
    return values


def find_codex_bin() -> Optional[str]:
    explicit = os.getenv("CODEX_BIN") or os.getenv("CODEX_PPT_CODEX_BIN")
    if explicit and Path(explicit).expanduser().is_file():
        return str(Path(explicit).expanduser())

    found: List[str] = []
    for pattern in CODEX_BIN_GLOBS:
        found.extend(p for p in glob.glob(str(Path(pattern).expanduser())) if os.path.isfile(p))
    if found:
        found.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        return found[0]

    which = shutil.which("codex")
    return which or None


def _proxy_reachable(port: int, host: str = "127.0.0.1", timeout: float = 1.0) -> bool:
    import socket

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def detect_proxy() -> Optional[str]:
    """Resolve the proxy used for chatgpt.com.

    Order: ``CODEX_PROXY`` env > ``CODEX_PPT_PROXY`` in runtime .env > port probe.
    """
    for candidate in (os.getenv("CODEX_PROXY"), os.getenv("CODEX_PPT_PROXY"), _runtime_env().get("CODEX_PPT_PROXY")):
        if candidate:
            return candidate.strip()
    ports = os.getenv("CODEX_PPT_PROXY_PORTS") or _runtime_env().get("CODEX_PPT_PROXY_PORTS")
    port_list: Iterable[int] = DEFAULT_PROXY_PORTS
    if ports:
        parsed = [int(p) for p in ports.replace(",", " ").split() if p.strip().isdigit()]
        if parsed:
            port_list = parsed
    for port in port_list:
        if _proxy_reachable(port):
            return f"http://127.0.0.1:{port}"
    return None


def _subprocess_env(proxy: Optional[str]) -> Dict[str, str]:
    env = os.environ.copy()
    # The sandbox injects its own proxy through lowercase variables and codex
    # cannot reach chatgpt.com through it, so both cases must be overwritten.
    if proxy:
        for key in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy"):
            env[key] = proxy
        for key in ("NO_PROXY", "no_proxy"):
            env.pop(key, None)
    return env


def _scratch_root() -> Path:
    """Directory used for per-request workspaces.

    The system temp directory is avoided on purpose: on Windows it can be
    redirected into a sandboxed or scan-guarded location where the freshly
    written PNG is briefly unreadable.
    """
    configured = (
        os.getenv("CODEX_PPT_SCRATCH")
        or _runtime_env().get("CODEX_PPT_SCRATCH")
    )
    if configured:
        return Path(configured).expanduser()
    return _resolve_runtime_home() / "scratch"


def _read_bytes_with_retry(path: Path, *, attempts: int = 5, delay: float = 0.6) -> Optional[bytes]:
    """Read a file that a just-exited child process may still hold open."""
    for attempt in range(attempts):
        try:
            if path.is_file() and path.stat().st_size > 0:
                return path.read_bytes()
        except (PermissionError, OSError):
            pass
        if attempt < attempts - 1:
            time.sleep(delay)
    return None


def _generated_root() -> Path:
    """Location codex writes generated images into (session subfolders)."""
    home = os.getenv("CODEX_HOME")
    root = Path(home).expanduser() if home else Path("~/.codex").expanduser()
    return root / "generated_images"


def _prepare_scratch() -> Path:
    root = _scratch_root()
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        return Path(tempfile.gettempdir())
    _cleanup_stale_scratch(root)
    return root


def _cleanup_stale_scratch(root: Path, max_age_hours: float = 6.0) -> None:
    """Remove workspaces left behind when deletion was blocked by a file guard."""
    cutoff = time.time() - max_age_hours * 3600
    try:
        entries = list(root.iterdir())
    except OSError:
        return
    for entry in entries:
        if not entry.is_dir() or not entry.name.startswith("codex-ppt-img-"):
            continue
        try:
            if entry.stat().st_mtime > cutoff:
                continue
            shutil.rmtree(entry, ignore_errors=True)
            os.rmdir(entry)
        except OSError:
            pass


# --------------------------------------------------------------------------- #
# post-processing
# --------------------------------------------------------------------------- #
def _parse_size(size: Optional[str]) -> Optional[Tuple[int, int]]:
    if not size or size == "auto":
        return None
    parts = str(size).lower().split("x")
    if len(parts) != 2 or not all(p.strip().isdigit() for p in parts):
        return None
    w, h = int(parts[0]), int(parts[1])
    return (w, h) if w > 0 and h > 0 else None


def _fit_aspect(data: bytes, target: Optional[Tuple[int, int]]) -> bytes:
    """Center-crop to the target aspect ratio, then resize to the target size."""
    if target is None:
        return data
    try:
        from io import BytesIO

        from PIL import Image
    except Exception:
        return data

    try:
        with Image.open(BytesIO(data)) as img:
            img.load()
            src_w, src_h = img.size
            dst_w, dst_h = target
            if (src_w, src_h) == (dst_w, dst_h):
                return data
            src_ratio = src_w / src_h
            dst_ratio = dst_w / dst_h
            if abs(src_ratio - dst_ratio) / dst_ratio > 0.01:
                if src_ratio > dst_ratio:
                    crop_w = int(round(src_h * dst_ratio))
                    box = ((src_w - crop_w) // 2, 0, (src_w + crop_w) // 2, src_h)
                else:
                    crop_h = int(round(src_w / dst_ratio))
                    box = (0, (src_h - crop_h) // 2, src_w, (src_h + crop_h) // 2)
                img = img.crop(box)
            resized = img.resize((dst_w, dst_h), Image.Resampling.LANCZOS)
            out = BytesIO()
            resized.save(out, format="PNG")
            return out.getvalue()
    except Exception:
        return data


def _convert_format(data: bytes, output_format: str) -> bytes:
    fmt = (output_format or "png").lower()
    if fmt == "jpg":
        fmt = "jpeg"
    if fmt == "png":
        return data
    try:
        from io import BytesIO

        from PIL import Image
    except Exception:
        return data
    try:
        with Image.open(BytesIO(data)) as img:
            img.load()
            if fmt == "jpeg":
                if img.mode in ("RGBA", "LA", "P"):
                    rgba = img.convert("RGBA")
                    canvas = Image.new("RGB", rgba.size, (255, 255, 255))
                    canvas.paste(rgba, mask=rgba.split()[-1])
                    img = canvas
                else:
                    img = img.convert("RGB")
                out = BytesIO()
                img.save(out, format="JPEG", quality=95, subsampling=0)
                return out.getvalue()
            if fmt == "webp":
                out = BytesIO()
                img.save(out, format="WEBP", quality=95)
                return out.getvalue()
    except Exception:
        return data
    return data


# --------------------------------------------------------------------------- #
# provider
# --------------------------------------------------------------------------- #
class CodexOAuthImageProvider(ImageProvider):
    def __init__(
        self,
        *,
        codex_bin: Optional[str] = None,
        proxy: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_parallel: int = 3,
        fallback_api_key: Optional[str] = None,
        fallback_base_url: Optional[str] = None,
        runner: Optional[Any] = None,
    ) -> None:
        self._codex_bin = codex_bin
        self._runner = runner or subprocess.run
        self._proxy = proxy
        self.timeout = timeout
        self.max_parallel = max(1, int(max_parallel))
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._fallback_key = fallback_api_key
        self._fallback_base_url = fallback_base_url

    # -- ImageProvider interface ------------------------------------------- #
    def generate(self, payload: Dict[str, Any]) -> List[str]:
        count = max(1, int(payload.get("n", 1)))
        return [self._generate_one(payload) for _ in range(count)]

    def edit(
        self,
        payload: Dict[str, Any],
        image_paths: List[Path],
        mask_path: Optional[Path],
    ) -> List[str]:
        if mask_path is not None:
            raise ValueError(
                "Codex OAuth image backend does not support --mask; "
                "describe the edit region in the prompt instead."
            )
        count = max(1, int(payload.get("n", 1)))
        return [self._generate_one(payload, refs=list(image_paths)) for _ in range(count)]

    async def generate_batch(
        self,
        payload: Dict[str, Any],
        *,
        attempts: int,
        job_label: str,
    ) -> List[str]:
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self.max_parallel)
        last_exc: Optional[Exception] = None
        for attempt in range(1, attempts + 1):
            try:
                async with self._semaphore:
                    return await asyncio.to_thread(lambda: self.generate(payload))
            except Exception as exc:  # noqa: BLE001 - surface after retries
                last_exc = exc
                if attempt == attempts:
                    raise
                sleep_s = min(120.0, 5.0 * (2 ** (attempt - 1)))
                print(
                    f"{job_label} attempt {attempt}/{attempts} failed "
                    f"({exc.__class__.__name__}); retrying in {sleep_s:.0f}s",
                    file=sys.stderr,
                )
                await asyncio.sleep(sleep_s)
        raise last_exc or RuntimeError("unknown error")

    # -- internals --------------------------------------------------------- #
    def _generate_one(self, payload: Dict[str, Any], refs: Optional[List[Path]] = None) -> str:
        try:
            data = self._invoke_codex(payload, refs or [])
        except Exception as exc:  # noqa: BLE001 - try the paid API before failing
            data = self._try_fallback(payload, refs or [], exc)

        data = _fit_aspect(data, _parse_size(payload.get("size")))
        data = _convert_format(data, str(payload.get("output_format") or "png"))
        return base64.b64encode(data).decode("ascii")

    def _invoke_codex(self, payload: Dict[str, Any], refs: List[Path]) -> bytes:
        codex = self._codex_bin or find_codex_bin()
        if not codex:
            raise RuntimeError(
                "codex.exe not found. Set CODEX_BIN to the Codex CLI path, "
                "or switch OPENAI_BASE_URL back to an Images API endpoint."
            )

        prompt = str(payload.get("prompt", "")).strip()
        if not prompt:
            raise RuntimeError("prompt is empty")

        workdir = Path(tempfile.mkdtemp(prefix="codex-ppt-img-", dir=str(_prepare_scratch())))
        target = workdir / "out.png"
        baseline = self._generated_image_dirs()
        try:
            instruction = self._build_instruction(prompt, payload, has_refs=bool(refs))
            cmd = [
                codex,
                "exec",
                "--skip-git-repo-check",
                "--ephemeral",
                "-s",
                "workspace-write",
                "-C",
                str(workdir),
            ]
            for ref in refs:
                cmd.extend(["-i", str(Path(ref).resolve())])
            cmd.append(instruction)

            proxy = self._proxy or detect_proxy()
            proc = self._runner(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=_subprocess_env(proxy),
                timeout=self.timeout,
                cwd=str(workdir),
            )
            log_tail = self._tail(proc.stdout, proc.stderr)

            # codex may still be flushing the file when the process exits, and on
            # Windows the handle can stay locked for a moment.
            data = _read_bytes_with_retry(target)
            if data is not None:
                return data

            recovered = self._recover_from_generated(baseline)
            if recovered is not None:
                return recovered

            raise RuntimeError(
                "codex image generation produced no readable output"
                + (f" (exit {proc.returncode})" if proc.returncode else "")
                + (f": {log_tail}" if log_tail else "")
            )
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    @staticmethod
    def _tail(stdout: bytes, stderr: bytes, limit: int = 400) -> str:
        text = b"\n".join(part for part in (stdout, stderr) if part).decode("utf-8", "replace")
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        lines = [ln for ln in lines if "WARNING" not in ln and "rmcp::transport" not in ln]
        joined = " | ".join(lines[-6:])
        return joined[-limit:]

    @staticmethod
    def _build_instruction(prompt: str, payload: Dict[str, Any], *, has_refs: bool) -> str:
        size = str(payload.get("size") or "").strip()
        quality = str(payload.get("quality") or "").strip()
        background = payload.get("background")

        action = (
            "Edit the attached image as described below and keep every part that is not "
            "mentioned unchanged."
            if has_refs
            else "Generate the image described below."
        )
        lines = [
            f"You are an image rendering backend. Call the image_gen tool exactly once. {action}",
            "",
            "Description:",
            prompt,
        ]
        extras: List[str] = []
        if size and size != "auto":
            extras.append(f"target size {size} pixels (exact aspect ratio required)")
        if quality and quality not in {"auto", "medium"}:
            extras.append(f"render quality {quality}")
        if background == "transparent":
            extras.append("fully transparent background")
        if extras:
            lines += ["", "Requirements: " + "; ".join(extras) + "."]
        lines += [
            "",
            "After the image exists, copy that PNG file to ./out.png in the current working "
            "directory, overwriting any existing file. Do not create any other file and do "
            "not modify the source workspace. Reply with only the path of ./out.png.",
        ]
        return "\n".join(lines)

    def _generated_image_dirs(self) -> set:
        root = _generated_root()
        try:
            return {p.name for p in root.iterdir() if p.is_dir()}
        except OSError:
            return set()

    def _recover_from_generated(self, baseline: set) -> Optional[bytes]:
        root = _generated_root()
        try:
            current = [p for p in root.iterdir() if p.is_dir() and p.name not in baseline]
        except OSError:
            return None
        files: List[str] = []
        for directory in current:
            files.extend(glob.glob(str(directory / "*.png")))
        if not files:
            return None
        files.sort(key=os.path.getmtime, reverse=True)
        for candidate in files[:3]:
            data = _read_bytes_with_retry(Path(candidate), attempts=2, delay=0.4)
            if data is not None:
                return data
        return None

    def _try_fallback(self, payload: Dict[str, Any], refs: List[Path], exc: Exception) -> bytes:
        if not (self._fallback_key and self._fallback_base_url):
            raise exc
        print(
            f"Codex OAuth image generation failed ({exc}); falling back to "
            f"{self._fallback_base_url}",
            file=sys.stderr,
        )
        from .openai_compatible import OpenAICompatibleImageProvider

        provider = OpenAICompatibleImageProvider(
            api_key=self._fallback_key,
            base_url=self._fallback_base_url,
        )
        api_payload = {k: v for k, v in payload.items() if k != "n"}
        api_payload["n"] = 1
        if refs:
            images = provider.edit(api_payload, refs, None)
        else:
            images = provider.generate(api_payload)
        if not images:
            raise RuntimeError("fallback Images API returned no data")
        return base64.b64decode(images[0])


def create_codex_oauth_provider_from_env(*, allow_fallback: bool = True) -> CodexOAuthImageProvider:
    """Build the provider from the shared runtime .env.

    ``allow_fallback=False`` disables the paid-API safety net. Call it when the
    user deliberately picked the subscription quota: silently spending API credit
    after they asked for "no charges" is worse than a clean failure.
    """
    env = _runtime_env()
    fallback_base = os.getenv("CODEX_PPT_FALLBACK_BASE_URL") or env.get("CODEX_PPT_FALLBACK_BASE_URL")
    return CodexOAuthImageProvider(
        proxy=os.getenv("CODEX_PROXY") or env.get("CODEX_PPT_PROXY"),
        timeout=float(env.get("CODEX_PPT_TIMEOUT") or DEFAULT_TIMEOUT),
        max_parallel=int(env.get("CODEX_PPT_MAX_PARALLEL") or 3),
        fallback_api_key=os.getenv("OPENAI_API_KEY") if allow_fallback else None,
        fallback_base_url=fallback_base if allow_fallback else None,
    )
