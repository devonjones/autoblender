"""Google Drive folder watcher: <root>/<process>/in  ->  <root>/<process>/out.

Runs as a scheduled Lambda (handler) or locally (python -m autoblender.drive).
Drive is the only state: an input is pending until a same-named file, or
<name>.error.txt, exists in out/. Delete the out file to re-run an input.

A placeholder <name>.error.txt is written before processing and removed when
the file is done: a processing failure replaces it with the traceback (permanent
until deleted), a Drive fault removes it (retried next tick), and a run the Lambda
kills mid-file (timeout, OOM) leaves it. Nothing head-of-line blocks later ticks.
ponytail: full folder listing per tick; fine to a few thousand files per folder.
"""

import json
import os
import shutil
import sys
import tempfile
import time
import traceback
import urllib.parse
import urllib.request
from functools import partial

from .cut import cut_stl

# folder name -> fn(src_path, dst_path). Adding a process is one line here;
# the folders get created on the next tick.
PROCESSES = {
    "cut-6mm": partial(cut_stl, z=6.0),
}

FOLDER = "application/vnd.google-apps.folder"
API = "https://www.googleapis.com/drive/v3"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable"
TOKEN = "https://oauth2.googleapis.com/token"
MARKER = ("processing started {started}\n"
          "If this file is still here the run died (timeout or crash). Delete it to retry.\n"
          "If the output file is also here, the run succeeded and only this cleanup failed.\n")
# ponytail: no new file starts inside the reserve, but a file can still outlive it.
# cut_stl is ~2s for a 327k-tri single shell and ~490s for a 102k-shell mesh
# (union repair is superlinear in shell count); raise this with slower processes.
RESERVE = 300   # seconds kept back from the Lambda timeout


class Drive:
    def __init__(self, client_id, client_secret, refresh_token):
        body = urllib.parse.urlencode(dict(
            client_id=client_id, client_secret=client_secret,
            refresh_token=refresh_token, grant_type="refresh_token",
        )).encode()
        with urllib.request.urlopen(TOKEN, body) as r:
            self.hdr = {"Authorization": f"Bearer {json.load(r)['access_token']}"}

    def _open(self, url, data=None, method=None, **headers):
        req = urllib.request.Request(url, data=data, method=method, headers={**self.hdr, **headers})
        return urllib.request.urlopen(req)

    def _json(self, url, data=None, method=None, **headers):
        with self._open(url, data, method, **headers) as r:
            return json.load(r)

    def list(self, parent):
        files, token = [], None
        while True:
            params = dict(q=f"'{parent}' in parents and trashed=false", pageSize=1000,
                          fields="nextPageToken,files(id,name,mimeType)")
            if token:
                params["pageToken"] = token
            page = self._json(f"{API}/files?{urllib.parse.urlencode(params)}")
            files += page["files"]
            token = page.get("nextPageToken")
            if not token:
                return files

    def folder(self, parent, name):
        """Id of child folder `name`, created if missing."""
        for f in self.list(parent):
            if f["name"] == name and f["mimeType"] == FOLDER:
                return f["id"]
        meta = json.dumps(dict(name=name, mimeType=FOLDER, parents=[parent])).encode()
        return self._json(f"{API}/files", meta, **{"Content-Type": "application/json"})["id"]

    def download(self, file_id, path):
        with self._open(f"{API}/files/{file_id}?alt=media") as r, open(path, "wb") as f:
            shutil.copyfileobj(r, f)

    def upload(self, parent, name, src, mime="application/octet-stream"):
        """Upload bytes or the file at path `src`; streams the file. Returns the file id."""
        meta = json.dumps(dict(name=name, parents=[parent])).encode()
        with self._open(UPLOAD, meta, "POST", **{"Content-Type": "application/json",
                                                 "X-Upload-Content-Type": mime}) as r:
            session = r.headers["Location"]
        if isinstance(src, bytes):
            body, size = src, len(src)
        else:
            body, size = open(src, "rb"), os.path.getsize(src)
        try:
            return self._json(session, body, "PUT", **{"Content-Type": mime, "Content-Length": str(size)})["id"]
        finally:
            if body is not src:
                body.close()

    def delete(self, file_id):
        with self._open(f"{API}/files/{file_id}", method="DELETE"):
            pass


def pending(inputs, outputs):
    done = {f["name"] for f in outputs}
    return [f for f in inputs
            if f["name"].lower().endswith(".stl")
            and f["name"] not in done
            and f["name"] + ".error.txt" not in done]


def _run(drive, out_id, f, fn):
    """Download, process, upload. Returns a traceback string if `fn` failed, else None.
    Drive faults propagate."""
    with tempfile.TemporaryDirectory() as d:
        src, dst = os.path.join(d, "in.stl"), os.path.join(d, "out.stl")
        drive.download(f["id"], src)
        try:
            fn(src, dst)
        except Exception:
            return traceback.format_exc()
        drive.upload(out_id, f["name"], dst, "model/stl")
    return None


def process_one(drive, out_id, f, fn, label=""):
    """One input. Returns True on success. A processing failure leaves
    <name>.error.txt with the traceback; a Drive fault (download/upload) leaves
    nothing behind so the next tick retries."""
    err_name = f["name"] + ".error.txt"
    started = time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime())
    marker = drive.upload(out_id, err_name, MARKER.format(started=started).encode(), "text/plain")
    t = time.time()
    try:
        failure = _run(drive, out_id, f, fn)
    except Exception:
        print(f"{label}{f['name']} drive fault, retrying next tick\n{traceback.format_exc()}", file=sys.stderr)
        drive.delete(marker)
        return False
    drive.delete(marker)
    if failure:
        drive.upload(out_id, err_name, failure.encode(), "text/plain")
        print(f"{label}{f['name']} FAILED\n{failure}", file=sys.stderr)
        return False
    print(f"{label}{f['name']} ok ({time.time() - t:.1f}s)", file=sys.stderr)
    return True


def run(drive, root, deadline, processes=PROCESSES):
    for name, fn in processes.items():
        proc = drive.folder(root, name)
        in_id, out_id = drive.folder(proc, "in"), drive.folder(proc, "out")
        for f in pending(drive.list(in_id), drive.list(out_id)):
            if time.time() > deadline:
                print(f"{name}: out of time, resuming next tick", file=sys.stderr)
                return
            try:
                process_one(drive, out_id, f, fn, label=f"{name}: ")
            except Exception:   # Drive fault on the marker itself: skip the file this tick
                print(f"{name}: {f['name']} drive fault, retrying next tick\n{traceback.format_exc()}", file=sys.stderr)


def handler(event, context):
    import boto3  # only in the lambda image

    sm = boto3.client("secretsmanager")
    creds = json.loads(sm.get_secret_value(SecretId=os.environ["GOOGLE_OAUTH_SECRET_ARN"])["SecretString"])
    deadline = time.time() + context.get_remaining_time_in_millis() / 1000 - RESERVE
    run(Drive(**creds), os.environ["DRIVE_ROOT_FOLDER_ID"], deadline)


class _FakeDrive:
    """In-memory stand-in with the five Drive methods run() and process_one() use."""

    def __init__(self):
        self.files = {}   # id -> (parent, name, bytes)
        self.seq = 0

    def list(self, parent):
        return [dict(id=i, name=n, mimeType="application/octet-stream")
                for i, (p, n, _) in self.files.items() if p == parent]

    def folder(self, parent, name):
        return f"{parent}/{name}"

    def download(self, file_id, path):
        with open(path, "wb") as fh:
            fh.write(self.files[file_id][2])

    def upload(self, parent, name, src, mime=""):
        data = src if isinstance(src, bytes) else open(src, "rb").read()
        self.seq += 1
        fid = f"f{self.seq}"
        self.files[fid] = (parent, name, data)
        return fid

    def delete(self, file_id):
        del self.files[file_id]


def _selfcheck():
    ins = [dict(name="a.stl"), dict(name="B.STL"), dict(name="c.stl"), dict(name="notes.txt"), dict(name="d.stl")]
    outs = [dict(name="a.stl"), dict(name="c.stl.error.txt")]
    assert [f["name"] for f in pending(ins, outs)] == ["B.STL", "d.stl"]

    def upper(src, dst):
        with open(src, "rb") as i, open(dst, "wb") as o:
            data = i.read()
            if data == b"bad":
                raise ValueError("boom")
            o.write(data.upper())

    d = _FakeDrive()
    d.upload("root/p/in", "good.stl", b"hello")
    d.upload("root/p/in", "bad.stl", b"bad")
    d.upload("root/p/in", "skip.txt", b"x")
    run(d, "root", time.time() + 60, {"p": upper})
    out = {n: data for (p, n, data) in d.files.values() if p == "root/p/out"}
    assert out["good.stl"] == b"HELLO", out
    assert b"ValueError: boom" in out["bad.stl.error.txt"], out
    assert set(out) == {"good.stl", "bad.stl.error.txt"}, out      # markers cleaned up
    n = len(d.files)
    run(d, "root", time.time() + 60, {"p": upper})
    assert len(d.files) == n, "second tick must be a no-op"
    d.upload("root/p/in", "late.stl", b"late")
    run(d, "root", time.time() - 1, {"p": upper})
    assert not any(p == "root/p/out" and n.startswith("late.stl") for (p, n, _) in d.files.values()), \
        "past deadline must not process"

    # a Drive fault (on download, the output upload, or the marker upload) leaves nothing
    # behind, doesn't stop the tick, and the file is retried on the next one
    class _FlakyDrive(_FakeDrive):
        def __init__(self, trip):
            super().__init__()
            self.trip, self.tripped = trip, False

        def _maybe_trip(self, op):
            if op == self.trip and not self.tripped:
                self.tripped = True
                raise OSError("drive 503")

        def download(self, file_id, path):
            self._maybe_trip("download")
            super().download(file_id, path)

        def upload(self, parent, name, src, mime=""):
            self._maybe_trip("output" if mime == "model/stl" else "marker")
            return super().upload(parent, name, src, mime)

    for trip in ("download", "output", "marker"):
        d = _FlakyDrive(None)
        d.upload("root/p/in", "x.stl", b"x")
        d.upload("root/p/in", "y.stl", b"y")
        d.trip = trip
        run(d, "root", time.time() + 60, {"p": upper})
        out = {n for (p, n, _) in d.files.values() if p == "root/p/out"}
        assert out == {"y.stl"}, (trip, out)          # x faulted: no marker left; y still processed
        run(d, "root", time.time() + 60, {"p": upper})
        out = {n for (p, n, _) in d.files.values() if p == "root/p/out"}
        assert out == {"x.stl", "y.stl"}, (trip, out)

    # the real upload(): bytes and path bodies both reach the PUT with a Content-Length
    calls = []
    real = Drive.__new__(Drive)
    real.hdr = {}

    class _R:
        headers = {"Location": "session"}
        def __enter__(self): return self
        def __exit__(self, *a): pass

    real._open = lambda url, data=None, method=None, **h: _R()
    real._json = lambda url, data=None, method=None, **h: calls.append((data if isinstance(data, bytes) else data.read(), h)) or {"id": "i"}
    assert real.upload("out", "a", b"abc") == "i" and calls[-1] == (b"abc", {"Content-Type": "application/octet-stream", "Content-Length": "3"})
    with tempfile.NamedTemporaryFile(suffix=".stl") as tmp:
        tmp.write(b"hello"); tmp.flush()
        real.upload("out", "b", tmp.name, "model/stl")
    assert calls[-1] == (b"hello", {"Content-Type": "model/stl", "Content-Length": "5"}), calls[-1]
    print("drive selfcheck ok")


if __name__ == "__main__":
    if len(sys.argv) == 3:   # python -m autoblender.drive <root_folder_id> <creds.json>
        with open(sys.argv[2]) as fh:
            run(Drive(**json.load(fh)), sys.argv[1], time.time() + 3000)
    else:
        _selfcheck()
