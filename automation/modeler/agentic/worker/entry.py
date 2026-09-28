"""The trusted supervisor inside the worker container (PID 1).

    python3 /opt/lenses/worker_entry.py /bundle/operation.json /work/out

Reads the immutable operation bundle, writes the harness job with every path inside the container, runs headless
Blender with a wall-clock limit and bounded, streamed logs, then stops and reaps every remaining child process,
takes a quiescent snapshot of the output (regular files only, hashed), writes ``READY.json`` (with the GL renderer the
renders used and the image's font inventory) and waits to be stopped by the host. The host copies the output out as a tar stream and validates it independently; this receipt is untrusted.
Nothing here reads a credential, the network is absent, and the only writable place is the bounded tmpfs.
"""
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time

LOG_LIMIT = 2 * 1024 * 1024
POLL_S = 0.5


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def reap_everything():
    """Kill every process except PID 1 (this supervisor) and wait: no background child may touch the output later."""
    for _ in range(3):
        others = []
        for name in os.listdir("/proc"):
            if name.isdigit() and int(name) not in (1, os.getpid()):
                others.append(int(name))
        for pid in others:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                while True:
                    os.waitpid(-1, os.WNOHANG)
                    time.sleep(0.05)
                    if not any(n.isdigit() and int(n) not in (1, os.getpid()) for n in os.listdir("/proc")):
                        return
            except ChildProcessError:
                if not any(n.isdigit() and int(n) not in (1, os.getpid()) for n in os.listdir("/proc")):
                    return
                time.sleep(0.1)


def snapshot(out_dir):
    files = []
    for root, dirs, names in os.walk(out_dir):
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
        for n in names:
            p = os.path.join(root, n)
            st = os.lstat(p)
            if not os.path.isfile(p) or os.path.islink(p) or not (st.st_mode & 0o170000) == 0o100000:
                continue
            rel = os.path.relpath(p, out_dir).replace(os.sep, "/")
            if rel == "READY.json":
                continue
            files.append({"rel": rel, "sha256": sha256_file(p), "bytes": st.st_size})
    files.sort(key=lambda f: f["rel"])
    return files


def bounded_tail(path, limit=2000):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - limit))
            return f.read().decode("utf-8", "replace")
    except OSError:
        return ""


USAGE = "usage: python3 /opt/lenses/worker_entry.py /bundle/operation.json /work/out"
DEFAULT_SAMPLES = 16          # the harness default; every bundle written by the host names its own samples anyway
BLENDER_ENV = {"HOME": "/work/home", "TMPDIR": "/work/tmp", "XDG_CACHE_HOME": "/work/cache", "XDG_CONFIG_HOME": "/work/config",
               "BLENDER_USER_RESOURCES": "/work/blender", "PATH": "/usr/local/bin:/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"}


def blender_env(environ=os.environ):
    """Blender's environment: the fixed whitelist above plus LP_NUM_THREADS when the host set it on the container
    (executor.DockerWorker.argv_run passes the integer part of the --cpus quota). Without it Mesa's llvmpipe starts one
    rasterizer thread per CPU the VM reports and the cgroup quota throttles them all. Only a positive integer is
    forwarded, and only in ASCII digits: str.isdigit() also accepts superscripts ('²'), on which int() raises; nothing
    else of the container environment reaches Blender.

    NOTE: this file is COPYed into the image (worker/Dockerfile), so a change here needs the image rebuilt and the
    doctor self-test rerun (`python -m modeler.agentic doctor --worker-config <file> --self-test --output <dir>`); the
    doctor record is bound to the harness fingerprint and refuses a launch otherwise."""
    env = dict(BLENDER_ENV)
    lp = str(environ.get("LP_NUM_THREADS", "")).strip()
    if lp.isascii() and lp.isdigit() and int(lp) > 0:
        env["LP_NUM_THREADS"] = str(int(lp))
    return env


# ---- provenance of a render: which rasterizer drew it, and the known-benign EGL lines (review 2026-09-28, INF-17)
# test-pilot-002's render logs never named the GL renderer (llvmpipe or anything else), so an image change could silently change
# render cost or look. GL_PROBE runs before the harness (--python-expr) and prints one GL_MARK line at the first render, when
# Blender has made its GL context; @persistent keeps it through the render_only open_mainfile. The line comes from the same
# Blender process as the untrusted program, so like the rest of this receipt it is provenance, never a trust decision.
GL_MARK = "LENSES_WORKER_GL| "
GL_KEYS = ("renderer", "vendor", "version", "backend", "device", "error")
GL_PROBE = """\
import json, sys
import bpy
from bpy.app.handlers import persistent

_lenses_gl_done = []


@persistent
def _lenses_worker_gl(*_args):
    if _lenses_gl_done:
        return
    _lenses_gl_done.append(1)
    try:
        import gpu
        p = gpu.platform
        info = {"renderer": p.renderer_get(), "vendor": p.vendor_get(), "version": p.version_get(), "backend": p.backend_type_get(),
                "device": p.device_type_get()}
    except Exception as e:
        info = {"error": "%s: %s" % (type(e).__name__, e)}
    sys.stdout.write(GL_MARK_LITERAL + json.dumps(info) + "\\n")
    sys.stdout.flush()


bpy.app.handlers.render_post.append(_lenses_worker_gl)
""".replace("GL_MARK_LITERAL", repr(GL_MARK))
# Blender's headless EGL context creation prints this once per configuration Mesa refuses before one succeeds: three lines
# in every render op of test-pilot-002, each of which completed. They are removed only when the probe shows a context was made
# (the renders then prove them benign) and replaced by one line that says so; without a context they are the diagnosis and stay.
EGL_BAD_MATCH = re.compile(r"^EGL Error \(0x3009\): EGL_BAD_MATCH\b")
FONT_INVENTORY = "/opt/lenses/fonts.sha256"       # written at image build time (worker/Dockerfile): '<sha256>  <path>' lines


def blender_argv(bundle_root, job_path):
    """Headless Blender: the GL probe first, then the harness from the bundle, then the job file."""
    return ["blender", "-b", "--factory-startup", "--python-expr", GL_PROBE, "--python", bundle_root + "/lib/harness.py", "--", job_path]


def parse_gl(stdout_text):
    """The probe's record from Blender's stdout: known keys only, each a bounded string; None when no render made a context."""
    for line in stdout_text.splitlines():
        if not line.startswith(GL_MARK):
            continue
        try:
            raw = json.loads(line[len(GL_MARK):])
        except ValueError:
            return {"error": "unreadable probe line"}
        if not isinstance(raw, dict):
            return {"error": "unreadable probe line"}
        return {k: str(raw[k])[:200] for k in GL_KEYS if k in raw}
    return None


def tidy_stderr(text, gl):
    """(text, removed): the EGL_BAD_MATCH probe lines replaced by one supervisor line, only when ``gl`` names a renderer."""
    if not gl or not gl.get("renderer") or gl.get("error"):
        return text, 0
    kept, removed = [], 0
    for line in text.splitlines(keepends=True):
        if EGL_BAD_MATCH.match(line):
            removed += 1
        else:
            kept.append(line)
    if not removed:
        return text, 0
    if kept and not kept[-1].endswith("\n"):
        kept[-1] += "\n"
    kept.append("[worker supervisor] removed %d 'EGL Error (0x3009): EGL_BAD_MATCH' lines: Blender's headless EGL context creation "
                "retrying a configuration Mesa refuses; benign, a context was made (GL renderer: %s)\n" % (removed, gl["renderer"]))
    return "".join(kept), removed


def finish_logs(so_path, se_path):
    """After Blender: read the GL record from stdout and tidy stderr in place. Returns the record (with the number of EGL lines
    removed) for READY.json, or None when no render made a GL context."""
    try:
        with open(so_path, "rb") as f:
            gl = parse_gl(f.read().decode("utf-8", "replace"))
    except OSError:
        return None
    if gl is None:
        return None
    removed = 0
    try:
        with open(se_path, "rb") as f:
            text = f.read().decode("utf-8", "replace")
        tidy, removed = tidy_stderr(text, gl)
        if removed:
            with open(se_path, "wb") as f:
                f.write(tidy.encode("utf-8"))
    except OSError:
        pass
    return dict(gl, egl_bad_match_lines_removed=removed)


def font_inventory(path=FONT_INVENTORY):
    """{font path: sha256} from the image's inventory; {} for an image built before fonts were installed."""
    fonts = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                m = re.match(r"^([0-9a-f]{64})\s+(/\S+)\s*$", line)
                if m:
                    fonts[m.group(2)] = m.group(1)
    except OSError:
        return {}
    return fonts


def parse_args(argv):
    """Exactly two arguments: the bundle's ``.../operation.json`` and the output directory ``/work/out``.

    Anything else is refused before any work (stderr message, exit 2, nothing created). In particular, docker runs
    ENTRYPOINT + <arguments after the image>; this file IS the image's ENTRYPOINT, so a host that named the interpreter
    and this script again after the image would launch us with ``[python3, <entry>, operation.json, /work/out]``. That
    must never be mistaken for a valid launch and silently build against op_path="python3".
    """
    ok = (len(argv) == 2 and isinstance(argv[0], str) and argv[0].endswith("/operation.json") and argv[1] == "/work/out")
    if not ok:
        sys.stderr.write("worker_entry: refusing to start: expected exactly two arguments, </bundle-path>/operation.json and /work/out, got %r\n%s\n"
                         % (list(argv), USAGE))
        sys.stderr.flush()
        sys.exit(2)
    return argv[0], argv[1]


def main():
    op_path, out_dir = parse_args(sys.argv[1:])
    bundle_root = os.path.dirname(os.path.abspath(op_path))
    os.makedirs(out_dir, exist_ok=True)
    for d in ("/work/home", "/work/tmp", "/work/cache", "/work/config", "/work/blender"):
        os.makedirs(d, exist_ok=True)
    with open(op_path, "r", encoding="utf-8") as f:
        op = json.load(f)
    limit = int(os.environ.get("LENSES_TIME_LIMIT_S", op.get("time_limit_s", 300)))
    job = {"lib_dir": bundle_root + "/lib", "out_dir": out_dir, "export": op.get("export", True), "save_blend": op.get("save_blend", True),
           "renders": op.get("renders", []), "samples": op.get("samples", DEFAULT_SAMPLES), "mode": op.get("mode", "build"),
           "modules": [{"name": m["name"], "path": bundle_root + "/" + m["file"]} for m in op.get("modules", [])],
           "evidence_path": (bundle_root + "/" + op["evidence"]) if op.get("evidence") else None}
    if op.get("mode") == "render_only":
        job["blend_path"] = bundle_root + "/" + op["blend"]
    job_path = "/work/job.json"
    with open(job_path, "w", encoding="utf-8") as f:
        json.dump(job, f, indent=1)
    so_path, se_path = os.path.join(out_dir, "blender.stdout.log"), os.path.join(out_dir, "blender.stderr.log")
    t0 = time.monotonic()
    status, exit_code = "completed", None
    with open(so_path, "wb") as so, open(se_path, "wb") as se:
        proc = subprocess.Popen(blender_argv(bundle_root, job_path), stdout=so, stderr=se, cwd="/work", env=blender_env())
        while True:
            rc = proc.poll()
            if rc is not None:
                exit_code = rc
                break
            if time.monotonic() - t0 > limit:
                status = "timed_out"
                break
            for p in (so_path, se_path):
                try:
                    if os.path.getsize(p) > LOG_LIMIT:
                        proc.kill()
                        status = "log_limit"
                except OSError:
                    pass
            if status != "completed":
                break
            time.sleep(POLL_S)
    reap_everything()
    for p in (so_path, se_path):
        try:
            if os.path.getsize(p) > LOG_LIMIT:
                with open(p, "rb") as f:
                    data = f.read(LOG_LIMIT)
                with open(p, "wb") as f:
                    f.write(data + b"\n[truncated by the worker supervisor]\n")
        except OSError:
            pass
    gl = finish_logs(so_path, se_path)       # before the tails: the author sees the tidied stderr
    result_ok = False
    rp = os.path.join(out_dir, "result.json")
    if os.path.isfile(rp):
        try:
            with open(rp, "r", encoding="utf-8") as f:
                result_ok = bool(json.load(f).get("ok"))
        except Exception:  # noqa: BLE001
            result_ok = False
    ready = {"protocol": "lenses_agentic_worker_v1", "operation_id": op.get("operation_id"), "status": status, "exit_code": exit_code,
             "ok": status == "completed" and exit_code == 0 and result_ok, "seconds": round(time.monotonic() - t0, 2),
             "files": snapshot(out_dir), "stdout_tail": bounded_tail(so_path), "stderr_tail": bounded_tail(se_path),
             "gl": gl, "fonts": font_inventory(),
             "note": "untrusted worker receipt: the host hashes the ingested bytes itself"}
    tmp = os.path.join(out_dir, "READY.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(ready, f, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, os.path.join(out_dir, "READY.json"))
    # hold the finished output for ingestion; the host stops the container when it has copied and verified it
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
