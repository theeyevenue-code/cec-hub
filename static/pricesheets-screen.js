/* Patient price sheets on screen (Mark, 10 Nov 2026). Loaded by hub/pricesheets.py into the approved
   sheet. Served fresh from static/, so changes here need no Hub restart.

   Tap a lens -> its page: pick a thickness, tap add-ons, the total adds up (Mark: "i can just click and
   it adds options and price"). Every price is READ FROM THE SHEET on the page, never typed here. The
   only things written here are the sheet's own rules, from its small print:
     - Single vision: blue-light and PhotoFusion are lens materials (one or the other); where the card
       says "Blue-light and PhotoFusion include grind", grind is not added again.
     - Multifocal / Anti-Fatigue: "Not on 1.53 rimless" (blue-light), light-reactive "1.50, 1.60, 1.67;
       1.74 on Everyday and Signature. Not on 1.53", PhotoFusion "Anti-Fatigue only. Not on 1.53";
       blue-light, light-reactive and tint are one-or-the-other; tint solid or graduated.
     - Bifocal and sunglasses: "Other lens types" and "Basic tint" are whole prices that replace the lens.
     - Anything priced "from" or "ask" is shown, never added. */
(function () {
  var css = `
  html, body { overflow-x:hidden; }
  .navlinks { gap:7mm !important; font-size:15px !important; }
  .navlinks a { color:inherit; text-decoration:none; padding:4mm 0; }
  .navlinks a.on { font-weight:600; color:#0E0F11; border-bottom:2px solid #0F2DB3; }
  .hub-home { font-size:15px; color:#606A76; text-decoration:none; margin-right:4mm; }
  main .rows .card { cursor:pointer; }
  main .rows .card:active { opacity:.85; }
  .ps-more { margin-top:3mm; font-size:12px; font-weight:600; color:#0F2DB3; }
  #ps-detail { display:none; position:fixed; inset:0; background:#fff; z-index:50; overflow:auto; padding:6mm 10mm;
               font-family:'Hind',Arial,sans-serif; color:#32373E; }
  #ps-detail.show { display:block; }
  #ps-detail .back { display:inline-block; font-size:20px; color:#0F2DB3; padding:2mm 0 5mm; cursor:pointer; }
  #ps-detail .top { display:flex; justify-content:space-between; align-items:baseline; gap:8mm; }
  #ps-detail .lname { font-size:34px; font-weight:700; color:#0E0F11; line-height:1.1; }
  #ps-detail .lsub { font-size:17px; color:#606A76; margin-top:1mm; }
  #ps-detail .total { text-align:right; }
  #ps-detail .total small { display:block; font-size:15px; color:#606A76; }
  #ps-detail .total b { font-size:52px; font-weight:700; color:#0F2DB3; line-height:1; }
  #ps-detail .grp { margin-top:7mm; }
  #ps-detail .grp h3 { font-size:14px; letter-spacing:1.5px; text-transform:uppercase; color:#606A76; font-weight:600; margin-bottom:3mm; }
  #ps-detail .opts { display:flex; flex-wrap:wrap; gap:3mm; }
  #ps-detail .opt { border:2px solid #DCE3E9; background:#F2F5F8; padding:3.5mm 5mm; min-width:46mm; cursor:pointer;
                    display:flex; justify-content:space-between; gap:6mm; font-size:18px; align-items:baseline; }
  #ps-detail .opt small { color:#606A76; font-size:14px; margin-left:2mm; }
  #ps-detail .opt b { color:#0E0F11; font-weight:600; white-space:nowrap; }
  #ps-detail .opt.on { border-color:#0F2DB3; background:#E8EEFC; }
  #ps-detail .opt.on b { color:#0F2DB3; }
  #ps-detail .opt.off { opacity:.35; cursor:default; }
  #ps-detail .why { font-size:14px; color:#606A76; margin-top:2mm; min-height:18px; }
  #ps-detail .info { font-size:15px; color:#606A76; margin-top:2mm; }
  #ps-detail .info b { color:#32373E; }
  #ps-detail .inc { margin-top:8mm; font-size:15px; color:#606A76; }
  @media print { #ps-detail, .hub-home, .ps-more { display:none !important; } }`;
  var st = document.createElement("style"); st.textContent = css; document.head.appendChild(st);

  function fit() {
    var w = document.documentElement.clientWidth, b = document.body;
    b.style.zoom = ""; var full = b.scrollWidth || w;
    if (full > w) b.style.zoom = (w / full).toFixed(3);
  }
  window.addEventListener("resize", fit); window.addEventListener("load", fit); fit();

  var main = document.querySelector("main");
  var rows = main && main.querySelector(".rows");
  if (!rows) return;
  var title = ((main.querySelector("h1") || {}).textContent || "").trim();
  var sheet = /single vision/i.test(title) ? "sv" : /multifocal/i.test(title) ? "mf" : /anti-fatigue/i.test(title) ? "af"
            : /bifocal/i.test(title) ? "bf" : /sunglasses/i.test(title) ? "sun" : "other";
  var detail = document.createElement("div"); detail.id = "ps-detail";
  document.body.appendChild(detail);
  window.addEventListener("popstate", function () { if (!location.hash) { detail.className = ""; detail.innerHTML = ""; } });

  function txt(el) {                    // text with a space between child elements ("1.60 Thin & light")
    if (!el) return "";
    var parts = [];
    el.childNodes.forEach(function (n) { parts.push(n.nodeType === 3 ? n.textContent : " " + n.textContent + " "); });
    return parts.join("").replace(/\s+/g, " ").trim();
  }
  function money(s) { var m = /\$\s*([\d,]+)/.exec(s || ""); return m ? Number(m[1].replace(/,/g, "")) : null; }
  function fmt(n) { return "$" + n.toLocaleString("en-AU"); }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }

  /* ---- read one card: thickness options, card add-ons, alternatives (all prices from the page) ---- */
  function readCard(card) {
    var name = txt(card.querySelector(".name")), lens = txt(card.querySelector(".lens"));
    var thick = [], adds = [], alts = [], note = txt(card.querySelector(".mto"));
    var idx = card.querySelector(".idx");
    if (idx) {                                         // SV / bifocal: one thickness per card
      thick.push({ label: txt(idx) + " " + name, idx: txt(idx), price: money(txt(card.querySelector(".price"))) });
    }
    var from = card.querySelector(".from");
    var ladder = card.querySelector(".ladder");
    if (ladder && ladder.classList.contains("coat3")) {          // sunglasses: index x coat table
      var heads = Array.prototype.map.call(ladder.querySelectorAll(".r.hd b"), txt);
      ladder.querySelectorAll(".r:not(.hd)").forEach(function (r) {
        var lab = txt(r.querySelector("span")), ix = txt(r.querySelector("span small"));
        r.querySelectorAll("b").forEach(function (b, k) {
          var p = money(txt(b));
          if (p != null) thick.push({ label: lab + " · " + (heads[k] || ""), idx: ix, price: p });
        });
      });
      ladder.querySelectorAll(".full").forEach(function (r) {
        var lab = txt(r.querySelector("span")).replace(txt(r.querySelector("span small")), "").trim();
        var p = txt(r.querySelector("b"));
        if (!r.querySelector("b")) return;
        if (/^\+/.test(p)) adds.push({ key: "grad", label: lab, price: money(p) });
        else alts.push({ label: lab + " (" + txt(r.querySelector("span small")) + ")", price: money(p), thick: true });
      });
    } else if (from) {                                 // multifocal / anti-fatigue ladder
      var fl = txt(from.querySelector("small"));
      thick.push({ label: fl, idx: fl.split(" ")[0], price: money(txt(from.querySelector(".price"))) });
      if (ladder) ladder.querySelectorAll(":scope > div").forEach(function (r) {
        var ix = txt(r.querySelector("span")).split(" ")[0];
        thick.push({ label: txt(r.querySelector("span")), idx: ix, price: money(txt(r.querySelector("b"))) });
      });
    }
    var cadds = card.querySelector(".adds");
    if (cadds) cadds.querySelectorAll(":scope > div").forEach(function (r) {
      var lab = txt(r.querySelector("span")), p = txt(r.querySelector("b"));
      if (!lab) return;
      if (/^\+/.test(p)) adds.push({ key: /grind/i.test(lab) ? "grind" : /blue/i.test(lab) ? "blue" : /photo/i.test(lab) ? "photo" : lab, label: lab, price: money(p) });
      else if (money(p) != null) alts.push({ label: lab, price: money(p) });
      else alts.push({ label: lab, price: null });
    });
    return { name: name, lens: lens, thick: thick, adds: adds, alts: alts, includesGrind: /include grind/i.test(note) };
  }

  /* ---- the sheet-wide add-ons (.ups), with "from"/"ask" kept as information only ---- */
  function readUps() {
    var out = [], info = [];
    document.querySelectorAll("main .ups .u").forEach(function (u) {
      var nameEl = u.querySelector(".un"), priceTxt = txt(nameEl.querySelector("span"));
      var label = txt(nameEl).replace(priceTxt, "").trim(), desc = txt(u.querySelector(".ud"));
      var key = /blue/i.test(label) ? "blue" : /light-reactive/i.test(label) ? "react" : /photofusion/i.test(label) ? "photo"
              : /^tint/i.test(label) ? "tint" : label;
      if (/from|ask/i.test(priceTxt) || /ask which/i.test(desc) || sheet === "sun") { info.push({ label: label, price: priceTxt, desc: desc }); return; }
      out.push({ key: key, label: label, price: money(priceTxt), desc: desc });
      if (key === "tint") {
        var g = u.querySelector(".ud b");
        if (g && /graduated/i.test(txt(g))) out.push({ key: "tintgrad", label: "Graduated tint", price: money(txt(g)), desc: "" });
      }
    });
    return { adds: out, info: info };
  }

  /* ---- the sheet's rules (from its small print) ---- */
  function allowed(key, s, c) {
    var ix = s.thick ? s.thick.idx : "";
    if ((key === "blue") && ix === "1.53") return "Not on 1.53 rimless.";
    if (key === "react") {
      if (ix === "1.53") return "Not on 1.53.";
      if (ix === "1.74" && !/everyday|signature/i.test(c.name)) return "1.74 on Everyday and Signature only.";
    }
    if (key === "photo" && sheet === "af") {
      if (!/anti-fatigue/i.test(c.name)) return "Anti-Fatigue only.";
      if (ix === "1.53") return "Not on 1.53.";
    }
    return "";
  }
  var EXCLUSIVE = { blue: ["photo", "react", "tint", "tintgrad"], photo: ["blue"], react: ["blue", "tint", "tintgrad"],
                    tint: ["blue", "react", "tintgrad"], tintgrad: ["blue", "react", "tint"] };

  function open(card, i) {
    var c = readCard(card), ups = readUps();
    var adds = c.adds.concat(ups.adds);
    var s = { thick: c.thick.length === 1 ? c.thick[0] : null, alt: null, on: {} };
    if (!s.thick && c.thick.length) s.thick = c.thick[0];

    function draw() {
      // drop any add-on the current thickness no longer allows
      Object.keys(s.on).forEach(function (k) { if (allowed(k, s, c)) delete s.on[k]; });
      var base = s.alt && s.alt.price != null ? s.alt.price : (s.thick ? s.thick.price : 0);
      var lines = [], total = base;
      adds.forEach(function (a) {
        if (!s.on[a.key]) return;
        if (a.key === "grind" && c.includesGrind && (s.on.blue || s.on.photo)) return;
        total += a.price || 0;
      });
      var h = '<a class="back">‹ All ' + esc(title.replace(/\.$/, "").toLowerCase()) + '</a>' +
        '<div class="top"><div><div class="lname">' + esc(c.name || (s.thick && s.thick.label) || "") + '</div>' +
        '<div class="lsub">' + esc(c.lens) + '</div></div>' +
        '<div class="total"><small>Your lenses, per pair</small><b>' + fmt(total) + '</b></div></div>';
      if (c.thick.length > 1) {
        h += '<div class="grp"><h3>Thickness</h3><div class="opts">' + c.thick.map(function (t, k) {
          return '<div class="opt' + (!s.alt && s.thick === t ? " on" : "") + '" data-t="' + k + '"><span>' + esc(t.label) + '</span><b>' + fmt(t.price) + '</b></div>';
        }).join("") + '</div></div>';
      }
      var priced = c.alts.filter(function (a) { return a.price != null; });
      if (priced.length) {
        h += '<div class="grp"><h3>' + (sheet === "sun" ? "Or" : "Lens type") + '</h3><div class="opts">' +
          (sheet === "sun" ? "" : '<div class="opt' + (!s.alt ? " on" : "") + '" data-alt="-1"><span>Clear</span><b>' + fmt(s.thick ? s.thick.price : 0) + '</b></div>') +
          priced.map(function (a, k) {
            return '<div class="opt' + (s.alt === a ? " on" : "") + '" data-alt="' + k + '"><span>' + esc(a.label) + '</span><b>' + fmt(a.price) + '</b></div>';
          }).join("") + '</div>';
        var notes = c.alts.filter(function (a) { return a.price == null; }).map(function (a) { return a.label; });
        if (notes.length) h += '<div class="info">' + esc(notes.join(" · ")) + '</div>';
        h += '</div>';
      } else if (c.alts.length) {
        h += '<div class="grp"><h3>Lens type</h3><div class="info">' + esc(c.alts.map(function (a) { return a.label; }).join(" · ")) + '</div></div>';
      }
      if (adds.length && !s.alt) {
        h += '<div class="grp"><h3>Add-ons</h3><div class="opts">' + adds.map(function (a, k) {
          var no = allowed(a.key, s, c), on = !!s.on[a.key];
          var incl = a.key === "grind" && c.includesGrind && (s.on.blue || s.on.photo);
          return '<div class="opt' + (on ? " on" : "") + (no || incl ? " off" : "") + '" data-a="' + k + '"' + (no ? ' title="' + esc(no) + '"' : "") +
                 '><span>' + esc(a.label) + (incl ? '<small>included</small>' : "") + '</span><b>+' + fmt(a.price || 0) + '</b></div>';
        }).join("") + '</div><div class="why"></div></div>';
      }
      if (ups.info.length) {
        h += '<div class="grp"><h3>Also, ask us</h3>' + ups.info.map(function (x) {
          return '<div class="info"><b>' + esc(x.label) + ' ' + esc(x.price) + '</b> ' + esc(x.desc) + '</div>';
        }).join("") + '</div>';
      }
      var inc = document.querySelector("main .inc, main .bottom");
      if (inc) h += '<div class="inc">' + esc(txt(inc)) + '</div>';
      detail.innerHTML = h;
      detail.querySelector(".back").onclick = function () { history.back(); };
      detail.querySelectorAll("[data-t]").forEach(function (el) { el.onclick = function () { s.thick = c.thick[+el.dataset.t]; s.alt = null; draw(); }; });
      detail.querySelectorAll("[data-alt]").forEach(function (el) { el.onclick = function () { var k = +el.dataset.alt; s.alt = k < 0 ? null : priced[k]; if (s.alt) s.on = {}; draw(); }; });
      detail.querySelectorAll("[data-a]").forEach(function (el) {
        el.onclick = function () {
          var a = adds[+el.dataset.a], why = detail.querySelector(".why"), no = allowed(a.key, s, c);
          if (no) { why.textContent = a.label + ": " + no; return; }
          if (a.key === "grind" && c.includesGrind && (s.on.blue || s.on.photo)) return;
          if (s.on[a.key]) delete s.on[a.key];
          else { (EXCLUSIVE[a.key] || []).forEach(function (x) { delete s.on[x]; }); s.on[a.key] = true; }
          draw();
        };
      });
    }
    draw();
    detail.className = "show";
    history.pushState({ card: i }, "", "#lens-" + (i + 1));
    detail.scrollTop = 0;
  }

  rows.querySelectorAll(".card").forEach(function (card, i) {
    var more = document.createElement("div"); more.className = "ps-more"; more.textContent = "Build your lenses ›";
    card.appendChild(more);
    card.addEventListener("click", function () { open(card, i); });
  });
})();
