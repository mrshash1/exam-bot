# -*- coding: utf-8 -*-
"""UI views: exam cards, menus, help text, results table & CSV, detailed per-question results."""
import time
from datetime import datetime, timezone, tzinfo

import config
import scoring
from tg import esc

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo(config.TZ_NAME)
except Exception:
    TZ = timezone.utc


def fa_ts(ts: int) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts, TZ).strftime("%Y-%m-%d  %H:%M")


def mmss(seconds: int) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def human_left(seconds: int) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds} ثانیه"
    m, s = divmod(seconds, 60)
    if m < 60:
        return f"{m} دقیقه"
    h, m = divmod(m, 60)
    return f"{h} ساعت و {m} دقیقه"


MODE_LABELS = {"text": "✍️ متنی", "app": "📱 اپ (داخل تلگرام)", "web": "🌐 سایت"}

FA_LETTERS = ["الف", "ب", "ج", "د", "ه", "و", "ز", "ح", "ط", "ی"]


def fa_num(x) -> str:
    """Persian-friendly number: 1 -> ۱، 0.33 -> ۰/۳۳"""
    try:
        x = float(x)
    except (TypeError, ValueError):
        return str(x)
    if x == int(x):
        return str(int(x)).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))
    s = ("%.2f" % x).rstrip("0").rstrip(".")
    return s.translate(str.maketrans("0123456789.", "۰۱۲۳۴۵۶۷۸۹/"))


def _trim(s, n: int = 30) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def window_line(exam: dict) -> str:
    w = exam.get("window") or {}
    st, en = w.get("start"), w.get("end")
    t = int(time.time())
    if not st and not en:
        return "🕛 بازه زمانی: آزاد (هر لحظه)"
    if st and t < st:
        return f"🕐 شروع: {fa_ts(st)}  (تا شروع: {human_left(st - t)})"
    if en:
        if t > en:
            return f"⛔ پایان: {fa_ts(en)}  (تمام شده)"
        return f"⏳ مهلت: {fa_ts(en)}  (تا پایان: {human_left(en - t)})"
    return f"🕐 از {fa_ts(st)} — بدون مهلت پایانی"


def exam_card(exam: dict, uid: int, is_teacher: bool, bot_username: str | None = None) -> tuple[str, list]:
    bot_username = bot_username or config.BOT_USERNAME
    code = exam["code"]
    nq = len(exam.get("questions", []))
    neg = exam.get("negative", "none")
    neg_txt = config.NEGATIVE_LABELS.get(neg, f"نمره منفی {neg}")
    att = exam.get("attempts", 1)
    att_txt = "نامحدود" if att == 0 else str(att)
    status = exam.get("status", "active")
    modes = exam.get("modes", config.DEFAULT_MODES)
    modes_txt = " | ".join(lbl for m, lbl in MODE_LABELS.items() if modes.get(m))

    lines = [
        f"📝 <b>{esc(exam.get('title', 'بدون نام'))}</b>",
        f"🆔 کد آزمون: <code>{code}</code>",
        f"👩‍🏫 دبیر: {esc(exam.get('teacher_name', '—'))}",
        f"❓ تعداد سوالات: <b>{nq}</b>",
        f"⏱ مدت: <b>{exam.get('duration', 60)} دقیقه</b>",
        f"➖ نمره منفی: <b>{esc(neg_txt)}</b>",
        f"🎯 شرکت در هر آزمون: <b>{att_txt}</b> بار",
        f"🚦 وضعیت: {'🟢 فعال' if status == 'active' else '🔴 پایان‌یافته'}",
        window_line(exam),
        f"🖥 حالت‌ها: {modes_txt or '—'}",
    ]
    n_img = len([q for q in exam.get("questions", []) if q.get("img")])
    if n_img:
        lines.append(f"📷 سوالات عکس‌دار: <b>{n_img}</b>")
    if nq:
        total = sum(float(q.get("p", 1)) for q in exam["questions"])
        lines.append(f"💯 نمره کل: <b>{round(total, 2)}</b>")

    kb = []
    if is_teacher:
        lines += [
            "",
            "🔗 <b>لینک‌های آزمون (همیشه در دسترس):</b>",
            f"• لینک مستقیم ربات:\n<code>https://t.me/{bot_username}?start=exam_{code}</code>",
            f"• لینک سایت آزمون (تایمر متحرک):\n<code>{config.WEB_EXAM_URL.format(code=code)}</code>",
            "",
            "💡 دانش‌آموزان با دکمه‌ی «🎯 شرکت در آزمون» و وارد کردن کد هم می‌توانند وارد شوند.",
        ]
        if status == "active" and modes.get("app"):
            kb.append([{"text": "📱 مشاهده در حالت اپ", "web_app": {"url": config.WEB_EXAM_URL.format(code=code)}}])
        kb.append([
            {"text": "📊 نتایج", "callback_data": f"exs:results:{code}"},
            {"text": "📄 خروجی CSV", "callback_data": f"exs:csv:{code}"},
        ])
        kb.append([
            {"text": "⚙️ تنظیمات", "callback_data": f"exs:menu:{code}"},
            {"text": "🗑 حذف", "callback_data": f"exs:del:{code}"},
        ])
        if status == "active":
            kb.append([{"text": "⛔ پایان دادن به آزمون", "callback_data": f"exs:endnow:{code}"}])
        else:
            kb.append([{"text": "▶️ فعال‌سازی مجدد", "callback_data": f"exs:startnow:{code}"}])
    else:
        if status != "active":
            lines.append("⛔ این آزمون پایان یافته است.")
        w = exam.get("window") or {}
        t = int(time.time())
        locked = False
        if w.get("start") and t < w["start"]:
            lines.append(f"⏰ آزمون هنوز شروع نشده؛ {human_left(w['start'] - t)} تا شروع.")
            locked = True
        if w.get("end") and t > w["end"]:
            lines.append("⏰ زمان آزمون به پایان رسیده است.")
            locked = True
        if status == "active" and not locked:
            row = []
            if modes.get("app"):
                row.append({"text": "📱 حالت اپ (تایمر زنده)", "web_app": {"url": config.WEB_EXAM_URL.format(code=code)}})
            if row:
                kb.append(row)
            row2 = []
            if modes.get("web"):
                row2.append({"text": "🌐 لینک سایت", "url": config.WEB_EXAM_URL.format(code=code)})
            if modes.get("text"):
                row2.append({"text": "✍️ شروع متنی", "callback_data": f"exa:start:{code}"})
            if row2:
                kb.append(row2)
    return "\n".join(lines), kb


def main_menu_kb(is_teacher_hint: bool = True) -> list:
    kb = [
        [{"text": "📝 ساخت آزمون جدید", "callback_data": "wiz:new"}],
        [{"text": "📋 آزمون‌های من", "callback_data": "exm:list"},
         {"text": "🎯 شرکت در آزمون", "callback_data": "join:ask"}],
        [{"text": "🌐 آزمون‌های عمومی", "callback_data": "pub:list"},
         {"text": "📖 راهنما", "callback_data": "help:show"}],
    ]
    return kb


HELP_TEXT = """ℹ️ <b>راهنمای ربات آزمون</b>

<b>👩‍🏫 برای دبیر (ساخت آزمون):</b>
1️⃣ «📝 ساخت آزمون جدید» → نام آزمون را بفرست.
2️⃣ بلافاصله کارت اطلاعات آزمون را می‌بینی (کد، لینک‌ها، تنظیمات پیش‌فرض).
3️⃣ سوالات را بفرست — <b>متن پیام</b>، فایل <code>txt</code> یا <b>📸 عکس سوال</b>.
4️⃣ «✅ انتشار آزمون» را بزن. لینک سایت و اپ همان‌جا داده می‌شود.

<b>📸 سوال با عکس (ساده‌ترین راه):</b>
• فقط <b>عکس سوال</b> را بفرست — سوال و هر ۴ گزینه داخل خود عکس باشد (نیازی به توضیح یا کپشن نیست).
• بعد از ارسال عکس، فقط با یک دکمه (الف/ب/ج/د) بگو کدام گزینه درست است — تمام!
• سوال همیشه ۴ گزینه دارد؛ سوالات متنی هم قابل ارسال‌اند (دکمه «📋 نمونه قالب سوال» را ببین).

<b>حالت‌های برگزاری (تایمر در همه!):</b>
✍️ <b>متنی</b>: سوالات داخل چت مطرح می‌شود و <b>تایمر زندهٔ متحرک</b> (هر ۱۵ ثانیه به‌روز) مانند کرونمتر زمان باقی‌مانده را نشان می‌دهد؛ دکمهٔ «⏱ زمان باقی‌مانده» هم در هر سوال هست.
📱 <b>اپ</b>: آزمون داخل خود تلگرام با <b>تایمر متحرک زنده</b> اجرا می‌شود.
🌐 <b>سایت</b>: آزمون در مرورگر با تایمر زنده و بزرگ اجرا می‌شود.

<b>🎯 برای دانش‌آموز:</b>
• روی لینک دبیر بزن یا کد آزمون را بفرست. مشخصات آزمون (مدت، تعداد سوال، نمره منفی) را قبل از شروع می‌بینی.
• در همهٔ حالت‌ها تایمر متحرک داری و در هر لحظه می‌توانی زمان باقی‌مانده را ببینی.
• 📷 در حالت سایت/اپ، روی عکس سوال بزن تا تمام‌صفحه باز شود؛ با دو انگشت یا دوبار لمس، بزرگش کن تا راحت بخوانی.
• بعد از پایان، نتیجه داخل چت ثبت و کارت نمره را می‌بینی.
• 📊 دکمه‌ی «نتایج دقیق» را بزن تا سوال‌به‌سوال ببینی: به هر سوال چه گزینه‌ای زدی، گزینهٔ صحیح کدام بود و کدام‌ها نمره منفی خورد.

<b>⚙️ تنظیمات هر آزمون:</b> مدت، نمره منفی (پیش‌فرض: ۱/۳ کنکوری)، تعداد مجاز شرکت، دسترسی عمومی/با لینک، حالت‌ها، شروع/پایان دستی.

🆘 اگر گیر کردی /start بزن."""




def results_table(exam: dict, results: list) -> str:
    if not results:
        return "📊 هنوز نتیجه‌ای ثبت نشده است."
    rows = sorted(results, key=lambda r: (-r.get("score", 0), r.get("ts", 0)))
    lines = [f"📊 <b>نتایج: {esc(exam.get('title', ''))}</b>  (کد <code>{exam['code']}</code>)",
             f"تعداد شرکت‌کننده: {len(rows)}\n"]
    neg = exam.get("negative", "none") != "none"
    for i, r in enumerate(rows, 1):
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"{i}.")
        uname = f" @{r['username']}" if r.get("username") else ""
        lines.append(
            f"{medal} <b>{esc(r.get('name', 'بی‌نام'))}</b>{uname}\n"
            f"    🎯 {r.get('score', 0)} از {r.get('total', 0)}  (٪{r.get('pct', 0)}) | "
            f"✅{r.get('c', 0)} ❌{r.get('w', 0)} ⬜{r.get('b', 0)}"
            + (" | ➖منفی اعمال شد" if neg and r.get("w", 0) else "")
        )
    return "\n".join(lines)


def build_csv(exam: dict, results: list) -> bytes:
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["rank", "name", "username", "telegram_id", "score", "total", "percent",
                "correct", "wrong", "blank", "duration_sec", "source", "time_iso"])
    rows = sorted(results, key=lambda r: (-r.get("score", 0), r.get("ts", 0)))
    for i, r in enumerate(rows, 1):
        w.writerow([i, r.get("name", ""), r.get("username", ""), r.get("uid", ""),
                    r.get("score", 0), r.get("total", 0), r.get("pct", 0),
                    r.get("c", 0), r.get("w", 0), r.get("b", 0),
                    r.get("dur", ""), r.get("via", ""),
                    datetime.fromtimestamp(r.get("ts", 0), TZ).strftime("%Y-%m-%d %H:%M:%S")])
    return "\ufeff".encode("utf-8") + buf.getvalue().encode("utf-8")


def detailed_results_text(exam: dict, result: dict) -> str:
    """Question-by-question breakdown of a student's own attempt:
    what they answered vs the correct answer, with per-question marks."""
    qs = exam.get("questions", [])
    ans = result.get("ans") or {}
    neg = exam.get("negative", "none")
    frac = scoring.neg_frac(neg)
    lines = [
        "📊 <b>نتایج دقیق شما</b>",
        f"📝 آزمون: {esc(exam.get('title', ''))} (کد <code>{exam['code']}</code>)",
        f"👤 نام: {esc(result.get('name', ''))}",
        f"🎯 نمره نهایی: <b>{fa_num(result.get('score', 0))}</b> از {fa_num(result.get('total', 0))} (٪{fa_num(result.get('pct', 0))})",
        f"✅ صحیح: {result.get('c', 0)} | ❌ غلط: {result.get('w', 0)} | ⬜ نزده: {result.get('b', 0)}",
        "━━━━━━━━━━━━━━━━━━",
    ]
    for i, q in enumerate(qs):
        p = float(q.get("p", 1) or 1)
        raw = ans.get(str(i), ans.get(i))
        is_img = bool(q.get("img")) and not q.get("o")
        tag = "📷 " if is_img else ""
        stem = _trim(q.get("t", ""), 42)
        if stem:
            head = f"<b>{i + 1}.</b> {tag}{esc(stem)}"
        else:
            head = f"<b>{i + 1}.</b> {tag}سوال تصویری"
        if raw is None or raw == "" or raw == -1:
            lines.append(f"{head}\n      ⬜ <b>بی‌پاسخ</b> — نمره‌ای نگرفتی (نمره منفی هم ندارد)")
            continue
        if scoring.is_mcq(q):
            n_opts = max(len(q.get("o") or []), int(q.get("n_opts") or 0))
            aidx = None
            ok = False
            try:
                aidx = int(raw)
                ok = 0 <= aidx < n_opts and aidx == q.get("a")
            except (TypeError, ValueError):
                pass
            ca = int(q.get("a", -1))
            corr_l = FA_LETTERS[ca] if 0 <= ca < len(FA_LETTERS) else str(ca + 1)
            your_l = FA_LETTERS[aidx] if aidx is not None and 0 <= aidx < len(FA_LETTERS) else "؟"
            if ok:
                extra = ""
                if q.get("o"):
                    extra = f" ({esc(_trim(q['o'][aidx], 26))})"
                lines.append(f"{head}\n      ✅ <b>درست</b> (+{fa_num(p)}) — پاسخ شما: <b>{your_l}</b>{extra}")
            else:
                extra = ""
                if q.get("o") and aidx is not None and 0 <= aidx < len(q["o"]):
                    extra = f" ({esc(_trim(q['o'][aidx], 26))})"
                neg_part = f" ({fa_num(-frac * p)})" if frac > 0 else ""
                lines.append(
                    f"{head}\n"
                    f"      ❌ <b>نادرست</b>{neg_part} — پاسخ شما: <b>{your_l}</b>{extra} | "
                    f"پاسخ صحیح: <b>{corr_l}</b>"
                )
        else:  # open (typed) question
            ok = scoring.norm_open(str(raw)) == scoring.norm_open(str(q.get("a", "")))
            if ok:
                lines.append(f"{head}\n      ✅ <b>درست</b> (+{fa_num(p)}) — پاسخ شما: «{esc(_trim(str(raw), 40))}»")
            else:
                neg_part = f" ({fa_num(-frac * p)})" if frac > 0 else ""
                lines.append(
                    f"{head}\n"
                    f"      ❌ <b>نادرست</b>{neg_part} — پاسخ شما: «{esc(_trim(str(raw), 40))}» | "
                    f"پاسخ صحیح: «{esc(_trim(str(q.get('a', '')), 40))}»"
                )
    lines.append("━━━━━━━━━━━━━━━━━━")
    if frac > 0:
        lines.append("💡 نمره منفی فقط برای پاسخ‌های غلط اعمال شده؛ سوال‌های بی‌پاسخ نمره منفی ندارند.")
    return "\n".join(lines)
