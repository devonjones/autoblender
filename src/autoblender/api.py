"""HTTPS API behind the STL tools desktop client (bin/stl-tools.ps1).

A Lambda function URL hands this raw events. Every request carries X-Api-Key.

    GET  /ops                  -> {"ops": [{name, label, help, options: [{name, label, default}]}]}
    POST /jobs                 -> {"id", "upload_url"}         the client PUTs the STL there
    POST /jobs/<id>/run        {"op", "options": {name: bool}}
                               -> {"download_url", "seconds"}  or 422 {"error"}

Files travel through S3 because function URL bodies cap at 6MB; the bucket
expires everything after a day. The client draws one tab per op and one
checkbox per option, so adding a tool is one PROCESSES entry and a deploy.
"""

import base64
import hmac
import inspect
import json
import os
import re
import sys
import tempfile
import time
import traceback
import uuid
from functools import partial

from .cut import cut_stl

# name -> label (tab title), help (shown on the tab), fn(src, dst, **options),
# options: name -> (checkbox label, default). Options are booleans.
PROCESSES = {
    "cut-6mm": dict(
        label="Cut base",
        help="Cuts off the bottom 6mm (the OpenForge base) and keeps everything above it.",
        fn=partial(cut_stl, z=6.0),
        options={"drop": ("Move the result down to z=0", True)},
    ),
}

JOB_ID = re.compile(r"[0-9a-f]{32}")
URL_TTL = 3600   # seconds a presigned upload/download link stays valid
KEY_TTL = 300    # seconds a container trusts its cached API key

_cache = {}


def _s3():
    if "s3" not in _cache:
        import boto3  # only in the lambda image
        _cache["s3"] = boto3.client("s3")
    return _cache["s3"]


def _api_key():
    # Re-read every KEY_TTL so a rotated key reaches warm containers without a deploy.
    if time.time() - _cache.get("key_at", 0) > KEY_TTL:
        import boto3
        sm = boto3.client("secretsmanager")
        key = sm.get_secret_value(SecretId=os.environ["API_KEY_SECRET_ARN"])["SecretString"].strip()
        if not key:   # fail closed: an empty key would match a request with no header
            raise RuntimeError("API key secret is empty")
        _cache["key"], _cache["key_at"] = key, time.time()
    return _cache["key"]


def _resp(status, body):
    return {"statusCode": status, "headers": {"content-type": "application/json"}, "body": json.dumps(body)}


def ops():
    return [dict(name=n, label=p["label"], help=p["help"],
                 options=[dict(name=k, label=lbl, default=d) for k, (lbl, d) in p["options"].items()])
            for n, p in PROCESSES.items()]


def run_job(job, op, options):
    p = PROCESSES[op]
    kw = {k: bool(options.get(k, d)) for k, (_, d) in p["options"].items()}
    s3, bucket = _s3(), os.environ["BUCKET"]
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = os.path.join(tmp, "in.stl"), os.path.join(tmp, "out.stl")
        try:
            s3.download_file(bucket, f"in/{job}.stl", src)
        except Exception as e:   # botocore ClientError; botocore isn't importable outside the image
            if getattr(e, "response", {}).get("Error", {}).get("Code") in ("404", "NoSuchKey"):
                return _resp(404, {"error": "upload not found (expired, or the upload failed)"})
            raise
        t = time.time()
        try:
            p["fn"](src, dst, **kw)
        except Exception as e:
            print(f"{op} {job} failed\n{traceback.format_exc()}", file=sys.stderr)
            return _resp(422, {"error": f"{type(e).__name__}: {e}"})
        s3.upload_file(dst, bucket, f"out/{job}.stl")
    secs = round(time.time() - t, 1)
    print(f"{op} {job} ok ({secs}s) {kw}", file=sys.stderr)
    url = s3.generate_presigned_url("get_object", Params=dict(Bucket=bucket, Key=f"out/{job}.stl"),
                                    ExpiresIn=URL_TTL)
    return _resp(200, {"download_url": url, "seconds": secs})


def route(event):
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    if not hmac.compare_digest(headers.get("x-api-key", "").encode(), _api_key().encode()):
        return _resp(401, {"error": "bad api key"})
    method, path = event["requestContext"]["http"]["method"], event.get("rawPath", "/")
    if method == "GET" and path == "/ops":
        return _resp(200, {"ops": ops()})
    if method == "POST" and path == "/jobs":
        job = uuid.uuid4().hex
        url = _s3().generate_presigned_url("put_object", Params=dict(Bucket=os.environ["BUCKET"], Key=f"in/{job}.stl"),
                                           ExpiresIn=URL_TTL)
        return _resp(200, {"id": job, "upload_url": url})
    m = re.fullmatch(r"/jobs/([^/]+)/run", path)
    if method == "POST" and m:
        if not JOB_ID.fullmatch(m[1]):
            return _resp(400, {"error": "bad job id"})
        body = event.get("body") or "{}"
        try:   # base64, utf-8 and JSON errors are all ValueErrors
            req = json.loads(base64.b64decode(body).decode() if event.get("isBase64Encoded") else body)
        except ValueError:
            return _resp(400, {"error": "body is not JSON"})
        if not isinstance(req, dict) or not isinstance(req.get("options") or {}, dict):
            return _resp(400, {"error": "body must be {\"op\": ..., \"options\": {...}}"})
        if not isinstance(req.get("op"), str) or req["op"] not in PROCESSES:
            return _resp(400, {"error": f"unknown op {req.get('op')!r}"})
        return run_job(m[1], req["op"], req.get("options") or {})
    return _resp(404, {"error": f"no route {method} {path}"})


def handler(event, context):
    try:
        return route(event)
    except Exception:
        print(traceback.format_exc(), file=sys.stderr)
        return _resp(500, {"error": "internal error, see the Lambda log"})


class _FakeS3:
    """The four S3 calls api.py makes, in memory. A missing key raises like botocore."""

    def __init__(self, missing_code="404"):
        self.objs, self.missing_code = {}, missing_code

    def generate_presigned_url(self, method, Params, ExpiresIn):
        return f"https://s3.test/{method}/{Params['Key']}"

    def download_file(self, bucket, key, path):
        if key not in self.objs:
            e = RuntimeError(f"An error occurred ({self.missing_code}) when calling the GetObject operation")
            e.response = {"Error": {"Code": self.missing_code}}
            raise e
        with open(path, "wb") as fh:
            fh.write(self.objs[key])

    def upload_file(self, path, bucket, key):
        with open(path, "rb") as fh:
            self.objs[key] = fh.read()


def _call(method, path, body=None, key="sekrit", raw=None, b64=False):
    r = handler(dict(headers={"X-Api-Key": key}, rawPath=path, isBase64Encoded=b64,
                     body=raw if raw is not None else json.dumps(body) if body else None,
                     requestContext=dict(http=dict(method=method))), None)
    return r["statusCode"], json.loads(r["body"])


def _check_registry():
    for name, p in PROCESSES.items():   # every declared option must be a parameter of its fn
        params = inspect.signature(p["fn"]).parameters
        assert set(p["options"]) <= set(params), (name, set(p["options"]) - set(params))


def _check_key():
    """A blank secret must fail closed, not match a request with no header."""
    import types
    secret = types.SimpleNamespace(get_secret_value=lambda SecretId: {"SecretString": "  \n"})
    saved = sys.modules.get("boto3"), dict(_cache), os.environ.get("API_KEY_SECRET_ARN")
    sys.modules["boto3"] = types.SimpleNamespace(client=lambda name: secret)
    os.environ["API_KEY_SECRET_ARN"] = "arn:test"
    _cache.clear()
    try:
        assert _call("GET", "/ops", key="")[0] == 500
    finally:
        boto3, cache, arn = saved
        if boto3 is None:
            sys.modules.pop("boto3")
        else:
            sys.modules["boto3"] = boto3
        _cache.clear()
        _cache.update(cache)
        if arn is None:
            os.environ.pop("API_KEY_SECRET_ARN")


def _check_routes():
    def upper(src, dst, shout):
        with open(src, "rb") as fh:
            data = fh.read()
        if data == b"bad":
            raise ValueError("boom")
        with open(dst, "wb") as fh:
            fh.write(data.upper() if shout else data)

    s3 = _FakeS3()
    _cache["s3"] = s3
    PROCESSES["t"] = dict(label="T", help="", fn=upper, options={"shout": ("Shout", True)})
    assert _call("GET", "/ops", key="nope")[0] == 401
    st, b = _call("GET", "/ops")
    assert st == 200 and b["ops"][-1]["options"] == [dict(name="shout", label="Shout", default=True)], b
    st, b = _call("POST", "/jobs")
    job = b["id"]
    assert st == 200 and b["upload_url"].endswith(f"in/{job}.stl"), b
    assert _call("POST", f"/jobs/{job}/run", {"op": "t"})[0] == 404, "nothing uploaded yet"
    s3.objs[f"in/{job}.stl"] = b"hi"
    st, b = _call("POST", f"/jobs/{job}/run", {"op": "t"})
    assert st == 200 and s3.objs[f"out/{job}.stl"] == b"HI", (st, b)
    st, b = _call("POST", f"/jobs/{job}/run", {"op": "t", "options": {"shout": False, "evil": True}})
    assert st == 200 and s3.objs[f"out/{job}.stl"] == b"hi", (st, b)
    raw = base64.b64encode(json.dumps({"op": "t", "options": {"shout": True}}).encode()).decode()
    st, b = _call("POST", f"/jobs/{job}/run", raw=raw, b64=True)
    assert st == 200 and s3.objs[f"out/{job}.stl"] == b"HI", (st, b)
    s3.objs[f"in/{job}.stl"] = b"bad"
    st, b = _call("POST", f"/jobs/{job}/run", {"op": "t"})
    assert st == 422 and b["error"] == "ValueError: boom", (st, b)
    bad = ("{nope", False), ("!!notbase64", True), ("[1]", False), ('{"op": "t", "options": [1]}', False), \
          ('{"op": [1]}', False), ('{"op": "nope"}', False)
    for raw, b64 in bad:
        assert _call("POST", f"/jobs/{job}/run", raw=raw, b64=b64)[0] == 400, raw
    assert _call("POST", "/jobs/..%2Fx/run", {"op": "t"})[0] == 400
    assert _call("DELETE", "/jobs")[0] == 404
    del PROCESSES["t"]
    return job


def _check_missing_upload(job):
    for code, status in (("NoSuchKey", 404), ("404", 404), ("AccessDenied", 500)):
        _cache["s3"] = _FakeS3(missing_code=code)
        assert _call("POST", f"/jobs/{job}/run", {"op": "cut-6mm"})[0] == status, code


def _check_real_cut(job):
    """The real registry entry, end to end: cut-6mm on a 10mm cube standing on z=0."""
    import io
    import trimesh
    s3 = _cache["s3"] = _FakeS3()
    box = trimesh.creation.box(extents=[10, 10, 10])
    box.apply_translation([0, 0, 5])
    s3.objs[f"in/{job}.stl"] = box.export(file_type="stl")
    for drop, zmin in ((True, 0.0), (False, 6.0)):
        st, b = _call("POST", f"/jobs/{job}/run", {"op": "cut-6mm", "options": {"drop": drop}})
        out = trimesh.load(io.BytesIO(s3.objs[f"out/{job}.stl"]), file_type="stl")
        assert st == 200 and abs(out.bounds[0][2] - zmin) < 1e-4 and abs(out.volume - 400) < 1e-3, (drop, st, b, out.bounds)


def _selfcheck():
    _check_registry()
    _cache.update(key="sekrit", key_at=time.time())
    os.environ["BUCKET"] = "b"
    job = _check_routes()
    _check_missing_upload(job)
    _check_real_cut(job)
    _check_key()
    print("api selfcheck ok")


if __name__ == "__main__":
    _selfcheck()
