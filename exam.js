/* Exam runner (Telegram Mini App + web) with live timer */
"use strict";
const $ = (id) => document.getElementById(id);
const qs = new URLSearchParams(location.search);
const CODE = (qs.get("e") || qs.get("code") || "").toUpperCase().replace(/[^A-Z0-9]/g, "");

let EXAM = null;
let t0 = 0, deadline = 0, cur = 0;
let answers = {};
let timerInt = null;
let saveDeb = null;

const LS = {
  t0: (c) => "ex_" + c + "_t0",
  ans: (c) => "ex_" + c + "_ans",
  cur: (c) => "ex_" + c + "_cur",
  fin: (c) => "ex_" + c + "_fin",
};
const LETTERS = ["الف", "ب", "ج", "د", "ه", "و", "ز", "ح", "ط", "ی"];

/* ---------- Telegram WebApp ---------- */
const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
if (tg) {
  try { tg.ready(); tg.expand(); } catch (e) {}
}

function show(id) {
  ["scr-loading", "scr-error", "scr-intro", "scr-exam", "scr-sending", "scr-done"]
    .forEach((s) => $(s).classList.add("hidden"));
  $(id).classList.remove("hidden");
  window.scrollTo(0, 0);
}

function esc(s) {
  const d = document.createElement("div");
  d.textContent = s == null ? "" : String(s);
  return d.innerHTML;
}

function fmt(sec) {
  sec = Math.max(0, Math.floor(sec));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  const mm = String(m).padStart(2, "0"), ss = String(s).padStart(2, "0");
  return h > 0 ? h + ":" + mm + ":" + ss : mm + ":" + ss;
}

function b64u(str) {
  const bytes = new TextEncoder().encode(str);
  let bin = "";
  const CH = 0x8000;
  for (let i = 0; i < bytes.length; i += CH) {
    bin += String.fromCharCode.apply(null, bytes.subarray(i, i + CH));
  }
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function saveState() {
  try {
    localStorage.setItem(LS.t0(CODE), String(t0));
    localStorage.setItem(LS.ans(CODE), JSON.stringify(answers));
    localStorage.setItem(LS.cur(CODE), String(cur));
  } catch (e) {}
}

function clearState() {
  [LS.t0(CODE), LS.ans(CODE), LS.cur(CODE), LS.fin(CODE)].forEach((k) => {
    try { localStorage.removeItem(k); } catch (e) {}
  });
}

/* ---------- error screen ---------- */
function showError(title, msg) {
  $("err-title").textContent = title;
  $("err-msg").textContent = msg;
  const botUser = (EXAM && EXAM.bot_username) || "mykonkuriexam2bot";
  $("err-botlink").href = "https://t.me/" + botUser;
  $("foot-bot").textContent = "@" + botUser;
  $("foot-bot").href = "https://t.me/" + botUser;
  show("scr-error");
}

/* ---------- load ---------- */
async function load() {
  if (!/^[A-Z0-9]{4,8}$/.test(CODE)) {
    showError("کد آزمون نامعتبر است", "لینک باید به شکل exam.html?e=کد باشد. لینک را از دبیر خود بگیرید.");
    return;
  }
  try {
    const r = await fetch("exams/" + CODE + ".json", { cache: "no-store" });
    if (r.status === 404) {
      showError("آزمون پیدا نشد", "کد «" + CODE + "» وجود ندارد یا حذف شده است.");
      return;
    }
    if (!r.ok) throw new Error("HTTP " + r.status);
    EXAM = await r.json();
  } catch (e) {
    showError("خطا در دریافت آزمون", "اتصال اینترنت را بررسی کنید و صفحه را دوباره باز کنید.");
    return;
  }
  const botUser = EXAM.bot_username || "mykonkuriexam2bot";
  $("brand-bot").textContent = "@" + botUser;
  $("foot-bot").textContent = "@" + botUser;
  $("foot-bot").href = "https://t.me/" + botUser;
  show("scr-intro");
  renderIntro();
}

/* ---------- intro ---------- */
function windowInfo() {
  const w = EXAM.window || {};
  const now = Date.now() / 1000;
  if (w.start && now < w.start) return { state: "soon", until: w.start };
  if (w.end && now > w.end) return { state: "over" };
  return { state: "open", until: w.end || null };
}

function renderIntro() {
  const qs2 = EXAM.questions || [];
  $("in-title").textContent = EXAM.title || "آزمون";
  $("in-teacher").textContent = "دبیر: " + (EXAM.teacher_name || "—") + " · کد: " + EXAM.code;
  $("in-count").textContent = qs2.length + " سوال";
  $("in-dur").textContent = EXAM.duration + " دقیقه";
  const neg = EXAM.negative;
  $("in-neg").textContent =
    neg === "none" ? "ندارد" :
    neg === "third" ? "۱/۳ (کنکوری)" :
    neg === "quarter" ? "۱/۴" :
    neg === "half" ? "۱/۲" : "دارد";
  const wi = windowInfo();
  const w = EXAM.window || {};
  const dt = (ts) => new Date(ts * 1000).toLocaleString("fa-IR");
  $("in-window").textContent = wi.state === "soon" ? dt(w.start) :
    w.end ? dt(w.end) : "آزاد";

  // prefill name in mini app
  try {
    if (tg && tg.initDataUnsafe && tg.initDataUnsafe.user) {
      const u = tg.initDataUnsafe.user;
      if (!$("in-name").value) $("in-name").value = [u.first_name, u.last_name].filter(Boolean).join(" ");
    }
  } catch (e) {}

  const banner = $("in-banner");
  const startBtn = $("in-start");
  banner.classList.remove("hidden", "red", "green");
  if (wi.state === "over") {
    banner.classList.add("red");
    banner.textContent = "⛔ مهلت این آزمون به پایان رسیده است.";
    startBtn.disabled = true;
  } else if (wi.state === "soon") {
    banner.classList.add("banner");
    banner.textContent = "⏰ آزمون هنوز شروع نشده؛ تا شروع: " + fmt(wi.until - Date.now() / 1000);
    startBtn.disabled = true;
    const iv = setInterval(() => {
      const left = wi.until - Date.now() / 1000;
      if (left <= 0) { clearInterval(iv); renderIntro(); }
      else banner.textContent = "⏰ آزمون هنوز شروع نشده؛ تا شروع: " + fmt(left);
    }, 1000);
  } else {
    banner.classList.add("hidden");
    startBtn.disabled = false;
  }

  // resume / already finished
  let finFlag = null, hasSaved = false;
  try {
    finFlag = localStorage.getItem(LS.fin(CODE));
    hasSaved = !!localStorage.getItem(LS.t0(CODE));
  } catch (e) {}
  if (finFlag && hasSaved) {
    banner.classList.remove("hidden");
    banner.classList.add("green");
    banner.textContent = "✅ شما قبلاً این آزمون را تمام کرده‌اید. می‌توانید دوباره کد نتیجه را دریافت کنید.";
    startBtn.textContent = "دریافت مجدد کد نتیجه";
    startBtn.disabled = false;
    startBtn.onclick = () => { restoreSaved(); submit(); };
    return;
  }
  startBtn.textContent = "شروع آزمون ▶";
  startBtn.onclick = startExam;
}

/* ---------- exam flow ---------- */
function startExam() {
  const name = $("in-name").value.trim();
  if (name.length < 3) {
    $("in-name").focus();
    $("in-name").style.borderColor = "var(--bad)";
    return;
  }
  const savedT0 = parseInt(localStorage.getItem(LS.t0(CODE)) || "0", 10);
  const savedAns = localStorage.getItem(LS.ans(CODE));
  if (savedT0 && savedAns) {
    t0 = savedT0;
    try { answers = JSON.parse(savedAns) || {}; } catch (e) { answers = {}; }
    cur = parseInt(localStorage.getItem(LS.cur(CODE)) || "0", 10) || 0;
  } else {
    t0 = Math.floor(Date.now() / 1000);
    answers = {}; cur = 0;
    saveState();
  }
  const dur = (EXAM.duration || 60) * 60;
  const w = EXAM.window || {};
  deadline = w.end ? Math.min(t0 + dur, w.end) : t0 + dur;
  localStorage.removeItem(LS.fin(CODE));
  showExam();
}

function restoreSaved() {
  t0 = parseInt(localStorage.getItem(LS.t0(CODE)) || "0", 10);
  try { answers = JSON.parse(localStorage.getItem(LS.ans(CODE)) || "{}") || {}; } catch (e) { answers = {}; }
  cur = parseInt(localStorage.getItem(LS.cur(CODE)) || "0", 10) || 0;
}

function showExam() {
  show("scr-exam");
  renderNav();
  renderQuestion(cur);
  if (timerInt) clearInterval(timerInt);
  timerInt = setInterval(tick, 500);
  tick();
}

function countAnswered() {
  let n = 0;
  for (const k in answers) if (answers[k] !== -1 && answers[k] !== "" && answers[k] != null) n++;
  return n;
}

function tick() {
  const left = deadline - Date.now() / 1000;
  const el = $("q-timer");
  el.textContent = fmt(left);
  el.classList.toggle("warn", left <= 300 && left > 60);
  el.classList.toggle("crit", left <= 60);
  if (left <= 0) {
    clearInterval(timerInt);
    autoSubmit();
  }
}

function renderNav() {
  const g = $("q-navgrid");
  g.innerHTML = "";
  const qs2 = EXAM.questions || [];
  qs2.forEach((_, i) => {
    const c = document.createElement("div");
    c.className = "chip" + (isAnswered(i) ? " done" : "") + (i === cur ? " cur" : "");
    c.textContent = i + 1;
    c.onclick = () => { cur = i; renderQuestion(i); renderNav(); };
    g.appendChild(c);
  });
}

function isAnswered(i) {
  const v = answers[i];
  return v !== undefined && v !== -1 && v !== "" && v != null;
}

function renderQuestion(i) {
  const qs2 = EXAM.questions || [];
  cur = Math.max(0, Math.min(i, qs2.length - 1));
  const q = qs2[cur];
  const left = Math.max(0, deadline - Date.now() / 1000);
  $("q-progress").textContent = "سوال " + (cur + 1) + " از " + qs2.length +
    " | پاسخ‌داده: " + countAnswered();
  $("q-bar").style.width = (countAnswered() / qs2.length * 100) + "%";
  $("q-timer").textContent = fmt(left);

  $("q-text").innerHTML = esc(q.t);

  const optsEl = $("q-opts");
  const openEl = $("q-openbox");
  optsEl.innerHTML = "";
  if (q.o && q.o.length) {
    openEl.classList.add("hidden");
    q.o.forEach((opt, oi) => {
      const d = document.createElement("div");
      d.className = "opt" + (answers[cur] === oi ? " sel" : "");
      d.innerHTML = "<b>" + LETTERS[oi] + "</b><span>" + esc(opt) + "</span>";
      d.onclick = () => {
        answers[cur] = oi;
        saveState();
        renderNav();
        optsEl.querySelectorAll(".opt").forEach((x, j) => x.classList.toggle("sel", answers[cur] === j));
        setTimeout(() => { if (cur < qs2.length - 1) gotoQ(cur + 1); }, 220);
      };
      optsEl.appendChild(d);
    });
  } else {
    openEl.classList.remove("hidden");
    const inp = $("q-open");
    inp.value = typeof answers[cur] === "string" ? answers[cur] : "";
    inp.oninput = () => {
      answers[cur] = inp.value;
      clearTimeout(saveDeb);
      saveDeb = setTimeout(saveState, 400);
      $("q-bar").style.width = (countAnswered() / qs2.length * 100) + "%";
    };
  }
  renderNav();
}

function gotoQ(i) {
  cur = Math.max(0, Math.min(i, EXAM.questions.length - 1));
  saveState();
  renderQuestion(cur);
}

$("q-prev").onclick = () => gotoQ(cur - 1);
$("q-next").onclick = () => gotoQ(cur + 1);

$("q-finish").onclick = () => {
  const total = EXAM.questions.length;
  const un = total - countAnswered();
  $("m-msg").textContent = un > 0
    ? un + " سوال بی‌پاسخ داری. سوالات بی‌پاسخ نمره منفی نمی‌گیرند."
    : "همه‌ی سوالات پاسخ داده شده‌اند.";
  $("modal").classList.remove("hidden");
};
$("m-no").onclick = () => $("modal").classList.add("hidden");
$("m-yes").onclick = () => { $("modal").classList.add("hidden"); submit(); };

function autoSubmit() {
  $("modal").classList.add("hidden");
  submit();
}

function buildPayload() {
  const name = ($("in-name").value || "").trim();
  const sid = ($("in-sid").value || "").trim();
  return {
    v: 1,
    e: CODE,
    n: name || "بی‌نام",
    s: sid,
    a: answers,
    t: t0,
    f: Math.floor(Date.now() / 1000),
  };
}

function submit() {
  const payload = buildPayload();
  try { localStorage.setItem(LS.fin(CODE), "1"); saveState(); } catch (e) {}
  const canMiniApp = tg && typeof tg.sendData === "function" && tg.initData && tg.initData.length > 0;
  if (canMiniApp) {
    show("scr-sending");
    try {
      tg.sendData(JSON.stringify(payload));
      // Telegram closes the app after sendData; screen stays as fallback
      setTimeout(() => { showDone(payload); }, 2500);
    } catch (e) {
      showDone(payload);
    }
  } else {
    showDone(payload);
  }
}

function showDone(payload) {
  const code = "EXM1." + b64u(JSON.stringify(payload));
  $("dn-code").textContent = code;
  const botUser = EXAM.bot_username || "mykonkuriexam2bot";
  $("dn-bot").href = "https://t.me/" + botUser;
  $("dn-copy").onclick = async () => {
    try {
      await navigator.clipboard.writeText(code);
      $("dn-copy").textContent = "✅ کپی شد!";
    } catch (e) {
      const ta = document.createElement("textarea");
      ta.value = code;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
      $("dn-copy").textContent = "✅ کپی شد!";
    }
    setTimeout(() => ($("dn-copy").textContent = "📋 کپی کد نتیجه"), 2500);
  };
  show("scr-done");
}

load();
