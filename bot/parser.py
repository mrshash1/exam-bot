# -*- coding: utf-8 -*-
"""
Flexible question parser for the exam bot.

Accepted format (blank line between questions recommended):

    1. متن سوال؟
    الف) گزینه اول
    ب) گزینه دوم
    ج) گزینه سوم
    د) گزینه چهارم
    پاسخ: ج

Option labels: الف ب ج د ه و  |  A B C D ...  |  1 2 3 4 ...
Answer lines:  پاسخ / پاسخ صحیح / جواب / جواب صحیح / گزینه صحیح / answer / key

A trailing answer-key block is also accepted:

    پاسخ‌ها: 1-ب، 2-B، 3: 60

Open (short-answer) questions = no options, with a text answer.
"""
import json
import re

import config

FA_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
AR_MAP = {"ي": "ی", "ك": "ک", "ﻻ": "لا", "أ": "ا", "إ": "ا", "ؤ": "و", "ة": "ه", "ئ": "ی"}
ZW_CHARS = "\u200c\u200f\u200e\ufeff"

LETTER_OPT = {"الف": 0, "ب": 1, "ج": 2, "چ": 2, "د": 3, "ه": 4, "و": 5,
              "a": 0, "b": 1, "c": 2, "d": 3, "e": 4, "f": 5, "g": 6, "h": 7}

Q_RE = re.compile(r"^\s*(?:سوال|سؤال|question)?\s*[\(\[]?\s*(\d{1,3})\s*[\)\]\.:\-]\s*(.+)$",
                  re.IGNORECASE)
OPT_RE = re.compile(r"^\s*[\(\[]?\s*(الف|[ء-ی]|[A-Za-z]{1,2}|[0-9]{1,2})\s*[\)\]\.:\-]\s*(.+)$")
ANS_RE = re.compile(
    r"^\s*(?:پاسخ\s*صحیح|جواب\s*صحیح|گزینه\s*صحیح|پاسخ|جواب|answer|key)\s*[:\-\.]?\s*(.+)$",
    re.IGNORECASE)
KEYS_RE = re.compile(
    r"^\s*(?:پاسخنامه|پاسخنامه‌ها|پاسخها|پاسخ\s*ها|پاسخ‌ها|کلیدها|کلید|answers|answer\s*key|key)\s*[:\-]?\s*(.*)$",
    re.IGNORECASE)
PAIR_RE = re.compile(r"(\d{1,3})\s*[-:.\)=]\s*")


def normalize(s: str) -> str:
    if not s:
        return ""
    s = s.translate(FA_DIGITS)
    for a, b in AR_MAP.items():
        s = s.replace(a, b)
    for ch in ZW_CHARS:
        s = s.replace(ch, " ")
    s = s.replace("\t", " ")
    s = re.sub(r"[ ]{2,}", " ", s)
    return s.strip()


def norm_answer_text(s: str) -> str:
    s = normalize(s).lower()
    s = re.sub(r"[\s\u0640]+", "", s)
    s = s.strip(".؟?!،,؛:«»\"'")
    return s


def parse_key_block(rest: str) -> dict[int, str]:
    """Parse '1-ب، 2-B، 3: 60' style key text into {num: raw_value}."""
    keys = {}
    pos = 0
    matches = list(PAIR_RE.finditer(rest))
    for i, m in enumerate(matches):
        num = int(m.group(1))
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(rest)
        val = rest[start:end].strip().strip(",،;؛. ")
        if num not in keys and val:
            keys[num] = val
    return keys


def try_parse_json(raw: str):
    """Support JSON upload: [{...}, ...] with fields q/text, o/options, a/answer."""
    t = raw.strip()
    if not (t.startswith("[") or t.startswith("{")):
        return None
    try:
        data = json.loads(t)
    except Exception:
        return None
    items = data if isinstance(data, list) else data.get("questions", [])
    if not isinstance(items, list) or not items:
        return None
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        text = normalize(str(it.get("q") or it.get("text") or it.get("سوال") or ""))
        opts = it.get("o") or it.get("options") or it.get("گزینه‌ها") or []
        ans = it.get("a", it.get("answer", it.get("پاسخ")))
        opts = [normalize(str(x)) for x in opts] if isinstance(opts, list) else []
        # JSON answers may be 0-based option indexes
        if isinstance(ans, int) and not (isinstance(ans, bool)) and 0 <= ans < max(1, len(opts)):
            a_raw = "!IDX:" + str(ans)
        else:
            a_raw = "" if ans is None else str(ans)
        q = {"t": text, "o": opts, "a_raw": a_raw, "num": len(out) + 1}
        out.append(q)
    return out or None


def parse_questions(raw: str) -> dict:
    """Returns {questions:[{t,o,a,p}], warnings:[], errors:[]}."""
    # JSON fast-path
    js = try_parse_json(raw)
    if js is not None:
        raw_items = js
    else:
        raw_items = None

    questions = []
    used_nums = set()
    cur = None
    cur_num = 0
    warnings = []
    errors = []
    keys_block = {}     # num -> raw value
    in_keys = False

    if raw_items is not None:
        questions = raw_items
    else:
        text = normalize(raw.replace("\r\n", "\n").replace("\r", "\n"))
        lines = text.split("\n")

        def flush():
            nonlocal cur
            if cur is not None and cur["t"].strip():
                questions.append(cur)
            cur = None

        prev_blank = True
        for ln in lines:
            s = ln.strip()
            if not s:
                prev_blank = True
                continue

            m_keys = KEYS_RE.match(ln)
            m_ans = ANS_RE.match(ln)
            m_q = Q_RE.match(ln)
            m_opt = OPT_RE.match(ln)

            # ---- answer-key block ----
            if m_keys and (PAIR_RE.search(m_keys.group(1)) or not m_ans):
                flush()
                in_keys = True
                keys_block.update(parse_key_block(m_keys.group(1)))
                continue

            if in_keys:
                if PAIR_RE.search(ln) and not m_q:
                    keys_block.update(parse_key_block(ln))
                    continue
                in_keys = False  # a real question resumes

            # ---- per-question answer line ----
            if m_ans and cur is not None and not m_q:
                cur["a_raw"] = m_ans.group(1).strip()
                prev_blank = False
                continue

            # ---- resolve digit lines that may be BOTH a question and an option ----
            if m_q and m_opt:
                label = normalize(m_opt.group(1)).lower()
                if label.isdigit():
                    num = int(label)
                    opt_candidate = (cur is not None and num <= 10
                                     and num == len(cur["o"]) + 1)
                    q_candidate = (cur is None or num == cur_num + 1 or num == cur_num
                                   or (num == 1 and prev_blank and cur_num >= 10))
                    if opt_candidate and not (q_candidate and prev_blank):
                        m_q = None
                    elif q_candidate:
                        m_opt = None
                    else:
                        m_q = None
                else:
                    m_q = None  # letter-labeled lines are always options

            # ---- numbered question start ----
            if m_q:
                num = int(m_q.group(1))
                is_new = (cur is None or num == cur_num + 1 or num == cur_num
                          or (num == 1 and prev_blank and cur_num >= 10))
                if is_new:
                    flush()
                    cur_num = num
                    used_nums.add(num)
                    cur = {"t": m_q.group(2).strip(), "o": [], "a_raw": "", "num": num}
                    prev_blank = False
                    continue
                # else: fall through as continuation text

            # ---- option line ----
            if m_opt and cur is not None:
                label = normalize(m_opt.group(1)).lower()
                body = m_opt.group(2).strip()
                if label in LETTER_OPT:
                    idx = LETTER_OPT[label]
                elif label.isdigit() and 1 <= int(label) <= 10:
                    idx = int(label) - 1
                else:
                    cur["t"] += " " + s
                    prev_blank = False
                    continue
                while len(cur["o"]) < idx:
                    cur["o"].append("—")
                if idx == len(cur["o"]):
                    cur["o"].append(body)
                else:
                    cur["o"][idx] = body
                prev_blank = False
                continue

            # ---- plain continuation of question text ----
            if cur is not None:
                cur["t"] += " " + s
            prev_blank = False

        flush()

    # apply the trailing key block by question number
    if keys_block and questions:
        for i, q in enumerate(questions, 1):
            if not q.get("a_raw"):
                for num, val in keys_block.items():
                    if num == (q.get("num") or i):
                        q["a_raw"] = val
                        break

    # ---- resolve + validate ----
    cleaned = []
    missing = []
    for i, q in enumerate(questions, 1):
        qt = normalize(q.get("t", ""))
        if len(qt) < 2:
            warnings.append(f"سوال {i} حذف شد (متن سوال خالی بود).")
            continue
        opts = [normalize(o) if normalize(o) else "—" for o in (q.get("o") or [])]
        a_raw = normalize(str(q.get("a_raw", "")))
        a = None

        if opts:
            opts = opts[:10]
            # resolve answer index
            al = a_raw.lower().lstrip("(«\"").rstrip(")»\"")
            al = re.sub(r"^گزینه\s*", "", al).strip()
            idx = None
            if a_raw.startswith("!IDX:"):
                try:
                    iv = int(a_raw[5:])
                    idx = iv if 0 <= iv < len(opts) else None
                except ValueError:
                    idx = None
                al = ""
            elif al in LETTER_OPT:
                idx = LETTER_OPT[al]
                if idx >= len(opts):
                    idx = None
            elif al.isdigit() and 1 <= int(al) <= len(opts):
                idx = int(al) - 1
            if idx is None and al:
                # match option TEXT (e.g. key written as "1-دو")
                for oi, otext in enumerate(opts):
                    if otext and otext != "—" and norm_answer_text(otext) == al:
                        idx = oi
                        break
            if idx is None:
                missing.append(i)
                continue
            a = idx
        else:
            if not a_raw:
                missing.append(i)
                continue
            a = norm_answer_text(a_raw)
            if not a:
                missing.append(i)
                continue

        cleaned.append({"t": qt, "o": opts, "a": a, "p": 1})

    if missing:
        warnings.append("⚠️ برای سوالات «" + "، ".join(map(str, missing)) + "» پاسخی پیدا نشد و حذف شدند.")
    if not cleaned:
        errors.append("هیچ سوالی قابل تشخیص نبود. لطفاً نمونه قالب را ببینید.")
    if len(cleaned) > config.MAX_QUESTIONS:
        warnings.append(f"بیش از {config.MAX_QUESTIONS} سوال؛ فقط {config.MAX_QUESTIONS} سوال اول نگه داشته شد.")
        cleaned = cleaned[: config.MAX_QUESTIONS]

    return {"questions": cleaned, "warnings": warnings, "errors": errors}


SAMPLE = (
    "📘 <b>نمونه قالب صحیح</b> (هم فارسی هم انگلیسی):\n\n"
    "<code>1. کدام سیاره به خورشید نزدیک‌تر است؟\n"
    "الف) زمین\n"
    "ب) عطارد\n"
    "ج) زحل\n"
    "د) مریخ\n"
    "پاسخ: ب\n\n"
    "2. پایتخت فرانسه چه نام دارد؟\n"
    "A) برلین\n"
    "B) پاریس\n"
    "C) مادرید\n"
    "D) رم\n"
    "پاسخ: B\n\n"
    "3. حاصل ۱۲ × ۵ چند است؟ (فقط عدد)\n"
    "پاسخ: 60</code>\n\n"
    "💡 می‌توانی به جای نوشتن «پاسخ:» بعد از هر سوال، در پایان یک خط کلید هم بفرستی:\n"
    "<code>پاسخ‌ها: 1-ب، 2-B، 3: 60</code>\n\n"
    "📎 فایل <code>.txt</code> هم قابل ارسال است.\n\n"
    "📸 <b>سوال عکس‌دار:</b> فقط عکس سوال را بفرست؛ اگر گزینه‌ها داخل عکس هستند، "
    "فقط تعداد گزینه‌ها و گزینهٔ صحیح را با دکمه انتخاب کن."
)
