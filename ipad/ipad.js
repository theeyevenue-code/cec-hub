/* iPad check-in form - plain JS, no build step, no links into the staff Hub.

   Every question is rendered GENERICALLY from the engine's questions.json
   (screens, types, options, audience, show_if, hints, required, skippable).
   No question wording lives in this file: Mark changes wording by editing
   questions.json only.

   Nothing is ever stored on the iPad: no localStorage / sessionStorage /
   cookies. The form lives in memory (`S`) and is wiped when it is sent, when
   reception cancels it, and on return to the idle screen. The device key
   comes from this page's URL fragment (#<key>) and travels in a header.

   Inactivity lock (Codex #4, 9 Oct 2026): no touch for WARN_MS -> "Still
   there?" with Continue; LOCK_MS -> the answers so far go to the server as a
   draft, the iPad is wiped and says "Please return the iPad to reception".
   Only reception can resume it (Hub: Resume on the iPad). The answers so far
   are also sent as a draft at every screen change, so a lock while the Wi-Fi
   is down loses at most one screen.

   No signature (Mark, 9 Oct 2026: the paper form never had one): the last
   screen is "Check and send" - the notice, the consent sentence, a big Send.

   No links: this page never links anywhere (the privacy policy address on
   the Start screen is plain text). */

"use strict";

(function () {
    const KEY = decodeURIComponent((location.hash || "").replace(/^#/, "")).trim();
    const POLL_MS = 3000;
    const THANKS_MS = 8000;
    const WARN_MS = 3 * 60 * 1000;          // no touch for 3 minutes: "Still there?"
    const LOCK_MS = WARN_MS + 2 * 60 * 1000; // 2 more minutes: hide it, back to reception
    const MONTHS = ["January", "February", "March", "April", "May", "June", "July",
                    "August", "September", "October", "November", "December"];

    const app = document.getElementById("app");
    const bar = document.getElementById("bar");

    let S = null;          // the one form in memory
    let mode = "boot";     // boot | setup | idle | welcome | form | sending | failed | thanks | paused
    let thanksTimer = null;
    let lastTouch = Date.now();
    let pausePending = null;   // token whose lock has not reached the server yet
    const warnEl = document.getElementById("still");

    function esc(s) {
        return String(s == null ? "" : s)
            .replace(/&/g, "&amp;").replace(/</g, "&lt;")
            .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
    }

    async function api(path, body) {
        const res = await fetch("/api/checkin/ipad" + path, {
            method: body ? "POST" : "GET",
            headers: { "X-Checkin-Key": KEY, "Content-Type": "application/json" },
            body: body ? JSON.stringify(body) : undefined,
            cache: "no-store",
            credentials: "omit",
        });
        let data = {};
        try { data = await res.json(); } catch (e) { /* empty body */ }
        return { status: res.status, data };
    }

    /* --- whole-page states ------------------------------------------------ */

    function setBar(html) {
        bar.innerHTML = html ? `<div class="bar-in">${html}</div>` : "";
        bar.hidden = !html;
    }

    function wipe() {
        S = null;
        if (thanksTimer) { clearTimeout(thanksTimer); thanksTimer = null; }
        hideWarning();
        app.innerHTML = "";
        setBar("");
    }

    function renderSetup() {
        wipe();
        mode = "setup";
        app.innerHTML = `<div class="center"><p class="big">Not set up yet</p>
            <p class="mid">Please see reception.</p></div>`;
    }

    function renderIdle() {
        wipe();
        mode = "idle";
        app.innerHTML = `<div class="center"><p class="big">Concord Eyecare</p>
            <p class="mid">Please see reception.</p></div>`;
    }

    function renderWelcome() {
        mode = "welcome";
        const name = S.name;
        const back = S.resumed ? " back" : "";
        const lines = S.audience === "child"
            // A child's form: greet the parent neutrally, name the child.
            ? `<p class="big">Welcome${back}.</p>
               <p class="mid">This form is for ${name ? esc(name) : "your child"}.</p>`
            : `<p class="big">Welcome${back}${name ? ", " + esc(name) : ""}.</p>`;
        // Collection notice at Start (Codex #10): questions.json "welcome", plain
        // text only - the policy address is NOT a link (this page never links out).
        const w = S.welcome || {};
        const notice = w.notice ? `<div class="notice"><p>${esc(w.notice)}</p>
            ${w.policy ? `<p>${esc(w.policy)}</p>` : ""}</div>` : "";
        app.innerHTML = `<div class="center">${lines}
            <p class="mid">${S.resumed ? "Tap Start to carry on." : "Tap Start to begin."}</p>
            <button class="btn primary" data-act="start">Start</button>${notice}</div>`;
        setBar("");
        window.scrollTo(0, 0);
    }

    function renderSending() {
        mode = "sending";
        app.innerHTML = `<div class="center"><p class="big">Sending...</p></div>`;
        setBar("");
    }

    function renderFailed() {
        mode = "failed";
        app.innerHTML = `<div class="center"><p class="big">Could not send.</p>
            <button class="btn primary" data-act="retry">Tap to try again</button>
            <button class="btn quiet" data-act="back-to-form">Back to the form</button></div>`;
        setBar("");
    }

    /* "already" = a retry of a form the server had already received (the
       first answer was lost on the way back): same thanks, never "cancelled". */
    function renderThanks(already) {
        mode = "thanks";
        hideWarning();
        app.innerHTML = `<div class="center"><p class="big">${already ? "Thank you, received." : "Thank you."}</p>
            <p class="mid">Please hand the iPad back to reception.</p></div>`;
        setBar("");
        window.scrollTo(0, 0);
        thanksTimer = setTimeout(renderIdle, THANKS_MS);
    }

    /* --- inactivity lock ------------------------------------------------------ */

    function renderPaused() {
        wipe();
        mode = "paused";
        app.innerHTML = `<div class="center"><p class="big">Please return the iPad to reception.</p>
            <p class="mid">Your answers are kept. Reception will carry on from here.</p></div>`;
        window.scrollTo(0, 0);
    }

    function showWarning() {
        if (!warnEl || !warnEl.hidden) return;
        warnEl.innerHTML = `<div class="still-box" role="alertdialog" aria-label="Still there?">
            <p class="big">Still there?</p>
            <p class="mid">Tap Continue to keep going.</p>
            <button class="btn primary" data-act="continue">Continue</button></div>`;
        warnEl.hidden = false;
    }

    function hideWarning() {
        if (!warnEl) return;
        warnEl.hidden = true;
        warnEl.innerHTML = "";
    }

    function touched() {
        lastTouch = Date.now();
        if (warnEl && !warnEl.hidden) hideWarning();
    }

    ["pointerdown", "keydown", "input", "touchstart"].forEach((t) =>
        document.addEventListener(t, touched, { capture: true, passive: true }));

    /* The answers so far, as the server keeps them: real question ids only,
       (no "Other" flags). */
    function draftBody() {
        const index = allQuestions();
        const answers = {};
        Object.keys(S.answers).forEach((k) => {
            const v = S.answers[k];
            if (!index[k] || index[k].type === "info") return;
            if (Array.isArray(v) ? v.length : (v && String(v).trim())) answers[k] = v;
        });
        return { answers: answers, skipped_screens: Array.from(S.skipped), idx: S.idx };
    }

    function saveDraft() {
        if (!S) return;
        api(`/session/${encodeURIComponent(S.token)}/draft`, draftBody()).catch(() => {});
    }

    async function sendPause(token, body) {
        try {
            const r = await api(`/session/${encodeURIComponent(token)}/draft`,
                                Object.assign({ pause: true }, body || {}));
            if (r.status === 200 || r.status === 409) pausePending = null;
        } catch (e) { /* offline: poll() tries again */ }
    }

    function lock() {
        const token = S.token;
        const body = draftBody();
        pausePending = token;
        renderPaused();               // wipe the iPad first, then tell the server
        sendPause(token, body);
    }

    setInterval(() => {
        if (!S || !["welcome", "form", "failed"].includes(mode)) return;
        const idle = Date.now() - lastTouch;
        if (idle >= LOCK_MS) lock();
        else if (idle >= WARN_MS) showWarning();
    }, 1000);

    /* --- polling ------------------------------------------------------------ */

    async function poll() {
        if (mode === "setup" || mode === "thanks" || mode === "sending" || mode === "boot") return;
        let r;
        try { r = await api("/current"); } catch (e) { return; }   // offline: keep everything
        if (r.status === 401 || r.status === 503) { renderSetup(); return; }
        if (r.status !== 200) return;
        const cur = r.data || {};
        if (mode === "paused") {
            if (pausePending) {
                // The lock never reached the server: say it again (no answers -
                // the server keeps the last draft). Never reopen the form here.
                if (cur.token === pausePending) sendPause(pausePending);
                else pausePending = null;
                return;
            }
            if (cur.state === "paused") return;          // waiting for reception
            if (cur.token) { await start(cur.token); return; }   // reception resumed it
            renderIdle();
            return;
        }
        if (S) {
            // Reception replaced or cancelled this form: back to idle, wipe.
            // (While "Could not send" is showing, the retry itself finds out.)
            if (mode !== "failed" && cur.token !== S.token) {
                if (cur.state === "paused") renderPaused(); else renderIdle();
            }
            return;
        }
        if (mode === "idle" && cur.state === "paused") { renderPaused(); return; }
        if (mode === "idle" && cur.token) await start(cur.token);
    }

    async function start(token) {
        let r;
        try { r = await api("/session/" + encodeURIComponent(token)); } catch (e) { return; }
        if (r.status !== 200 || !r.data || !r.data.questions || !["idle", "paused"].includes(mode)) return;
        const d = r.data;
        S = {
            token: token,
            audience: d.audience,
            name: d.name || "",
            prefill: d.prefill || {},
            screens: (d.questions.screens || []).filter((sc) => (sc.questions || []).length),
            lists: d.lists || {},
            answers: {},
            dates: {},          // partly typed dates: {qid: {d, m, y}}
            skipped: new Set(),
            idx: -1,
            err: "",
            welcome: d.questions.welcome || null,
            resumed: false,
        };
        if (!S.screens.length) { S = null; return; }
        if (d.draft && d.draft.answers) {               // reception resumed a locked form
            S.answers = Object.assign({}, d.draft.answers);
            (d.draft.skipped || []).forEach((x) => S.skipped.add(x));
            S.resumeAt = Math.max(0, Math.min(Number(d.draft.idx) || 0, S.screens.length - 1));
            S.resumed = true;
        }
        lastTouch = Date.now();
        renderWelcome();
    }

    /* --- questions ------------------------------------------------------------- */

    function allQuestions() {
        const out = {};
        S.screens.forEach((sc) => (sc.questions || []).forEach((q) => { out[q.id] = q; }));
        return out;
    }

    function isShown(q, index) {
        const cond = q.show_if;
        if (!cond) return true;
        return Object.keys(cond).every((ref) => {
            if (index[ref] && !isShown(index[ref], index)) return false;
            const want = Array.isArray(cond[ref]) ? cond[ref] : [cond[ref]];
            const got = S.answers[ref];
            const gots = Array.isArray(got) ? got : (got ? [got] : []);
            return gots.some((g) => want.includes(g));
        });
    }

    function controlsShowIf(qid) {
        return S.screens.some((sc) => (sc.questions || []).some((q) =>
            q.show_if && Object.prototype.hasOwnProperty.call(q.show_if, qid)));
    }

    function labelOf(q) { return q.label || ""; }

    /* Fill an unanswered question from the session prefill: on a screen marked
       "prefilled" (the details screen) from what Optomate holds for q.field; on
       any screen from q.prefill_from (e.g. the online booking's reason).
       prefill_from: false = never seed (How heard must be the patient's tap). */
    function prefillKey(sc, q) {
        if (q.prefill_from === false) return null;
        if (q.prefill_from) return q.prefill_from;
        return sc.prefilled && q.field ? q.field : null;
    }

    function seedPrefill(sc) {
        (sc.questions || []).forEach((q) => {
            if (Object.prototype.hasOwnProperty.call(S.answers, q.id)) return;
            const key = prefillKey(sc, q);
            const v = key ? S.prefill[key] : undefined;
            if (v === undefined || v === null || v === "") return;
            if (q.type === "choice" && !q.allow_other && !(q.options || []).includes(v)) return;
            if (q.type === "yesno" && v !== "Yes" && v !== "No") return;
            S.answers[q.id] = String(v);
        });
    }

    function dateParts(q) {
        if (S.dates[q.id]) return S.dates[q.id];
        const m = String(S.answers[q.id] || "").match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);
        return m ? { d: String(Number(m[1])), m: String(Number(m[2])), y: m[3] } : { d: "", m: "", y: "" };
    }

    function setDate(qid, parts) {
        S.dates[qid] = parts;
        const d = parseInt(parts.d, 10), m = parseInt(parts.m, 10), y = parseInt(parts.y, 10);
        if (d && m && /^\d{4}$/.test(parts.y)) {
            S.answers[qid] = `${String(d).padStart(2, "0")}/${String(m).padStart(2, "0")}/${y}`;
        } else {
            delete S.answers[qid];
        }
    }

    function dateProblem(q) {
        const p = S.dates[q.id];
        if (!p || (!p.d && !p.m && !p.y)) return "";
        const d = parseInt(p.d, 10), m = parseInt(p.m, 10), y = parseInt(p.y, 10);
        if (!d || !m || !/^\d{4}$/.test(p.y)) return "Please finish the date: " + labelOf(q);
        const dt = new Date(y, m - 1, d);
        if (dt.getMonth() !== m - 1 || dt.getDate() !== d || y < 1900 || dt > new Date()) {
            return "Please check the date: " + labelOf(q);
        }
        return "";
    }

    function inputAttrs(q) {
        const mode = q.inputmode || (q.type === "number" ? "decimal" : "");
        const a = [];
        if (mode) a.push(`inputmode="${esc(mode)}"`);
        if (mode === "email") a.push(`autocapitalize="off"`);
        else if (mode === "numeric" || mode === "tel" || mode === "decimal") a.push(`autocapitalize="off"`);
        else a.push(`autocapitalize="sentences"`);
        a.push(`autocomplete="off" autocorrect="off" spellcheck="false"`);
        a.push(`aria-label="${esc(labelOf(q))}"`);
        return a.join(" ");
    }

    /* v2 layout (Mark, 5 Oct 2026): every question is a BAND - the question on
       the left (with "Tap one" / "Tap all that apply"), the answer on the right.
       Tap answers are EQUAL tiles in straight columns (4 across in landscape, 2
       in portrait): a square = tap several, a circle = tap one, the None tile is
       dashed. A follow-up opens INLINE under its question's tiles. */

    function hintFor(q) {
        if (q.type === "multi") return "Tap all that apply";
        if (q.type === "choice" || q.type === "yesno") return "Tap one";
        return "";
    }

    /* none_value: one tile, or a list ("None" / "Never worn glasses"). */
    function nonesOf(q) {
        return [].concat(q.none_value || []);
    }

    function tile(q, o, on, multi) {
        const none = multi && nonesOf(q).includes(o);
        return `<button type="button" class="t${multi ? "" : " r"}${on ? " on" : ""}${none ? " none" : ""}"
            data-${multi ? "tick" : "pick"}="${esc(q.id)}" data-v="${esc(o)}" aria-pressed="${on}"><i class="g" aria-hidden="true"></i><span>${esc(o)}</span></button>`;
    }

    /* The answer side of a question: tiles, a typed box, a date or a pick list. */
    function controlHTML(q) {
        const v = S.answers[q.id];
        const id = "q-" + q.id;
        switch (q.type) {
            case "text":
            case "number":
                return `<input id="${id}" type="text" data-q="${esc(q.id)}"
                    value="${esc(v || "")}" ${inputAttrs(q)}${q.type === "number" ? ` class="short"` : ""}>`;
            case "longtext":
                return `${noneTile(q, v)}<textarea id="${id}" data-q="${esc(q.id)}"
                    ${inputAttrs(q)}>${esc(v || "")}</textarea>`;
            case "date": {
                const p = dateParts(q);
                const opts = MONTHS.map((name, i) =>
                    `<option value="${i + 1}"${String(i + 1) === p.m ? " selected" : ""}>${name}</option>`).join("");
                return `<div class="date">
                    <div><input id="${id}" type="text" inputmode="numeric" maxlength="2" data-date="${esc(q.id)}"
                        data-part="d" value="${esc(p.d)}" autocomplete="off" aria-label="Day"><div class="cap">Day</div></div>
                    <div><select data-date="${esc(q.id)}" data-part="m" aria-label="Month">
                        <option value=""${p.m ? "" : " selected"}>Month</option>${opts}</select>
                        <div class="cap">Month</div></div>
                    <div><input type="text" inputmode="numeric" maxlength="4" data-date="${esc(q.id)}"
                        data-part="y" value="${esc(p.y)}" autocomplete="off" aria-label="Year"><div class="cap">Year</div></div>
                    </div>`;
            }
            case "yesno":
                return `<div class="grid">${["Yes", "No"].map((o) => tile(q, o, v === o, false)).join("")}</div>`;
            case "choice": {
                const opts = q.options || [];
                const other = q.allow_other && v && !opts.includes(v);
                const otherOn = other || (q.allow_other && S.answers["__other_" + q.id]);
                return `<div class="grid">${opts.map((o) => tile(q, o, v === o, false)).join("")}
                    ${q.allow_other ? `<button type="button" class="t r${otherOn ? " on" : ""}"
                        data-other="${esc(q.id)}"><i class="g" aria-hidden="true"></i><span>Other</span></button>` : ""}</div>
                    ${otherOn ? `<input class="other-input" type="text" data-q="${esc(q.id)}"
                        value="${esc(other ? v : "")}" aria-label="Other" ${inputAttrs(q)}>` : ""}`;
            }
            case "multi": {
                const have = Array.isArray(v) ? v : [];
                return `<div class="grid">${(q.options || []).map((o) => tile(q, o, have.includes(o), true)).join("")}</div>`;
            }
            case "picklist":
                return `<input id="${id}" type="text" data-q="${esc(q.id)}"
                    data-list="${esc(q.list || "")}" value="${esc(v || "")}" ${inputAttrs(q)}
                    placeholder="Start typing"><div class="picks" id="picks-${esc(q.id)}"></div>`;
            default:
                return "";
        }
    }

    /* A one-tap answer on a typed question (questions.json none_option, e.g.
       Medications: "None"). Tapping it fills the box; tapping again clears it. */
    function noneTile(q, v) {
        if (!q.none_option) return "";
        const on = v === q.none_option;
        return `<div class="grid none-row"><button type="button" class="t none${on ? " on" : ""}"
            data-pick="${esc(q.id)}" data-v="${esc(q.none_option)}" aria-pressed="${on}"><i class="g" aria-hidden="true"></i><span>${esc(q.none_option)}</span></button></div>`;
    }

    function typed(q) {
        return ["text", "number", "longtext", "picklist"].includes(q.type);
    }

    /* One question as a band, with its revealed follow-ups under the tiles. */
    function bandHTML(q, follows, title) {
        if (q.type === "info") {
            return `<div class="info${q.style === "small" ? " small" : ""}">${esc(q.text || "")}</div>`;
        }
        const req = q.required ? ` <span class="req">(needed)</span>` : "";
        // A question worded the same as its screen title is not said twice.
        const same = labelOf(q).trim().toLowerCase() === String(title || "").trim().toLowerCase();
        const hint = hintFor(q);
        const text = (same ? "" : esc(labelOf(q))) + req;
        const lab = typed(q) ? `<label class="lab" for="q-${esc(q.id)}">${text}</label>`
            : `<div class="lab">${text}${hint ? `<span class="hint">${hint}</span>` : ""}</div>`;
        const reveals = follows.map((f) => `<div class="reveal" data-fu="${esc(f.id)}">
            ${typed(f) ? `<label class="cap" for="q-${esc(f.id)}">${esc(labelOf(f))}</label>`
                       : `<div class="cap">${esc(labelOf(f))}</div>`}${controlHTML(f)}</div>`).join("");
        return `<section class="band" id="b-${esc(q.id)}">${lab}<div class="body">${controlHTML(q)}${reveals}</div></section>`;
    }

    /* follow-up id -> the question it hangs off (one show_if key, same screen) */
    function parentOf(q, sc) {
        if (!q.follow_up) return null;
        const keys = Object.keys(q.show_if || {});
        if (keys.length !== 1) return null;
        return (sc.questions || []).some((x) => x.id === keys[0]) ? keys[0] : null;
    }

    function shownFollowUps() {
        return Array.from(app.querySelectorAll(".reveal")).map((e) => e.dataset.fu);
    }

    function renderScreen(keepScroll) {
        mode = "form";
        const sc = S.screens[S.idx];
        seedPrefill(sc);
        const index = allQuestions();
        const total = S.screens.length;
        const last = S.idx === total - 1;
        let html = `<div class="progress">${S.idx + 1} of ${total}</div>
            <h1>${esc(sc.title)}</h1>
            ${sc.skip_button ? `<button type="button" class="skipsmall" data-act="skip">${esc(sc.skip_button)}</button>` : ""}
            ${sc.hint ? `<p class="hint">${esc(sc.hint)}</p>` : ""}`;
        const follows = {};
        (sc.questions || []).forEach((q) => {
            const p = parentOf(q, sc);
            if (p && isShown(q, index)) (follows[p] = follows[p] || []).push(q);
        });
        let group = null;
        (sc.questions || []).forEach((q) => {
            if (parentOf(q, sc) || !isShown(q, index)) return;
            if (q.group && q.group !== group) html += `<h2 class="group">${esc(q.group)}</h2>`;
            group = q.group || null;
            html += bandHTML(q, follows[q.id] || [], sc.title);
        });
        if (S.err) html += `<div class="err" role="alert">${esc(S.err)}</div>`;
        const y = window.scrollY;
        app.innerHTML = html;
        setBar(`<button class="btn" data-act="back">Back</button>
            <span class="grow"></span>
            ${sc.skippable ? `<button class="btn quiet" data-act="skip">Skip</button>` : ""}
            <button class="btn primary${last ? " send" : ""}" data-act="next">${last ? "Send" : "Next"}</button>`);
        roomForBar();
        window.scrollTo(0, keepScroll ? y : 0);
    }

    /* After a tap opened a follow-up: keep it in view above the Back/Next bar. */
    function revealNew(before) {
        const fresh = Array.from(app.querySelectorAll(".reveal"))
            .filter((e) => !before.includes(e.dataset.fu));
        if (!fresh.length) return;
        const lastEl = fresh[fresh.length - 1];
        const barH = bar.hidden ? 0 : bar.getBoundingClientRect().height;
        const over = lastEl.getBoundingClientRect().bottom - (window.innerHeight - barH - 16);
        if (over > 0) window.scrollBy(0, over);
    }

    /* --- moving between screens ------------------------------------------------- */

    function screenQuestionsShown(sc) {
        const index = allQuestions();
        return (sc.questions || []).filter((q) => isShown(q, index));
    }

    function problemOn(sc) {
        for (const q of screenQuestionsShown(sc)) {
            if (q.type === "date") {
                const p = dateProblem(q);
                if (p) return p;
            }
            if (!q.required) continue;
            if (q.type !== "info") {
                const v = S.answers[q.id];
                if (!v || (Array.isArray(v) && !v.length) || !String(v).trim()) {
                    return "Please fill in: " + labelOf(q);
                }
            }
        }
        return "";
    }

    function go(delta) {
        S.err = "";
        S.idx += delta;
        if (S.idx < 0) { S.idx = -1; renderWelcome(); saveDraft(); return; }
        renderScreen(false);
        saveDraft();
    }

    /* Long pages: always leave room to scroll the last question clear of the
       fixed Back / Next bar, however tall the bar wraps (portrait, big text). */
    function roomForBar() {
        const h = bar.hidden ? 0 : bar.getBoundingClientRect().height;
        app.style.paddingBottom = Math.round(h + 48) + "px";
        document.documentElement.style.scrollPaddingBottom = Math.round(h + 16) + "px";
    }

    function next() {
        const sc = S.screens[S.idx];
        const p = problemOn(sc);
        if (p) { S.err = p; renderScreen(true); scrollToErr(); return; }
        S.skipped.delete(sc.id);
        if (S.idx === S.screens.length - 1) { submit(); return; }
        go(1);
    }

    function skip() {
        const sc = S.screens[S.idx];
        // A skipped screen sends nothing: its answers are dropped.
        (sc.questions || []).forEach((q) => { delete S.answers[q.id]; delete S.dates[q.id]; });
        S.skipped.add(sc.id);
        go(1);
    }

    function scrollToErr() {
        const e = app.querySelector(".err");
        if (e) e.scrollIntoView({ block: "center" });
    }

    /* Only what the patient was actually shown, on screens not skipped. */
    function finalAnswers() {
        const index = allQuestions();
        const out = {};
        S.screens.forEach((sc) => {
            if (S.skipped.has(sc.id)) return;
            (sc.questions || []).forEach((q) => {
                if (q.type === "info") return;
                if (!isShown(q, index)) return;
                const v = S.answers[q.id];
                if (Array.isArray(v) ? v.length : (v && String(v).trim())) out[q.id] = v;
            });
        });
        return out;
    }

    async function submit() {
        renderSending();
        let r;
        try {
            r = await api(`/session/${encodeURIComponent(S.token)}/submit`, {
                answers: finalAnswers(),
                skipped_screens: Array.from(S.skipped),
            });
        } catch (e) {
            renderFailed();      // Wi-Fi dropped: everything is still in memory
            return;
        }
        if (r.status === 200 && r.data.ok) { S = null; renderThanks(!!r.data.already); return; }
        if (r.status === 409 && r.data.gone) { renderIdle(); return; }   // cancelled at reception
        if (r.status === 400 && r.data.error) {
            const at = r.data.field
                ? S.screens.findIndex((sc) => (sc.questions || []).some((q) => q.id === r.data.field))
                : -1;
            S.idx = at >= 0 ? at : S.screens.length - 1;
            S.err = r.data.error;
            renderScreen(false);
            scrollToErr();
            return;
        }
        renderFailed();
    }

    /* --- events (delegated, so re-rendering never loses a handler) --------------- */

    document.addEventListener("click", (ev) => {
        const t = ev.target.closest("button");
        if (!t || !S && t.dataset.act !== "retry") return;
        const act = t.dataset.act;
        if (act === "continue") {
            touched();
        } else if (act === "start") {
            api(`/session/${encodeURIComponent(S.token)}/filling`, {}).catch(() => {});
            S.idx = S.resumed ? S.resumeAt : 0;
            renderScreen(false);
        } else if (act === "back") {
            go(-1);
        } else if (act === "next") {
            next();
        } else if (act === "skip") {
            skip();
        } else if (act === "retry") {
            if (S) submit();
        } else if (act === "back-to-form") {
            renderScreen(false);
        } else if (t.dataset.pick) {
            const qid = t.dataset.pick, v = t.dataset.v;
            S.answers[qid] = S.answers[qid] === v ? "" : v;      // tap again to clear
            delete S.answers["__other_" + qid];
            S.err = "";
            const before = shownFollowUps();
            renderScreen(true);
            revealNew(before);
        } else if (t.dataset.other) {
            const qid = t.dataset.other;
            const q = allQuestions()[qid];
            const was = S.answers["__other_" + qid] || (S.answers[qid] && !(q.options || []).includes(S.answers[qid]));
            if (was) { delete S.answers["__other_" + qid]; S.answers[qid] = ""; }
            else {
                S.answers["__other_" + qid] = "1";
                if ((q.options || []).includes(S.answers[qid])) S.answers[qid] = "";
            }
            renderScreen(true);
            const inp = app.querySelector(`input.other-input[data-q="${qid}"]`);
            if (inp) inp.focus();
        } else if (t.dataset.tick) {
            const qid = t.dataset.tick, v = t.dataset.v;
            const q = allQuestions()[qid] || {};
            const nones = nonesOf(q);
            let have = Array.isArray(S.answers[qid]) ? S.answers[qid].slice() : [];
            if (have.includes(v)) have = have.filter((x) => x !== v);
            else if (nones.includes(v)) have = [v];                          // None clears the others
            else have = have.filter((x) => !nones.includes(x)).concat([v]); // and any other clears None
            // Kept in the order the tiles are shown, so the notes read the same way.
            S.answers[qid] = (q.options || []).filter((o) => have.includes(o));
            S.err = "";
            const before = shownFollowUps();
            renderScreen(true);
            revealNew(before);
        } else if (t.dataset.choose) {
            const qid = t.dataset.choose;
            S.answers[qid] = t.dataset.v;
            const inp = app.querySelector(`input[data-q="${qid}"]`);
            if (inp) inp.value = t.dataset.v;
            const box = document.getElementById("picks-" + qid);
            if (box) box.innerHTML = "";
        }
    });

    document.addEventListener("input", (ev) => {
        const el = ev.target;
        if (!S) return;
        if (el.dataset.q) {
            S.answers[el.dataset.q] = el.value;
            if (el.dataset.list) showPicks(el);
        } else if (el.dataset.date) {
            const qid = el.dataset.date;
            const parts = Object.assign({}, dateParts({ id: qid }));
            parts[el.dataset.part] = el.value.replace(/\D/g, "");
            if (el.dataset.part !== "m" && el.value !== parts[el.dataset.part]) el.value = parts[el.dataset.part];
            setDate(qid, parts);
        }
    });

    document.addEventListener("change", (ev) => {
        const el = ev.target;
        if (!S || mode !== "form") return;
        if (el.dataset.date && el.dataset.part === "m") {
            const qid = el.dataset.date;
            const parts = Object.assign({}, dateParts({ id: qid }), { m: el.value });
            setDate(qid, parts);
        } else if (el.dataset.q && controlsShowIf(el.dataset.q)) {
            renderScreen(true);
        }
    });

    /* type-to-filter pick list (e.g. occupation): up to 8 matches as buttons */
    function showPicks(el) {
        const qid = el.dataset.q;
        const box = document.getElementById("picks-" + qid);
        if (!box) return;
        const q = el.value.trim().toLowerCase();
        const list = S.lists[el.dataset.list] || [];
        if (q.length < 1) { box.innerHTML = ""; return; }
        const starts = list.filter((n) => n.toLowerCase().startsWith(q));
        const has = list.filter((n) => !n.toLowerCase().startsWith(q) && n.toLowerCase().includes(q));
        const hits = starts.concat(has).slice(0, 8);
        box.innerHTML = hits.map((n) => `<button type="button" class="opt" data-choose="${esc(qid)}"
            data-v="${esc(n)}">${esc(n)}</button>`).join("");
    }

    /* --- go ------------------------------------------------------------------- */

    if (!KEY) {
        renderSetup();
    } else {
        renderIdle();
        poll();
        setInterval(poll, POLL_MS);
    }
})();
