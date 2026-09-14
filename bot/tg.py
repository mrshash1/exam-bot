# -*- coding: utf-8 -*-
"""Minimal async Telegram Bot API client (long polling + helpers)."""
import asyncio
import html
import io
import json

import aiohttp

import config


class TGConflict(Exception):
    pass


class TGError(Exception):
    pass


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""), quote=False)


class TG:
    def __init__(self, token: str):
        self.token = token
        self.base = f"https://api.telegram.org/bot{token}"
        self.session: aiohttp.ClientSession | None = None
        self.offset = 0
        self.bot_username = config.BOT_USERNAME
        self.bot_id = 0

    async def start(self):
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=90))
        me = await self.api("getMe")
        self.bot_id = me["id"]
        self.bot_username = me["username"]
        return me

    async def stop(self):
        if self.session:
            await self.session.close()
            self.session = None

    async def api(self, method: str, max_retry: int = 5, **params):
        assert self.session
        for attempt in range(max_retry):
            try:
                async with self.session.post(f"{self.base}/{method}", json=params) as r:
                    data = await r.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                if attempt == max_retry - 1:
                    raise TGError(f"{method}: network error {e}")
                await asyncio.sleep(2 + 2 * attempt)
                continue
            if data.get("ok"):
                return data["result"]
            desc = str(data.get("description", ""))
            code = data.get("error_code", 0)
            if code == 409 or "Conflict" in desc:
                raise TGConflict(desc)
            if code == 429:
                retry = data.get("parameters", {}).get("retry_after", 3)
                await asyncio.sleep(min(retry + 1, 30))
                continue
            if code >= 500 and attempt < max_retry - 1:
                await asyncio.sleep(2 + 2 * attempt)
                continue
            raise TGError(f"{method} -> {code}: {desc}")
        raise TGError(f"{method}: retries exhausted")

    # ---------------- helpers ----------------

    async def send(self, chat_id: int, text: str, kb=None, disable_preview: bool = True):
        params = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": disable_preview},
        }
        if kb is not None:
            params["reply_markup"] = {"inline_keyboard": kb}
        try:
            return await self.api("sendMessage", **params)
        except TGError as e:
            if "message is too long" in str(e):
                parts = split_message(text)
                m = None
                for p in parts:
                    pp = dict(params)
                    pp["text"] = p
                    m = await self.api("sendMessage", **pp)
                return m
            raise

    async def edit(self, chat_id: int, message_id: int, text: str, kb=None):
        params = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        }
        if kb is not None:
            params["reply_markup"] = {"inline_keyboard": kb}
        try:
            return await self.api("editMessageText", **params)
        except TGError as e:
            if "message is not modified" in str(e):
                return None
            raise

    async def answer_cb(self, cb_id: str, text: str = "", alert: bool = False):
        try:
            await self.api("answerCallbackQuery", callback_query_id=cb_id,
                           text=text[:190] if text else None, show_alert=alert)
        except TGError:
            pass

    async def send_document(self, chat_id: int, data: bytes, filename: str, caption: str = ""):
        form = aiohttp.FormData()
        form.add_field("chat_id", str(chat_id))
        if caption:
            form.add_field("caption", caption)
            form.add_field("parse_mode", "HTML")
        form.add_field("document", io.BytesIO(data), filename=filename)
        async with self.session.post(f"{self.base}/sendDocument", data=form) as r:
            res = await r.json(content_type=None)
        if not res.get("ok"):
            raise TGError(f"sendDocument -> {res}")
        return res["result"]

    async def download_file(self, file_id: str) -> bytes:
        f = await self.api("getFile", file_id=file_id)
        path = f.get("file_path", "")
        url = f"https://api.telegram.org/file/bot{self.token}/{path}"
        async with self.session.get(url) as r:
            if r.status != 200:
                raise TGError(f"download -> {r.status}")
            return await r.read()

    async def set_commands(self):
        cmds = [
            {"command": "start", "description": "منوی اصلی"},
            {"command": "new", "description": "ساخت آزمون جدید"},
            {"command": "myexams", "description": "آزمون‌های من (دبیر)"},
            {"command": "join", "description": "شرکت در آزمون با کد"},
            {"command": "help", "description": "راهنما"},
        ]
        try:
            await self.api("setMyCommands", commands=cmds)
        except TGError:
            pass

    # ---------------- polling ----------------

    async def poll(self, allowed=None):
        params = {
            "timeout": config.POLL_TIMEOUT,
            "offset": self.offset,
            "allowed_updates": allowed or ["message", "callback_query"],
        }
        res = await self.api("getUpdates", max_retry=1, **params)
        updates = res or []
        if updates:
            self.offset = updates[-1]["update_id"] + 1
        return updates


def split_message(text: str, limit: int = 3900):
    parts = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        parts.append(text[:cut])
        text = text[cut:]
    parts.append(text)
    return parts
