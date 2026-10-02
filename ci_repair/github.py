"""GitHub REST client with an on-disk response cache and rate-limit handling."""

import gzip
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import requests

API = "https://api.github.com"


def default_token():
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        return token
    try:
        return subprocess.run(["gh", "auth", "token"], capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        print("warning: no GitHub token; limited to 60 requests/hour", file=sys.stderr)
        return None


class GitHub:
    def __init__(self, cache_dir, token=None):
        self.s = requests.Session()
        self.s.headers.update({
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ci-repair-thesis",
        })
        token = token or default_token()
        if token:
            self.s.headers["Authorization"] = f"Bearer {token}"
        self.cache = Path(cache_dir)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.calls = 0

    def _key(self, url, params):
        raw = url + "?" + json.dumps(params or {}, sort_keys=True)
        return self.cache / (hashlib.sha1(raw.encode()).hexdigest() + ".json")

    def _wait_for_rate_limit(self, r):
        if r.status_code in (403, 429) and (
            r.headers.get("X-RateLimit-Remaining") == "0" or "rate limit" in r.text.lower()
        ):
            reset = int(r.headers.get("X-RateLimit-Reset", time.time() + 60))
            wait = max(reset - time.time(), 0) + 5
            print(f"  rate limited; sleeping {wait:.0f}s", file=sys.stderr)
            time.sleep(wait)
            return True
        return False

    def _get(self, url, **kw):
        for attempt in range(6):
            try:
                r = self.s.get(url, timeout=120, **kw)
            except requests.ConnectionError:
                time.sleep(2 ** attempt)
                continue
            self.calls += 1
            if self._wait_for_rate_limit(r):
                continue
            if r.status_code >= 500:
                time.sleep(2 ** attempt)
                continue
            return r
        raise RuntimeError(f"GET {url} failed repeatedly")

    def get_json(self, path, params=None, fresh=False):
        """GET a JSON resource. Cached; pass fresh=True for listings that change over time."""
        url = path if path.startswith("http") else API + path
        key = self._key(url, params)
        if key.exists() and not fresh:
            return json.loads(key.read_text())
        r = self._get(url, params=params)
        if r.status_code in (404, 410, 422):
            return None
        r.raise_for_status()
        data = r.json()
        key.write_text(json.dumps(data))
        return data

    def paginate(self, path, params=None, item_key=None, fresh=False, max_pages=50):
        params = dict(params or {}, per_page=100)
        for page in range(1, max_pages + 1):
            data = self.get_json(path, dict(params, page=page), fresh=fresh)
            items = (data or {}).get(item_key, []) if item_key else (data or [])
            yield from items
            if len(items) < 100:
                return

    def download_log(self, owner, repo, job_id, dest):
        """Save a job's full log to dest (.txt.gz). Returns the text, or None if expired."""
        dest = Path(dest)
        if dest.exists():
            return gzip.decompress(dest.read_bytes()).decode("utf-8", errors="replace")
        r = self._get(f"{API}/repos/{owner}/{repo}/actions/jobs/{job_id}/logs")
        if r.status_code in (404, 410):
            return None
        r.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".tmp")
        tmp.write_bytes(gzip.compress(r.content))
        tmp.rename(dest)
        return r.content.decode("utf-8", errors="replace")
