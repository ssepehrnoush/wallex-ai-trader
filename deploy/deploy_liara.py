"""Deploy the public control room (ui/server.py --public) to Liara through its REST API.

    python -I deploy/deploy_liara.py --check     # balance + app status, changes nothing
    python -I deploy/deploy_liara.py --create    # create the app if it does not exist (starts hourly billing)
    python -I deploy/deploy_liara.py             # pack, upload, set envs, release, follow the build
    python -I deploy/deploy_liara.py --scale 0   # stop the app (1 starts it again)

Secrets (LIARA_API_TOKEN, BOXAPI_KEY, PUSH_TOKEN) come from agentic-hackathon/.env and are never printed. PUSH_TOKEN is
created there on the first deploy; ui/push_live.py reads the same line. Only files tracked by git are packed, so
nothing local (runs, keys, caches) can travel with the image.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import secrets
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent      # wallex-bot/
ENV_FILE = ROOT.parent / ".env"                     # agentic-hackathon/.env (never in git)
API = "https://api.iran.liara.ir"
APP = os.environ.get("LIARA_APP", "wallex-ai-trader-live")
PLAN = os.environ.get("LIARA_PLAN", "small-g2")
BUNDLE = os.environ.get("LIARA_BUNDLE", "standard")
PORT = 8000
TEAM = os.environ.get("LIARA_TEAM_ID", "6ad4685525646e1982f75717")  # the hackathon credit sits on the team
INCLUDE = ["Dockerfile", "liara.json", "config.json", "wallexbot/", "ui/", "docs/img/", "docs/app/data.json"]


def env() -> dict:
    out = {}
    if ENV_FILE.is_file():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out.setdefault(k.strip(), v.strip())
    if not out.get("PUSH_TOKEN"):
        out["PUSH_TOKEN"] = secrets.token_urlsafe(32)
        with open(ENV_FILE, "a", encoding="utf-8") as fh:
            fh.write(f"\nPUSH_TOKEN={out['PUSH_TOKEN']}\n")
        print("PUSH_TOKEN created in", ENV_FILE)
    return out


ENV = env()
TOKEN = ENV.get("LIARA_API_TOKEN", "")
# api.iran.liara.ir is an Iranian host: go direct, not through the local VPN proxy
DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(method: str, path: str, body: dict | None = None, raw: bytes | None = None,
         ctype: str = "application/json", timeout: int = 60):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    if TEAM:
        path += ("&" if "?" in path else "?") + f"teamID={TEAM}"
    req = urllib.request.Request(API + path, data=data, method=method,
                                 headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": ctype})
    try:
        with DIRECT.open(req, timeout=timeout) as r:
            txt = r.read().decode("utf-8", "replace")
            return r.status, (json.loads(txt) if txt.strip().startswith(("{", "[")) else txt)
    except urllib.error.HTTPError as e:
        txt = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(txt)
        except ValueError:
            return e.code, txt[:500]


def balance() -> float | None:
    st, b = call("GET", "/v1/me")
    if st == 200 and isinstance(b, dict):
        owner = b.get("team") or b.get("user") or {}
        return float(owner.get("balance", 0))
    return None


def app_status() -> dict | None:
    st, d = call("GET", f"/v1/projects/{APP}")
    return d.get("project") if st == 200 and isinstance(d, dict) else None


def pack() -> bytes:
    files = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z", "--", *INCLUDE],
                           capture_output=True, check=True).stdout.decode("utf-8").split("\0")
    files = [f for f in files if f and "__pycache__" not in f]
    if "Dockerfile" not in files or "ui/server.py" not in files:
        sys.exit("Dockerfile or ui/server.py is not committed; commit first, only tracked files are packed")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", compresslevel=6) as tar:
        for rel in files:
            tar.add(ROOT / rel, arcname=rel, recursive=False)
    print(f"packed {len(files)} tracked files")
    return buf.getvalue()


def multipart(field: str, filename: str, payload: bytes) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{filename}\"\r\n"
            f"Content-Type: application/gzip\r\n\r\n").encode()
    return head + payload + f"\r\n--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


def set_envs() -> None:
    variables = [{"key": "BOXAPI_KEY", "value": ENV.get("BOXAPI_KEY", "")},
                 {"key": "PUSH_TOKEN", "value": ENV["PUSH_TOKEN"]}]
    st, d = call("POST", "/v1/projects/update-envs", {"project": APP, "variables": variables})
    print("envs:", st, "ok" if st == 200 else (d.get("message") if isinstance(d, dict) else str(d))[:120])


def follow(release_id: str) -> str | None:
    t0 = time.time()
    while time.time() - t0 < 1200:
        st, rel = call("GET", f"/v1/projects/{APP}/releases")
        cur = next((r for r in (rel.get("releases", []) if st == 200 and isinstance(rel, dict) else [])
                    if r.get("_id") == release_id), None)
        state = (cur or {}).get("state") or (cur or {}).get("status")
        if state in ("READY", "DEPLOYED", "SUCCEEDED", "FAILED", "CANCELED", "ERROR"):
            print("release state:", state)
            if state not in ("READY", "DEPLOYED", "SUCCEEDED"):
                st, logs = call("GET", f"/v2/releases/{release_id}/build-logs")
                for entry in (logs if isinstance(logs, list) else [])[-40:]:
                    print("  |", (entry.get("line") if isinstance(entry, dict) else str(entry)).rstrip()[:300])
            return state
        time.sleep(6)
    print("stopped following after 20 minutes; check the Liara console")
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--create", action="store_true")
    ap.add_argument("--scale", type=int, help="0 stops the app, 1 starts it again")
    a = ap.parse_args()
    if not TOKEN:
        sys.exit("LIARA_API_TOKEN missing in agentic-hackathon/.env")
    if a.scale is not None:
        st, d = call("POST", f"/v1/projects/{APP}/actions/scale", {"scale": a.scale})
        print("scale", a.scale, "->", st)
        return
    proj = app_status()
    print(f"balance: {balance()} toman | app '{APP}': {'exists, status=' + str(proj.get('status')) if proj else 'missing'}")
    if a.check:
        return
    if not proj:
        if not a.create:
            sys.exit("app does not exist; run with --create (this starts hourly billing)")
        body = {"name": APP, "planID": PLAN, "platform": "docker", "bundlePlanID": BUNDLE, "readOnlyRootFilesystem": False}
        st, nets = call("GET", "/v1/networks")  # g2 plans need a private network
        if st == 200 and isinstance(nets, dict) and nets.get("networks"):
            body["network"] = nets["networks"][0]["_id"]
        st, d = call("POST", "/v1/projects", body)
        print("create:", st, "ok" if st in (200, 201) else d)
        if st not in (200, 201):
            sys.exit(1)
    set_envs()
    blob = pack()
    print(f"source: {len(blob) / 1048576:.1f} MB")
    body, ctype = multipart("file", "source.tar.gz", blob)
    st, d = call("POST", f"/v2/projects/{APP}/sources", raw=body, ctype=ctype, timeout=600)
    if st not in (200, 201) or not isinstance(d, dict) or not d.get("sourceID"):
        sys.exit(f"upload failed: {st} {str(d)[:300]}")
    st, d = call("POST", f"/v2/projects/{APP}/releases", {"sourceID": d["sourceID"], "port": PORT, "type": "docker"})
    if st not in (200, 201) or not isinstance(d, dict) or not d.get("releaseID"):
        sys.exit(f"release failed: {st} {str(d)[:300]}")
    print("release:", d["releaseID"])
    follow(d["releaseID"])
    print(f"url: https://{APP}.liara.run")


if __name__ == "__main__":
    main()
