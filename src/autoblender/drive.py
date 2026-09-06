"""Google Drive folder watcher: <root>/<process>/in  ->  <root>/<process>/out.

Runs as a scheduled Lambda (handler) or locally (python -m autoblender.drive).
Drive is the only state: an input is pending until a same-named file, or
<name>.error.txt, exists in out/. Delete the out file to re-run an input.
ponytail: full folder listing per tick; fine to a few thousand files per folder.
ponytail: a single file that outlives the Lambda timeout is retried every tick.
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

    def upload(self, parent, name, path, mime="application/octet-stream"):
        meta = json.dumps(dict(name=name, parents=[parent])).encode()
        with self._open(UPLOAD, meta, "POST", **{"Content-Type": "application/json",
                                                 "X-Upload-Content-Type": mime}) as r:
            session = r.headers["Location"]
        with open(path, "rb") as f:
            return self._json(session, f.read(), "PUT", **{"Content-Type": mime})


def pending(inputs, outputs):
    done = {f["name"] for f in outputs}
    return [f for f in inputs
            if f["name"].lower().endswith(".stl")
            and f["name"] not in done
            and f["name"] + ".error.txt" not in done]


def run(drive, root, deadline):
    for name, fn in PROCESSES.items():
        proc = drive.folder(root, name)
        in_id, out_id = drive.folder(proc, "in"), drive.folder(proc, "out")
        for f in pending(drive.list(in_id), drive.list(out_id)):
            if time.time() > deadline:
                print(f"{name}: out of time, resuming next tick", file=sys.stderr)
                return
            with tempfile.TemporaryDirectory() as d:
                src, dst = os.path.join(d, "in.stl"), os.path.join(d, "out.stl")
                drive.download(f["id"], src)
                t = time.time()
                try:
                    fn(src, dst)
                    drive.upload(out_id, f["name"], dst, "model/stl")
                    print(f"{name}: {f['name']} ok ({time.time() - t:.1f}s)", file=sys.stderr)
                except Exception:
                    err = os.path.join(d, "error.txt")
                    with open(err, "w") as fh:
                        fh.write(traceback.format_exc())
                    drive.upload(out_id, f["name"] + ".error.txt", err, "text/plain")
                    print(f"{name}: {f['name']} FAILED", file=sys.stderr)


def handler(event, context):
    import boto3  # only in the lambda image

    sm = boto3.client("secretsmanager")
    creds = json.loads(sm.get_secret_value(SecretId=os.environ["GOOGLE_OAUTH_SECRET_ARN"])["SecretString"])
    deadline = time.time() + context.get_remaining_time_in_millis() / 1000 - 120
    run(Drive(**creds), os.environ["DRIVE_ROOT_FOLDER_ID"], deadline)


def _selfcheck():
    ins = [dict(name="a.stl"), dict(name="B.STL"), dict(name="c.stl"), dict(name="notes.txt"), dict(name="d.stl")]
    outs = [dict(name="a.stl"), dict(name="c.stl.error.txt")]
    assert [f["name"] for f in pending(ins, outs)] == ["B.STL", "d.stl"]
    print("drive selfcheck ok")


if __name__ == "__main__":
    if len(sys.argv) == 3:   # python -m autoblender.drive <root_folder_id> <creds.json>
        with open(sys.argv[2]) as fh:
            run(Drive(**json.load(fh)), sys.argv[1], time.time() + 3600)
    else:
        _selfcheck()
