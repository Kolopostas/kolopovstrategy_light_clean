# core/log_uploader.py
import os, time, json, base64, hashlib
from pathlib import Path
import urllib.request, urllib.error

GH_TOKEN   = os.getenv("GH_TOKEN")
GH_REPO    = os.getenv("GH_REPO")
GH_BRANCH  = os.getenv("GH_BRANCH", "logs")
GH_PATH    = os.getenv("GH_PATH", "logs/trades.csv")
UPLOAD_EVERY_SEC = int(os.getenv("UPLOAD_EVERY_SEC", "60"))

_last_push_ts = 0
_last_etag    = ""

def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()

def _github_get_file_sha():
    if not (GH_TOKEN and GH_REPO and GH_BRANCH and GH_PATH):
        return None
    url = f"https://api.github.com/repos/{GH_REPO}/contents/{GH_PATH}?ref={GH_BRANCH}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {GH_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode("utf-8"))
            return data.get("sha")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise

def _github_put_file(content_b64: str, sha: str | None, message: str):
    url = f"https://api.github.com/repos/{GH_REPO}/contents/{GH_PATH}"
    body = {
        "message": message,
        "content": content_b64,
        "branch": GH_BRANCH,
    }
    if sha:
        body["sha"] = sha
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers={
        "Authorization": f"Bearer {GH_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
    }, method="PUT")
    with urllib.request.urlopen(req, timeout=20) as r:
        r.read()

def push_file_if_needed(local_path: str | Path, reason: str = "trade-log update"):
    global _last_push_ts, _last_etag
    if not (GH_TOKEN and GH_REPO):
        return  # нет конфигурации — тихо выходим

    path = Path(local_path)
    if not path.exists():
        return

    # дебаунс
    now = time.time()
    if now - _last_push_ts < UPLOAD_EVERY_SEC:
        return

    # если файл не менялся — не пушим
    etag = _sha256_file(path)
    if etag == _last_etag:
        return

    content = path.read_bytes()
    b64 = base64.b64encode(content).decode("ascii")
    sha = _github_get_file_sha()
    try:
        _github_put_file(b64, sha, f"{reason}: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        _last_push_ts = now
        _last_etag = etag
        print(f"[log_uploader] pushed {GH_PATH} to {GH_REPO}@{GH_BRANCH} ({len(content)} bytes)")
    except Exception as e:
        print(f"[log_uploader] push failed: {e}")
