/* iPad check-in form - plain JS, no build step, no links into the staff Hub.

   Every question is rendered GENERICALLY from the engine's questions.json
   (screens, types, options, audience, show_if, hints, required, skippable).
   No question wording lives in this file: Mark changes wording by editing
   questions.json only.

   Nothing is ever stored on the iPad: no localStorage / sessionStorage /
   cookies. The form lives in memory (`S`) and is wiped when it is sent, when
   reception cancels it, and on return to the idle screen. The device key
   comes from this page's URL fragment (#<key>) and travels in a header. */

"use strict";

(function () {
    const KEY = decodeURIComponent((location.hash || "").replace(/^#/, "")).trim();
    const POLL_MS = 3000;
    const THANKS_MS = 8000;
    const MONTHS = ["January", "February", "March", "April", "May", "June", "July",
                    "August", "September", "October", "November", "December"];

    const app = document.getElementById("app");
    const bar = document.getElementById("bar");

    let S = null;          // the one form in memory
    let mode = "boot";     // boot | setup | idle | welcome | form | sending | failed | thanks
    let thanksTimer = null;

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
        const lines = S.audience === "child"
            // A child's form: greet the parent neutrally, name the child.
            ? `<p class="big">Welcome.</p>
               <p class="mid">This form is for ${name ? esc(name) : "your child"}.</p>`
            : `<p class="big">Welcome${name ? ", " + esc(name) : ""}.</p>`;
        app.innerHTML = `<div class="center">${lines}
            <p class="mid">Tap Start to begin.</p>
            <button class="btn primary" data-act="start">Start</button></div>`;
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

    function renderThanks() {
        mode = "thanks";
        app.innerHTML = `<div class="center"><p class="big">Thank you.</p>
            <p class="mid">Please hand the iPad back to reception.</p></div>`;
        setBar("");
        window.scrollTo(0, 0);
        thanksTimer = setTimeout(renderIdle, THANKS_MS);
    }

    /* --- polling ------------------------------------------------------------ */

    async function poll() {
        if (mode === "setup" || mode === "thanks" || mode === "sending" || mode === "boot") return;
        let r;
        try { r = await api("/current"); } catch (e) { return; }   // offline: keep everything
        if (r.status === 401 || r.status === 503) { renderSetup(); return; }
        if (r.status !== 200) return;
        const cur = r.data || {};
        if (S) {
            // Reception replaced or cancelled this form: back to idle, wipe.
            // (While "Could not send" is showing, the retry itself finds out.)
            if (mode !== "failed" && cur.token !== S.token) renderIdle();
            return;
        }
        if (mode === "idle" && cur.token) await start(cur.token);
    }

    async function start(token) {
        let r;
        try { r = await api("/session/" + encodeURIComponent(token)); } catch (e) { return; }
        if (r.status !== 200 || !r.data || !r.data.questions || mode !== "idle") return;
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
            sig: "",
            err: "",
        };
        if (!S.screens.length) { S = null; return; }
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

    /* Fill an unanswered question from what Optomate already holds, only on a
       screen marked "prefilled" (the details screen). */
    function seedPrefill(sc) {
        if (!sc.prefilled) return;
        (sc.questions || []).forEach((q) => {
            if (Object.prototype.hasOwnProperty.call(S.answers, q.id)) return;
            const v = q.field ? S.prefill[q.field] : undefined;
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

    function qHTML(q, inGroup, title) {
        const v = S.answers[q.id];
        const id = "q-" + q.id;
        const req = q.required ? ` <span class="req">(needed)</span>` : "";
        const cls = "q" + (inGroup ? " in-group" : "");
        // A question worded the same as its screen title is not said twice.
        const same = labelOf(q).trim().toLowerCase() === String(title || "").trim().toLowerCase();
        const lab = same ? (req ? `<label for="${id}">${req}</label>` : "")
            : `<label for="${id}">${esc(labelOf(q))}${req}</label>`;
        const plain = same ? (req ? `<div class="label">${req}</div>` : "")
            : `<div class="label">${esc(labelOf(q))}${req}</div>`;
        switch (q.type) {
            case "text":
            case "number":
                return `<div class="${cls}">${lab}<input id="${id}" type="text" data-q="${esc(q.id)}"
                    value="${esc(v || "")}" ${inputAttrs(q)}${q.type === "number" ? ` class="short"` : ""}></div>`;
            case "longtext":
                return `<div class="${cls}">${lab}<textarea id="${id}" data-q="${esc(q.id)}"
                    ${inputAttrs(q)}>${esc(v || "")}</textarea></div>`;
            case "date": {
                const p = dateParts(q);
                const opts = MONTHS.map((name, i) =>
                    `<option value="${i + 1}"${String(i + 1) === p.m ? " selected" : ""}>${name}</option>`).join("");
                return `<div class="${cls}">${plain}<div class="date">
                    <div><input id="${id}" type="text" inputmode="numeric" maxlength="2" data-date="${esc(q.id)}"
                        data-part="d" value="${esc(p.d)}" autocomplete="off" aria-label="Day"><div class="cap">Day</div></div>
                    <div><select data-date="${esc(q.id)}" data-part="m" aria-label="Month">
                        <option value=""${p.m ? "" : " selected"}>Month</option>${opts}</select>
                        <div class="cap">Month</div></div>
                    <div><input type="text" inputmode="numeric" maxlength="4" data-date="${esc(q.id)}"
                        data-part="y" value="${esc(p.y)}" autocomplete="off" aria-label="Year"><div class="cap">Year</div></div>
                    </div></div>`;
            }
            case "yesno":
                return `<div class="${cls}">${plain}<div class="opts yesno">
                    ${["Yes", "No"].map((o) => `<button type="button" class="opt${v === o ? " on" : ""}"
                        data-pick="${esc(q.id)}" data-v="${o}">${o}</button>`).join("")}</div></div>`;
            case "choice": {
                const opts = q.options || [];
                const other = q.allow_other && v && !opts.includes(v);
                const otherOn = other || (q.allow_other && S.answers["__other_" + q.id]);
                return `<div class="${cls}">${plain}<div class="opts">
                    ${opts.map((o) => `<button type="button" class="opt${v === o ? " on" : ""}"
                        data-pick="${esc(q.id)}" data-v="${esc(o)}">${esc(o)}</button>`).join("")}
                    ${q.allow_other ? `<button type="button" class="opt${otherOn ? " on" : ""}"
                        data-other="${esc(q.id)}">Other</button>` : ""}</div>
                    ${otherOn ? `<input class="other-input" type="text" data-q="${esc(q.id)}"
                        value="${esc(other ? v : "")}" aria-label="Other" ${inputAttrs(q)}>` : ""}</div>`;
            }
            case "multi": {
                const have = Array.isArray(v) ? v : [];
                return `<div class="${cls}">${plain}<div class="opts multi">
                    ${(q.options || []).map((o) => `<button type="button" class="opt${have.includes(o) ? " on" : ""}"
                        data-tick="${esc(q.id)}" data-v="${esc(o)}" aria-pressed="${have.includes(o)}">${esc(o)}</button>`).join("")}
                    </div></div>`;
            }
            case "picklist":
                return `<div class="${cls}">${lab}<input id="${id}" type="text" data-q="${esc(q.id)}"
                    data-list="${esc(q.list || "")}" value="${esc(v || "")}" ${inputAttrs(q)}
                    placeholder="Start typing"><div class="picks" id="picks-${esc(q.id)}"></div></div>`;
            case "info":
                return `<div class="info">${esc(q.text || "")}</div>`;
            case "signature":
                return `<div class="${cls}">${plain}<div class="sig-wrap">
                    <canvas class="sig" id="sig" aria-label="Signature box"></canvas>
                    <div class="sig-line"></div></div>
                    <div class="sig-tools"><button type="button" class="btn" data-act="clear-sig">Clear</button></div></div>`;
            default:
                return "";
        }
    }

    function renderScreen(keepScroll) {
        mode = "form";
        const sc = S.screens[S.idx];
        seedPrefill(sc);
        const index = allQuestions();
        const total = S.screens.length;
        const last = S.idx === total - 1;
        let html = `<div class="progress">${S.idx + 1} of ${total}</div>
            <h1>${esc(sc.title)}</h1>${sc.hint ? `<p class="hint">${esc(sc.hint)}</p>` : ""}`;
        let group = null;
        (sc.questions || []).forEach((q) => {
            if (!isShown(q, index)) return;
            if (q.group && q.group !== group) html += `<h2 class="group">${esc(q.group)}</h2>`;
            group = q.group || null;
            html += qHTML(q, !!q.group, sc.title);
        });
        if (S.err) html += `<div class="err" role="alert">${esc(S.err)}</div>`;
        const y = window.scrollY;
        app.innerHTML = html;
        setBar(`<button class="btn" data-act="back">Back</button>
            <span class="grow"></span>
            ${sc.skippable ? `<button class="btn quiet" data-act="skip">Skip</button>` : ""}
            <button class="btn primary" data-act="next">${last ? "Finish" : "Next"}</button>`);
        window.scrollTo(0, keepScroll ? y : 0);
        if (app.querySelector("canvas.sig")) setupSignature();
    }

    /* --- signature --------------------------------------------------------------- */

    function setupSignature() {
        const c = document.getElementById("sig");
        const ratio = window.devicePixelRatio || 1;
        const w = c.clientWidth, h = c.clientHeight;
        c.width = Math.round(w * ratio);
        c.height = Math.round(h * ratio);
        const ctx = c.getContext("2d");
        ctx.scale(ratio, ratio);
        ctx.fillStyle = "#fff";
        ctx.fillRect(0, 0, w, h);
        ctx.lineWidth = 3;
        ctx.lineCap = "round";
        ctx.lineJoin = "round";
        ctx.strokeStyle = "#111";
        if (S.sig) {
            const img = new Image();
            img.onload = () => ctx.drawImage(img, 0, 0, w, h);
            img.src = S.sig;
        }
        let drawing = false, inked = !!S.sig;
        const at = (ev) => {
            const r = c.getBoundingClientRect();
            return [ev.clientX - r.left, ev.clientY - r.top];
        };
        c.addEventListener("pointerdown", (ev) => {
            ev.preventDefault();
            drawing = true;
            if (S.err) {                      // signing answers "please sign"
                S.err = "";
                const e = app.querySelector(".err");
                if (e) e.remove();
            }
            try { c.setPointerCapture(ev.pointerId); } catch (e) { /* older Safari */ }
            const [x, y] = at(ev);
            ctx.beginPath();
            ctx.moveTo(x, y);
            ctx.lineTo(x + 0.1, y + 0.1);
            ctx.stroke();
        });
        c.addEventListener("pointermove", (ev) => {
            if (!drawing) return;
            ev.preventDefault();
            const [x, y] = at(ev);
            ctx.lineTo(x, y);
            ctx.stroke();
            inked = true;
        });
        const end = () => {
            if (!drawing) return;
            drawing = false;
            if (inked) S.sig = c.toDataURL("image/png");
        };
        c.addEventListener("pointerup", end);
        c.addEventListener("pointercancel", end);
        c.addEventListener("pointerleave", end);
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
            if (q.type === "signature") {
                if (!S.sig) return "Please sign with your finger in the box.";
            } else if (q.type !== "info") {
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
        if (S.idx < 0) { S.idx = -1; renderWelcome(); return; }
        renderScreen(false);
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
                if (q.type === "info" || q.type === "signature") return;
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
                signature_png: S.sig,
            });
        } catch (e) {
            renderFailed();      // Wi-Fi dropped: everything is still in memory
            return;
        }
        if (r.status === 200 && r.data.ok) { S = null; renderThanks(); return; }
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
        if (act === "start") {
            api(`/session/${encodeURIComponent(S.token)}/filling`, {}).catch(() => {});
            S.idx = 0;
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
        } else if (act === "clear-sig") {
            S.sig = "";
            renderScreen(true);
        } else if (t.dataset.pick) {
            const qid = t.dataset.pick, v = t.dataset.v;
            S.answers[qid] = S.answers[qid] === v ? "" : v;      // tap again to clear
            delete S.answers["__other_" + qid];
            S.err = "";
            renderScreen(true);
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
            const have = Array.isArray(S.answers[qid]) ? S.answers[qid].slice() : [];
            const i = have.indexOf(v);
            if (i >= 0) have.splice(i, 1); else have.push(v);
            S.answers[qid] = have;
            S.err = "";
            renderScreen(true);
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

    // Turning the iPad round: redraw the signature box at its new width. (Only
    // on a width change - the on-screen keyboard must not wipe a text field.)
    let lastWidth = window.innerWidth;
    window.addEventListener("resize", () => {
        if (window.innerWidth === lastWidth) return;
        lastWidth = window.innerWidth;
        if (S && mode === "form" && app.querySelector("canvas.sig")) renderScreen(true);
    });

    /* --- go ------------------------------------------------------------------- */

    if (!KEY) {
        renderSetup();
    } else {
        renderIdle();
        poll();
        setInterval(poll, POLL_MS);
    }
})();
