# -*- coding: utf-8 -*-
"""Configuration for the exam bot (loaded from environment)."""
import os

# --- Secrets (GitHub Actions secrets) ---
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
GH_TOKEN = os.environ.get("GH_PAT", os.environ.get("GITHUB_TOKEN", ""))

# --- GitHub repo ---
GH_REPO = os.environ.get("GH_REPO", "mrshash1/exam-bot")
GH_BRANCH = os.environ.get("GH_BRANCH", "main")
OWNER, REPO_NAME = GH_REPO.split("/", 1)

# --- GitHub Pages base URL ---
PAGES_BASE = os.environ.get("PAGES_BASE", f"https://{OWNER}.github.io/{REPO_NAME}")

# Filled at runtime after getMe; fallback keeps deep links working even before that.
BOT_USERNAME = os.environ.get("BOT_USERNAME", "mykonkuriexam2bot")

# --- Data locations inside the repo (served over GitHub Pages) ---
EXAMS_DIR = "exams"              # public exam definitions (NO answer keys) -> exams/<code>.json
INDEX_FILE = "exams/index.json"  # public list of public exams
VAULT_DIR = "data/vault"         # encrypted answer keys + results -> data/vault/<code>.json
SESSIONS_FILE = "data/sessions.json"  # encrypted in-progress text-mode sessions
WIZARDS_FILE = "data/wizards.json"    # encrypted teacher wizard states

# --- Defaults ---
DEFAULT_DURATION = 60            # minutes
DEFAULT_NEGATIVE = "third"       # 1/3 of a point per wrong answer (konkur style)
DEFAULT_ATTEMPTS = 1
DEFAULT_ACCESS = "link"          # "link" (only via link/code) | "public" (also listed publicly)
DEFAULT_MODES = {"text": True, "app": True, "web": True}

MAX_QUESTIONS = 300
EXAM_CODE_LEN = 6
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I

# Negative marking presets -> fraction of point removed per wrong answer
NEGATIVE_PRESETS = {
    "none": 0.0,
    "third": 1.0 / 3.0,
    "quarter": 0.25,
    "half": 0.5,
}
NEGATIVE_LABELS = {
    "none": "بدون نمره منفی",
    "third": "۱/۳ نمره منفی (کنکوری)",
    "quarter": "۱/۴ نمره منفی",
    "half": "۱/۲ نمره منفی",
}

SELF_RESTART_AFTER = 290 * 60     # seconds (4h50m) — restart before the ~6h Actions limit
POLL_TIMEOUT = 25                 # Telegram long-poll seconds

TZ_NAME = "Asia/Tehran"

WEB_EXAM_URL = PAGES_BASE + "/exam.html?e={code}"
WEB_INDEX_URL = PAGES_BASE + "/"
