# -*- coding: utf-8 -*-
"""Exam bot entry point: boot, router, long-polling lifecycle."""
import asyncio
import json
import re
import sys
import time

import config
import scoring
import views
from gh import GH
from run import Runner
from store import Store
from tg import TG, TGConflict, TGError, esc
from wizard import Wizard

CODE_RE = re.compile(r"^[A-Z0-9]{4,8}$")


def main_menu_kb():
    return views.main_menu_kb()


class Bot:
    def __init__(self):
        self.gh = GH(config.GH_TOKEN, config.GH_REPO, config.GH_BRANCH)
        self.tg = TG(config.BOT_TOKEN)
        self.store = Store(self.gh)
        self.wizard = Wizard(self.tg, self.store)
        self.runner = Runner(self.tg, self.store)
        self.started_at = time.time()

    # ------------------------------------------------------------------ boot

    async def boot(self):
        await self.gh.start()
        await self.tg.start()          # getMe; raises if token invalid
        self.store.gh_bot_username = self.tg.bot_username
        await self.tg.set_commands()
        await self.store.load()
        for uid in list(self.store.sessions.keys()):
            self.runner.arm_timer(uid)
        self.store.start_flusher()
        print(f"[bot] ready as @{self.tg.bot_username}")

    async def shutdown(self, code=0):
        try:
            await self.store.flush_all()
        except Exception:
            pass
        try:
            await self.store.stop()
        except Exception:
            pass
        try:
            await self.tg.stop()
        except Exception:
            pass
        try:
            await self.gh.stop()
        except Exception:
            pass
        sys.exit(code)

    # ------------------------------------------------------------------ messages

    async def cmd_start(self, chat_id, uid, payload, name=""):
        if payload.startswith("exam_"):
            code = payload[5:].strip().upper()
            await self.runner.view_exam(chat_id, uid, code)
            return
        if payload.startswith("join_"):
            code = payload[5:].strip().upper()
            await self.runner.view_exam(chat_id, uid, code)
            return
        txt = (f"سلام {esc(name)} 👋\n\n"
               "به <b>ربات آزمون</b> خوش آمدی!\n\n"
               "👩‍🏫 <b>دبیری:</b> با «ساخت آزمون جدید» یک آزمون با حالت متنی + اپ + سایت بساز؛ "
               "لینک و کد آزمون همان‌جا به تو داده می‌شود.\n"
               "🎯 <b>دانش‌آموزی:</b> روی لینک دبیر بزن یا کد آزمون را بفرست.\n\n"
               "👇 یکی از گزینه‌های زیر را انتخاب کن:")
        await self.tg.send(chat_id, txt, main_menu_kb())

    async def handle_message(self, m):
        frm = m.get("from") or {}
        uid = frm.get("id")
        chat_id = m["chat"]["id"]
        if uid is None:
            return
        name = ((frm.get("first_name", "") + " " + frm.get("last_name", "")).strip()) or frm.get("username", "")
        username = frm.get("username", "")
        text = (m.get("text") or "").strip()

        # ---- Mini App result submission ----
        wad = m.get("web_app_data")
        if wad:
            try:
                payload = json.loads(wad.get("data", ""))
            except Exception:
                payload = None
            if isinstance(payload, dict) and str(payload.get("v")) == "1":
                await self.runner.handle_result_payload(chat_id, uid, name, username,
                                                        payload, "app")
            else:
                await self.tg.send(chat_id, "❌ داده‌ی ارسالی از اپ قابل خواندن نبود؛ دوباره تلاش کن.")
            return

        # ---- teacher wizard: photo questions (photo or image document) ----
        if uid in self.store.wizards:
            ph = m.get("photo")
            if ph:
                big = ph[-1] if isinstance(ph, list) and ph else {}
                if await self.wizard.handle_photo(chat_id, uid, big.get("file_id", ""),
                                                  m.get("caption", ""), big.get("file_size", 0) or 0):
                    return
            doc = m.get("document")
            if doc and str(doc.get("mime_type", "")).startswith("image/"):
                if await self.wizard.handle_photo(chat_id, uid, doc.get("file_id", ""),
                                                  m.get("caption", ""), doc.get("file_size", 0) or 0):
                    return

        # ---- commands ----
        if text.startswith("/"):
            cmd = text.split()[0].split("@")[0].lower()
            arg = text.split(maxsplit=1)[1].strip() if len(text.split(maxsplit=1)) > 1 else ""
            if cmd == "/start":
                await self.cmd_start(chat_id, uid, arg, name)
                return
            if cmd in ("/new", "/newexam"):
                await self.wizard.start_new(chat_id, uid, name, username)
                return
            if cmd in ("/myexams", "/exams"):
                await self.wizard.my_exams(chat_id, uid)
                return
            if cmd in ("/join", "/exam"):
                if arg:
                    await self.runner.view_exam(chat_id, uid, arg.upper())
                else:
                    await self.runner.join_ask(chat_id, uid)
                return
            if cmd == "/help":
                await self.tg.send(chat_id, views.HELP_TEXT, main_menu_kb())
                return
            await self.tg.send(chat_id, "دستور ناشناخته؛ از منوی زیر استفاده کن:", main_menu_kb())
            return

        # ---- active text-mode exam session has priority ----
        if uid in self.store.sessions:
            await self.runner.session_text(chat_id, uid, text)
            return

        # ---- teacher wizard states ----
        if uid in self.store.wizards:
            if await self.wizard.handle_text(chat_id, uid, name, username, text):
                return

        # ---- pending join code ----
        if uid in self.runner.pending_join:
            self.runner.pending_join.discard(uid)
            await self.runner.view_exam(chat_id, uid, text.upper())
            return

        # ---- web result code ----
        if scoring.is_result_code(text):
            payload = scoring.decode_result_code(text)
            if payload:
                await self.runner.handle_result_payload(chat_id, uid, name, username,
                                                        payload, "code")
            else:
                await self.tg.send(chat_id, "❌ کد نتیجه قابل خواندن نبود؛ کامل کپی کرده‌ای؟")
            return

        # ---- bare exam code ----
        t = text.upper()
        if CODE_RE.fullmatch(t) and t in self.store.exams:
            await self.runner.view_exam(chat_id, uid, t)
            return

        await self.tg.send(
            chat_id,
            "🤔 متوجه نشدم. کد آزمون (۶ کاراکتر) بفرست یا از منو استفاده کن:",
            main_menu_kb(),
        )

    # ------------------------------------------------------------------ callbacks

    async def handle_callback(self, cb):
        data = cb.get("data", "")
        cb_id = cb.get("id", "")
        frm = cb.get("from") or {}
        uid = frm.get("id")
        msg = cb.get("message") or {}
        chat_id = msg.get("chat", {}).get("id")
        msg_id = msg.get("message_id")
        if uid is None or chat_id is None:
            await self.tg.answer_cb(cb_id)
            return
        try:
            parts = data.split(":")
            if parts[0] == "menu":
                await self.cmd_start(chat_id, uid, "", frm.get("first_name", ""))
            elif parts[0] == "help":
                await self.tg.send(chat_id, views.HELP_TEXT, main_menu_kb())
            elif parts[0] == "join":
                await self.runner.join_ask(chat_id, uid, edit_msg=msg_id)
            elif parts[0] == "pub":
                await self.runner.public_list(chat_id, uid, edit_msg=msg_id)
            elif parts[0] == "exa":
                if len(parts) >= 3:
                    kind = parts[1]
                    code = parts[2]
                    qi = int(parts[3]) if len(parts) > 3 and parts[3].lstrip("-").isdigit() else 0
                    oi = int(parts[4]) if len(parts) > 4 and parts[4].lstrip("-").isdigit() else -1
                    await self.runner.cb_answer(chat_id, msg_id, uid, cb_id, kind, code, qi, oi)
                elif len(parts) == 2 and parts[1] in ("fin", "fin2", "quit", "quit2", "backq"):
                    await self.runner.cb_answer(chat_id, msg_id, uid, cb_id, parts[1], "", 0, -1)
                else:
                    await self.tg.answer_cb(cb_id)
            elif parts[0] in ("wiz", "exm", "exs"):
                await self.wizard.handle_cb(chat_id, msg_id, uid, data)
            else:
                await self.tg.answer_cb(cb_id)
        except TGError as e:
            print("[cb] tg error:", e)
            await self.tg.answer_cb(cb_id, "خطا! دوباره تلاش کن.", alert=True)
        except Exception as e:
            print("[cb] error:", repr(e))
            await self.tg.answer_cb(cb_id, "خطای غیرمنتظره! دوباره تلاش کن.", alert=True)
        finally:
            await self.tg.answer_cb(cb_id)

    # ------------------------------------------------------------------ polling

    async def poll_loop(self):
        t0 = time.time()
        last_beat = 0.0
        conflict_since = None
        while True:
            try:
                updates = await self.tg.poll()
                conflict_since = None
                for up in updates:
                    try:
                        if up.get("message"):
                            await self.handle_message(up["message"])
                        elif up.get("callback_query"):
                            await self.handle_callback(up["callback_query"])
                    except Exception as e:
                        print("[update] error:", repr(e))
            except TGConflict as e:
                # Another instance is polling. Wait for it to go away (e.g. a
                # cancelled job's zombie) before giving up.
                if conflict_since is None:
                    conflict_since = time.time()
                    print("[poll] conflict (another instance polling) -> retrying:", e)
                if time.time() - conflict_since > 600:
                    print("[poll] conflict persists >10min -> exiting quietly")
                    await self.shutdown(0)
                    return
                await asyncio.sleep(20)
                continue
            except TGError as e:
                print("[poll] tg error:", e)
                await asyncio.sleep(3)
            except Exception as e:
                print("[poll] unexpected:", repr(e))
                await asyncio.sleep(2)

            # daily-ish heartbeat commit keeps the repo active (GitHub disables
            # cron schedules on repos with no activity for 60 days)
            if time.time() - last_beat > 6 * 3600:
                last_beat = time.time()
                try:
                    await self.gh.put_json("data/heartbeat.json",
                                           {"ts": int(time.time()),
                                            "bot": self.tg.bot_username},
                                           message="heartbeat")
                except Exception as e:
                    print("[poll] heartbeat failed:", e)

            if time.time() - t0 > config.SELF_RESTART_AFTER:
                print("[poll] self-restart triggered")
                try:
                    await self.gh.dispatch("restart-bot")
                    print("[poll] restart dispatched")
                except Exception as e:
                    print("[poll] dispatch failed (cron will catch):", e)
                await asyncio.sleep(3)
                await self.shutdown(0)
                return


async def amain():
    if not config.BOT_TOKEN or ":" not in config.BOT_TOKEN:
        print("FATAL: BOT_TOKEN missing")
        await asyncio.sleep(300)
        sys.exit(1)
    if not config.GH_TOKEN:
        print("FATAL: GH_PAT missing")
        await asyncio.sleep(300)
        sys.exit(1)
    bot = Bot()
    try:
        await bot.boot()
    except Exception as e:
        print("FATAL: boot failed:", repr(e))
        await bot.shutdown(1)
        return
    try:
        await bot.poll_loop()
    except SystemExit:
        raise
    except Exception as e:
        print("FATAL: loop crashed:", repr(e))
        try:
            await bot.gh.dispatch("restart-bot")
        except Exception:
            pass
        await bot.shutdown(1)


if __name__ == "__main__":
    try:
        asyncio.run(amain())
    except SystemExit as e:
        sys.exit(int(e.code or 0))
