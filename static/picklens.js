/* Pick a lens -> make the job (#/picklens). HIDDEN behind pick_lens_job.show / CEC_HUB_PICK_LENS.
   Mark, 8 Nov 2026. One screen at a time, big tappable cards, a clear Back at every step:
     1 Patient   the last three patients with a saved Rx, or search by full name
     2 Lens      the best two or three lenses for the Rx, price first; "More options" folds the rest
     3 Frame     scan or type the frame; PD per eye from the Rx; blank check per eye; next best if it doesn't fit
     4 Check     everything the job will carry, the Rx verbatim, the price built Touch's way; Make job
     Result      "Job 35xxx made. Next: open it in Optomate, press Patient Invoice."
   Every number on screen is Optomate's or the price sheet's, copied; nothing is worked out here.
   Helpers (esc, ciCall, ciLocked, ciUnlockPanel, ZEISS_LOGO, coatShort) come from app.js, which loads after
   this file; they are only called once the page runs. */

const pj = { step: 1, status: null, patient: null, data: null, rx: 0, tab: "", picked: null, frame: null,
             frameMode: "scan", heights: { R: "", L: "" }, tint: "", tints: null, preview: null, result: null,
             secondPair: false, seq: 0, accepted: false };

const PJ_STEPS = ["Patient", "Lens", "Frame", "Check"];

function pjMoney(v) { return v == null || v === "" ? "" : "$" + Number(v).toFixed(2).replace(/\.00$/, ""); }
function pjMoneyC(v) { return v == null || v === "" ? "" : "$" + Number(v).toFixed(2); }
function pjSupplier(s) { return /^zeiss$/i.test(s || "") ? ZEISS_LOGO : `<span class="pj-sup">${esc(s || "")}</span>`; }
function pjReset() {
    Object.assign(pj, { step: 1, patient: null, data: null, rx: 0, tab: "", picked: null, frame: null, frameMode: "scan",
                        heights: { R: "", L: "" }, tint: "", preview: null, result: null, secondPair: false, accepted: false });
}

function renderPickLens() {
    pjReset();
    document.body.classList.add("pj-body");
    window.addEventListener("hashchange", () => document.body.classList.remove("pj-body"), { once: true });
    view.innerHTML = `<div class="pj-page"><div id="pj-top"></div><div id="pj-body"><div class="pj-loading">Opening…</div></div></div>`;
    pjStatus();
}

async function pjStatus() {
    const r = await ciCall("/api/picklens/status");
    if (ciLocked(r)) { ciUnlockPanel(r.data, renderPickLens, "Make job"); return; }
    if (r.status === 404 || !r.data || r.data.enabled === false) {
        document.getElementById("pj-top").innerHTML = pjTop();
        document.getElementById("pj-body").innerHTML = `<div class="pj-card pj-center">
            <h1 class="pj-h1">Not switched on yet</h1>
            <p class="pj-lead">Make job is waiting its turn. The Lens options page works as before.</p>
            <a class="pj-btn pj-btn-quiet" href="#/lensoptions">Open Lens options</a></div>`;
        return;
    }
    pj.status = r.data;
    pjDraw();
}

function pjTop() {
    const st = pj.status || {};
    const mode = st.fixture ? "Fake test patients" : st.write_live ? "LIVE" : "Dry run";
    const cls = st.write_live && !st.fixture ? "pj-mode-live" : "pj-mode-dry";
    const back = pj.step === 1 ? `<a class="pj-back" href="#/">Home</a>` : `<button class="pj-back" type="button" id="pj-back">Back</button>`;
    const steps = PJ_STEPS.map((s, i) => `<li class="${i + 1 === pj.step ? "pj-on" : i + 1 < pj.step ? "pj-done" : ""}"><span class="pj-n">${i + 1}</span>${esc(s)}</li>`).join("");
    return `<div class="pj-topbar">${back}<ol class="pj-steps">${steps}</ol><span class="pj-mode ${cls}">${esc(mode)}</span></div>`;
}

function pjDraw() {
    document.getElementById("pj-top").innerHTML = pjTop();
    const b = document.getElementById("pj-body");
    if (pj.step === 1) pjDrawPatient(b);
    else if (pj.step === 2) pjDrawLens(b);
    else if (pj.step === 3) pjDrawFrame(b);
    else if (pj.step === 4) pjDrawCheck(b);
    else pjDrawResult(b);
    const back = document.getElementById("pj-back");
    if (back) back.addEventListener("click", pjBack);
    window.scrollTo({ top: 0, behavior: "smooth" });
}

function pjBack() {
    if (pj.step === 5) { pjReset(); pjDraw(); return; }
    if (pj.step === 4) { pj.preview = null; pj.step = 3; }
    else if (pj.step === 3) { pj.frame = null; pj.accepted = false; pj.step = 2; }
    else if (pj.step === 2) { pj.data = null; pj.picked = null; pj.step = 1; }
    pjDraw();
}

/* --- 1 Patient ---------------------------------------------------------------------------- */

function pjDrawPatient(b) {
    b.innerHTML = `<h1 class="pj-h1">Who are the glasses for?</h1>
        <p class="pj-lead">Tap the patient. The last three saved Rx come first.</p>
        <div id="pj-recent" class="pj-grid3"><div class="pj-loading">Getting today's Rx…</div></div>
        <form id="pj-find" class="pj-find">
            <input id="pj-q" type="text" autocomplete="off" spellcheck="false" aria-label="Find a patient: surname and first name"
                   placeholder="Or search: surname and first name">
            <button class="pj-btn" type="submit">Find</button>
        </form>
        <div id="pj-found" class="pj-grid3"></div>`;
    document.getElementById("pj-find").addEventListener("submit", pjSearch);
    pjRecent();
}

async function pjRecent() {
    const box = document.getElementById("pj-recent");
    const r = await ciCall("/api/lensoptions/recent");
    if (ciLocked(r)) { ciUnlockPanel(r.data, renderPickLens, "Make job"); return; }
    const d = r.data || {};
    if (d.error) { box.innerHTML = `<div class="pj-note">${esc(d.error)}</div>`; return; }
    const ps = d.patients || [];
    box.innerHTML = ps.length ? ps.map((p) => `<button class="pj-card pj-tap pj-person" type="button" data-pick="${esc(p.id)}">
            <span class="pj-person-name">${esc(p.name)}</span>
            <span class="pj-person-sub">Rx saved ${esc(p.saved)}</span></button>`).join("")
        : `<div class="pj-note">No spectacle Rx saved today yet. Search below.</div>`;
    if (d.fixture) box.insertAdjacentHTML("beforeend", `<div class="pj-small pj-span">Fake ZZTEST patients</div>`);
    pjWirePicks(box);
}

async function pjSearch(ev) {
    ev.preventDefault();
    const found = document.getElementById("pj-found");
    found.innerHTML = `<div class="pj-loading">Searching…</div>`;
    const r = await ciCall("/api/lensoptions/search", { q: document.getElementById("pj-q").value });
    if (ciLocked(r)) { ciUnlockPanel(r.data, renderPickLens, "Make job"); return; }
    if (r.status !== 200 || r.data.error) { found.innerHTML = `<div class="pj-note">${esc(r.data.error || "Search didn't work.")}</div>`; return; }
    const ps = r.data.patients || [];
    found.innerHTML = (ps.length ? ps.map((p) => `<button class="pj-card pj-tap pj-person" type="button" data-pick="${esc(p.id)}">
            <span class="pj-person-name">${esc(p.name)}</span>
            <span class="pj-person-sub">Born ${esc(p.dob || "—")} · last Rx ${esc(p.last_rx || "—")}</span></button>`).join("")
        : `<div class="pj-note">Nobody with a saved spectacle Rx by that name. Check the spelling.</div>`)
        + (r.data.more ? `<div class="pj-small pj-span">More matches - type more of the name</div>` : "");
    pjWirePicks(found);
}

function pjWirePicks(root) {
    root.querySelectorAll("[data-pick]").forEach((el) => el.addEventListener("click", () => pjLoad(Number(el.dataset.pick))));
}

async function pjLoad(pid, frame) {
    const seq = ++pj.seq;
    const body = { patient_id: pid };
    if (frame) body.frame = frame;
    if (pj.picked) body.picked = pj.picked.ref;
    const b = document.getElementById("pj-body");
    if (!frame) b.innerHTML = `<div class="pj-loading">Reading the Rx and checking the lenses…</div>`;
    const r = await ciCall("/api/picklens/options", body);
    if (seq !== pj.seq) return;
    if (ciLocked(r)) { ciUnlockPanel(r.data, renderPickLens, "Make job"); return; }
    const d = r.data || {};
    if (r.status !== 200 || d.error) { b.innerHTML = `<div class="pj-bad">${esc(d.error || "Couldn't read the Rx.")}</div>`; return; }
    pj.data = d; pj.patient = d.patient;
    if (!frame) { pj.rx = 0; pj.tab = ""; pj.step = 2; }
    pjDraw();
}

/* --- shared pieces -------------------------------------------------------------------------- */

function pjRx() { return pj.data && pj.data.rxs ? (pj.data.rxs[pj.rx] || pj.data.rxs[0]) : null; }

function pjTab() {
    const rx = pjRx();
    if (!rx) return null;
    const tabs = rx.tabs || [];
    const t = tabs.find((x) => x.key === pj.tab) || tabs.find((x) => x.key === rx.default_tab) || tabs[0];
    pj.tab = t ? t.key : "";
    return t;
}

// The part (lens type) the picked lens belongs to, else the tab's first part with an answer.
function pjPart() {
    const t = pjTab();
    if (!t) return null;
    const parts = t.parts || [];
    if (pj.picked) {
        const p = parts.find((x) => x.key === pj.picked.part);
        if (p) return p;
    }
    return parts.find((x) => x.answer && (x.answer.options || []).length) || parts[0] || null;
}

function pjRxTable(rx) {
    const row = (label, e) => e ? `<tr><th scope="row">${label}</th>
        <td>${esc(e.sph || "—")}</td><td>${esc(e.cyl || "")}</td><td>${esc(e.axis || "")}</td>
        <td>${esc(e.add || "")}</td><td>${esc(e.pd || "")}</td><td>${esc(e.near_pd || "")}</td><td>${esc(e.prism || "")}</td></tr>`
        : `<tr><th scope="row">${label}</th><td colspan="7" class="pj-small">not on this Rx</td></tr>`;
    return `<table class="pj-rx"><thead><tr><th></th><th>Sph</th><th>Cyl</th><th>Axis</th><th>Add</th><th>PD</th><th>Near PD</th><th>Prism</th></tr></thead>
        <tbody>${row("R", rx.right)}${row("L", rx.left)}</tbody></table>`;
}

function pjPatientHead(rx) {
    const p = pj.patient || {};
    return `<div class="pj-phead"><span class="pj-pname">${esc(p.name || "")}</span>
        ${p.dob ? `<span class="pj-small">born ${esc(p.dob)}</span>` : ""}
        ${rx ? `<span class="pj-small">Rx saved ${esc(rx.saved_date)} ${esc(rx.saved)}${rx.optom ? " · " + esc(rx.optom) : ""}</span>` : ""}
        ${pj.data && pj.data.fixture ? `<span class="pj-chip pj-chip-grey">fake test patient</span>` : ""}</div>`;
}

function pjLensTitle(o) {
    return `<span class="pj-idx">${esc(o.index)}</span> <span class="pj-lname">${esc(o.name.toLowerCase() === o.type.toLowerCase() ? o.category : o.name)}</span>`;
}

function pjKind(o) {
    const stock = o.type === "Stock";
    return `<span class="pj-chip ${o.fits === false ? "pj-chip-bad" : stock ? "pj-chip-stock" : "pj-chip-grind"}">${o.fits === false ? "Too small" : stock ? "Stock" : "Made to order"}</span>`;
}

function pjReady(o) {
    const l = o.link || {};
    return l.ready ? `<span class="pj-ready">Ready for Optomate</span>`
        : `<span class="pj-touch" title="${esc(l.why || "")}">${esc(l.words || "Make this job in Touch")}</span>`;
}

function pjLensCard(o, i, part) {
    return `<button class="pj-card pj-tap pj-lens ${o.fits === false ? "pj-lens-bad" : ""}" type="button" data-lens="${i}" data-part="${esc(part)}">
        <div class="pj-lens-top">${pjLensTitle(o)}<span class="pj-price">${o.sell != null ? pjMoney(o.sell) : "—"}<small>a pair</small></span></div>
        <div class="pj-lens-mid">${pjSupplier(o.supplier)} ${pjKind(o)} <span class="pj-small">${esc(coatShort(o.coating))}</span></div>
        ${o.why ? `<div class="pj-why">${esc(o.why)}</div>` : ""}
        <div class="pj-lens-foot">${pjReady(o)}</div></button>`;
}

function pjWireLensCards(root, listOf) {
    root.querySelectorAll("[data-lens]").forEach((el) => el.addEventListener("click", () => {
        const list = listOf(el.dataset.part);
        const o = list[Number(el.dataset.lens)];
        if (!o) return;
        pj.picked = { ...o, part: el.dataset.part };
        pj.accepted = false;
        if (pj.step === 2) { pj.step = 3; pjDraw(); }
        else pjFrameApply();
    }));
}

/* --- 2 Lens ---------------------------------------------------------------------------------- */

function pjDrawLens(b) {
    const d = pj.data, rx = pjRx();
    if (!rx) {
        b.innerHTML = `${pjPatientHead(null)}<div class="pj-card pj-center"><h1 class="pj-h1">No spectacle Rx saved</h1>
            <p class="pj-lead">${esc(d.empty || "Nothing saved for this patient in Optomate.")}</p></div>`;
        return;
    }
    const tab = pjTab();
    // only lens types this Rx can make (or the optom wrote on it): a tab with nothing to offer is noise
    const tabs = (rx.tabs || []).filter((t) => t.written || t.key === tab.key
        || (t.parts || []).some((p) => !p.unavailable && p.answer && (p.answer.options || []).length));
    const several = d.rxs.length > 1 ? `<div class="pj-seg" role="tablist">${d.rxs.map((x, i) => `<button type="button" class="${i === pj.rx ? "pj-seg-on" : ""}" data-rx="${i}">
            Rx ${i + 1} · ${esc(x.saved)}${x.type_words && x.type_words.length ? " · " + esc(x.type_words.join(", ")) : ""}</button>`).join("")}</div>
        <div class="pj-note">${esc(d.several)}</div>` : "";
    const notes = [rx.type_note].concat(rx.flags || []).filter(Boolean);
    const comment = [rx.comment, rx.practice_comment].filter(Boolean).join(" · ");
    const parts = (tab.parts || []);
    const sections = parts.map((part) => {
        const a = part.answer;
        if (part.unavailable) return `<div class="pj-note">${esc(part.unavailable)}</div>`;
        if (!a || !(a.options || []).length) return `<div class="pj-card pj-center"><div class="pj-h2">No lens we order covers this Rx</div></div>`;
        const best = a.best || [];
        const rest = a.rest || [];
        // rows are numbered through best + rest, so a tap finds the same object either way
        const rowOf = (o) => `<button class="pj-row pj-tap" type="button" data-lens="${best.length + rest.indexOf(o)}" data-part="${esc(part.key)}">
                    <span class="pj-row-name">${pjLensTitle(o)} <span class="pj-small">${esc(coatShort(o.coating))}</span></span>
                    <span>${pjSupplier(o.supplier)}</span>${pjKind(o)}<span class="pj-small">${pjReady(o)}</span>
                    <span class="pj-row-price">${o.sell != null ? pjMoney(o.sell) : `<span class="pj-small">not on price sheet</span>`}</span></button>`;
        const priced = rest.filter((o) => o.sell != null), unpriced = rest.filter((o) => o.sell == null);
        return `${parts.length > 1 ? `<div class="pj-h2">${esc(part.title)}</div>` : ""}
            ${(part.working || []).length ? `<div class="pj-working">${part.working.map(esc).join("<br>")}</div>` : ""}
            <div class="pj-grid3">${best.map((o, i) => pjLensCard(o, i, part.key)).join("")}</div>
            ${rest.length ? `<details class="pj-more"><summary>More options <span class="pj-small">(${priced.length})</span></summary>
                <div class="pj-list">${priced.map(rowOf).join("")}</div>
                ${unpriced.length ? `<details class="pj-more pj-more-in"><summary>Not on our price sheet <span class="pj-small">(${unpriced.length}) - make these in Touch</span></summary>
                    <div class="pj-list">${unpriced.map(rowOf).join("")}</div></details>` : ""}</details>` : ""}`;
    }).join("");
    b.innerHTML = `${pjPatientHead(rx)}
        <div class="pj-card pj-rxcard">${pjRxTable(rx)}${comment ? `<div class="pj-small pj-comment">Rx note: ${esc(comment)}</div>` : ""}</div>
        ${several}
        ${notes.length ? `<ul class="pj-notes">${notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>` : ""}
        <h1 class="pj-h1">Which lens?</h1>
        <p class="pj-lead">Cheapest that covers the Rx first. Tap one to go on.</p>
        <div class="pj-seg pj-types" role="tablist">${tabs.map((t) => `<button type="button" role="tab" class="${t.key === tab.key ? "pj-seg-on" : ""}"
            data-tab="${esc(t.key)}" aria-selected="${t.key === tab.key}">${esc(t.label)}${t.written ? `<span class="pj-onrx">on Rx</span>` : ""}</button>`).join("")}</div>
        ${sections}`;
    b.querySelectorAll("[data-tab]").forEach((x) => x.addEventListener("click", () => { pj.tab = x.dataset.tab; pjDraw(); }));
    b.querySelectorAll("[data-rx]").forEach((x) => x.addEventListener("click", () => { pj.rx = Number(x.dataset.rx); pj.tab = ""; pjDraw(); }));
    pjWireLensCards(b, (key) => {
        const part = parts.find((p) => p.key === key);
        return part && part.answer ? (part.answer.best || []).concat(part.answer.rest || []) : [];
    });
}

/* --- 3 Frame --------------------------------------------------------------------------------- */

function pjPickedLine() {
    const o = pj.picked;
    if (!o) return "";
    return `<div class="pj-pickline">${pjSupplier(o.supplier)} ${pjLensTitle(o)} <span class="pj-small">${esc(coatShort(o.coating))} · ${esc(o.type)}</span>
        <span class="pj-price pj-price-sm">${o.sell != null ? pjMoney(o.sell) : "—"}<small>a pair</small></span></div>`;
}

function pjDrawFrame(b) {
    const f = pj.frame || {};
    const v = (x) => (x != null && x !== "" ? esc(x) : "");
    const last = pj.data && pj.data.frame && pj.data.frame.source === "order" ? pj.data.frame : null;
    b.innerHTML = `${pjPatientHead(pjRx())}${pjPickedLine()}
        <h1 class="pj-h1">Which frame?</h1>
        <p class="pj-lead">Scan the frame tag. Or type its size from the temple arm.</p>
        <div class="pj-card">
            <form id="pj-scan" class="pj-scan">
                <input id="pj-barcode" type="text" autocomplete="off" spellcheck="false" aria-label="Frame barcode" placeholder="Scan the frame barcode" value="${v(f.barcode)}">
                <button class="pj-btn" type="submit">Look up</button>
            </form>
            <div id="pj-framecard">${pjFrameCard()}</div>
            <details class="pj-more" ${pj.frameMode !== "scan" || (!f.barcode && last) ? "open" : ""}>
                <summary>Type the size instead</summary>
                <form id="pj-typed" class="pj-typed">
                    <label>Eye size (A)<input id="pj-a" inputmode="decimal" placeholder="52" value="${v(f.a != null ? f.a : last && last.a)}"></label>
                    <label>Bridge (DBL)<input id="pj-dbl" inputmode="decimal" placeholder="18" value="${v(f.dbl != null ? f.dbl : last && last.dbl)}"></label>
                    <label>Diagonal (ED), if known<input id="pj-ed" inputmode="decimal" placeholder="57" value="${v(f.ed != null ? f.ed : last && last.ed)}"></label>
                    <label class="pj-switch"><input id="pj-own" type="checkbox" ${pj.frameMode === "own" ? "checked" : ""}><span>Own frame</span></label>
                    <button class="pj-btn pj-btn-quiet" type="submit">Use this size</button>
                </form>
                ${last ? `<div class="pj-small">Sizes from the last spectacle order${last.order_date ? " (" + esc(last.order_date) + ")" : ""} - change them if the frame is new.</div>` : ""}
            </details>
        </div>
        <div id="pj-fit">${pjFitPanel()}</div>`;
    document.getElementById("pj-scan").addEventListener("submit", pjScan);
    document.getElementById("pj-typed").addEventListener("submit", pjTyped);
    pjWireFit(b);
    if (!f.barcode && pj.frameMode === "scan") document.getElementById("pj-barcode").focus();
}

function pjFrameCard() {
    const f = pj.frame;
    if (!f) return "";
    const size = [f.a != null ? `eye ${f.a}` : "", f.dbl != null ? `bridge ${f.dbl}` : "", f.ed != null ? `ED ${f.ed}` : ""].filter(Boolean).join(" · ");
    if (f.own) return `<div class="pj-framecard"><div class="pj-h2">Own frame</div><div class="pj-small">${esc(size || "size not typed")}</div></div>`;
    if (f.source === "typed") return `<div class="pj-framecard"><div class="pj-h2">Typed size</div><div class="pj-small">${esc(size)}</div></div>`;
    return `<div class="pj-framecard"><div class="pj-h2">${esc(f.model || f.barcode)}${f.colour ? ` <span class="pj-small">${esc(f.colour)}</span>` : ""}</div>
        <div class="pj-small">${esc(size || "no size on the frame record - type it below")}${f.price != null ? ` · ${pjMoneyC(f.price)}` : " · no price on the frame record"}${f.sunglass ? " · sunglass" : ""}</div></div>`;
}

async function pjScan(ev) {
    ev.preventDefault();
    const code = document.getElementById("pj-barcode").value.trim();
    const box = document.getElementById("pj-framecard");
    if (!code) { box.innerHTML = `<div class="pj-note">Scan or type the frame barcode first.</div>`; return; }
    box.innerHTML = `<div class="pj-loading">Looking up the frame…</div>`;
    const r = await ciCall("/api/picklens/frame", { barcode: code });
    if (ciLocked(r)) { ciUnlockPanel(r.data, renderPickLens, "Make job"); return; }
    if (r.status !== 200 || r.data.error) { box.innerHTML = `<div class="pj-bad">${esc(r.data.error || "Couldn't read that frame.")}</div>`; return; }
    pj.frame = { ...r.data.frame, own: false };
    pj.frameMode = "scan";
    pj.accepted = false;
    pjFrameApply();
}

function pjTyped(ev) {
    ev.preventDefault();
    const val = (id) => document.getElementById(id).value.trim();
    const own = document.getElementById("pj-own").checked;
    pj.frame = { a: val("pj-a") || null, dbl: val("pj-dbl") || null, ed: val("pj-ed") || null, own, source: "typed", barcode: "" };
    pj.frameMode = own ? "own" : "typed";
    pj.accepted = false;
    pjFrameApply();
}

// Re-run the options with the frame so the blank check and next best are the server's words.
async function pjFrameApply() {
    const f = pj.frame || {};
    document.getElementById("pj-framecard").innerHTML = pjFrameCard();
    document.getElementById("pj-fit").innerHTML = `<div class="pj-loading">Checking the blank…</div>`;
    const frame = f.a ? { a: f.a, dbl: f.dbl, ed: f.ed } : null;
    if (!frame) { document.getElementById("pj-fit").innerHTML = pjFitPanel(); pjWireFit(document.getElementById("pj-fit")); return; }
    await pjLoad(pj.patient.id, frame);
}

function pjFitPanel() {
    const part = pjPart();
    if (!pj.frame) return "";
    const a = part && part.answer;
    const est = part && part.blank;
    const nb = a && a.next_best;
    const picked = nb && nb.picked ? nb.picked : pj.picked;
    const haveSize = pj.frame.a != null && pj.frame.a !== "" && pj.frame.dbl != null && pj.frame.dbl !== "";
    if (!haveSize) {
        return `<div class="pj-card"><div class="pj-h2">Size needed for the blank check</div>
            <p class="pj-small">Type the eye size and bridge above. Own frame still needs its size.</p>${pjContinue(false, "")}</div>`;
    }
    const pdLine = est && est.inputs ? `<div class="pj-fact"><span class="pj-k">PD, from the Rx</span><span>${esc(est.inputs.replace(/^.*· PD /, ""))}</span></div>` : "";
    const eyes = est && est.per_eye ? Object.keys(est.per_eye).sort((x, y) => (x === "R" ? -1 : 1)) : [];
    const differ = eyes.length > 1 && new Set(eyes.map((s) => est.per_eye[s])).size > 1;
    const needLine = est ? `<div class="pj-fact"><span class="pj-k">Blank needed</span><span>${esc(est.value)} mm${est.approx ? " <span class='pj-small'>(approximate, no ED)</span>" : ""}
        ${differ ? `<span class="pj-small">· ${eyes.map((s) => `${s} ${est.per_eye[s]}`).join(" · ")}</span>` : ""}</span></div>` : "";
    let verdict = "";
    if (picked) {
        const ok = picked.fits !== false && picked.link && picked.link.ready;
        const line = picked.fits === false ? (nb && nb.problem) || picked.blank_line
            : picked.type === "Stock" ? (picked.blank_line || "Stock blank fits") : "Made to order, cut to this frame";
        verdict = `<div class="pj-verdict ${picked.fits === false ? "pj-v-bad" : picked.type === "Stock" ? "pj-v-stock" : "pj-v-grind"}">
            <div class="pj-v-head">${picked.fits === false ? "Doesn't fit" : "Fits"}</div>
            <div>${pjLensTitle(picked)} <span class="pj-small">${esc(line || "")}</span></div>
            ${!picked.link || !picked.link.ready ? `<div class="pj-small pj-touchline">${esc((picked.link || {}).why || "Not linked to Optomate yet")}: make this job in Touch.</div>` : ""}
        </div>`;
        const sug = nb && nb.suggest ? nb.suggest : [];
        if ((picked.fits === false || !ok) && sug.length) {
            verdict += `<div class="pj-h2">Next best <span class="pj-small">stock that fits first, then made to order</span></div>
                <div class="pj-grid3">${sug.map((o, i) => pjLensCard(o, i, part.key)).join("")}</div>`;
        }
    }
    const lensOk = !!(picked && picked.fits !== false && picked.link && picked.link.ready);
    const frameOk = !!(pj.frame && (pj.frame.barcode || pj.frame.own));
    return `<div class="pj-card">${pdLine}${needLine}${verdict}${pjContinue(lensOk && frameOk,
        !lensOk ? "Pick a lens that fits and is ready for Optomate." : "Scan the frame, or tick Own frame, to go on. Typed sizes only check the blank.")}</div>`;
}

function pjContinue(ok, hint) {
    return `<div class="pj-actions"><button class="pj-btn pj-btn-big" type="button" id="pj-next" ${ok ? "" : "disabled"}>Check and make job</button>
        ${ok ? "" : `<div class="pj-small">${esc(hint || "")}</div>`}</div>`;
}

function pjWireFit(root) {
    const part = pjPart();
    pjWireLensCards(root, () => (part && part.answer && part.answer.next_best ? part.answer.next_best.suggest : []));
    const next = root.querySelector("#pj-next");
    if (next) next.addEventListener("click", () => { pj.step = 4; pj.preview = null; pjDraw(); });
}

/* --- 4 Check --------------------------------------------------------------------------------- */

function pjRequest() {
    const rx = pjRx(), o = pj.picked, f = pj.frame || {};
    return { patient_id: pj.patient.id, rx_id: rx.rx_id, rx_saved_at: rx.saved_at || "", option: o.ref,
             lens_use: o.use || "D", hub_pair_price: o.sell != null ? String(o.sell) : "",
             frame: { own: !!f.own, barcode: f.own ? "" : (f.barcode || ""), a: f.a || "", dbl: f.dbl || "", ed: f.ed || "" },
             heights: pj.heights, tint_barcode: pj.tint, second_pair: pj.secondPair };
}

async function pjDrawCheck(b) {
    b.innerHTML = `${pjPatientHead(pjRx())}<h1 class="pj-h1">Check, then make the job</h1><div class="pj-loading">Reading everything fresh from Optomate…</div>`;
    const seq = ++pj.seq;
    if (!pj.tints) {
        const t = await ciCall("/api/picklens/tints");
        pj.tints = t.status === 200 && t.data && t.data.tints ? t.data.tints : [];
    }
    const r = await ciCall("/api/picklens/check", pjRequest());
    if (seq !== pj.seq) return;
    if (ciLocked(r)) { ciUnlockPanel(r.data, renderPickLens, "Make job"); return; }
    pj.preview = r.data || { ok: false, refusals: ["Couldn't check the job."] };
    pjDrawCheckBody(b);
}

function pjDrawCheckBody(b) {
    const p = pj.preview, pv = p.preview || {}, pr = pv.price || {};
    const rx = pv.rx || {};
    const row = (k, v, cls) => `<div class="pj-crow ${cls || ""}"><div class="pj-k">${k}</div><div class="pj-cv">${v}</div></div>`;
    const o = pj.picked || {};
    const lens = pv.lens ? `${pjSupplier(o.supplier)} ${pjLensTitle(o)} <span class="pj-small">${esc(coatShort(o.coating))} · ${esc(pv.lens.stockgrind)} · ${esc(pv.lens.use)}</span>
            <div class="pj-small">Optomate ${esc(pv.lens.barcode)} · ${esc(pv.lens.description)}</div>`
        : `<span class="pj-touch">Not linked to Optomate - make this job in Touch</span>`;
    const coat = pv.coat ? (pv.coat.barcode ? `${esc(pv.coat.description)} <span class="pj-small">add-on ${esc(pv.coat.barcode)} · ${pjMoneyC(pv.coat.sellinc)} a lens</span>` : esc(pv.coat.description || "")) : "";
    const frame = pv.frame ? (pv.frame.own ? `Own frame <span class="pj-small">${esc(pv.frame.size || "")}</span>`
        : `${esc(pv.frame.model || pv.frame.barcode)}${pv.frame.colour ? " " + esc(pv.frame.colour) : ""} <span class="pj-small">${esc(pv.frame.barcode)} · ${esc(pv.frame.size)} · ${pjMoneyC(pv.frame.sellinc)}${pv.frame.sunglass ? " · sunglass" : ""}</span>`) : "";
    const heightsNeeded = (pj.picked && ["M", "B"].includes(pj.picked.use));
    const hIn = (s) => `<label class="pj-h">${s === "R" ? "Right" : "Left"}<input data-h="${s}" inputmode="decimal" placeholder="mm" value="${esc(pj.heights[s] || "")}"></label>`;
    const heights = `<div class="pj-heights">${hIn("R")}${hIn("L")}<span class="pj-small">${heightsNeeded ? "Measured, in mm. Required for this lens." : "Optional for single vision."}</span></div>`;
    const tint = `<select id="pj-tint" class="pj-select"><option value="">None</option>${(pj.tints || []).map((t) => `<option value="${esc(t.barcode)}" ${t.barcode === pj.tint ? "selected" : ""}>${esc(t.description)} · ${pjMoneyC(t.sellinc)} a lens</option>`).join("")}</select>`;
    const agree = pr.agree === true ? `<span class="pj-ready">Matches the price sheet</span>` : pr.agree === false ? `<span class="pj-badword">Prices don't agree</span>` : "";
    const price = pv.lens ? `<table class="pj-pricetable"><tbody>
        <tr><td>Lenses</td><td class="pj-num">${pjMoneyC(pr.lens_each)} x 2</td><td class="pj-num">${pjMoneyC(Number(pr.lens_each) * 2)}</td></tr>
        ${pv.coat && pv.coat.barcode ? `<tr><td>Coating add-on</td><td class="pj-num">${pjMoneyC(pr.coat_each)} x 2</td><td class="pj-num">${pjMoneyC(Number(pr.coat_each) * 2)}</td></tr>` : ""}
        ${pv.tint ? `<tr><td>Tint</td><td class="pj-num">${pjMoneyC(pr.tint_each)} x 2</td><td class="pj-num">${pjMoneyC(Number(pr.tint_each) * 2)}</td></tr>` : ""}
        ${pv.frame && !pv.frame.own ? `<tr><td>Frame</td><td></td><td class="pj-num">${pjMoneyC(pr.frame)}</td></tr>` : ""}
        <tr class="pj-total"><td>Touch will charge</td><td class="pj-small">price sheet ${pjMoneyC(pr.hub_pair)} a pair</td><td class="pj-num">${pjMoneyC(pr.touch_total)}</td></tr>
        </tbody></table>${agree}` : "";
    const refusals = (p.refusals || []).length ? `<div class="pj-bad"><div class="pj-bad-head">Can't make the job yet</div><ul>${p.refusals.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>` : "";
    const warnings = (p.warnings || []).length ? `<ul class="pj-notes">${p.warnings.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : "";
    const second = (p.refusals || []).some((x) => /second pair/i.test(x)) ? `<label class="pj-switch"><input id="pj-second" type="checkbox" ${pj.secondPair ? "checked" : ""}><span>It's a second pair</span></label>` : "";
    const mode = p.mode || pj.status || {};
    const modeLine = mode.fixture ? "Fake test patients: nothing is written." : mode.write_live ? "This WILL create the job in Optomate." : "Dry run: shows the job it would make, writes nothing.";
    b.innerHTML = `${pjPatientHead(pjRx())}
        <h1 class="pj-h1">Check, then make the job</h1>
        <p class="pj-lead">Everything below goes on the job exactly as shown.</p>
        <div class="pj-card pj-check">
            ${row("Patient", `${esc((pv.patient || {}).name || "")} <span class="pj-small">born ${esc((pv.patient || {}).dob || "")}</span>`)}
            ${row("Rx", `${pjRxTable(rx)}<div class="pj-small">Copied exactly as saved · Rx date ${esc(rx.date || "")} · ${esc(rx.optom || "")} · Rx #${esc(rx.id || "")}</div>`)}
            ${row("Lens", lens)}
            ${row("Coating", coat)}
            ${row("Tint", tint)}
            ${row("Frame", frame)}
            ${row("Heights", heights, heightsNeeded ? "pj-req" : "")}
            ${pv.lens ? row("Price", price) : ""}
        </div>
        ${warnings}${refusals}${second}
        <div class="pj-actions">
            <button class="pj-btn pj-btn-big ${p.ok ? "" : "pj-off"}" type="button" id="pj-make" ${p.ok ? "" : "disabled"}>Make job</button>
            <div class="pj-small">${esc(modeLine)}</div>
        </div>`;
    b.querySelectorAll("[data-h]").forEach((inp) => inp.addEventListener("change", () => { pj.heights[inp.dataset.h] = inp.value.trim(); pjRecheck(); }));
    document.getElementById("pj-tint").addEventListener("change", (ev) => { pj.tint = ev.target.value; pjRecheck(); });
    const sec = document.getElementById("pj-second");
    if (sec) sec.addEventListener("change", () => { pj.secondPair = sec.checked; pjRecheck(); });
    const make = document.getElementById("pj-make");
    if (make) make.addEventListener("click", pjMake);
}

async function pjRecheck() {
    const seq = ++pj.seq;
    const r = await ciCall("/api/picklens/check", pjRequest());
    if (seq !== pj.seq) return;
    pj.preview = r.data || { ok: false, refusals: ["Couldn't check the job."] };
    pjDrawCheckBody(document.getElementById("pj-body"));
}

async function pjMake() {
    const btn = document.getElementById("pj-make");
    const mode = (pj.preview && pj.preview.mode) || pj.status || {};
    if (mode.write_live && !mode.fixture) {
        const name = ((pj.preview.preview || {}).patient || {}).name || "this patient";
        if (!window.confirm(`Make this job for ${name} in Optomate now?\n\nIt will be created unsent. It can be cancelled in Touch.`)) return;
    }
    btn.disabled = true;
    btn.textContent = "Making the job…";
    const r = await ciCall("/api/picklens/make", pjRequest());
    if (ciLocked(r)) { ciUnlockPanel(r.data, renderPickLens, "Make job"); return; }
    pj.result = r.data || { ok: false, refusals: ["No answer from Optomate - check Touch before trying again."] };
    pj.step = 5;
    pjDraw();
}

/* --- Result ---------------------------------------------------------------------------------- */

function pjDrawResult(b) {
    const r = pj.result || {};
    const body = r.body || {};
    const rows = Object.keys(body).map((k) => `<tr><th>${esc(k)}</th><td>${esc(typeof body[k] === "object" ? JSON.stringify(body[k]) : String(body[k]))}</td></tr>`).join("");
    let head, lead, cls = "";
    if (r.created) {
        head = `Job ${esc(r.created)} made`;
        lead = r.verified === false ? `Made but not as sent: check it in Touch before Patient Invoice. Differences: ${esc((r.mismatch || []).join(", "))}`
            : `Next: open it in Optomate, press Patient Invoice.`;
        if (r.verified === false) cls = "pj-v-bad";
    } else if (r.ok) {
        head = "Dry run: nothing was made";
        lead = "This is the job it would send to Optomate.";
    } else {
        head = "Nothing was made";
        lead = (r.refusals || []).join(" ") || "Optomate didn't make the job.";
        cls = "pj-v-bad";
    }
    b.innerHTML = `${pjPatientHead(pjRx())}
        <div class="pj-card pj-center pj-result ${cls}"><h1 class="pj-h1">${head}</h1><p class="pj-lead">${lead}</p>
            <div class="pj-actions"><button class="pj-btn pj-btn-big" type="button" id="pj-again">Start another</button></div></div>
        ${rows ? `<details class="pj-more" ${r.created ? "" : "open"}><summary>The job, field by field</summary>
            <table class="pj-body-table"><tbody>${rows}</tbody></table></details>` : ""}`;
    document.getElementById("pj-again").addEventListener("click", () => { pjReset(); pjDraw(); });
}
