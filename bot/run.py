# -*- coding: utf-8 -*-
"""Exam runner: text-mode sessions with LIVE ticking timer + photo questions + result submissions."""
import asyncio
import json
import time

import config
import crypto
import scoring
import views
from tg import TGError, esc


class Runner:
    def __init__(self, tg, store):
        self.tg = tg
        self.store = store
        self.tasks: dict[int, asyncio.Task] = {}
        self.pending_join: set[int] = set()

    # ================================================================= join flow

    async def join_ask(self, chat_id, uid, edit_msg=None):
        self.pending_join.add(uid)
        txt = ("🎯 <b>شرکت در آزمون</b>\n\n"
               "کد آزمون را بفرست. (کد ۶ رقمی مثل <code>A7K2QP</code> که دبیر به تو داده)\n\n"
               "یا مستقیم روی لینکی که دبیر فرستاده کلیک کن.")
        kb = [[{"text": "🔙 منوی اصلی", "callback_data": "menu:main"}]]
        if edit_msg:
            await self.tg.edit(chat_id, edit_msg, txt, kb)
        else:
            await self.tg.send(chat_id, txt, kb)

    def eligibility(self, exam: dict, uid: int) -> str | None:
        """Return a rejection reason or None if OK."""
        t = int(time.time())
        if exam.get("status", "active") != "active":
            return "⛔ این آزمون پایان یافته است."
        w = exam.get("window") or {}
        if w.get("start") and t < w["start"]:
            return f"⏰ آزمون هنوز شروع نشده؛ {views.human_left(w['start'] - t)} مانده."
        if w.get("end") and t > w["end"]:
            return "⏰ مهلت این آزمون تمام شده است."
        att = exam.get("attempts", 1)
        if att and self.store.attempts_used(exam["code"], uid) >= att:
            return "🚫 شما قبلاً در این آزمون شرکت کرده‌اید (تعداد دفعات مجاز تکمیل شده)."
        return None

    async def view_exam(self, chat_id, uid, code, edit_msg=None):
        exam = self.store.exams.get(code)
        if not exam:
            txt = "❌ آزمونی با این کد پیدا نشد. کد را دوباره چک کن."
            if edit_msg:
                await self.tg.edit(chat_id, edit_msg, txt)
            else:
                await self.tg.send(chat_id, txt)
            return
        card, kb = views.exam_card(exam, uid, is_teacher=False,
                                   bot_username=self.store.gh_bot_username)
        if edit_msg:
            await self.tg.edit(chat_id, edit_msg, card, kb)
        else:
            await self.tg.send(chat_id, card, kb)

    async def start_text(self, chat_id, uid, code, name, username):
        exam = self.store.exams.get(code)
        if not exam:
            await self.tg.send(chat_id, "❌ آزمونی با این کد پیدا نشد.")
            return
        if uid in self.store.sessions:
            s = self.store.sessions[uid]
            if s.get("code") == code:
                await self.send_q(chat_id, uid, s["qi"])
                return
            await self.tg.send(chat_id,
                               "⚠️ شما یک آزمون فعال دارید. اول همان را تمام کنید (دکمه «🏁 پایان آزمون»).")
            return
        if not exam.get("modes", {}).get("text", True):
            await self.tg.send(chat_id, "✋ این آزمون فقط در حالت اپ/سایت برگزار می‌شود. از لینک دبیر استفاده کن.")
            return
        if not exam.get("questions"):
            await self.tg.send(chat_id, "⚠️ این آزمون هنوز سوالی ندارد.")
            return
        reason = self.eligibility(exam, uid)
        if reason:
            await self.tg.send(chat_id, reason)
            return
        t = int(time.time())
        s = {
            "code": code, "start": t, "deadline": t + exam.get("duration", 60) * 60,
            "qi": 0, "answers": {}, "await_open": False,
            "name": name or f"user{uid}", "username": username or "",
            "warned": [],
        }
        self.store.sessions[uid] = s
        self.store.touch_sessions()
        self.arm_timer(uid)
        nq = len(exam["questions"])
        await self.tg.send(
            chat_id,
            f"▶️ <b>آزمون شروع شد!</b>\n\n"
            f"📝 {esc(exam.get('title', ''))}\n"
            f"❓ {nq} سوال | ⏱ {views.mmss(exam.get('duration', 60) * 60)} زمان\n"
            f"➖ نمره منفی: {esc(config.NEGATIVE_LABELS.get(exam.get('negative'), str(exam.get('negative'))))}\n\n"
            f"⏰ <b>زمان تحویل: {views.fa_ts(s['deadline'])}</b> — اگر وقت تمام شود، آزمون به‌طور خودکار پایان می‌یابد.\n"
            f"⏳ تایمر زنده در پیام بعدی هر ۱۵ ثانیه به‌روز می‌شود.",
        )
        try:
            tm = await self.tg.send(chat_id, self._timer_text(s, exam))
            s["tmsg"] = tm.get("message_id")
            self.store.touch_sessions()
        except Exception as e:
            print("[runner] timer msg failed:", e)
        await self.send_q(chat_id, uid, 0)

    # ================================================================= rendering

    def _timer_text(self, s: dict, exam: dict) -> str:
        left = s["deadline"] - int(time.time())
        nq = len(exam.get("questions", []))
        answered = len([v for v in s.get("answers", {}).values() if v not in (None, "", -1)])
        icon = "🚨" if left <= 60 else ("⏰" if left <= 300 else "⏳")
        return (f"{icon} <b>زمان باقی‌مانده: {views.mmss(left)}</b>\n"
                f"📝 سوال: {min(s.get('qi', 0) + 1, nq) if nq else 0}/{nq} | پاسخ‌داده: {answered}")

    async def _edit_timer(self, uid):
        """Edit the sticky live-timer message with the current remaining time."""
        s = self.store.sessions.get(uid)
        if not s:
            return
        mid = s.get("tmsg")
        if not mid:
            return
        exam = self.store.exams.get(s["code"])
        if not exam:
            return
        try:
            await self.tg.edit(uid, mid, self._timer_text(s, exam))
        except TGError as e:
            if "not found" in str(e).lower() or "message to edit" in str(e).lower():
                s["tmsg"] = None
                self.store.touch_sessions()
        except Exception as e:
            print("[runner] timer edit failed:", e)

    def render_q(self, exam: dict, s: dict, i: int) -> tuple[str, list]:
        qs = exam["questions"]
        nq = len(qs)
        i = max(0, min(i, nq - 1))
        q = qs[i]
        left = s["deadline"] - int(time.time())
        answered = len([k for k, v in s["answers"].items() if v not in (None, "", -1)])
        has_img = bool(q.get("img"))
        text = (f"⏳ <b>زمان باقی‌مانده: {views.mmss(left)}</b>\n"
                f"📊 سوال {i + 1} از {nq} | پاسخ‌داده: {answered}\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"{esc(q['t'])}")
        code = exam["code"]
        kb = []
        has_text_opts = bool(q.get("o"))
        n_opts = int(q.get("n_opts") or 0)
        if has_text_opts or n_opts:
            letters = ["الف", "ب", "ج", "د", "ه", "و", "ز", "ح", "ط", "ی"]
            count = len(q["o"]) if has_text_opts else n_opts
            row = []
            for oi in range(count):
                label = letters[oi] if oi < len(letters) else str(oi + 1)
                row.append({"text": label, "callback_data": f"exa:ans:{code}:{i}:{oi}"})
                if len(row) == 2:
                    kb.append(row)
                    row = []
            if row:
                kb.append(row)
        else:
            text += "\n\n✍️ <b>پاسخ خود را تایپ و ارسال کن:</b>"
        kb.append([{"text": "⏱ زمان باقی‌مانده", "callback_data": f"exa:time:{code}"}])
        kb.append([{"text": "⏭ رد کردن", "callback_data": f"exa:skip:{code}:{i}"},
                   {"text": "⏮ سوال قبل", "callback_data": f"exa:prev:{code}:{i}"},
                   {"text": "🏁 پایان", "callback_data": "exa:fin"}])
        return text, kb

    async def send_q(self, chat_id, uid, i: int):
        s = self.store.sessions.get(uid)
        if not s:
            return
        exam = self.store.exams.get(s["code"])
        if not exam:
            return
        nq = len(exam["questions"])
        if i >= nq:
            await self.tg.send(chat_id,
                               "🎉 به آخر سوال رسیدی! اگر آماده‌ای، آزمون را تمام کن.",
                               [[{"text": "🏁 پایان آزمون و دیدن نتیجه", "callback_data": "exa:fin"}],
                                [{"text": "⏮ بازگشت به سوال آخر", "callback_data": f"exa:prev:{s['code']}:{nq - 1}"}]])
            return
        text, kb = self.render_q(exam, s, i)
        q = exam["questions"][max(0, min(i, nq - 1))]
        if q.get("img"):
            photo = self.store.file_id_for(exam["code"], q["img"]) or (config.PAGES_BASE + "/" + q["img"])
            try:
                if len(text) <= 950:
                    await self.tg.send_photo(chat_id, photo, caption=text, kb=kb)
                else:
                    await self.tg.send_photo(chat_id, photo)
                    await self.tg.send(chat_id, text, kb)
                return
            except Exception as e:
                print("[runner] sendPhoto failed:", e)
                await self.tg.send(chat_id,
                                   f"🖼 عکس سوال: {config.PAGES_BASE + '/' + q['img']}\n\n" + text, kb)
                return
        await self.tg.send(chat_id, text, kb)

    # ================================================================= session handlers

    async def cb_answer(self, chat_id, uid, cb_id, kind, code, qi, oi):
        s = self.store.sessions.get(uid)
        if kind in ("fin", "fin2", "quit", "quit2", "backq"):
            if not s:
                await self.tg.answer_cb(cb_id, "شما آزمون فعالی ندارید.")
                return
            code = s.get("code")
        elif not s or s.get("code") != code:
            await self.tg.answer_cb(cb_id, "این آزمون دیگر فعال نیست.")
            return
        exam = self.store.exams.get(code)
        if not exam:
            return
        nq = len(exam["questions"])
        if kind == "ans":
            if qi != s["qi"]:
                await self.tg.answer_cb(cb_id, "این سوال پاسخ داده شده است.")
                return
            if s.get("await_open"):
                await self.tg.answer_cb(cb_id, "این سوال تشریحی است؛ پاسخ را تایپ کن.")
                return
            s["answers"][str(qi)] = int(oi)
            self.store.touch_sessions()
            await self.tg.answer_cb(cb_id, "✅ ثبت شد")
            if s["qi"] + 1 >= nq:
                s["qi"] = nq  # at end
                await self.send_q(chat_id, uid, nq)
            else:
                s["qi"] += 1
                await self.send_q(chat_id, uid, s["qi"])
        elif kind == "skip":
            if qi == s["qi"]:
                s["await_open"] = False
                s["answers"][str(qi)] = -1
                self.store.touch_sessions()
                s["qi"] = min(nq, qi + 1)
                await self.tg.answer_cb(cb_id, "⏭ رد شد")
                await self.send_q(chat_id, uid, s["qi"])
            else:
                await self.tg.answer_cb(cb_id)
        elif kind == "prev":
            if nq:
                s["qi"] = max(0, min(int(qi), nq - 1))
                s["await_open"] = False
                await self.tg.answer_cb(cb_id)
                await self.send_q(chat_id, uid, s["qi"])
        elif kind == "fin":
            kb = [[{"text": "✅ بله، پایان و ثبت نتیجه", "callback_data": "exa:fin2"}],
                  [{"text": "لغو", "callback_data": "exa:backq"}]]
            await self.tg.answer_cb(cb_id)
            await self.tg.send(chat_id, "🏁 مطمنی آزمون را تمام می‌کنی؟ (نتیجه بر اساس پاسخ‌های ثبت‌شده محاسبه می‌شود)", kb)
        elif kind == "fin2":
            await self.tg.answer_cb(cb_id)
            await self.finish(uid, "با درخواست خودتان پایان یافت")
        elif kind == "backq":
            await self.tg.answer_cb(cb_id)
            await self.send_q(chat_id, uid, s["qi"])
        elif kind == "quit":
            kb = [[{"text": "✅ بله، انصراف", "callback_data": "exa:quit2"}],
                  [{"text": "لغو", "callback_data": "exa:backq"}]]
            await self.tg.answer_cb(cb_id)
            await self.tg.send(chat_id, "⚠️ انصراف از آزمون؟ نتیجه‌ای ثبت نمی‌شود.", kb)
        elif kind == "quit2":
            self.store.sessions.pop(uid, None)
            self.store.touch_sessions()
            t = self.tasks.pop(uid, None)
            if t:
                t.cancel()
            await self.tg.answer_cb(cb_id)
            await self.tg.send(chat_id, "🚪 از آزمون انصراف داده شد.")
        elif kind == "time":
            left = s["deadline"] - int(time.time())
            await self.tg.answer_cb(cb_id, f"⏳ {views.mmss(left)} از آزمون باقی مانده", alert=True)
        elif kind == "start":
            await self.tg.answer_cb(cb_id)
            await self.start_text(chat_id, uid, code, "", "")
        elif kind == "view":
            await self.tg.answer_cb(cb_id)
            await self.view_exam(chat_id, uid, code)

    async def session_text(self, chat_id, uid, text):
        """Text received while a session is active."""
        s = self.store.sessions.get(uid)
        exam = self.store.exams.get(s["code"])
        if not exam:
            self.store.sessions.pop(uid, None)
            return
        qs = exam["questions"]
        if s.get("await_open"):
            qi = min(s["qi"], len(qs) - 1)
            s["answers"][str(qi)] = text.strip()[:300]
            s["await_open"] = False
            self.store.touch_sessions()
            nq = len(qs)
            if s["qi"] + 1 >= nq:
                s["qi"] = nq
                await self.send_q(chat_id, uid, nq)
            else:
                s["qi"] += 1
                await self.send_q(chat_id, uid, s["qi"])
        else:
            await self.tg.send(chat_id,
                               "✋ شما در وسط آزمون هستید. برای پاسخ از دکمه‌ها استفاده کن؛ برای پایان «🏁 پایان» را بزن.",
                               [[{"text": "🏁 پایان آزمون", "callback_data": "exa:fin"}],
                                [{"text": "نمایش سوال فعلی", "callback_data": f"exa:prev:{s['code']}:{s['qi']}"}]])

    # ================================================================= timer & finish

    def arm_timer(self, uid):
        old = self.tasks.pop(uid, None)
        if old:
            old.cancel()
        self.tasks[uid] = asyncio.create_task(self._timer(uid))

    async def _timer(self, uid):
        """Live exam clock: edits the sticky timer message every TIMER_EDIT_EVERY
        seconds, fires 5min/1min warnings, auto-finishes at the deadline."""
        try:
            s = self.store.sessions.get(uid)
            if not s:
                return
            deadline = s["deadline"]
            warns = ((300, "⏰ <b>۵ دقیقه</b> به پایان آزمون مانده!"),
                     (60, "🚨 <b>۱ دقیقه</b> مانده!"))
            wi = 0
            last_edit = 0.0
            while True:
                t = time.time()
                left = deadline - t
                while wi < len(warns) and left <= warns[wi][0]:
                    warn_at, msg = warns[wi]
                    cur = self.store.sessions.get(uid)
                    if cur and cur.get("deadline") == deadline and warn_at not in cur.get("warned", []):
                        cur.setdefault("warned", []).append(warn_at)
                        self.store.touch_sessions()
                        try:
                            await self.tg.send(uid, msg)
                        except Exception:
                            pass
                    wi += 1
                if left <= 0:
                    break
                if t - last_edit >= config.TIMER_EDIT_EVERY:
                    last_edit = t
                    await self._edit_timer(uid)
                await asyncio.sleep(1)
            cur = self.store.sessions.get(uid)
            if cur and cur["deadline"] == deadline:
                await self._edit_timer(uid)
                await self.finish(uid, "زمان آزمون تمام شد", auto=True)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print("[runner] timer error:", e)

    async def finish(self, uid, reason: str, auto: bool = False):
        s = self.store.sessions.pop(uid, None)
        self.store.touch_sessions()
        t = self.tasks.pop(uid, None)
        if t:
            t.cancel()
        if not s:
            return
        # close the live-timer message
        if s.get("tmsg"):
            try:
                await self.tg.edit(uid, s["tmsg"],
                                   f"🏁 <b>آزمون پایان یافت</b> — {esc(reason)}\n"
                                   f"⏱ مدت استفاده‌شده: {views.mmss(int(time.time() - s['start']))}")
            except Exception:
                pass
        exam = self.store.exams.get(s["code"])
        if not exam:
            return
        # treat unanswered current question as blank implicitly (missing keys = blank)
        res0 = scoring.score_exam(exam["questions"], s["answers"], exam.get("negative", "none"))
        result = {
            "uid": uid, "name": s.get("name", ""), "username": s.get("username", ""),
            "score": res0["score"], "total": res0["total"], "c": res0["c"],
            "w": res0["w"], "b": res0["b"], "pct": res0["pct"],
            "ts": int(time.time()), "via": "text",
            "dur": int(time.time() - s["start"]),
        }
        self.store.add_result(s["code"], result)
        await self.store.save_vault(s["code"])
        neg_txt = config.NEGATIVE_LABELS.get(exam.get("negative"), str(exam.get("negative")))
        rank, total_n = self.rank_of(s["code"], uid, result["ts"])
        await self.tg.send(
            uid,
            f"🛑 <b>{esc(reason)}</b>\n\n"
            f"✅ <b>نتیجه‌ی شما ثبت شد!</b>\n"
            f"📝 آزمون: {esc(exam.get('title', ''))}\n"
            f"✅ صحیح: {result['c']} | ❌ غلط: {result['w']} | ⬜ نزده: {result['b']}\n"
            f"🎯 نمره: <b>{result['score']}</b> از {result['total']}  (٪{result['pct']})\n"
            f"➖ نمره منفی: {esc(neg_txt)}\n"
            f"🏅 رتبه فعلی: {rank} از {total_n}",
        )
        await self.notify_teacher(exam, result)

    def rank_of(self, code, uid, ts):
        rows = sorted(self.store.results(code), key=lambda r: (-r.get("score", 0), r.get("ts", 0)))
        rank = 1
        for i, r in enumerate(rows, 1):
            if r.get("uid") == uid and r.get("ts") == ts:
                rank = i
                break
        return rank, len(rows)

    async def notify_teacher(self, exam, result):
        if not exam.get("notify", True):
            return
        tid = exam.get("teacher_id")
        if not tid or tid == result.get("uid"):
            return
        try:
            await self.tg.send(
                tid,
                f"📊 <b>نتیجه جدید</b> — {esc(exam.get('title', ''))} (<code>{exam['code']}</code>)\n"
                f"👤 {esc(result.get('name', ''))}"
                + (f" @{result['username']}" if result.get("username") else "")
                + f"\n🎯 نمره: {result['score']} از {result['total']} (٪{result['pct']})\n"
                f"✅{result['c']} ❌{result['w']} ⬜{result['b']} | 🖥 {result.get('via', '')}",
            )
        except Exception as e:
            print("[runner] teacher notify failed:", e)

    # ================================================================= submissions (web / mini app)

    async def handle_result_payload(self, chat_id, uid, profile_name, username, payload: dict, via: str):
        code = str(payload.get("e", ""))
        exam = self.store.exams.get(code)
        if not exam:
            await self.tg.send(chat_id, "❌ کد آزمون در این نتیجه معتبر نیست.")
            return
        name = str(payload.get("n", "") or profile_name or f"user{uid}")[:80]
        answers = payload.get("a", {})
        if not isinstance(answers, dict):
            await self.tg.send(chat_id, "❌ ساختار نتیجه نامعتبر است.")
            return
        finished_at = int(payload.get("f", time.time()))
        started_at = int(payload.get("t", finished_at))
        dur = max(0, finished_at - started_at)

        # window enforcement (grace of 90s for network lag)
        w = exam.get("window") or {}
        if w.get("end") and finished_at > w["end"] + 90:
            await self.tg.send(chat_id, "⛔ زمان این آزمون تمام شده و نتیجه پذیرفته نمی‌شود.")
            return
        if w.get("start") and started_at and started_at < w["start"] - 300:
            await self.tg.send(chat_id, "⛔ زمان شروع نامعتبر است.")
            return
        if exam.get("status", "active") != "active" and not (w.get("end") and finished_at <= w["end"] + 90):
            await self.tg.send(chat_id, "⛔ این آزمون پایان یافته است.")
            return

        sig = crypto.short_sig(json.dumps({"e": code, "u": uid, "a": answers}, sort_keys=True, ensure_ascii=False))
        att = exam.get("attempts", 1)
        used = self.store.attempts_used(code, uid)
        if att and used >= att:
            # idempotent resend of the same attempt?
            for r in self.store.results(code):
                if r.get("uid") == uid and r.get("sig") == sig:
                    await self.send_result_card(chat_id, exam, r, resend=True)
                    return
            await self.tg.send(chat_id,
                               "🚫 قبلاً در این آزمون شرکت کرده‌ای و دفعات مجاز تکمیل شده است.")
            return

        res0 = scoring.score_exam(exam["questions"], answers, exam.get("negative", "none"))
        result = {
            "uid": uid, "name": name, "username": username or "",
            "score": res0["score"], "total": res0["total"], "c": res0["c"],
            "w": res0["w"], "b": res0["b"], "pct": res0["pct"],
            "ts": int(time.time()), "via": via, "dur": dur, "sig": sig,
        }
        self.store.add_result(code, result)
        await self.store.save_vault(code)
        await self.send_result_card(chat_id, exam, result)
        await self.notify_teacher(exam, result)

    async def send_result_card(self, chat_id, exam, result, resend: bool = False):
        neg_txt = config.NEGATIVE_LABELS.get(exam.get("negative"), str(exam.get("negative")))
        rank, total_n = self.rank_of(exam["code"], result.get("uid"), result.get("ts"))
        head = "🔁 نتیجه‌ی شما قبلاً ثبت شده است:" if resend else "✅ <b>نتیجه‌ی شما ثبت شد!</b>"
        await self.tg.send(
            chat_id,
            f"{head}\n"
            f"📝 آزمون: {esc(exam.get('title', ''))}\n"
            f"👤 نام: {esc(result.get('name', ''))}\n"
            f"✅ صحیح: {result['c']} | ❌ غلط: {result['w']} | ⬜ نزده: {result['b']}\n"
            f"🎯 نمره: <b>{result['score']}</b> از {result['total']}  (٪{result['pct']})\n"
            f"➖ نمره منفی: {esc(neg_txt)}\n"
            f"⏱ مدت: {views.mmss(result.get('dur', 0))}\n"
            f"🏅 رتبه فعلی: {rank} از {total_n}",
        )

    # ================================================================= public list

    async def public_list(self, chat_id, uid, edit_msg=None):
        pubs = [e for e in self.store.exams.values()
                if e.get("access") == "public" and e.get("status", "active") == "active"]
        if not pubs:
            txt = "🌐 فعلاً آزمون عمومی فعالی وجود ندارد."
            kb = [[{"text": "🔙 منوی اصلی", "callback_data": "menu:main"}]]
        else:
            pubs.sort(key=lambda e: -e.get("created", 0))
            txt = "🌐 <b>آزمون‌های عمومی فعال:</b>"
            kb = []
            for e in pubs[:20]:
                kb.append([{"text": f"📝 {e.get('title', 'بی‌نام')[:40]} — {e.get('teacher_name', '')}",
                            "callback_data": f"exa:view:{e['code']}"}])
            kb.append([{"text": "🔙 منوی اصلی", "callback_data": "menu:main"}])
        if edit_msg:
            await self.tg.edit(chat_id, edit_msg, txt, kb)
        else:
            await self.tg.send(chat_id, txt, kb)
