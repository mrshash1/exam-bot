# -*- coding: utf-8 -*-
"""Teacher wizard: create exam (title -> info card -> questions -> publish) + settings.

Supports photo questions: teacher sends a photo, then picks the number of
on-image options and the correct option with two taps (or sends the options
as text / puts the whole question in the photo caption).
"""
import re
import time
import uuid

import config
import parser as qparser
import views
from tg import esc
from views import fa_ts, human_left

LETTERS = ["الف", "ب", "ج", "د", "ه", "و", "ز", "ح", "ط", "ی"]


class Wizard:
    def __init__(self, tg, store):
        self.tg = tg
        self.store = store

    # ------------------------------------------------------------------ helpers

    def draft_card(self, wiz: dict) -> tuple[str, list]:
        d = wiz["draft"]
        code = d["code"]
        nq = len(d.get("questions", []))
        neg_txt = config.NEGATIVE_LABELS.get(d.get("negative"), "نمره منفی")
        bu = self.store.gh_bot_username
        text = (
            f"📝 <b>آزمون: {esc(d.get('title', 'بدون نام'))}</b>\n"
            f"🆔 کد آزمون: <code>{code}</code>\n\n"
            f"❓ سوالات ثبت‌شده: <b>{nq}</b>\n"
            f"⏱ مدت: {d.get('duration', 60)} دقیقه\n"
            f"➖ نمره منفی: {esc(neg_txt)}\n"
            f"🎟 شرکت هر نفر: {'نامحدود' if d.get('attempts', 1) == 0 else d.get('attempts', 1)} بار\n"
            f"🔒 دسترسی: {'عمومی (نمایش در لیست عمومی)' if d.get('access') == 'public' else 'فقط با لینک/کد'}\n"
            f"🖥 حالت‌ها: {' | '.join(v for m, v in views.MODE_LABELS.items() if d.get('modes', {}).get(m))}\n\n"
            f"🔗 لینک‌ها (از همین حالا معتبر می‌شوند پس از انتشار):\n"
            f"<code>https://t.me/{bu}?start=exam_{code}</code>\n"
            f"<code>{config.WEB_EXAM_URL.format(code=code)}</code>\n\n"
            f"📨 حالا سوالات را بفرست: <b>متن</b>، فایل <code>txt</code> یا <b>📸 عکس سوال</b>. "
            f"هر بار که بفرستی اضافه می‌شود.\n"
            f"📸 در حالت عکس: اگر گزینه‌ها داخل عکس هستند فقط تعدادشان و گزینه‌ی صحیح را با دکمه انتخاب کن."
        )
        return text, self.draft_kb(code)

    def draft_kb(self, code="") -> list:
        return [
            [{"text": "✅ انتشار آزمون", "callback_data": f"wiz:done:{code}"}],
            [{"text": "📋 نمونه قالب سوال", "callback_data": "wiz:sample"}],
            [{"text": "❌ انصراف و حذف پیش‌نویس", "callback_data": "wiz:cancel"}],
        ]

    # ------------------------------------------------------------------ entry points

    async def start_new(self, chat_id, uid, name, username):
        code = self.store.new_code()
        draft = {
            "code": code, "title": "", "teacher_id": uid,
            "teacher_name": name or f"user{uid}", "created": int(time.time()),
            "duration": config.DEFAULT_DURATION, "negative": config.DEFAULT_NEGATIVE,
            "attempts": config.DEFAULT_ATTEMPTS, "notify": True,
            "access": config.DEFAULT_ACCESS, "modes": dict(config.DEFAULT_MODES),
            "window": {"start": None, "end": None}, "questions": [], "status": "active",
        }
        self.store.wizards[uid] = {"st": "title", "draft": draft}
        self.store.touch_wizards()
        await self.tg.send(chat_id, (
            "🛠 <b>ساخت آزمون جدید</b>\n\n"
            "1/2 — <b>نام آزمون</b> را بفرست. (مثلاً: <code>آزمون فیزیک فصل ۲</code>)\n\n"
            "بعد از وارد کردن نام، کارت کامل اطلاعات آزمون را می‌بینی."
        ))

    async def got_title(self, chat_id, uid, text, name, username):
        wiz = self.store.wizards.get(uid)
        if not wiz or wiz["st"] != "title":
            return
        title = text.strip()[:120]
        wiz["draft"]["title"] = title
        wiz["st"] = "questions"
        self.store.touch_wizards()
        text_card, kb = self.draft_card(wiz)
        await self.tg.send(chat_id,
                           f"✅ نام ثبت شد!\n\n{text_card}\n\n📩 سوالات را به صورت پیام متنی یا فایل <code>.txt</code> بفرست.",
                           kb)

    async def add_parsed(self, chat_id, uid, raw: str, mode: str = "draft"):
        """mode: draft | append_pub | replace_pub"""
        wiz = self.store.wizards.get(uid, {})
        res = qparser.parse_questions(raw)
        if res["errors"]:
            await self.tg.send(chat_id, "❌ " + res["errors"][0] + "\n\n" + qparser.SAMPLE)
            return
        qs = res["questions"]
        if mode == "draft":
            draft = wiz.get("draft", {})
            code = draft.get("code")
            if code is None:
                return
            existing = draft.setdefault("questions", [])
            room = config.MAX_QUESTIONS - len(existing)
            added, qs = qs[:room], qs[:room]
            existing.extend(qs)
            self.store.touch_wizards()
            msgs = []
            if added:
                msgs.append(f"✅ <b>{len(added)}</b> سوال اضافه شد. (مجموع: {len(existing)})")
            for wmsg in res["warnings"]:
                msgs.append(wmsg)
            if not added:
                msgs.append("هیچ سوال جدیدی اضافه نشد.")
            card, kb = self.draft_card(wiz)
            await self.tg.send(chat_id, "\n".join(msgs) + "\n\n" + card, kb)
        else:
            code = wiz.get("code")
            exam = self.store.exams.get(code)
            if not exam:
                await self.tg.send(chat_id, "❌ آزمون پیدا نشد.")
                return
            if mode == "replace_pub":
                exam["questions"] = []
                self.store.vault[code] = {"teacher_id": exam.get("teacher_id"),
                                          "answers": [], "results": self.store.results(code)}
            room = config.MAX_QUESTIONS - len(exam["questions"])
            added, qs = qs[:room], qs[:room]
            exam["questions"].extend(qs)
            v = self.store.vault.setdefault(code, {"teacher_id": exam.get("teacher_id"),
                                                   "answers": [], "results": []})
            v.setdefault("answers", []).extend([q["a"] for q in qs])
            v["teacher_id"] = exam.get("teacher_id")
            await self.store.save_exam(code)
            await self.store.save_vault(code)
            msgs = [f"✅ <b>{len(added)}</b> سوال به آزمون اضافه شد. (مجموع: {len(exam['questions'])})"]
            msgs += res["warnings"]
            await self.tg.send(chat_id, "\n".join(msgs))
            self.store.wizards.pop(uid, None)
            self.store.touch_wizards()
            await self.show_exam_card(chat_id, exam.get("teacher_id") or uid, code)

    # ------------------------------------------------------------------ photo questions

    def _photo_ctx(self, wiz: dict):
        """Return (ctx, code) for the current wizard state, or (None, None)."""
        st = wiz.get("st")
        if st == "questions":
            return "draft", (wiz.get("draft") or {}).get("code")
        if st in ("append_pub", "replace_pub"):
            return st, wiz.get("code")
        if st in ("pq_n", "pq_a"):
            ctx = wiz.get("ctx", "draft")
            code = (wiz.get("draft") or {}).get("code") if ctx == "draft" else wiz.get("code")
            return ctx, code
        return None, None

    async def handle_photo(self, chat_id, uid, file_id, caption, file_size=0) -> bool:
        """Teacher sent a photo while adding questions. Returns True if handled."""
        wiz = self.store.wizards.get(uid)
        if not wiz:
            return False
        ctx, code = self._photo_ctx(wiz)
        if not ctx or not code:
            return False
        if file_size and file_size > config.MAX_PHOTO_BYTES:
            await self.tg.send(chat_id,
                               f"❌ حجم عکس زیاد است (حداکثر {config.MAX_PHOTO_BYTES // (1024 * 1024)} مگابایت). "
                               "عکس کوچک‌تر بفرست.")
            return True
        try:
            data = await self.tg.download_file(file_id)
        except Exception as e:
            print("[wizard] photo download failed:", e)
            await self.tg.send(chat_id, "❌ دریافت عکس ناموفق بود. لطفاً دوباره بفرست.")
            return True
        ext = "png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "jpg"
        path = f"{config.PHOTOS_DIR}/{code}/{int(time.time()):x}-{uuid.uuid4().hex[:8]}.{ext}"
        try:
            await self.store.gh.put_file(path, data, message=f"photo {code}")
        except Exception as e:
            print("[wizard] photo upload failed:", e)
            await self.tg.send(chat_id, "❌ ذخیره‌ی عکس ناموفق بود. لطفاً دوباره تلاش کن.")
            return True

        cap = (caption or "").strip()
        # quick path: the caption itself is a complete question (options + answer)
        if cap:
            res = qparser.parse_questions(cap)
            if not res["errors"] and len(res["questions"]) == 1 and res["questions"][0]["o"]:
                q = res["questions"][0]
                q["img"] = path
                q["file_id"] = file_id
                await self._commit_photo_question(chat_id, uid, ctx, code, q, auto=True)
                return True

        wiz.update({"st": "pq_n", "ctx": ctx, "code": code, "img": path,
                    "file_id": file_id, "caption": cap})
        wiz.pop("n_opts", None)
        self.store.touch_wizards()
        kb = [
            [{"text": "۲ گزینه", "callback_data": f"wiz:pqn:{code}:2"},
             {"text": "۳ گزینه", "callback_data": f"wiz:pqn:{code}:3"},
             {"text": "۴ گزینه", "callback_data": f"wiz:pqn:{code}:4"}],
            [{"text": "۵ گزینه", "callback_data": f"wiz:pqn:{code}:5"},
             {"text": "۶ گزینه", "callback_data": f"wiz:pqn:{code}:6"},
             {"text": "✍️ گزینه‌ها متنی", "callback_data": f"wiz:pqhint:{code}"}],
            [{"text": "❌ کنسل این عکس", "callback_data": f"wiz:pqcancel:{code}"}],
        ]
        await self.tg.send(
            chat_id,
            "📸 <b>عکس سوال دریافت و ذخیره شد!</b>\n\n"
            "حالا بگو <b>تعداد گزینه‌های داخل عکس</b> چند تا است؟ (بیشتر از ۶؟ عددش را تایپ کن)\n\n"
            "💡 اگر می‌خواهی گزینه‌ها زیر عکس به‌صورت <b>متن</b> نوشته شوند، دکمه‌ی «گزینه‌ها متنی» را بزن و "
            "گزینه‌ها را مثل نمونه‌ی قالب بفرست.",
            kb,
        )
        return True

    def _answer_kb(self, code: str, n_opts: int) -> list:
        kb = []
        row = []
        for i in range(max(2, min(10, n_opts))):
            row.append({"text": LETTERS[i], "callback_data": f"wiz:pqa:{code}:{i}"})
            if len(row) == 4:
                kb.append(row)
                row = []
        if row:
            kb.append(row)
        kb.append([{"text": "❌ کنسل این عکس", "callback_data": f"wiz:pqcancel:{code}"}])
        return kb

    async def photo_pick_n(self, chat_id, msg_id, uid, code, count_s):
        wiz = self.store.wizards.get(uid)
        if not wiz or wiz.get("st") != "pq_n" or wiz.get("code") != code:
            await self.tg.send(chat_id, "❌ این پیام قدیمی است.")
            return
        try:
            n = max(2, min(10, int(count_s)))
        except ValueError:
            return
        wiz["st"] = "pq_a"
        wiz["n_opts"] = n
        self.store.touch_wizards()
        await self.tg.edit(chat_id, msg_id,
                           f"✅ تعداد گزینه‌ها: <b>{n}</b>\n\nحالا <b>گزینه‌ی صحیح</b> را انتخاب کن:",
                           self._answer_kb(code, n))

    async def photo_pick_a(self, chat_id, msg_id, uid, code, idx_s):
        wiz = self.store.wizards.get(uid)
        if not wiz or wiz.get("st") != "pq_a" or wiz.get("code") != code:
            await self.tg.send(chat_id, "❌ این پیام قدیمی است.")
            return
        try:
            idx = int(idx_s)
        except ValueError:
            return
        n = int(wiz.get("n_opts") or 4)
        if not (0 <= idx < n):
            return
        q = {
            "t": wiz.get("caption") or "📸 با دقت به عکس سوال نگاه کن و گزینه‌ی درست را انتخاب کن.",
            "o": [], "a": idx, "p": 1,
            "img": wiz["img"], "file_id": wiz.get("file_id", ""), "n_opts": n,
        }
        try:
            await self.tg.edit(chat_id, msg_id, "⏳ در حال ثبت سوال…")
        except Exception:
            pass
        await self._commit_photo_question(chat_id, uid, wiz.get("ctx", "draft"), code, q)

    async def photo_cancel(self, chat_id, msg_id, uid, code):
        wiz = self.store.wizards.get(uid)
        if not wiz or wiz.get("code") != code:
            await self.tg.send(chat_id, "❌ این پیام قدیمی است.")
            return
        self._reset_wiz(wiz)
        self.store.touch_wizards()
        await self.tg.edit(chat_id, msg_id,
                           "❌ عکس کنار گذاشته شد. عکس بعدی یا متن سوال بعدی را بفرست.")

    def _reset_wiz(self, wiz: dict):
        ctx = wiz.get("ctx", "draft")
        wiz["st"] = "questions" if ctx == "draft" else ctx
        for k in ("img", "file_id", "caption", "n_opts"):
            wiz.pop(k, None)

    async def photo_text(self, chat_id, uid, text):
        """Text reply while in a photo-question state."""
        wiz = self.store.wizards.get(uid)
        if not wiz:
            return
        st = wiz.get("st")
        ctx, code = wiz.get("ctx", "draft"), wiz.get("code")

        def parsed_q(t: str):
            """Parse as full question; retry with a synthetic stem (photo = question)."""
            res = qparser.parse_questions(t)
            if not res["errors"] and len(res["questions"]) == 1 and res["questions"][0]["o"]:
                return res["questions"][0], False
            res2 = qparser.parse_questions("1. \u0633\u0648\u0627\u0644\n" + t)
            if not res2["errors"] and len(res2["questions"]) == 1 and res2["questions"][0]["o"]:
                return res2["questions"][0], True   # synthetic stem -> prefer caption
            return None, False

        if st == "pq_n":
            t = text.strip().translate(str.maketrans("\u06f0\u06f1\u06f2\u06f3\u06f4\u06f5\u06f6\u06f7\u06f8\u06f9", "0123456789"))
            q, synthetic = parsed_q(text)
            if q is not None:
                if synthetic:
                    q["t"] = wiz.get("caption") or "\U0001F4F8 \u0628\u0627 \u062f\u0642\u062a \u0628\u0647 \u0639\u06a9\u0633 \u0633\u0648\u0627\u0644 \u0646\u06af\u0627\u0647 \u06a9\u0646 \u0648 \u06af\u0632\u06cc\u0646\u0647\u0654 \u062f\u0631\u0633\u062a \u0631\u0627 \u0627\u0646\u062a\u062e\u0627\u0628 \u06a9\u0646."
                q["img"] = wiz["img"]
                q["file_id"] = wiz.get("file_id", "")
                await self._commit_photo_question(chat_id, uid, ctx, code, q)
                return
            if t.isdigit() and 2 <= int(t) <= 10:
                wiz["st"] = "pq_a"
                wiz["n_opts"] = int(t)
                self.store.touch_wizards()
                await self.tg.send(chat_id,
                                   f"\u2705 \u062a\u0639\u062f\u0627\u062f \u06af\u0632\u06cc\u0646\u0647\u200c\u0647\u0627: <b>{int(t)}</b>\n\n\u062d\u0627\u0644\u0627 <b>\u06af\u0632\u06cc\u0646\u0647\u0654 \u0635\u062d\u06cc\u062d</b> \u0631\u0627 \u0627\u0646\u062a\u062e\u0627\u0628 \u06a9\u0646:",
                                   self._answer_kb(code, int(t)))
                return
            await self.tg.send(chat_id,
                               "\u274c \u0645\u062a\u0648\u062c\u0647 \u0646\u0634\u062f\u0645. \u062a\u0639\u062f\u0627\u062f \u06af\u0632\u06cc\u0646\u0647\u200c\u0647\u0627 \u0631\u0627 \u0628\u0627 \u062f\u06a9\u0645\u0647 \u0627\u0646\u062a\u062e\u0627\u0628 \u06a9\u0646\u060c \u06cc\u0627 \u06af\u0632\u06cc\u0646\u0647\u200c\u0647\u0627 \u0631\u0627 \u0628\u0647 \u0635\u0648\u0631\u062a \u0645\u062a\u0646 \u0628\u0641\u0631\u0633\u062a:\n\n"
                               + qparser.SAMPLE)
            return
        if st == "pq_a":
            q, synthetic = parsed_q(text)
            if q is not None:
                if synthetic:
                    q["t"] = wiz.get("caption") or "\U0001F4F8 \u0628\u0627 \u062f\u0642\u062a \u0628\u0647 \u0639\u06a9\u0633 \u0633\u0648\u0627\u0644 \u0646\u06af\u0627\u0647 \u06a9\u0646 \u0648 \u06af\u0632\u06cc\u0646\u0647\u0654 \u062f\u0631\u0633\u062a \u0631\u0627 \u0627\u0646\u062a\u062e\u0627\u0628 \u06a9\u0646."
                q["img"] = wiz["img"]
                q["file_id"] = wiz.get("file_id", "")
                await self._commit_photo_question(chat_id, uid, ctx, code, q)
                return
            al = qparser.normalize(text).lower()
            al = re.sub(r"^\u06af\u0632\u06cc\u0646\u0647\s*", "", al).strip()
            idx = qparser.LETTER_OPT.get(al)
            if idx is None and al.isdigit():
                j = int(al)
                if 1 <= j <= int(wiz.get("n_opts") or 4):
                    idx = j - 1
            if idx is None or idx >= int(wiz.get("n_opts") or 4):
                await self.tg.send(chat_id,
                                   "\u274c \u06af\u0632\u06cc\u0646\u0647\u0654 \u0635\u062d\u06cc\u062d \u0631\u0627 \u0628\u0627 \u062f\u06a9\u0645\u0647 \u0627\u0646\u062a\u062e\u0627\u0628 \u06a9\u0646\u060c \u06cc\u0627 \u0645\u062b\u0644\u0627\u064b \u0628\u0646\u0648\u06cc\u0633 \u00ab\u0628\u00bb \u06cc\u0627 \u00ab2\u00bb.")
                return
            q = {
                "t": wiz.get("caption") or "\U0001F4F8 \u0628\u0627 \u062f\u0642\u062a \u0628\u0647 \u0639\u06a9\u0633 \u0633\u0648\u0627\u0644 \u0646\u06af\u0627\u0647 \u06a9\u0646 \u0648 \u06af\u0632\u06cc\u0646\u0647\u0654 \u062f\u0631\u0633\u062a \u0631\u0627 \u0627\u0646\u062a\u062e\u0627\u0628 \u06a9\u0646.",
                "o": [], "a": idx, "p": 1,
                "img": wiz["img"], "file_id": wiz.get("file_id", ""),
                "n_opts": int(wiz.get("n_opts") or 4),
            }
            await self._commit_photo_question(chat_id, uid, ctx, code, q)

    async def _commit_photo_question(self, chat_id, uid, ctx, code, q, auto=False):
        if ctx == "draft":
            wiz = self.store.wizards.get(uid)
            draft = (wiz or {}).get("draft") or {}
            if draft.get("code") != code:
                await self.tg.send(chat_id, "❌ پیش‌نویس پیدا نشد.")
                return
            if len(draft.get("questions", [])) >= config.MAX_QUESTIONS:
                await self.tg.send(chat_id, f"⚠️ حداکثر {config.MAX_QUESTIONS} سوال مجاز است.")
                return
            draft.setdefault("questions", []).append(q)
            self._reset_wiz(wiz)
            self.store.touch_wizards()
            n = len(draft["questions"])
            await self.tg.send(
                chat_id,
                f"✅ <b>سوال عکس‌دار ثبت شد!</b> (مجموع: {n})\n\n"
                "📩 عکس یا متن سوال بعدی را بفرست، یا «✅ انتشار آزمون» را بزن.",
                self.draft_kb(code),
            )
        else:
            exam = self.store.exams.get(code)
            if not exam or exam.get("teacher_id") != uid:
                await self.tg.send(chat_id, "❌ آزمون پیدا نشد یا دسترسی نداری.")
                return
            if len(exam.get("questions", [])) >= config.MAX_QUESTIONS:
                await self.tg.send(chat_id, f"⚠️ حداکثر {config.MAX_QUESTIONS} سوال مجاز است.")
                return
            exam.setdefault("questions", []).append(q)
            v = self.store.vault.setdefault(
                code, {"teacher_id": exam.get("teacher_id"), "answers": [], "results": []})
            v.setdefault("answers", []).append(q["a"])
            if q.get("img"):
                v.setdefault("file_ids", {})[q["img"]] = q.get("file_id", "")
            await self.store.save_exam(code)
            await self.store.save_vault(code)
            wiz = self.store.wizards.get(uid)
            if wiz:
                self._reset_wiz(wiz)
                self.store.touch_wizards()
            n = len(exam["questions"])
            await self.tg.send(
                chat_id,
                f"✅ <b>سوال عکس‌دار به آزمون اضافه شد!</b> (مجموع: {n})\n\n"
                "📩 عکس یا متن سوال بعدی را بفرست.",
            )

    async def publish(self, chat_id, uid, code):
        wiz = self.store.wizards.get(uid)
        if not wiz or wiz.get("draft", {}).get("code") != code:
            await self.tg.send(chat_id, "❌ پیش‌نویس پیدا نشد.")
            return
        draft = wiz["draft"]
        if not draft.get("questions"):
            await self.tg.send(chat_id, "⚠️ هنوز هیچ سوالی ثبت نکرده‌ای! اول سوالات را بفرست.")
            return
        exam = dict(draft)
        exam["status"] = "active"
        self.store.exams[code] = exam
        file_ids = {q["img"]: q.get("file_id", "") for q in exam["questions"] if q.get("img")}
        self.store.vault[code] = {
            "teacher_id": uid,
            "answers": [q["a"] for q in exam["questions"]],
            "results": [],
            "file_ids": file_ids,
        }
        await self.store.save_exam(code)
        await self.store.save_vault(code)
        self.store.wizards.pop(uid, None)
        self.store.touch_wizards()
        card, kb = views.exam_card(exam, uid, is_teacher=True,
                                   bot_username=self.store.gh_bot_username)
        await self.tg.send(chat_id,
                           "🎉 <b>آزمون منتشر شد!</b> لینک‌ها و کد از همین لحظه فعال‌اند.\n\n" + card,
                           kb)

    async def show_exam_card(self, chat_id, uid, code, edit_msg=None):
        exam = self.store.exams.get(code)
        if not exam:
            await self.tg.send(chat_id, "❌ آزمون پیدا نشد (شاید حذف شده باشد).")
            return
        is_teacher = exam.get("teacher_id") == uid
        card, kb = views.exam_card(exam, uid, is_teacher, self.store.gh_bot_username)
        if edit_msg:
            await self.tg.edit(chat_id, edit_msg, card, kb)
        else:
            await self.tg.send(chat_id, card, kb)

    # ------------------------------------------------------------------ settings

    def settings_kb(self, exam: dict) -> list:
        code = exam["code"]
        neg_txt = config.NEGATIVE_LABELS.get(exam.get("negative"), str(exam.get("negative")))
        att = exam.get("attempts", 1)
        m = exam.get("modes", {})
        return [
            [{"text": f"⏱ مدت: {exam.get('duration', 60)} دقیقه — تغییر", "callback_data": f"exs:dur:{code}"}],
            [{"text": f"➖ نمره منفی: {neg_txt} — تغییر", "callback_data": f"exs:neg:{code}"}],
            [{"text": f"🎟 شرکت هر نفر: {'نامحدود' if att == 0 else att} — تغییر", "callback_data": f"exs:att:{code}"}],
            [{"text": f"🔒 دسترسی: {'عمومی' if exam.get('access') == 'public' else 'فقط با لینک/کد'} — تغییر",
              "callback_data": f"exs:acc:{code}"}],
            [{"text": f"🖥 حالت متنی: {'✅' if m.get('text') else '❌'}  اپ: {'✅' if m.get('app') else '❌'}  سایت: {'✅' if m.get('web') else '❌'} — تغییر",
              "callback_data": f"exs:modes:{code}"}],
            [{"text": "🔔 اطلاع‌رسانی نتایج: " + ("روشن" if exam.get("notify", True) else "خاموش") + " — تغییر",
              "callback_data": f"exs:notify:{code}"}],
            [{"text": "➕ افزودن سوال", "callback_data": f"exs:addq:{code}"},
             {"text": "🔄 جایگزینی همه سوالات", "callback_data": f"exs:replq:{code}"}],
            [{"text": "▶️ شروع زمان‌بندی از الان", "callback_data": f"exs:startnow:{code}"},
             {"text": "⛔ پایان آزمون", "callback_data": f"exs:endnow:{code}"}],
            [{"text": "🧹 حذف بازه زمانی", "callback_data": f"exs:clearwin:{code}"}],
            [{"text": "🔙 بازگشت به کارت آزمون", "callback_data": f"exm:view:{code}"}],
        ]

    async def settings_menu(self, chat_id, uid, code, edit_msg=None):
        exam = self.store.exams.get(code)
        if not exam or exam.get("teacher_id") != uid:
            await self.tg.send(chat_id, "❌ فقط سازنده‌ی آزمون به تنظیمات دسترسی دارد.")
            return
        w = exam.get("window") or {}
        win_txt = views.window_line(exam)
        text = (f"⚙️ <b>تنظیمات آزمون {esc(exam.get('title', ''))}</b> (کد <code>{code}</code>)\n\n"
                f"🖼 {win_txt}\n\n"
                "برای تغییر، یکی از گزینه‌ها را انتخاب کن:")
        kb = self.settings_kb(exam)
        if edit_msg:
            await self.tg.edit(chat_id, edit_msg, text, kb)
        else:
            await self.tg.send(chat_id, text, kb)

    async def settings_cb(self, chat_id, msg_id, uid, action, code):
        exam = self.store.exams.get(code)
        if not exam or exam.get("teacher_id") != uid:
            await self.tg.send(chat_id, "❌ فقط سازنده‌ی آزمون به تنظیمات دسترسی دارد.")
            return
        need_save = False

        if action == "dur":
            self.store.wizards[uid] = {"st": "duration", "code": code}
            self.store.touch_wizards()
            await self.tg.send(chat_id, "⏱ مدت آزمون را به <b>دقیقه</b> بفرست. (مثلاً <code>45</code>)")
            return
        if action == "neg":
            kb = [
                [{"text": "بدون نمره منفی", "callback_data": f"exs:negset:{code}:none"}],
                [{"text": "۱/۳ (کنکوری) — پیش‌فرض", "callback_data": f"exs:negset:{code}:third"}],
                [{"text": "۱/۴", "callback_data": f"exs:negset:{code}:quarter"}],
                [{"text": "۱/۲", "callback_data": f"exs:negset:{code}:half"}],
                [{"text": "✍️ عدد سفارشی", "callback_data": f"exs:negcustom:{code}"}],
                [{"text": "🔙 بازگشت", "callback_data": f"exs:menu:{code}"}],
            ]
            await self.tg.edit(chat_id, msg_id, "➖ مقدار نمره منفی را انتخاب کن (کسر از یک نمره‌ی کامل به ازای هر غلط):", kb)
            return
        if action == "negset":
            pass  # handled by caller splitting
        if action == "acc":
            exam["access"] = "public" if exam.get("access") != "public" else "link"
            need_save = True
        if action == "modes":
            m = exam.setdefault("modes", dict(config.DEFAULT_MODES))
            order = ["text", "app", "web"]
            on = [k for k in order if m.get(k)]
            if len(on) == 3:
                m["text"], m["app"], m["web"] = True, False, False
            elif m.get("text") and m.get("app"):
                m["text"], m["app"], m["web"] = True, False, True
            elif m.get("text"):
                m["text"], m["app"], m["web"] = False, True, True
            else:
                m.update(dict(config.DEFAULT_MODES))
            need_save = True
        if action == "att":
            cur = exam.get("attempts", 1)
            exam["attempts"] = {1: 2, 2: 3, 3: 0, 0: 1}[cur]
            need_save = True
        if action == "notify":
            exam["notify"] = not exam.get("notify", True)
            need_save = True
        if action == "startnow":
            t = int(time.time())
            exam["window"] = {"start": t, "end": t + exam.get("duration", 60) * 60}
            exam["status"] = "active"
            need_save = True
        if action == "endnow":
            exam["status"] = "ended"
            need_save = True
        if action == "clearwin":
            exam["window"] = {"start": None, "end": None}
            need_save = True
        if action == "del":
            kb = [
                [{"text": "🗑 بله، حذف کن", "callback_data": f"exs:dely:{code}"},
                 {"text": "لغو", "callback_data": f"exs:menu:{code}"}],
            ]
            await self.tg.edit(chat_id, msg_id,
                               f"⚠️ مطمئنی آزمون «{esc(exam.get('title', ''))}» با همه‌ی نتایجش حذف شود؟", kb)
            return
        if action == "dely":
            await self.store.delete_exam(code)
            await self.tg.edit(chat_id, msg_id, "🗑 آزمون و نتایجش کامل حذف شد.")
            return
        if action == "results":
            rows = self.store.results(code)
            await self.tg.send(chat_id, views.results_table(exam, rows))
            return
        if action == "csv":
            rows = self.store.results(code)
            if not rows:
                await self.tg.send(chat_id, "📊 هنوز نتیجه‌ای ثبت نشده است.")
                return
            data = views.build_csv(exam, rows)
            await self.tg.send_document(chat_id, data, f"results-{code}.csv",
                                        caption=f"📊 نتایج {esc(exam.get('title', ''))}")
            return
        if action == "addq":
            self.store.wizards[uid] = {"st": "append_pub", "code": code}
            self.store.touch_wizards()
            await self.tg.send(chat_id, "➕ سوالات جدید را بفرست (متن یا فایل). به سوالات فعلی <b>اضافه</b> می‌شوند.\n\n" + qparser.SAMPLE)
            return
        if action == "replq":
            self.store.wizards[uid] = {"st": "replace_pub", "code": code}
            self.store.touch_wizards()
            await self.tg.send(chat_id, "🔄 سوالات جدید را بفرست؛ <b>جایگزین تمام سوالات فعلی</b> می‌شود.\n\n" + qparser.SAMPLE)
            return
        if action == "menu":
            await self.settings_menu(chat_id, uid, code, edit_msg=msg_id)
            return

        if need_save:
            await self.store.save_exam(code)
        await self.settings_menu(chat_id, uid, code, edit_msg=msg_id)

    async def set_negative(self, chat_id, msg_id, uid, code, value):
        exam = self.store.exams.get(code)
        if not exam or exam.get("teacher_id") != uid:
            return
        if value == "custom":
            self.store.wizards[uid] = {"st": "negcustom", "code": code}
            self.store.touch_wizards()
            await self.tg.send(chat_id, "✍️ ضریب نمره منفی را بفرست (بین 0 و 1). مثلاً <code>0.33</code> یا <code>0.25</code>")
            return
        exam["negative"] = value
        await self.store.save_exam(code)
        await self.settings_menu(chat_id, uid, code, edit_msg=msg_id)

    # ------------------------------------------------------------------ my exams

    async def my_exams(self, chat_id, uid, edit_msg=None):
        mine = [e for e in self.store.exams.values() if e.get("teacher_id") == uid]
        if not mine:
            txt = "📋 هنوز آزمونی نساخته‌ای. با دکمه‌ی پایین شروع کن:"
            kb = [[{"text": "📝 ساخت آزمون جدید", "callback_data": "wiz:new"}]]
        else:
            mine.sort(key=lambda e: -e.get("created", 0))
            txt = "📋 <b>آزمون‌های تو:</b> روی هرکدام بزن تا کارت کامل + لینک‌ها را ببینی."
            kb = []
            for e in mine[:30]:
                st = "🟢" if e.get("status", "active") == "active" else "🔴"
                kb.append([{"text": f"{st} {e.get('title', 'بی‌نام')[:40]} ({e['code']})",
                            "callback_data": f"exm:view:{e['code']}"}])
            kb.append([{"text": "📝 ساخت آزمون جدید", "callback_data": "wiz:new"}])
        if edit_msg:
            await self.tg.edit(chat_id, edit_msg, txt, kb)
        else:
            await self.tg.send(chat_id, txt, kb)

    # ------------------------------------------------------------------ text / doc handlers

    async def handle_text(self, chat_id, uid, name, username, text) -> bool:
        wiz = self.store.wizards.get(uid)
        if not wiz:
            return False
        st = wiz.get("st")
        if st in ("pq_n", "pq_a"):
            await self.photo_text(chat_id, uid, text)
            return True
        if st == "title":
            await self.got_title(chat_id, uid, text, name, username)
            return True
        if st == "questions":
            await self.add_parsed(chat_id, uid, text, "draft")
            return True
        if st == "append_pub":
            await self.add_parsed(chat_id, uid, text, "append_pub")
            return True
        if st == "replace_pub":
            await self.add_parsed(chat_id, uid, text, "replace_pub")
            return True
        if st == "duration":
            code = wiz.get("code")
            exam = self.store.exams.get(code)
            if exam and exam.get("teacher_id") == uid:
                try:
                    minutes = max(1, min(600, int(text.strip().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")))))
                except ValueError:
                    await self.tg.send(chat_id, "❌ عدد نامعتبر. فقط عدد دقیقه بفرست. مثلاً <code>45</code>")
                    return True
                exam["duration"] = minutes
                await self.store.save_exam(code)
                self.store.wizards.pop(uid, None)
                self.store.touch_wizards()
                await self.settings_menu(chat_id, uid, code)
            return True
        if st == "negcustom":
            code = wiz.get("code")
            exam = self.store.exams.get(code)
            if exam and exam.get("teacher_id") == uid:
                try:
                    val = float(text.strip().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")))
                    val = max(0.0, min(1.0, val))
                except ValueError:
                    await self.tg.send(chat_id, "❌ عدد نامعتبر. مثلاً <code>0.33</code>")
                    return True
                exam["negative"] = round(val, 3)
                await self.store.save_exam(code)
                self.store.wizards.pop(uid, None)
                self.store.touch_wizards()
                await self.settings_menu(chat_id, uid, code)
            return True
        return False

    async def handle_document(self, chat_id, uid, doc, file_id, file_name):
        wiz = self.store.wizards.get(uid)
        if not wiz:
            return False
        st = wiz.get("st")
        if st not in ("questions", "append_pub", "replace_pub"):
            return False
        if not (file_name or "").lower().endswith((".txt", ".text", ".json", ".csv", ".md")):
            await self.tg.send(chat_id, "❌ فقط فایل متنی (txt/json/csv) قابل قبول است.")
            return True
        raw = (await self.tg.download_file(file_id)).decode("utf-8", errors="replace")
        mode = {"questions": "draft", "append_pub": "append_pub", "replace_pub": "replace_pub"}[st]
        await self.add_parsed(chat_id, uid, raw, mode)
        return True

    async def handle_cb(self, chat_id, msg_id, uid, data) -> bool:
        parts = data.split(":")
        if parts[0] == "wiz":
            act = parts[1]
            if act == "new":
                await self.start_new(chat_id, uid, "", "")
                return True
            if act == "sample":
                await self.tg.send(chat_id, qparser.SAMPLE)
                return True
            if act == "cancel":
                self.store.wizards.pop(uid, None)
                self.store.touch_wizards()
                await self.tg.edit(chat_id, msg_id, "🗑 پیش‌نویس حذف شد. با /start می‌توانی دوباره شروع کنی.")
                return True
            if act == "done" and len(parts) >= 3:
                await self.publish(chat_id, uid, parts[2])
                return True
            if act == "pqn" and len(parts) >= 4:
                await self.photo_pick_n(chat_id, msg_id, uid, parts[2], parts[3])
                return True
            if act == "pqa" and len(parts) >= 4:
                await self.photo_pick_a(chat_id, msg_id, uid, parts[2], parts[3])
                return True
            if act == "pqcancel" and len(parts) >= 3:
                await self.photo_cancel(chat_id, msg_id, uid, parts[2])
                return True
            if act == "pqhint" and len(parts) >= 3:
                await self.tg.send(chat_id,
                                   "✍️ حالا گزینه‌ها را به صورت متن بفرست (همراه با پاسخ صحیح). مثال:\n\n"
                                   "<code>الف) گزینه اول\n"
                                   "ب) گزینه دوم\n"
                                   "ج) گزینه سوم\n"
                                   "د) گزینه چهارم\n"
                                   "پاسخ: ج</code>")
                return True
        if parts[0] == "exm":
            if parts[1] == "list":
                await self.my_exams(chat_id, uid, edit_msg=msg_id)
                return True
            if parts[1] == "view" and len(parts) >= 3:
                await self.show_exam_card(chat_id, uid, parts[2], edit_msg=msg_id)
                return True
        if parts[0] == "exs":
            if len(parts) >= 4 and parts[1] == "negset":
                await self.set_negative(chat_id, msg_id, uid, parts[2], parts[3])
                return True
            if len(parts) >= 3:
                await self.settings_cb(chat_id, msg_id, uid, parts[1], parts[2])
                return True
        return False
