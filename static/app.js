/* CEC Hub frontend — plain JS, hash routing, no build step.
   Server-parsed SOP block HTML is trusted (the server escapes it);
   everything else goes through esc() before touching the page. */

"use strict";

const view = document.getElementById("view");

function esc(s) {
    return String(s == null ? "" : s)
        .replace(/&/g, "&amp;").replace(/</g, "&lt;")
        .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

async function getJSON(url) {
    const res = await fetch(url);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || "Something went wrong.");
    return data;
}

function errorPanel(message) {
    return `<div class="error-panel">${esc(message)}<br>
        <small>Try again, or close this tab and double-click START.bat.
        Still stuck? Ask Mark.</small></div>`;
}

/* --- Who's using this? (cookie only, for the Hub's own log lines) ------- */

function getStaff() {
    const m = document.cookie.match(/(?:^|;\s*)hub_staff=([^;]*)/);
    return m ? decodeURIComponent(m[1]) : "";
}

function setStaff(name) {
    document.cookie = "hub_staff=" + encodeURIComponent(name) +
        "; max-age=31536000; path=/; SameSite=Lax";
}

async function initStaffPicker() {
    const picker = document.getElementById("staff-picker");
    try {
        const data = await getJSON("/api/staff");
        (data.staff || []).forEach((name) => {
            const opt = document.createElement("option");
            opt.value = name;
            opt.textContent = name;
            picker.appendChild(opt);
        });
        picker.value = getStaff();
    } catch (e) { /* the picker is optional — never block the Hub on it */ }
    picker.addEventListener("change", () => {
        setStaff(picker.value);
        if (location.hash === "" || location.hash === "#/") renderHome();
    });
}

/* --- Router -------------------------------------------------------------- */

const routes = [
    { re: /^#?\/?$/, fn: renderHome },
    { re: /^#\/sops$/, fn: renderSopList },
    { re: /^#\/sop\/([A-Za-z0-9._-]+)$/, fn: (m) => renderSop(m[1]) },
    { re: /^#\/reviews$/, fn: renderReviews },
    { re: /^#\/orders$/, fn: renderOrders },
    { re: /^#\/stock$/, fn: renderStock },
    { re: /^#\/lenses$/, fn: renderLenses },
];

function route() {
    const hash = location.hash || "#/";
    for (const r of routes) {
        const m = hash.match(r.re);
        if (m) { r.fn(m); window.scrollTo(0, 0); return; }
    }
    renderHome();
}

window.addEventListener("hashchange", route);

/* --- Home ---------------------------------------------------------------- */

async function renderHome() {
    view.innerHTML = `<div class="loading-panel">Opening the Hub…</div>`;
    let data;
    try {
        data = await getJSON("/api/tiles");
    } catch (e) {
        view.innerHTML = errorPanel(e.message);
        return;
    }
    const who = getStaff();
    const hello = who ? `Hello ${esc(who)} — what do you need?`
                      : "What do you need?";
    const tiles = (data.tiles || []).map((t) => {
        const external = !!t.external;
        const target = external ? ` target="_blank" rel="noopener"` : "";
        return `<a class="tile" href="${esc(t.link)}"${target}>
            <span class="tile-icon" aria-hidden="true">${esc(t.icon)}</span>
            <span class="tile-name">${esc(t.name)}</span>
            <span class="tile-desc">${esc(t.description)}</span>
            ${external ? `<span class="tile-external">Opens in a new tab</span>` : ""}
        </a>`;
    }).join("");
    view.innerHTML = `
        <div class="greeting">${hello}</div>
        <div class="tile-grid">${tiles}</div>`;
}

/* --- SOP list -------------------------------------------------------------- */

let sopCategory = "All";

async function renderSopList() {
    view.innerHTML = `<div class="loading-panel">Getting the guides…</div>`;
    let data;
    try {
        data = await getJSON("/api/sops");
    } catch (e) {
        view.innerHTML = errorPanel(e.message);
        return;
    }
    const sops = data.sops || [];
    const cats = ["All", ...new Set(sops.map((s) => s.category))];
    if (!cats.includes(sopCategory)) sopCategory = "All";

    view.innerHTML = `
        <a class="btn btn-quiet btn-back" href="#/">← Home</a>
        <h1 class="page-title">How-To Guides</h1>
        <p class="page-sub">Tap a guide and follow the steps. Use the search box if you know what you're after.</p>
        <div class="sops-layout">
            <nav class="cat-sidebar" aria-label="Guide categories">
                ${cats.map((c) => `<button class="cat-btn${c === sopCategory ? " active" : ""}"
                    data-cat="${esc(c)}">${esc(c)}</button>`).join("")}
            </nav>
            <div class="sops-main">
                <input class="search-box" id="sop-search" type="search"
                    placeholder="Search the guides… (e.g. paper, HYLO, scorecard)"
                    autocomplete="off" aria-label="Search the guides">
                <div id="sop-results"></div>
            </div>
        </div>`;

    const resultsEl = document.getElementById("sop-results");
    const searchEl = document.getElementById("sop-search");

    function showList() {
        const visible = sopCategory === "All"
            ? sops : sops.filter((s) => s.category === sopCategory);
        if (!visible.length) {
            resultsEl.innerHTML = `<div class="empty-panel">No guides in this category yet.</div>`;
            return;
        }
        resultsEl.innerHTML = visible.map((s) => `
            <a class="sop-card" href="#/sop/${esc(s.slug)}">
                <div class="sop-card-title">${esc(s.title)}</div>
                <div class="sop-card-summary">${esc(s.summary)}</div>
                <div class="sop-card-meta">
                    <span class="chip">${esc(s.category)}</span>
                    ${s.updated ? `<span class="chip">Updated ${esc(s.updated)}</span>` : ""}
                    ${s.owner ? `<span class="chip">Owner: ${esc(s.owner)}</span>` : ""}
                    ${s.mark_count ? `<span class="chip chip-amber">Some details still being confirmed</span>` : ""}
                </div>
            </a>`).join("");
    }

    let timer = null;
    searchEl.addEventListener("input", () => {
        clearTimeout(timer);
        timer = setTimeout(async () => {
            const q = searchEl.value.trim();
            if (!q) { showList(); return; }
            try {
                const found = await getJSON("/api/sop-search?q=" + encodeURIComponent(q));
                const results = found.results || [];
                resultsEl.innerHTML = results.length
                    ? results.map((r) => `
                        <a class="sop-card" href="#/sop/${esc(r.slug)}">
                            <div class="sop-card-title">${esc(r.title)}</div>
                            <div class="sop-card-summary">${esc(r.snippet)}</div>
                            <div class="sop-card-meta"><span class="chip">${esc(r.category)}</span></div>
                        </a>`).join("")
                    : `<div class="empty-panel">Nothing found for “${esc(q)}”.
                        Try a different word, or browse the categories on the left.</div>`;
            } catch (e) {
                resultsEl.innerHTML = errorPanel(e.message);
            }
        }, 250);
    });

    view.querySelectorAll(".cat-btn").forEach((btn) => {
        btn.addEventListener("click", () => {
            sopCategory = btn.dataset.cat;
            searchEl.value = "";
            view.querySelectorAll(".cat-btn").forEach((b) =>
                b.classList.toggle("active", b === btn));
            showList();
        });
    });

    showList();
}

/* --- SOP detail ------------------------------------------------------------ */

function blockHTML(b) {
    switch (b.type) {
        case "heading":
            return `<h2>${esc(b.text)}</h2>`;
        case "para":
            return `<p>${b.html}</p>`;
        case "bullets":
            return `<ul>${b.items.map((i) => `<li>${i}</li>`).join("")}</ul>`;
        case "image":
            return `<img src="${esc(b.src)}" alt="${esc(b.alt)}">`;
        case "note":
            return `<div class="note-box">${b.html}</div>`;
        case "branch":
            return `<div class="branch">
                <span class="branch-label">DECISION</span>
                <div class="branch-q">If ${b.condition} …</div>
                <div class="branch-row"><span class="branch-yesno branch-yes">YES</span>
                    <span>${b.action}</span></div>
                <div class="branch-row"><span class="branch-yesno branch-no">NO</span>
                    <span>Carry on to the next step.</span></div>
            </div>`;
        case "step":
            return `<div class="step" role="checkbox" aria-checked="false" tabindex="0">
                <span class="step-tick" aria-hidden="true">✓</span>
                <span class="step-text"><span class="step-number">${esc(b.number)}.</span>
                    ${b.html}</span>
            </div>`;
        default:
            return "";
    }
}

async function renderSop(slug) {
    view.innerHTML = `<div class="loading-panel">Opening the guide…</div>`;
    let sop;
    try {
        sop = await getJSON("/api/sops/" + encodeURIComponent(slug));
    } catch (e) {
        view.innerHTML = `${errorPanel(e.message)}
            <p style="text-align:center;margin-top:18px">
                <a class="btn" href="#/sops">Back to the guides</a></p>`;
        return;
    }
    const meta = sop.meta || {};
    view.innerHTML = `
        <a class="btn btn-quiet btn-back" href="#/sops">← All guides</a>
        <div class="sop-header">
            <div>
                <h1 class="sop-title">${esc(sop.title)}</h1>
                <div class="sop-meta">
                    ${meta.category ? `<span class="chip">${esc(meta.category)}</span>` : ""}
                    ${meta.updated ? `<span class="chip">Updated ${esc(meta.updated)}</span>` : ""}
                    ${meta.owner ? `<span class="chip">Owner: ${esc(meta.owner)}</span>` : ""}
                    ${(sop.mark_flags || []).length
                        ? `<span class="chip chip-amber">Some details still being confirmed — ask Mark if unsure</span>` : ""}
                </div>
            </div>
            <button class="btn btn-quiet" id="print-btn">🖨️ Print this guide</button>
        </div>
        <p class="tick-hint">Tap each step as you do it. Ticks are just for you — they reset when you leave the page.</p>
        <div class="sop-blocks">${(sop.blocks || []).map(blockHTML).join("")}</div>`;

    document.getElementById("print-btn").addEventListener("click", () => window.print());

    view.querySelectorAll(".step").forEach((step) => {
        const toggle = (e) => {
            if (e.target.closest("a")) return;  // links inside steps still work
            step.classList.toggle("done");
            step.setAttribute("aria-checked", step.classList.contains("done"));
        };
        step.addEventListener("click", toggle);
        step.addEventListener("keydown", (e) => {
            if (e.key === " " || e.key === "Enter") { e.preventDefault(); toggle(e); }
        });
    });
}

/* --- Reviews ---------------------------------------------------------------- */

async function renderReviews() {
    view.innerHTML = `<div class="loading-panel">Checking the review numbers…</div>`;
    let data;
    try {
        data = await getJSON("/api/reviews/status");
    } catch (e) {
        view.innerHTML = errorPanel(e.message);
        return;
    }
    const head = `
        <a class="btn btn-quiet btn-back" href="#/">← Home</a>
        <h1 class="page-title">Google Reviews</h1>
        <p class="page-sub">The review helper texts happy patients a link to leave us a Google review.</p>`;

    if (!data.connected) {
        view.innerHTML = head + `<div class="empty-panel">${esc(data.message)}</div>`;
        return;
    }
    const n = data.sent_last_7_days;
    view.innerHTML = head + `
        <div class="card stat-card">
            <div class="stat-number">${n == null ? "—" : esc(n)}</div>
            <div class="stat-label">review invitations sent in the last 7 days</div>
            <div class="stat-foot">
                ${data.bot_enabled === true ? `<span class="chip chip-on">Helper is ON</span>` : ""}
                ${data.bot_enabled === false ? `<span class="chip chip-off">Helper is switched OFF</span>` : ""}
                ${data.last_run ? `&nbsp; Last run ${esc(data.last_run)}${data.last_result ? ` — ${esc(data.last_result)}` : ""}` : ""}
            </div>
        </div>
        <div class="card">
            <h2>What this page tells you</h2>
            <p>Nothing to do here day-to-day — it's just a window. If a patient mentions
            they got a review text, this is where it came from. If the number looks stuck
            on zero for a couple of weeks, mention it to Mark.</p>
        </div>`;
}

/* --- Orders ------------------------------------------------------------------- */

async function renderOrders() {
    view.innerHTML = `<div class="loading-panel">Getting the orders list…</div>`;
    let data;
    try {
        data = await getJSON("/api/orders/digest");
    } catch (e) {
        view.innerHTML = errorPanel(e.message);
        return;
    }
    const head = `
        <a class="btn btn-quiet btn-back" href="#/">← Home</a>
        <h1 class="page-title">Orders &amp; Collections</h1>
        <p class="page-sub">What the Optomate helper has seen lately — read-only, Optomate is still the boss.</p>`;

    if (!data.connected) {
        view.innerHTML = head + `<div class="empty-panel">${esc(data.message)}</div>`;
        return;
    }
    view.innerHTML = head + `
        <div class="card">
            <h2>🕶️ Ready to collect — not picked up yet</h2>
            ${data.uncollected != null
                ? `${data.uncollected_updated ? `<div class="updated-line">Last updated ${esc(data.uncollected_updated)}</div>` : ""}
                   <div class="log-text">${esc(data.uncollected.trim() || "Nothing on the list — all collected.")}</div>`
                : `<p>No ready-to-collect list on this computer yet.</p>`}
        </div>
        <div class="card">
            <h2>📦 Recent orders digest</h2>
            ${data.digest != null
                ? `${data.digest_updated ? `<div class="updated-line">Last updated ${esc(data.digest_updated)}</div>` : ""}
                   <div class="log-text">${esc(data.digest.trim() || "Nothing in the digest yet.")}</div>`
                : `<p>No orders digest on this computer yet.</p>`}
        </div>`;
}

/* --- Stock ---------------------------------------------------------------------- */

async function renderStock() {
    view.innerHTML = `<div class="loading-panel">Getting the stock proposals…</div>`;
    let data;
    try {
        data = await getJSON("/api/stock/proposals");
    } catch (e) {
        view.innerHTML = errorPanel(e.message);
        return;
    }
    const head = `
        <a class="btn btn-quiet btn-back" href="#/">← Home</a>
        <h1 class="page-title">Stock Orders</h1>
        <p class="page-sub">Suggested stock orders, one file each. Approving just flags it —
        <strong>nothing is ordered or entered automatically.</strong></p>`;

    if (!data.connected) {
        view.innerHTML = head + `<div class="empty-panel">${esc(data.message)}</div>`;
        return;
    }
    const proposals = data.proposals || [];
    if (!proposals.length) {
        view.innerHTML = head + `<div class="empty-panel">No stock proposals waiting right now. All clear.</div>`;
        return;
    }

    view.innerHTML = head + proposals.map((p, i) => {
        const table = p.error
            ? `<p class="status-msg error">${esc(p.error)}</p>`
            : `<div class="table-scroll"><table class="stock-table">
                 <thead><tr>${p.headers.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead>
                 <tbody>${p.rows.map((r) =>
                     `<tr>${r.map((c) => `<td>${esc(c)}</td>`).join("")}</tr>`).join("")}
                 </tbody></table></div>
               ${p.truncated ? `<p class="updated-line">Showing the first rows — the full file has ${esc(p.row_count)} lines.</p>` : ""}`;
        const actions = p.approved
            ? `<div class="approved-banner">✓ Approved — Mark/Claude will enter this into Optomate.</div>`
            : `<div class="stock-actions">
                 <button class="btn" data-approve="${esc(p.filename)}" data-idx="${i}">
                     ✓ Approved — mark for entry</button>
               </div>
               <div id="confirm-${i}"></div>`;
        return `<div class="card">
            <h2>${esc(p.filename)}</h2>
            ${p.updated ? `<div class="updated-line">File date ${esc(p.updated)} · ${esc(p.row_count)} line${p.row_count === 1 ? "" : "s"}</div>` : ""}
            ${table}
            ${actions}
        </div>`;
    }).join("");

    view.querySelectorAll("[data-approve]").forEach((btn) => {
        btn.addEventListener("click", () => {
            const filename = btn.dataset.approve;
            const holder = document.getElementById("confirm-" + btn.dataset.idx);
            holder.innerHTML = `<div class="confirm-strip">
                <p>This tells Mark the list is checked and OK. Nothing is sent or ordered by itself.</p>
                <button class="btn" data-yes="1">Yes, approve it</button>
                <button class="btn btn-quiet" data-no="1">Go back</button>
                <div class="status-msg" data-msg></div>
            </div>`;
            holder.querySelector("[data-no]").addEventListener("click", () => {
                holder.innerHTML = "";
            });
            holder.querySelector("[data-yes]").addEventListener("click", async (e) => {
                const msg = holder.querySelector("[data-msg]");
                e.target.disabled = true;
                try {
                    const res = await fetch("/api/stock/approve", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ filename }),
                    });
                    const out = await res.json().catch(() => ({}));
                    if (!res.ok) throw new Error(out.error || "Something went wrong.");
                    renderStock();
                } catch (err) {
                    e.target.disabled = false;
                    msg.className = "status-msg error";
                    msg.textContent = err.message;
                }
            });
        });
    });
}

/* --- Lens Finder ------------------------------------------------------------------- */

function fmtPower(v) { return (v >= 0 ? "+" : "") + Number(v).toFixed(2); }
function fmtMoney(v) { return "$" + Number(v).toFixed(2); }
function fmtMM(v) { return parseFloat(v) + "mm"; }

function lensRowHTML(l, extraCellHTML) {
    const warnings = (l.warnings || []).map((w) =>
        `<div class="warn-note">⚠ ${esc(w)}</div>`).join("");
    const meta = [(l.category && l.category !== "Single vision") ? l.category : "",
                  l.form].filter(Boolean).join(" · ");
    return `<tr class="${l.best ? "best-row" : ""}">
        <td><strong>${esc((l.brand + " " + l.name).trim())}</strong>
            ${l.best ? `<span class="badge-best">★ Best value</span>` : ""}
            ${meta ? `<div class="cell-sub design-sub">${esc(meta)}</div>` : ""}
            ${l.add_range ? `<div class="cell-sub">${esc(l.add_range)}</div>` : ""}
            ${l.code ? `<div class="cell-sub code-sub">${esc(l.code)}</div>` : ""}
            ${l.coating ? `<div class="cell-sub">${esc(l.coating)}</div>` : ""}
            ${l.notes ? `<div class="cell-sub">${esc(l.notes)}</div>` : ""}
            ${warnings}</td>
        <td>${l.index != null ? esc(l.index) : "—"}</td>
        <td><span class="chip ${l.type === "stock" ? "chip-on" : ""}">${l.type === "stock" ? "Stock" : "Grind"}</span></td>
        <td>${l.blank_mm != null ? esc(fmtMM(l.blank_mm)) : (l.type === "grind" ? "made to size" : "—")}</td>
        <td>${l.sph_min != null ? `${esc(fmtPower(l.sph_min))} to ${esc(fmtPower(l.sph_max))}` : "not in file"}</td>
        <td>${l.cyl_max != null ? "to −" + Number(l.cyl_max).toFixed(2) : "—"}</td>
        <td>${l.price != null ? esc(fmtMoney(l.price)) : "no price yet"}
            ${l.dearer_by > 0 ? `<div class="cell-sub">+${esc(fmtMoney(l.dearer_by))} vs best</div>` : ""}</td>
        ${extraCellHTML || ""}
    </tr>`;
}

const LENS_TABLE_HEAD = `<tr><th>Lens</th><th>Index</th><th>Type</th>
    <th>Blank</th><th>Sphere</th><th>Cyl</th><th>Price / lens</th></tr>`;

function lensCatalogHTML(cat) {
    if (cat.message) {
        return `<div class="card"><h2>📚 Lens library</h2>
            <div class="empty-panel">${esc(cat.message)}</div></div>`;
    }
    const files = (cat.files || []).map((f) => `
        <span class="chip">${esc(f.filename)} · ${esc(f.count)} lens${f.count === 1 ? "" : "es"}</span>`).join(" ");
    const errors = (cat.files || []).flatMap((f) => f.errors || []);
    return `<div class="card"><h2>📚 Lens library</h2>
        <p>Browse the whole range — narrow by type, index, design and coating, the way you'd
        pick a lens — or just type a name or code. Every detail comes from the price files.</p>
        <div class="lens-filters">
            <select id="lf-cat" class="lens-select" aria-label="Lens type"></select>
            <select id="lf-index" class="lens-select" aria-label="Index"></select>
            <select id="lf-form" class="lens-select" aria-label="Design (spheric / aspheric)"></select>
            <select id="lf-coat" class="lens-select" aria-label="Coating"></select>
        </div>
        <input class="search-box" id="lens-lookup" type="search" autocomplete="off"
            placeholder="…or type a name / code (e.g. stellify · S-NULUX · myself)"
            aria-label="Search lenses">
        <div class="lens-guide-note">
            💡 <strong>Aspheric (Nulux)</strong> — our go-to for plus powers &amp; higher Rx
            (flatter, thinner). <strong>Spherical (Hilux)</strong> — better on curved /
            high-base frames, e.g. wrap sunnies.</div>
        <div id="lens-lookup-results"></div>
        <div class="sop-card-meta" style="margin:14px 0 0">${files}</div>
        ${errors.length ? `<div class="confirm-strip"><p>Some rows couldn't be read:</p>
            ${errors.map((e) => `<div class="warn-note">⚠ ${esc(e)}</div>`).join("")}</div>` : ""}
    </div>`;
}

const JOB_STATUS_CHIP = {
    stock: `<span class="chip chip-on">✅ Stock covers it</span>`,
    grind: `<span class="chip">🛠 Grind job</span>`,
    check: `<span class="chip chip-amber">⚠ Worth a look</span>`,
    none: `<span class="chip chip-amber">⚠ Outside the loaded ranges</span>`,
    no_rx: `<span class="chip chip-off">No Rx on the job</span>`,
};

async function renderLensJobs() {
    const holder = document.getElementById("lens-jobs");
    if (!holder) return;
    let data;
    try {
        data = await getJSON("/api/lenses/jobs");
    } catch (e) {
        return; // the panel is optional — never break the page over it
    }
    if (!data.connected || !(data.jobs || []).length) return;

    holder.innerHTML = `<div class="card">
        <h2>🗒️ Recent lens jobs from Optomate</h2>
        <p class="updated-line">Each job entered in Optomate, checked against the loaded
            price files.${data.updated ? ` Last updated ${esc(data.updated)}.` : ""}
            Nothing is changed or sent — this is a second pair of eyes only.</p>
        ${data.jobs.map((j) => {
            const c = j.check || {};
            const eyes = ["right", "left"].filter((s) => (c.eyes || {})[s])
                .map((s) => `${s === "right" ? "R" : "L"} ${esc(c.eyes[s].rx)}`)
                .join(" · ");
            const chosenNotes = ((c.chosen || {}).notes || []).map((n) =>
                `<div class="warn-note">⚠ ${esc(n)}</div>`).join("");
            return `<div class="job-row">
                <div class="job-head">
                    <strong>Job ${esc(j.job || "?")}</strong>
                    ${JOB_STATUS_CHIP[c.status] || ""}
                    ${j.supplier ? `<span class="chip">${esc(j.supplier)}</span>` : ""}
                    ${j.code ? `<span class="chip">${esc(j.code)}</span>` : ""}
                    ${j.entered ? `<span class="updated-line" style="margin:0">${esc(j.entered)}</span>` : ""}
                </div>
                ${eyes ? `<div class="job-rx">${eyes}${c.min_blank ? ` · blank ≥ ${esc(c.min_blank)}mm` : ""}</div>` : ""}
                <div class="job-verdict">${esc(c.headline || "")}</div>
                ${chosenNotes}
            </div>`;
        }).join("")}
    </div>`;
}

const LOOKUP_LIMIT = 60;
const CAT_ORDER = ["Single vision", "Progressive", "Occupational", "Bifocal"];

function wireLensLookup(cat) {
    const box = document.getElementById("lens-lookup");
    const out = document.getElementById("lens-lookup-results");
    const catEl = document.getElementById("lf-cat");
    const idxEl = document.getElementById("lf-index");
    const formEl = document.getElementById("lf-form");
    const coatEl = document.getElementById("lf-coat");
    if (!box || !out || !catEl) return;

    const rows = (cat.lenses || []).map((l) => ({
        lens: l,
        hay: [l.brand, l.name, l.code, l.coating, l.index, l.category,
              l.form, l.add_range, l.notes].join(" ").toLowerCase(),
    }));

    function fill(el, allLabel, values) {
        el.innerHTML = `<option value="">${esc(allLabel)}</option>` +
            values.map((v) => `<option value="${esc(v)}">${esc(v)}</option>`).join("");
    }
    fill(catEl, "All lens types",
         CAT_ORDER.filter((c) => rows.some((r) => r.lens.category === c)));
    fill(idxEl, "Any index",
         [...new Set(rows.map((r) => r.lens.index).filter((v) => v != null))]
             .sort((a, b) => a - b).map(String));
    fill(formEl, "Spheric & aspheric",
         [...new Set(rows.map((r) => r.lens.form).filter(Boolean))].sort());
    fill(coatEl, "Any coating",
         [...new Set(rows.map((r) => r.lens.coating).filter(Boolean))].sort());

    function apply() {
        const cCat = catEl.value, cIdx = idxEl.value,
              cForm = formEl.value, cCoat = coatEl.value;
        const words = box.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
        if (!(cCat || cIdx || cForm || cCoat || words.length)) {
            out.innerHTML = `<div class="empty-panel">Pick a type, index or coating above,
                or start typing a lens name or code.</div>`;
            return;
        }
        const hits = rows.filter((r) => {
            const l = r.lens;
            if (cCat && l.category !== cCat) return false;
            if (cIdx && String(l.index) !== cIdx) return false;
            if (cForm && l.form !== cForm) return false;
            if (cCoat && l.coating !== cCoat) return false;
            return words.every((w) => r.hay.includes(w));
        }).map((r) => r.lens);
        if (!hits.length) {
            out.innerHTML = `<div class="empty-panel">Nothing loaded matches those filters.
                Try widening them, or it may be a lens we don't have a price file for yet.</div>`;
            return;
        }
        out.innerHTML = `
            <div class="updated-line" style="margin-top:12px">${hits.length} matching
                line${hits.length === 1 ? "" : "s"}${hits.length > LOOKUP_LIMIT
                ? ` — showing the first ${LOOKUP_LIMIT}` : ""}</div>
            <div class="table-scroll"><table class="stock-table">
                <thead>${LENS_TABLE_HEAD}</thead>
                <tbody>${hits.slice(0, LOOKUP_LIMIT).map((l) => lensRowHTML(l)).join("")}</tbody>
            </table></div>`;
    }
    [catEl, idxEl, formEl, coatEl].forEach((el) =>
        el.addEventListener("change", apply));
    let timer = null;
    box.addEventListener("input", () => {
        clearTimeout(timer);
        timer = setTimeout(apply, 150);
    });
}

async function renderLenses() {
    view.innerHTML = `<div class="loading-panel">Getting the lens catalogue…</div>`;
    let cat;
    try {
        cat = await getJSON("/api/lenses");
    } catch (e) {
        view.innerHTML = errorPanel(e.message);
        return;
    }

    view.innerHTML = `
        <a class="btn btn-quiet btn-back" href="#/">← Home</a>
        <h1 class="page-title">Lens Finder</h1>
        <p class="page-sub">Type the Rx (one eye at a time) and see every lens that can make the
        job — cheapest first. A dearer-index stock lens often beats a grind on price.</p>

        <div class="card">
            <h2>🔍 Find the best lens for a job</h2>
            <div class="lens-form">
                <div class="field"><label for="lf-sph">Sphere</label>
                    <input id="lf-sph" inputmode="text" placeholder="-2.75" autocomplete="off"></div>
                <div class="field"><label for="lf-cyl">Cyl (optional)</label>
                    <input id="lf-cyl" inputmode="text" placeholder="-1.25" autocomplete="off"></div>
                <div class="field"><label for="lf-blank">Smallest blank that fits the frame (optional)</label>
                    <input id="lf-blank" inputmode="numeric" placeholder="e.g. 68" autocomplete="off"></div>
                <button class="btn" id="lf-go">Find lenses</button>
            </div>
            <details class="blank-helper">
                <summary>Not sure what blank size the frame needs?</summary>
                <div class="helper-row">
                    <div class="field"><label for="lf-a">Frame eye size (A)</label>
                        <input id="lf-a" inputmode="numeric" placeholder="52"></div>
                    <div class="field"><label for="lf-dbl">Bridge (DBL)</label>
                        <input id="lf-dbl" inputmode="numeric" placeholder="18"></div>
                    <div class="field"><label for="lf-pd">Patient PD</label>
                        <input id="lf-pd" inputmode="numeric" placeholder="62"></div>
                    <div class="helper-out" id="lf-suggest"></div>
                </div>
                <p class="helper-note">Rough rule: eye size + (frame PD − patient PD) + a few mm
                spare. Big decentration or a wide ED frame needs more — when in doubt, size up.</p>
            </details>
            <div id="lens-results"></div>
        </div>

        <div id="lens-jobs"></div>

        <div id="lens-catalog">${lensCatalogHTML(cat)}</div>

        <div class="card">
            <h2>⬆️ Add or update a price list</h2>
            <p>Upload a supplier price CSV (e.g. the Hoya guide turned into a
            spreadsheet). Uploading a file with the same name <strong>replaces</strong> the old
            one — that's how new pricing goes in. The column layout is described in the
            <code>lenses\\README.md</code> file — give a Claude session the supplier's PDF guide
            and point it at that file to make the CSV.</p>
            <div class="lens-form">
                <div class="field"><label for="lf-upname">Supplier / file name (optional)</label>
                    <input id="lf-upname" placeholder="hoya" autocomplete="off"></div>
                <div class="field"><label for="lf-upfile">CSV file</label>
                    <input id="lf-upfile" type="file" accept=".csv,text/csv"></div>
                <button class="btn" id="lf-upload">Upload it</button>
            </div>
            <div class="status-msg" id="lf-upmsg"></div>
        </div>`;

    wireLensLookup(cat);
    renderLensJobs();  // fills #lens-jobs only when the agent file exists

    const sphEl = document.getElementById("lf-sph");
    const cylEl = document.getElementById("lf-cyl");
    const blankEl = document.getElementById("lf-blank");
    const resultsEl = document.getElementById("lens-results");

    async function runFind() {
        const params = new URLSearchParams();
        params.set("sph", sphEl.value.trim());
        if (cylEl.value.trim()) params.set("cyl", cylEl.value.trim());
        if (blankEl.value.trim()) params.set("blank", blankEl.value.trim());
        resultsEl.innerHTML = `<div class="loading-panel">Checking the catalogue…</div>`;
        let data;
        try {
            data = await getJSON("/api/lenses/find?" + params.toString());
        } catch (e) {
            resultsEl.innerHTML = errorPanel(e.message);
            return;
        }
        if (data.catalog_message) {
            resultsEl.innerHTML = `<div class="empty-panel">${esc(data.catalog_message)}</div>`;
            return;
        }
        const rxLine = `Checked for <strong>${esc(data.rx.display)}</strong>` +
            (data.rx.transposed ? ` <span class="chip chip-amber">plus cyl — transposed to minus form first</span>` : "") +
            (data.min_blank != null ? `, needing a blank of at least ${esc(fmtMM(data.min_blank))}` : "");
        const options = data.options || [];
        const misses = data.misses || [];
        const SHOW = 15;
        const shown = options.slice(0, SHOW);
        const rest = options.slice(SHOW);
        resultsEl.innerHTML = `
            <div class="updated-line" style="margin-top:18px">${rxLine}</div>
            <div class="${options.length && options[0].best ? "approved-banner" : "empty-panel"}"
                 style="margin-bottom:16px">${esc(data.verdict)}</div>
            ${shown.length ? `<div class="table-scroll"><table class="stock-table">
                <thead>${LENS_TABLE_HEAD}</thead>
                <tbody>${shown.map((l) => lensRowHTML(l)).join("")}</tbody>
            </table></div>` : ""}
            ${rest.length ? `<details class="miss-details">
                <summary>Show the other ${rest.length} dearer options</summary>
                <div class="table-scroll"><table class="stock-table">
                    <thead>${LENS_TABLE_HEAD}</thead>
                    <tbody>${rest.map((l) => lensRowHTML(l)).join("")}</tbody>
                </table></div>
            </details>` : ""}
            ${misses.length ? `<details class="miss-details">
                <summary>Why the other ${misses.length} lens${misses.length === 1 ? " doesn't" : "es don't"} fit</summary>
                ${misses.map((m) => `<div class="miss-item">
                    <strong>${esc((m.brand + " " + m.name).trim())}</strong> —
                    ${esc(m.reasons.join("; "))}</div>`).join("")}
            </details>` : ""}`;
    }

    document.getElementById("lf-go").addEventListener("click", runFind);
    [sphEl, cylEl, blankEl].forEach((el) => el.addEventListener("keydown", (e) => {
        if (e.key === "Enter") runFind();
    }));

    // Blank-size helper: eye size + total decentration + 2mm spare.
    const aEl = document.getElementById("lf-a");
    const dblEl = document.getElementById("lf-dbl");
    const pdEl = document.getElementById("lf-pd");
    const suggestEl = document.getElementById("lf-suggest");
    function suggest() {
        const a = parseFloat(aEl.value), dbl = parseFloat(dblEl.value),
              pd = parseFloat(pdEl.value);
        if (!(a > 0) || !(dbl >= 0) || !(pd > 0)) { suggestEl.innerHTML = ""; return; }
        const size = Math.ceil((a + 2) + Math.max(a + dbl - pd, 0) + 2);
        suggestEl.innerHTML = `<span>Suggested: <strong>${esc(size)}mm</strong></span>
            <button class="btn btn-quiet" id="lf-usesize" type="button">Use it</button>`;
        document.getElementById("lf-usesize").addEventListener("click", () => {
            blankEl.value = size;
        });
    }
    [aEl, dblEl, pdEl].forEach((el) => el.addEventListener("input", suggest));

    // Upload a price list CSV.
    const upBtn = document.getElementById("lf-upload");
    upBtn.addEventListener("click", async () => {
        const msg = document.getElementById("lf-upmsg");
        const file = document.getElementById("lf-upfile").files[0];
        if (!file) {
            msg.className = "status-msg error";
            msg.textContent = "Pick a CSV file first.";
            return;
        }
        const fd = new FormData();
        fd.append("file", file);
        const name = document.getElementById("lf-upname").value.trim();
        if (name) fd.append("name", name);
        upBtn.disabled = true;
        msg.className = "status-msg";
        msg.textContent = "Uploading…";
        try {
            const res = await fetch("/api/lenses/upload", { method: "POST", body: fd });
            const out = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(out.error || "Something went wrong.");
            msg.className = "status-msg ok";
            msg.textContent = out.message +
                ((out.row_errors || []).length
                    ? ` (${out.row_errors.length} row${out.row_errors.length === 1 ? "" : "s"} couldn't be read — details in the loaded list below.)`
                    : "");
            const fresh = await getJSON("/api/lenses");
            document.getElementById("lens-catalog").innerHTML = lensCatalogHTML(fresh);
            wireLensLookup(fresh);
        } catch (err) {
            msg.className = "status-msg error";
            msg.textContent = err.message;
        } finally {
            upBtn.disabled = false;
        }
    });
}

/* --- Go ---------------------------------------------------------------------------- */

initStaffPicker();
route();
