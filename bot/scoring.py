# -*- coding: utf-8 -*-
"""Scoring engine + web result-code encode/decode."""
import base64
import binascii
import json
import re
import time

import config


def neg_frac(negative) -> float:
    if isinstance(negative, (int, float)):
        return max(0.0, min(1.0, float(negative)))
    return config.NEGATIVE_PRESETS.get(negative, config.NEGATIVE_PRESETS[config.DEFAULT_NEGATIVE])


_FA_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def norm_open(s: str) -> str:
    import re as _re
    s = (s or "").strip().lower()
    s = s.translate(_FA_DIGITS)
    s = _re.sub(r"[\s\u0640]+", "", s)
    return s.strip(".؟?!،,؛:«»\"'")


def is_mcq(q: dict) -> bool:
    """MCQ if it has text options OR is an image-question with on-image options."""
    return bool(q.get("o")) or bool(int(q.get("n_opts") or 0))


def score_exam(questions: list, answers: dict, negative) -> dict:
    """questions: [{t,o,a,p}]; answers: {idx_str: opt_idx_or_text}.

    Returns {score, total, c, w, b, pct}.
    """
    frac = neg_frac(negative)
    total = 0.0
    gained = 0.0
    c = w = b = 0
    for i, q in enumerate(questions):
        p = float(q.get("p", 1) or 1)
        total += p
        raw = answers.get(str(i), answers.get(i, None))
        if raw is None or raw == "" or raw == -1:
            b += 1
            continue
        if is_mcq(q):  # MCQ (text options or image-only options)
            n_opts = max(len(q.get("o") or []), int(q.get("n_opts") or 0))
            try:
                aidx = int(raw)
            except (TypeError, ValueError):
                w += 1
                gained -= frac * p
                continue
            if 0 <= aidx < n_opts and aidx == q["a"]:
                c += 1
                gained += p
            else:
                w += 1
                gained -= frac * p
        else:       # open
            if norm_open(str(raw)) == norm_open(str(q["a"])):
                c += 1
                gained += p
            else:
                w += 1
                gained -= frac * p
    score = round(gained, 2)
    pct = round(max(0.0, score) / total * 100, 1) if total else 0.0
    return {"score": score, "total": round(total, 2), "c": c, "w": w, "b": b, "pct": pct}


# ---------------- result codes (web / mini-app -> bot) ----------------

MAGIC = "EXM1."


def encode_result_code(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    b = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    # chunk for readability
    chunks = [b[i:i + 96] for i in range(0, len(b), 96)]
    return MAGIC + "\n".join(chunks)


def decode_result_code(code: str):
    """Accepts the code with/without newlines & surrounding text. Returns dict or None."""
    try:
        s = (code or "").strip()
        if not s:
            return None
        idx = s.find(MAGIC)
        if idx >= 0:
            s = s[idx + len(MAGIC):]
        # keep only base64url characters (drops Persian/whitespace junk around the code)
        s = re.sub(r"[^A-Za-z0-9_\-=]", "", s)
        s = s.replace("-", "+").replace("_", "/")
        s += "=" * (-len(s) % 4)
        raw = base64.b64decode(s.encode())
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict) or int(payload.get("v", 0)) != 1:
            return None
        if "e" not in payload or "a" not in payload:
            return None
        return payload
    except (binascii.Error, ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def is_result_code(text: str) -> bool:
    return MAGIC in (text or "")


def build_payload(exam_code: str, name: str, sid: str, answers: dict, started: int) -> dict:
    return {
        "v": 1,
        "e": exam_code,
        "n": (name or "").strip()[:80],
        "s": (sid or "").strip()[:40],
        "a": answers,
        "t": started,
        "f": int(time.time()),
    }
