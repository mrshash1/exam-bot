# -*- coding: utf-8 -*-
"""
State store: exams (public), vault (encrypted answers+results), sessions, wizards.

- exams/<code>.json      : public exam definition served on GitHub Pages (NO answer key)
- data/vault/<code>.json : ENCRYPTED {teacher_id, answers, results}
- data/sessions.json     : ENCRYPTED in-progress text-mode sessions
- data/wizards.json      : ENCRYPTED teacher wizard states
- exams/index.json       : public list of public active exams
"""
import asyncio
import random
import time

import config
import crypto


def now() -> int:
    return int(time.time())


class Store:
    def __init__(self, gh):
        self.gh = gh
        self.exams: dict[str, dict] = {}      # code -> exam (with teacher_id in memory)
        self.vault: dict[str, dict] = {}      # code -> {teacher_id, answers, results}
        self.sessions: dict[int, dict] = {}   # uid -> session
        self.wizards: dict[int, dict] = {}    # uid -> wizard state
        self._dirty_sessions = False
        self._dirty_wizards = False
        self._flush_task = None
        self._stopped = False

    # ================= boot =================

    async def load(self):
        # exams
        try:
            for item in await self.gh.list_dir(config.EXAMS_DIR):
                if not item["name"].endswith(".json") or item["name"] == "index.json":
                    continue
                code = item["name"][:-5]
                data, _ = await self.gh.get_json(f"{config.EXAMS_DIR}/{item['name']}")
                if data and data.get("code"):
                    self.exams[code] = data
        except Exception as e:
            print("[store] exams load failed:", e)
        # vault
        try:
            for item in await self.gh.list_dir(config.VAULT_DIR):
                if not item["name"].endswith(".json"):
                    continue
                code = item["name"][:-5]
                data, _ = await self.gh.get_json(f"{config.VAULT_DIR}/{item['name']}")
                dec = crypto.decrypt_json(data, config.BOT_TOKEN, code) if data else None
                if dec:
                    self.vault[code] = dec
                    if code in self.exams and "teacher_id" not in self.exams[code]:
                        self.exams[code]["teacher_id"] = dec.get("teacher_id")
        except Exception as e:
            print("[store] vault load failed:", e)
        # sessions / wizards
        for attr, path in (("sessions", config.SESSIONS_FILE), ("wizards", config.WIZARDS_FILE)):
            try:
                data, _ = await self.gh.get_json(path)
                dec = crypto.decrypt_json(data, config.BOT_TOKEN, path) if data else None
                if isinstance(dec, dict):
                    converted = {}
                    for k, v in dec.items():
                        try:
                            converted[int(k)] = v
                        except (TypeError, ValueError):
                            pass
                    setattr(self, attr, converted)
            except Exception as e:
                print(f"[store] {attr} load failed:", e)
        # re-arm timers for sessions still within their deadline
        t = now()
        for uid, s in list(self.sessions.items()):
            exam = self.exams.get(s.get("code"))
            if not exam or t >= s.get("deadline", 0):
                self.sessions.pop(uid, None)
                self._dirty_sessions = True
        print(f"[store] loaded: {len(self.exams)} exams, {len(self.vault)} vaults, "
              f"{len(self.sessions)} sessions, {len(self.wizards)} wizards")

    # ================= exam codes =================

    def new_code(self) -> str:
        for _ in range(200):
            code = "".join(random.choice(config.CODE_ALPHABET) for _ in range(config.EXAM_CODE_LEN))
            if code not in self.exams:
                return code
        return str(now())[-8:]

    # ================= exams =================

    def public_exam(self, exam: dict) -> dict:
        pub = {k: v for k, v in exam.items() if k not in ("teacher_id",)}
        qs = []
        for q in exam.get("questions", []):
            pq = {"t": q["t"], "o": q.get("o", []), "p": q.get("p", 1)}
            if q.get("img"):
                pq["img"] = config.PAGES_BASE + "/" + q["img"]
            if not pq["o"] and q.get("n_opts"):
                pq["n_opts"] = int(q["n_opts"])
            qs.append(pq)
        pub["questions"] = qs
        return pub

    def file_id_for(self, code: str, img_path: str):
        """Telegram file_id of an uploaded question photo (for reliable sendPhoto)."""
        return ((self.vault.get(code) or {}).get("file_ids") or {}).get(img_path)

    async def save_exam(self, code: str):
        exam = self.exams.get(code)
        if not exam:
            return
        exam["bot_username"] = self.gh_bot_username
        await self.gh.put_json(f"{config.EXAMS_DIR}/{code}.json", self.public_exam(exam),
                               message=f"exam {code}: update")
        await self.rebuild_index()

    gh_bot_username = config.BOT_USERNAME

    async def save_vault(self, code: str):
        v = self.vault.get(code)
        if v is None:
            return
        enc = crypto.encrypt_json(v, config.BOT_TOKEN, code)
        await self.gh.put_json(f"{config.VAULT_DIR}/{code}.json", enc,
                               message=f"vault {code}: update",
                               merge=self._vault_merge(code))

    def _vault_merge(self, code: str):
        def merge(remote, _local):
            dec = crypto.decrypt_json(remote, config.BOT_TOKEN, code)
            if not dec:
                return crypto.encrypt_json(self.vault.get(code, {}), config.BOT_TOKEN, code)
            local = self.vault.get(code, {})
            # merge results by (uid, ts) keeping everything
            have = {(r.get("uid"), r.get("ts")) for r in local.get("results", [])}
            for r in dec.get("results", []):
                if (r.get("uid"), r.get("ts")) not in have:
                    local.setdefault("results", []).append(r)
            local["answers"] = local.get("answers") or dec.get("answers")
            local["teacher_id"] = local.get("teacher_id") or dec.get("teacher_id")
            self.vault[code] = local
            return crypto.encrypt_json(local, config.BOT_TOKEN, code)
        return merge

    async def rebuild_index(self):
        t = now()
        items = []
        for code, e in self.exams.items():
            if e.get("access") == "public" and e.get("status", "active") == "active":
                items.append({
                    "code": code, "title": e.get("title", ""), "teacher": e.get("teacher_name", ""),
                    "count": len(e.get("questions", [])), "duration": e.get("duration", 60),
                    "negative": e.get("negative", "none") != "none",
                    "window_start": e.get("window", {}).get("start"),
                    "window_end": e.get("window", {}).get("end"),
                    "ago": t - e.get("created", t),
                })
        items.sort(key=lambda x: x.get("ago", 0))  # newest first
        await self.gh.put_json(config.INDEX_FILE, {"updated": t, "exams": items[:100]},
                               message="index update")

    async def delete_exam(self, code: str):
        self.exams.pop(code, None)
        self.vault.pop(code, None)
        for uid in [u for u, s in self.sessions.items() if s.get("code") == code]:
            self.sessions.pop(uid, None)
        self._dirty_sessions = True
        for f in (f"{config.EXAMS_DIR}/{code}.json", f"{config.VAULT_DIR}/{code}.json"):
            try:
                await self.gh.delete_file(f, message=f"delete {code}")
            except Exception as e:
                print("[store] delete", f, e)
        self.gh._shas.pop(f"{config.EXAMS_DIR}/{code}.json", None)
        self.gh._shas.pop(f"{config.VAULT_DIR}/{code}.json", None)
        try:
            await self.rebuild_index()
        except Exception:
            pass

    # ================= results =================

    def results(self, code: str) -> list:
        return self.vault.get(code, {}).get("results", [])

    def add_result(self, code: str, result: dict):
        v = self.vault.setdefault(code, {"teacher_id": None, "answers": [], "results": []})
        if "results" not in v:
            v["results"] = []
        v["results"].append(result)

    def replace_last_result(self, code: str, uid: int, result: dict) -> bool:
        v = self.vault.get(code)
        if not v or not v.get("results"):
            return False
        for i in range(len(v["results"]) - 1, -1, -1):
            if v["results"][i].get("uid") == uid:
                v["results"][i] = result
                return True
        return False

    def attempts_used(self, code: str, uid: int) -> int:
        return sum(1 for r in self.results(code) if r.get("uid") == uid)

    # ================= debounced persistence (sessions & wizards) =================

    def touch_sessions(self):
        self._dirty_sessions = True

    def touch_wizards(self):
        self._dirty_wizards = True

    async def _flush_loop(self):
        while not self._stopped:
            try:
                await asyncio.sleep(25)
                if self._dirty_sessions:
                    self._dirty_sessions = False
                    await self._put_sessions()
                if self._dirty_wizards:
                    self._dirty_wizards = False
                    await self._put_wizards()
            except asyncio.CancelledError:
                return
            except Exception as e:
                print("[store] flush error:", e)

    def _int_keys(self, d: dict) -> dict:
        return {str(k): v for k, v in d.items()}

    async def _put_sessions(self):
        data = crypto.encrypt_json(self._int_keys(self.sessions), config.BOT_TOKEN, config.SESSIONS_FILE)
        await self.gh.put_json(config.SESSIONS_FILE, data, message="sessions sync")

    async def _put_wizards(self):
        data = crypto.encrypt_json(self._int_keys(self.wizards), config.BOT_TOKEN, config.WIZARDS_FILE)
        await self.gh.put_json(config.WIZARDS_FILE, data, message="wizards sync")

    async def flush_all(self):
        try:
            if self._dirty_sessions:
                self._dirty_sessions = False
                await self._put_sessions()
            if self._dirty_wizards:
                self._dirty_wizards = False
                await self._put_wizards()
        except Exception as e:
            print("[store] flush_all error:", e)

    def start_flusher(self):
        self._flush_task = asyncio.create_task(self._flush_loop())

    async def stop(self):
        self._stopped = True
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        await self.flush_all()
