"""Run bin/ab from the batch drivers (apply-gaps.py, build-cores.py).

`run()` never raises for the child's sake: a hang, a crash and a missing bin/ab
all come back as a CompletedProcess with a non-zero returncode, so a batch
counts the row as failed and moves on.  `why()` turns one into a FAIL line that
names the exit status -- a segfaulting bpy child prints nothing at all.
"""
import os
import signal
import subprocess
import uuid

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AB = os.path.join(REPO, "bin", "ab")
# A stale docker.socket hangs a run forever with no output.  Generous, because a
# Q-sized MANIFOLD boolean legitimately takes minutes: a hang guard, not a deadline.
TIMEOUT = 1800
NOISE = ("Info:", "Timer", "Read blend")


def run(args, *, python=False, timeout=TIMEOUT, **kw):
    """bin/ab ARGS; stdout and stderr captured as text.

    On timeout the docker client is killed by subprocess, but the container
    would keep running, so it is named (AB_NAME) and removed explicitly.
    """
    name = f"ab-{uuid.uuid4().hex[:12]}"
    env = dict(kw.pop("env", None) or os.environ, AB_NAME=name)
    if python:
        env["AB_ENTRYPOINT"] = "python"
    cmd = [AB, *args]
    try:
        return subprocess.run(cmd, env=env, capture_output=True, text=True,
                              timeout=timeout, **kw)
    except subprocess.TimeoutExpired as e:
        return subprocess.CompletedProcess(
            cmd, 124, _text(e.stdout),
            _text(e.stderr) + f"\ntimed out after {timeout}s -- check for a stale docker.socket"
            + _remove(name))
    except OSError as e:
        return subprocess.CompletedProcess(cmd, 127, "", f"cannot run {AB}: {e}")
    except BaseException:
        _remove(name)               # Ctrl-C: the container would outlive its client
        raise


def _remove(name):
    """docker rm -f NAME; '' if gone, else a note for the failure text."""
    try:
        r = subprocess.run(["docker", "rm", "-f", name], capture_output=True,
                           text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"\ncontainer {name} NOT removed: {e}"
    if r.returncode != 0 and "No such container" not in r.stderr:
        return f"\ncontainer {name} NOT removed: {r.stderr.strip()}"
    return ""


def _text(b):
    return b.decode(errors="replace") if isinstance(b, bytes) else (b or "")


def status(rc):
    """'exit 1', or 'exit 139 (SIGSEGV)' -- docker reports a container killed by
    signal N as 128+N; subprocess reports a killed client as -N."""
    if rc < 0:
        n = -rc
    elif 128 < rc < 160:
        n = rc - 128
    else:
        n = 0
    try:
        return f"exit {rc} ({signal.Signals(n).name})" if n else f"exit {rc}"
    except ValueError:
        return f"exit {rc}"


def why(proc, lines=3):
    """The last few meaningful output lines plus the exit status.  `#` lines are
    progress reports; they are shown only when there is nothing else."""
    text = [l for l in ((proc.stdout or "") + (proc.stderr or "")).splitlines()
            if l.strip() and not l.startswith(NOISE)]
    text = [l for l in text if not l.startswith("#")] or text
    tail = "\n     ".join(text[-lines:]) or "no output"
    return f"{status(proc.returncode)}: {tail}"


def demo():
    """`python3 bin/abrun.py --demo`"""
    assert status(1) == "exit 1"
    assert status(139) == "exit 139 (SIGSEGV)"
    assert status(-11) == "exit -11 (SIGSEGV)"
    assert status(124) == "exit 124"
    p = subprocess.CompletedProcess([], 139, "", "")
    assert why(p) == "exit 139 (SIGSEGV): no output", why(p)
    p = subprocess.CompletedProcess([], 1, "# wrote x\n", "Info: noise\nboom\n")
    assert why(p) == "exit 1: boom", why(p)
    p = subprocess.CompletedProcess([], 1, "# square dx=+9\n", "")
    assert why(p) == "exit 1: # square dx=+9", why(p)
    # a missing executable and a hang are failed rows, not a crashed batch
    import tempfile
    global AB
    real = AB
    with tempfile.TemporaryDirectory() as d:
        hang = os.path.join(d, "ab")
        with open(hang, "w") as fh:
            fh.write("#!/bin/sh\necho started\nexec sleep 30\n")
        os.chmod(hang, 0o755)
        # a fake docker on PATH records the rm, and can be made to fail it
        log = os.path.join(d, "docker.log")
        with open(os.path.join(d, "docker"), "w") as fh:
            fh.write(f'#!/bin/sh\necho "$@" >> {log}\n[ -e {d}/rmfail ] && exit 1\nexit 0\n')
        os.chmod(os.path.join(d, "docker"), 0o755)
        path = os.environ["PATH"]
        os.environ["PATH"] = f"{d}:{path}"
        try:
            AB = os.path.join(d, "missing")
            p = run(["info"])
            assert p.returncode == 127 and "cannot run" in p.stderr, p
            AB = hang
            p = run(["info"], timeout=1)
            assert p.returncode == 124 and "timed out after 1s" in p.stderr, p
            assert open(log).read().startswith("rm -f ab-"), open(log).read()
            assert "NOT removed" not in p.stderr, p.stderr
            open(os.path.join(d, "rmfail"), "w").close()
            p = run(["info"], timeout=1)
            assert "NOT removed" in p.stderr, p.stderr
        finally:
            AB = real
            os.environ["PATH"] = path
    print("abrun demo ok")


if __name__ == "__main__":
    demo()
