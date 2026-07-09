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

/* --- Go ---------------------------------------------------------------------------- */

initStaffPicker();
route();
