# -*- coding: utf-8 -*-
"""Teacher wizard: create exam (title -> info card -> questions -> publish) + settings."""
import time

import config
import parser as qparser
import views
from tg import esc
from views import fa_ts, human_left


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
            "📨 حالا سوالات را بفرست (متن یا فایل txt). هر بار که بفرستی اضافه می‌شود."
        )
        kb = [
            [{"text": "✅ انتشار آزمون", "callback_data": f"wiz:done:{code}"}],
            [{"text": "📋 نمونه قالب سوال", "callback_data": "wiz:sample"}],
            [{"text": "❌ انصراف و حذف پیش‌نویس", "callback_data": "wiz:cancel"}],
        ]
        return text, kb

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
        self.store.vault[code] = {
            "teacher_id": uid,
            "answers": [q["a"] for q in exam["questions"]],
            "results": [],
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
