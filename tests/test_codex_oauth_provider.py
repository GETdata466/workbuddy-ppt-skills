"""Offline test suite for the codex-oauth image provider.

Run: python tests/test_codex_oauth_provider.py

The provider accepts an injectable runner, so this suite never starts a Codex
process and never touches the ChatGPT quota. Covers parameter wiring, workdir
isolation under concurrency, aspect fitting, format conversion, read-lock
recovery, API fallback, and the generated_images recovery path.
"""

import base64
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "codex-ppt" / "scripts"))

import image_providers.codex_oauth as co
from PIL import Image

RESULTS = []
SCRATCH = Path(tempfile.mkdtemp(prefix="codex-ppt-test-"))


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(("PASS  " if ok else "FAIL  ") + name + (f"   [{detail}]" if detail else ""))


def make_png(color, size=(1024, 1024)):
    buf = BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


class FakeRunner:
    """Stands in for subprocess.run, emulating `codex exec` side effects."""

    def __init__(self, mode="ok", color=(220, 40, 40), delay=0.0, env_capture=None,
                 session_root=None):
        self.mode = mode
        self.session_root = session_root
        self.color = color
        self.delay = delay
        self.calls = []
        self.dirs = []
        self.env_capture = env_capture if env_capture is not None else {}
        self._lock = threading.Lock()

    def __call__(self, cmd, **kwargs):
        workdir = Path(cmd[cmd.index("-C") + 1])
        with self._lock:
            self.calls.append(list(cmd))
            self.dirs.append(workdir)
            self.env_capture.update(kwargs.get("env") or {})

        if self.delay:
            time.sleep(self.delay)

        if self.mode == "fail":
            return subprocess.CompletedProcess(cmd, 1, b"boom", b"upstream error")
        if self.mode == "nofile":
            if self.session_root is not None:
                # Emulate codex writing the image into its own session folder
                # instead of honouring the copy instruction.
                import uuid

                session = Path(self.session_root) / f"session-{uuid.uuid4().hex[:8]}"
                session.mkdir(parents=True, exist_ok=True)
                (session / "img.png").write_bytes(make_png(self.color))
            return subprocess.CompletedProcess(cmd, 0, b"nothing", b"")

        target = workdir / "out.png"
        target.write_bytes(make_png(self.color))
        return subprocess.CompletedProcess(cmd, 0, f"[stub] wrote {target}".encode(), b"")


def main():
    SCRATCH.mkdir(parents=True, exist_ok=True)
    co._scratch_root = lambda: SCRATCH

    # ---------------------------------------------------------------- 1 ------
    fake = FakeRunner()
    p = co.CodexOAuthImageProvider(codex_bin="fake-codex", max_parallel=2, runner=fake)
    imgs = p.generate({"prompt": "slide with a red block", "n": 1, "size": "2560x1440",
                       "quality": "medium", "output_format": "png"})
    data = base64.b64decode(imgs[0])
    with Image.open(BytesIO(data)) as im:
        size, fmt = im.size, im.format
    check("single generation returns one image", len(imgs) == 1 and len(data) > 1000)
    check("aspect fitted to requested size", size == (2560, 1440), str(size))
    check("png passthrough keeps PNG", fmt == "PNG", str(fmt))

    cmd = fake.calls[0]
    check("uses codex exec subcommand", cmd[1] == "exec", " ".join(cmd[:2]))
    check("passes --skip-git-repo-check", "--skip-git-repo-check" in cmd)
    check("passes --ephemeral", "--ephemeral" in cmd)
    check("runs in a private workdir", "codex-ppt-img-" in cmd[cmd.index("-C") + 1])
    check("instruction is the final argument", "out.png" in cmd[-1])
    check("prompt embedded in the instruction", "red block" in cmd[-1])
    check("instruction pins the target size", "2560x1440" in cmd[-1])

    # ---------------------------------------------------------------- 2 ------
    fake2 = FakeRunner()
    p2 = co.CodexOAuthImageProvider(codex_bin="fake-codex", runner=fake2)
    d2 = base64.b64decode(p2.generate({"prompt": "x", "n": 1, "size": "1024x1024",
                                       "output_format": "jpeg"})[0])
    check("jpeg output has JPEG magic bytes", d2[:2] == b"\xff\xd8", d2[:2].hex())

    # ---------------------------------------------------------------- 3 ------
    fake3 = FakeRunner(color=(40, 80, 220), delay=0.3)
    p3 = co.CodexOAuthImageProvider(codex_bin="fake-codex", max_parallel=3, runner=fake3)

    import asyncio

    async def many():
        return await asyncio.gather(*[
            p3.generate_batch({"prompt": f"slide {i}", "n": 1, "size": "1024x1024",
                               "output_format": "png"}, attempts=1, job_label=f"[job{i}]")
            for i in range(3)
        ])

    asyncio.run(many())
    dirs = [str(d) for d in fake3.dirs]
    check("three concurrent jobs ran", len(dirs) == 3, str(len(dirs)))
    check("each job used its own workdir", len(set(dirs)) == 3, str(len(set(dirs))))
    check("workdirs removed after success", not [d for d in dirs if Path(d).exists()], str(sum(Path(d).exists() for d in dirs)))

    # ---------------------------------------------------------------- 4 ------
    fake4 = FakeRunner(mode="fail")
    p4 = co.CodexOAuthImageProvider(codex_bin="fake-codex", runner=fake4)
    try:
        p4.generate({"prompt": "x", "n": 1, "size": "1024x1024", "output_format": "png"})
        exc = None
    except Exception as e:  # noqa: BLE001
        exc = e
    check("failure without fallback raises", isinstance(exc, RuntimeError), type(exc).__name__)
    check("error explains missing output", "no readable output" in str(exc), str(exc)[:52])

    # ---------------------------------------------------------------- 5 ------
    calls = {}

    class FakeAPI:
        def __init__(self, *, api_key, base_url):
            calls["key"], calls["base"] = api_key, base_url

        def generate(self, payload):
            calls["payload"] = payload
            return [base64.b64encode(make_png((1, 2, 3))).decode()]

        def edit(self, payload, paths, mask):
            raise AssertionError("edit path not expected")

    import image_providers.openai_compatible as oc

    orig_api = oc.OpenAICompatibleImageProvider
    oc.OpenAICompatibleImageProvider = FakeAPI
    try:
        p5 = co.CodexOAuthImageProvider(codex_bin="fake-codex", runner=FakeRunner(mode="fail"),
                                        fallback_api_key="sk-test",
                                        fallback_base_url="https://example.invalid/v1")
        imgs5 = p5.generate({"prompt": "x", "n": 1, "size": "1024x1024", "output_format": "png"})
    finally:
        oc.OpenAICompatibleImageProvider = orig_api
    check("fallback delegates to API provider", calls.get("base") == "https://example.invalid/v1", str(calls.get("base")))
    check("fallback passes the API key", calls.get("key") == "sk-test")
    check("fallback sends n=1 per request", calls.get("payload", {}).get("n") == 1, str(calls.get("payload", {}).get("n")))
    check("fallback result returned", len(base64.b64decode(imgs5[0])) > 100)

    # ---------------------------------------------------------------- 6 ------
    orig_find = co.find_codex_bin
    co.find_codex_bin = lambda: None
    try:
        p6 = co.CodexOAuthImageProvider(codex_bin=None, runner=FakeRunner())
        try:
            p6.generate({"prompt": "x", "n": 1, "size": "1024x1024", "output_format": "png"})
            exc6 = None
        except Exception as e:  # noqa: BLE001
            exc6 = e
    finally:
        co.find_codex_bin = orig_find
    check("missing codex raises a clear error", isinstance(exc6, RuntimeError), type(exc6).__name__)
    check("error points at CODEX_BIN", "CODEX_BIN" in str(exc6), str(exc6)[:52])

    # ---------------------------------------------------------------- 7 ------
    # read-lock recovery, scoped to the helper itself
    tmpfile = SCRATCH / "lock_probe.png"
    tmpfile.parent.mkdir(parents=True, exist_ok=True)
    tmpfile.write_bytes(make_png((9, 9, 9)))
    orig_read = Path.read_bytes
    state = {"n": 0}

    def flaky_read(self_path, *a, **kw):
        if self_path == tmpfile and state["n"] < 2:
            state["n"] += 1
            raise PermissionError(13, "Permission denied", str(self_path))
        return orig_read(self_path, *a, **kw)

    Path.read_bytes = flaky_read
    try:
        got = co._read_bytes_with_retry(tmpfile, attempts=4, delay=0.05)
    finally:
        Path.read_bytes = orig_read
    check("retries a briefly locked file", got is not None and len(got) > 100, f"attempts={state['n'] + 1}")

    # ---------------------------------------------------------------- 8 ------
    # generated_images recovery when the agent ignores the copy instruction
    genroot = SCRATCH / "fake_codex_home" / "generated_images"
    genroot.mkdir(parents=True, exist_ok=True)
    fake8 = FakeRunner(mode="nofile", color=(5, 6, 7), session_root=genroot)
    orig_root = co._generated_root
    co._generated_root = lambda: genroot
    try:
        p8 = co.CodexOAuthImageProvider(codex_bin="fake-codex", runner=fake8)
        out8 = p8.generate({"prompt": "x", "n": 1, "size": "1024x1024", "output_format": "png"})
    finally:
        co._generated_root = orig_root
    check("recovers image from generated_images", len(base64.b64decode(out8[0])) > 100)

    # ---------------------------------------------------------------- 9 ------
    fake9 = FakeRunner()
    p9 = co.CodexOAuthImageProvider(codex_bin="fake-codex", runner=fake9)
    imgs9 = p9.generate({"prompt": "x", "n": 2, "size": "1024x1024", "output_format": "png"})
    check("n=2 returns two images", len(imgs9) == 2, str(len(imgs9)))
    check("n=2 issues two codex calls", len(fake9.calls) == 2, str(len(fake9.calls)))

    # --------------------------------------------------------------- 10 ------
    p10 = co.CodexOAuthImageProvider(codex_bin="fake-codex", runner=FakeRunner())
    try:
        p10.edit({"prompt": "x", "n": 1}, [Path("a.png")], Path("m.png"))
        exc10 = None
    except Exception as e:  # noqa: BLE001
        exc10 = e
    check("mask rejected with guidance", isinstance(exc10, ValueError) and "mask" in str(exc10).lower(), str(exc10)[:48])

    # --------------------------------------------------------------- 11 ------
    envbox = {}
    p11 = co.CodexOAuthImageProvider(codex_bin="fake-codex", proxy="http://127.0.0.1:9999",
                                     runner=FakeRunner(env_capture=envbox))
    try:
        p11.generate({"prompt": "x", "n": 1, "size": "1024x1024", "output_format": "png"})
    except Exception:
        pass
    check("proxy exported upper case", envbox.get("HTTPS_PROXY") == "http://127.0.0.1:9999", str(envbox.get("HTTPS_PROXY")))
    check("proxy exported lower case", envbox.get("https_proxy") == "http://127.0.0.1:9999", str(envbox.get("https_proxy")))
    check("NO_PROXY stripped", "NO_PROXY" not in envbox)

    # --------------------------------------------------------------- 12 ------
    # edit passes reference images via -i
    fake12 = FakeRunner()
    p12 = co.CodexOAuthImageProvider(codex_bin="fake-codex", runner=fake12)
    ref = SCRATCH / "ref.png"
    ref.parent.mkdir(parents=True, exist_ok=True)
    ref.write_bytes(make_png((10, 20, 30)))
    p12.edit({"prompt": "make it green", "n": 1, "size": "1024x1024", "output_format": "png"}, [ref], None)
    cmd12 = fake12.calls[0]
    check("edit attaches reference via -i", "-i" in cmd12 and str(ref.resolve()) in cmd12, " ".join(cmd12[6:9]))
    check("edit instruction mentions preserving context", "unchanged" in cmd12[-1])

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n{passed}/{len(RESULTS)} passed")
    shutil.rmtree(SCRATCH, ignore_errors=True)
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
