/* Patient price sheets on screen (Mark, 10 Nov 2026). Loaded by hub/pricesheets.py into the approved
   sheet. Served fresh from static/, so changes here need no Hub restart. Words and prices are never
   written here: the detail page re-uses the sheet's own elements. */
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
  #ps-detail { display:none; position:fixed; inset:0; background:#fff; z-index:50; overflow:auto; padding:8mm 12mm; }
  #ps-detail.show { display:block; }
  #ps-detail .back { display:inline-block; font-size:22px; color:#0F2DB3; text-decoration:none; padding:3mm 0 6mm; cursor:pointer; }
  #ps-detail .wrap { display:flex; gap:8mm; align-items:flex-start; zoom:1.5; }
  #ps-detail .wrap > .card { flex:0 0 38%; max-width:38%; cursor:default; }
  #ps-detail .side { min-width:0; }
  #ps-detail .side { flex:1; display:flex; flex-direction:column; gap:6mm; }
  #ps-detail .side > * { margin:0 !important; }
  #ps-detail .side .row, #ps-detail .side > div { display:block !important; }
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
  var title = (main.querySelector("h1") || {}).textContent || "";
  var detail = document.createElement("div"); detail.id = "ps-detail";
  document.body.appendChild(detail);

  function close() { detail.className = ""; detail.innerHTML = ""; if (location.hash) history.replaceState(null, "", location.pathname); }
  window.addEventListener("popstate", function () { if (!location.hash) { detail.className = ""; detail.innerHTML = ""; } });

  rows.querySelectorAll(".card").forEach(function (card, i) {
    var more = document.createElement("div"); more.className = "ps-more"; more.textContent = "See details ›";
    card.appendChild(more);
    card.addEventListener("click", function () {
      detail.innerHTML = "";
      var back = document.createElement("a"); back.className = "back";
      back.textContent = "‹ All " + title.replace(/\.$/, "").toLowerCase();
      back.addEventListener("click", function () { history.back(); });
      var wrap = document.createElement("div"); wrap.className = "wrap";
      var big = card.cloneNode(true); var m = big.querySelector(".ps-more"); if (m) m.remove();
      wrap.appendChild(big);
      var side = document.createElement("div"); side.className = "side";
      // the sheet's own sections after the cards: add-ons, "on every lens", etc.
      var sib = rows.nextElementSibling;
      while (sib) { side.appendChild(sib.cloneNode(true)); sib = sib.nextElementSibling; }
      if (side.children.length) wrap.appendChild(side);
      detail.appendChild(back); detail.appendChild(wrap);
      detail.className = "show";
      history.pushState({ card: i }, "", "#lens-" + (i + 1));
      detail.scrollTop = 0;
    });
  });
})();
