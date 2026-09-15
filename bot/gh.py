# -*- coding: utf-8 -*-
"""Async GitHub Contents API client used for all state persistence."""
import asyncio
import base64
import json

import aiohttp

import config


class GHError(Exception):
    pass


class GH:
    def __init__(self, token: str, repo: str, branch: str = "main"):
        self.token = token
        self.repo = repo
        self.branch = branch
        self.api = f"https://api.github.com/repos/{repo}"
        self.session: aiohttp.ClientSession | None = None
        self._shas: dict[str, str | None] = {}      # path -> last known sha
        self._locks: dict[str, asyncio.Lock] = {}
        self._merge_hooks: dict[str, object] = {}   # path -> callable(remote_data, local_data) -> merged

    async def start(self):
        self.session = aiohttp.ClientSession(
            headers={
                "Authorization": f"token {self.token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "exam-bot",
            },
            timeout=aiohttp.ClientTimeout(total=60),
        )

    async def stop(self):
        if self.session:
            await self.session.close()
            self.session = None

    def _lock(self, path: str) -> asyncio.Lock:
        if path not in self._locks:
            self._locks[path] = asyncio.Lock()
        return self._locks[path]

    def set_merge_hook(self, path: str, fn):
        """fn(remote_json_or_None, local_json) -> merged_json ; used on sha conflicts."""
        self._merge_hooks[path] = fn

    async def _req(self, method: str, url: str, payload: dict | None = None):
        assert self.session
        async with self.session.request(method, url, json=payload) as r:
            text = await r.text()
            return r.status, text

    # ---------------- raw file access ----------------

    async def get_json(self, path: str):
        """Return (data, sha). (None, None) if the file does not exist."""
        url = f"{self.api}/contents/{path}?ref={self.branch}"
        status, text = await self._req("GET", url)
        if status == 404:
            self._shas[path] = None
            return None, None
        if status != 200:
            raise GHError(f"GET {path} -> {status}: {text[:200]}")
        info = json.loads(text)
        self._shas[path] = info.get("sha")
        if info.get("encoding") == "base64":
            raw = base64.b64decode(info.get("content", ""))
            try:
                return json.loads(raw.decode("utf-8")), info.get("sha")
            except Exception:
                return None, info.get("sha")
        # big files have no content; fall back to raw
        raw_url = info.get("raw_url") or f"https://raw.githubusercontent.com/{self.repo}/{self.branch}/{path}"
        status2, text2 = await self._req("GET", raw_url)
        if status2 != 200:
            return None, info.get("sha")
        try:
            return json.loads(text2), info.get("sha")
        except Exception:
            return None, info.get("sha")

    async def list_dir(self, path: str) -> list[dict]:
        url = f"{self.api}/contents/{path}?ref={self.branch}"
        status, text = await self._req("GET", url)
        if status == 404:
            return []
        if status != 200:
            raise GHError(f"LIST {path} -> {status}")
        return json.loads(text)

    async def put_json(self, path: str, data, message: str = "update", merge=None, retries: int = 4) -> str:
        """Write JSON file. On sha conflict optionally merge with remote and retry."""
        async with self._lock(path):
            for attempt in range(retries):
                if path not in self._shas:
                    _, sha = await self.get_json(path)
                sha = self._shas.get(path)
                body = {
                    "message": message,
                    "branch": self.branch,
                    "content": base64.b64encode(
                        json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8")
                    ).decode(),
                }
                if sha:
                    body["sha"] = sha
                status, text = await self._req("PUT", f"{self.api}/contents/{path}", body)
                if status in (200, 201):
                    info = json.loads(text)
                    self._shas[path] = info.get("content", {}).get("sha")
                    return self._shas[path]
                if status == 409 or (status == 422 and "does not match" in text):
                    # refresh remote; try merge
                    remote, rsha = await self.get_json(path)
                    self._shas[path] = rsha
                    if merge is not None and remote is not None:
                        try:
                            data = merge(remote, data)
                        except Exception:
                            pass
                    await asyncio.sleep(0.8 + attempt)
                    continue
                raise GHError(f"PUT {path} -> {status}: {text[:300]}")
            raise GHError(f"PUT {path} failed after {retries} retries")

    async def put_file(self, path: str, data: bytes, message: str = "upload") -> str:
        """Write a binary file (e.g. question images) with conflict retry."""
        async with self._lock(path):
            for attempt in range(4):
                if path not in self._shas:
                    await self.get_json(path)
                sha = self._shas.get(path)
                body = {
                    "message": message,
                    "branch": self.branch,
                    "content": base64.b64encode(data).decode(),
                }
                if sha:
                    body["sha"] = sha
                status, text = await self._req("PUT", f"{self.api}/contents/{path}", body)
                if status in (200, 201):
                    info = json.loads(text)
                    self._shas[path] = info.get("content", {}).get("sha")
                    return self._shas[path]
                if status == 409 or (status == 422 and "does not match" in text):
                    await self.get_json(path)
                    await asyncio.sleep(0.8 + attempt)
                    continue
                raise GHError(f"PUT {path} -> {status}: {text[:300]}")
            raise GHError(f"PUT {path} failed after retries")

    async def delete_file(self, path: str, message: str = "delete"):
        async with self._lock(path):
            if path not in self._shas:
                _, sha = await self.get_json(path)
            sha = self._shas.get(path)
            self._shas.pop(path, None)
            if not sha:
                return  # already gone
            body = {"message": message, "branch": self.branch, "sha": sha}
            status, text = await self._req("DELETE", f"{self.api}/contents/{path}", body)
            if status not in (200, 200 + 5):
                if status != 200:
                    raise GHError(f"DELETE {path} -> {status}: {text[:200]}")

    async def dispatch(self, event_type: str = "restart-bot"):
        url = f"{self.api}/dispatches"
        status, text = await self._req("POST", url, {"event_type": event_type})
        if status not in (204, 200):
            raise GHError(f"dispatch -> {status}: {text[:200]}")

    # ---------------- helpers ----------------

    async def repo_public_key(self):
        status, text = await self._req("GET", f"{self.api}/actions/secrets/public-key")
        if status != 200:
            raise GHError(f"public-key -> {status}: {text[:200]}")
        d = json.loads(text)
        return d["key_id"], d["key"]
