/* ============================================================
   Dental Info prototype — hash-routed SPA, no build step, no backend.
   Views: #/browse  #/case/:id  #/contribute  #/verify
   ============================================================ */

/* ── State ───────────────────────────────────────────────── */
const state = {
  query: "",
  procedure: null,        // leaf procedure id
  complications: new Set(),
  tools: new Set(),
  sort: "recent",
};

/* Cases and the signed-in user come from the API now, not data.js. */
let CASES = [];
let SESSION = null;       // null = signed out

/* ── API ─────────────────────────────────────────────────── */
const NO_API_MESSAGE =
  "No backend at this address. This page is being served without its API — " +
  "run it locally with “python server/app.py” and open http://localhost:8000.";

/* The CSRF cookie is readable on purpose: we echo it back in a header.
   A cross-site page can make the browser SEND cookies but cannot READ
   them, so it cannot produce the matching header. */
function csrfToken() {
  const m = document.cookie.match(/(?:^|;\s*)dental_info_csrf=([^;]*)/);
  return m ? decodeURIComponent(m[1]) : "";
}

const MUTATING = ["POST", "PUT", "PATCH", "DELETE"];

const API = {
  async call(method, path, body) {
    const headers = {};
    if (body) headers["Content-Type"] = "application/json";
    if (MUTATING.includes(method)) {
      const t = csrfToken();
      if (t) headers["X-CSRF-Token"] = t;
    }
    let res;
    try {
      res = await fetch(path, {
        method,
        credentials: "same-origin",
        headers,
        body: body ? JSON.stringify(body) : undefined,
      });
    } catch (netErr) {
      // fetch itself failed: offline, wrong origin, or opened via file://
      const err = new Error(NO_API_MESSAGE);
      err.noApi = true;
      throw err;
    }

    let payload = null;
    try { payload = await res.json(); } catch (e) { /* not JSON */ }

    // A non-JSON error body means we never reached this app's API — the host
    // answered for it. Distinguish the two cases, because the fix differs:
    //   404 → no backend deployed at this address
    //   5xx → backend IS deployed but crashed before it could reply
    if (payload === null && !res.ok) {
      const err = new Error(
        res.status >= 500
          ? `The backend is deployed but crashed (HTTP ${res.status}). ` +
            `Open /api/health for diagnostics, or check the Vercel function logs.`
          : NO_API_MESSAGE
      );
      err.noApi = res.status < 500;
      err.crashed = res.status >= 500;
      err.status = res.status;
      throw err;
    }

    if (!res.ok) {
      const err = new Error((payload && payload.error) || `Request failed (${res.status})`);
      err.status = res.status;
      throw err;
    }
    return payload;
  },
  me:        ()          => API.call("GET", "/api/me"),
  login:     (email, pw) => API.call("POST", "/api/login", { email, password: pw }),
  logout:    ()          => API.call("POST", "/api/logout"),
  posts:     ()          => API.call("GET", "/api/posts"),
  createPost:(data)      => API.call("POST", "/api/posts", data),
  updatePost:(id, data)  => API.call("PUT", "/api/posts/" + encodeURIComponent(id), data),
  deletePost:(id)        => API.call("DELETE", "/api/posts/" + encodeURIComponent(id)),

  signup:    (data)      => API.call("POST", "/api/signup", data),
  myRequest: ()          => API.call("GET", "/api/verification"),
  submitLicense:(data)   => API.call("POST", "/api/verification", data),
  vQueue:    ()          => API.call("GET", "/api/verification/queue"),
  decide:    (id, d, n)  => API.call("POST", `/api/verification/${encodeURIComponent(id)}/decide`,
                                     { decision: d, note: n }),
};

/* Pull fresh session + cases, then repaint. */
async function refresh({ session = true, posts = true } = {}) {
  const jobs = [];
  if (session) jobs.push(API.me().then((r) => { SESSION = r.user; }));
  if (posts)   jobs.push(API.posts().then((r) => { CASES = r.posts; }));
  await Promise.all(jobs);
}

/* ── Tiny helpers ────────────────────────────────────────── */
const $  = (s, r = document) => r.querySelector(s);
const el = (h) => { const t = document.createElement("template"); t.innerHTML = h.trim(); return t.content.firstElementChild; };
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;" }[c]));
const initials = (name) => name.replace(/^Dr\.?\s*/i, "").split(/\s+/).slice(0, 2).map((w) => w[0]).join("").toUpperCase();
const fmtDate = (iso) => new Date(iso + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
const fmtNum  = (n) => (n >= 1000 ? (n / 1000).toFixed(1).replace(/\.0$/, "") + "k" : String(n));

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toast._id);
  toast._id = setTimeout(() => t.classList.remove("show"), 2600);
}

const ICON = {
  check:  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="m20 6-11 11-5-5"/></svg>',
  eye:    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="3"/></svg>',
  msg:    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a8 8 0 0 1-8 8H7l-4 3v-11a8 8 0 0 1 8-8h2a8 8 0 0 1 8 8z"/></svg>',
  image:  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="m21 15-5-5L5 21"/></svg>',
  video:  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="m10 9 5 3-5 3z"/><rect x="2" y="4" width="20" height="16" rx="3"/></svg>',
  lock:   '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="10" width="16" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/></svg>',
  clock:  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.5 2"/></svg>',
  shield: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/><path d="m9 12 2 2 4-4"/></svg>',
  upload: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 16V4"/><path d="m7 9 5-5 5 5"/><path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/></svg>',
  plus:   '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>',
  chev:   '<svg class="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="m9 6 6 6-6 6"/></svg>',
  left:   '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="m14 6-6 6 6 6"/></svg>',
  bookmk: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 4h12v17l-6-4-6 4z"/></svg>',
  search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>',
};

const verifiedBadge = () => `<span class="verified">${ICON.check}Verified</span>`;

/* Authorisation questions, answered by the server and mirrored here so the
   UI can hide what the API would refuse. The server is the real gate. */
const signedIn  = () => SESSION !== null;
const canPost   = () => !!(SESSION && SESSION.can_post);
const isAdmin   = () => !!(SESSION && SESSION.is_admin);
const canComment = () => !!(SESSION && SESSION.can_comment);
const isMine    = (c) => !!(SESSION && c && c.author && c.author.id === SESSION.id);

/* Mirrors the server's rule exactly:
     admin       -> any case          (can_delete_any)
     contributor -> only their own    (can_delete_own + ownership)
     reader      -> nothing           (neither grant)
   Previously this treated ownership alone as sufficient, which would offer
   Delete to a read-only account on its own case. The server would still have
   refused, but the button should never have been there. */
const canDelete = (c) => !!(SESSION && (
  SESSION.can_delete_any || (SESSION.can_delete_own && isMine(c))
));
const canEdit   = (c) => !!(SESSION && (
  SESSION.can_edit_any || (SESSION.can_edit_own && isMine(c))
));

/* Difficulty as an ordinal 3-step meter. The text label is always
   rendered alongside, so the hue never carries the meaning alone. */
function diffMeter(level) {
  return `<span class="diff" data-level="${level}" title="${level} difficulty">
    <span class="diff-track"><i class="diff-seg"></i><i class="diff-seg"></i><i class="diff-seg"></i></span>
    <span class="diff-label">${esc(level)}</span>
  </span>`;
}

/* computed on demand — CASES is loaded from the API after boot */
const allTools = () => [...new Set(CASES.flatMap((c) => c.tools || []))].sort();

/* ── Filtering ───────────────────────────────────────────── */
function procLabel(id) {
  for (const g of PROCEDURES) {
    const hit = g.children.find((c) => c.id === id);
    if (hit) return { group: g.label, leaf: hit.label };
  }
  return null;
}
const countFor = (id) => CASES.filter((c) => c.procedure === id).length;
const countForGroup = (g) => g.children.reduce((n, c) => n + countFor(c.id), 0);
const isFiltered = () => !!(state.procedure || state.query.trim() || state.complications.size || state.tools.size);

function filteredCases() {
  const q = state.query.trim().toLowerCase();
  const out = CASES.filter((c) => {
    if (state.procedure && c.procedure !== state.procedure) return false;
    if (state.complications.size && !(c.complications || []).some((x) => state.complications.has(x))) return false;
    if (state.tools.size && !(c.tools || []).some((x) => state.tools.has(x))) return false;
    if (!q) return true;
    const hay = [c.title, c.summary, c.presentation, c.unusual, c.procedurePath,
                 (c.complications || []).join(" "), (c.tools || []).join(" "), c.author.name].join(" ").toLowerCase();
    return q.split(/\s+/).every((w) => hay.includes(w));
  });
  const by = { recent: (a, b) => b.date.localeCompare(a.date),
               reads:  (a, b) => b.reads - a.reads,
               saves:  (a, b) => b.saves - a.saves };
  return out.sort(by[state.sort]);
}

/* ── Hero ────────────────────────────────────────────────── */
/* Stat tiles: label + value only, no plot — so no hover layer needed.
   Values are derived from the real data set, not invented. */
function heroStats() {
  const contributors = new Set(CASES.map((c) => c.author.name)).size;
  const countries = new Set(CASES.map((c) => c.author.location.split(",").pop().trim())).size;
  const procedures = PROCEDURES.reduce((n, g) => n + g.children.length, 0);
  return [
    { label: "Documented cases", value: CASES.length },
    { label: "Verified contributors", value: contributors },
    { label: "Procedure categories", value: procedures },
    { label: "Countries represented", value: countries },
  ];
}

function heroFull() {
  const stats = heroStats();
  return el(`<section class="hero">
    <div class="hero-inner">
      <span class="hero-eyebrow">
        <span class="vpill">${ICON.check}</span>
        Every contributor is a license-verified dentist
      </span>
      <h1>The cases that <span class="grad">go wrong</span>,<br>written up properly.</h1>
      <p>A structured, procedure-indexed record of real complications — what happened,
         which tools were used, and exactly how it was resolved. Not a forum thread.</p>
    </div>
    <div class="hero-cta">
      <a class="btn btn-light" href="#/contribute">${ICON.plus} Document a case</a>
      <button class="btn btn-light" id="heroSearch">${ICON.search} Search the library</button>
    </div>
    <div class="kpi-row">
      ${stats.map((s) => `<div class="kpi">
        <span class="kpi-val">${s.value}</span>
        <span class="kpi-label">${esc(s.label)}</span>
      </div>`).join("")}
    </div>
  </section>`);
}

function heroCompact(p, count) {
  const bits = [];
  if (state.query.trim()) bits.push(`matching “${esc(state.query.trim())}”`);
  if (state.complications.size) bits.push(`${state.complications.size} complication filter${state.complications.size > 1 ? "s" : ""}`);
  if (state.tools.size) bits.push(`${state.tools.size} tool filter${state.tools.size > 1 ? "s" : ""}`);
  return el(`<section class="hero compact">
    <div class="hero-inner">
      ${p ? `<p class="hero-eyebrow" style="margin-bottom:14px">${esc(p.group)}</p>` : ""}
      <h1>${p ? esc(p.leaf) : "Search results"}</h1>
      <p>${count} case${count === 1 ? "" : "s"}${bits.length ? " · " + bits.join(" · ") : ""}</p>
    </div>
    <div class="hero-cta">
      <button class="btn btn-light btn-sm" id="clearAll">Clear all filters</button>
    </div>
  </section>`);
}

/* ── Browse view ─────────────────────────────────────────── */
function renderBrowse() {
  const results = filteredCases();
  const p = state.procedure ? procLabel(state.procedure) : null;

  const view = el(`<div>
    <div id="heroSlot"></div>
    <div class="layout">
      <aside class="rail rail-left">
        <p class="rail-title">Procedures</p>
        <div id="taxonomy"></div>
        <div class="rail-divider"></div>
        <button class="btn btn-primary btn-block btn-sm" id="newCaseBtn">${ICON.plus} Document a case</button>
      </aside>

      <section>
        <div class="results-bar">
          <span class="results-count"><b>${results.length}</b> case${results.length === 1 ? "" : "s"}</span>
          <div class="sort-wrap">
            <span>Sort</span>
            <select id="sortSelect" aria-label="Sort cases">
              <option value="recent">Most recent</option>
              <option value="reads">Most read</option>
              <option value="saves">Most saved</option>
            </select>
          </div>
        </div>
        <div class="case-list" id="caseList"></div>
      </section>

      <aside class="rail rail-right">
        <div class="filter-block">
          <p class="rail-title">Complication</p>
          <div class="chips" id="compChips"></div>
        </div>
        <div class="filter-block">
          <p class="rail-title">Tools &amp; materials</p>
          <div class="chips" id="toolChips"></div>
        </div>
        <button class="clear-link" id="clearFilters">Clear all filters</button>
      </aside>
    </div>
  </div>`);

  /* hero */
  const slot = $("#heroSlot", view);
  slot.appendChild(isFiltered() ? heroCompact(p, results.length) : heroFull());
  const clearAll = () => {
    state.complications.clear(); state.tools.clear();
    state.procedure = null; state.query = "";
    $("#globalSearch").value = ""; render();
  };
  const ca = $("#clearAll", view);   if (ca) ca.onclick = clearAll;
  const hs = $("#heroSearch", view); if (hs) hs.onclick = () => $("#globalSearch").focus();

  /* taxonomy */
  const tax = $("#taxonomy", view);
  PROCEDURES.forEach((g) => {
    const openGroup = g.children.some((c) => c.id === state.procedure);
    const grp = el(`<div class="tax-group${openGroup ? " open" : ""}">
      <button class="tax-parent">${ICON.chev}<span>${esc(g.label)}</span><span class="cnt">${countForGroup(g)}</span></button>
      <div class="tax-children"><div></div></div>
    </div>`);
    $(".tax-parent", grp).onclick = () => grp.classList.toggle("open");
    const kids = $(".tax-children > div", grp);
    g.children.forEach((c) => {
      const b = el(`<button class="tax-child${state.procedure === c.id ? " active" : ""}">
        <span>${esc(c.label)}</span><span class="cnt">${countFor(c.id)}</span></button>`);
      b.onclick = () => { state.procedure = state.procedure === c.id ? null : c.id; render(); };
      kids.appendChild(b);
    });
    tax.appendChild(grp);
  });

  /* filter chips */
  const chipRow = (host, values, set) => {
    values.forEach((v) => {
      const b = el(`<button class="chip${set.has(v) ? " on" : ""}">${esc(v)}</button>`);
      b.onclick = () => { set.has(v) ? set.delete(v) : set.add(v); render(); };
      host.appendChild(b);
    });
  };
  chipRow($("#compChips", view), COMPLICATIONS, state.complications);
  chipRow($("#toolChips", view), allTools().slice(0, 14), state.tools);

  /* results, with a staggered entrance */
  const list = $("#caseList", view);
  if (!results.length) {
    list.appendChild(el(`<div class="empty"><h3>No cases match these filters</h3>
      <p>Try clearing a filter or broadening the search.</p></div>`));
  } else {
    results.forEach((c, i) => {
      const card = caseCard(c);
      card.style.animationDelay = Math.min(i * 45, 360) + "ms";
      list.appendChild(card);
    });
  }

  const sort = $("#sortSelect", view);
  sort.value = state.sort;
  sort.onchange = (e) => { state.sort = e.target.value; render(); };
  $("#clearFilters", view).onclick = clearAll;
  $("#newCaseBtn", view).onclick = () => { location.hash = "#/contribute"; };

  return view;
}

function caseCard(c) {
  return el(`<a class="case-card" href="#/case/${c.id}">
    <div class="case-meta-top">
      <span class="tag tag-proc">${esc(c.procedurePath)}</span>
      ${(c.complications || []).map((x) => `<span class="tag tag-comp">${esc(x)}</span>`).join("")}
      ${diffMeter(c.difficulty)}
    </div>
    <h3 class="case-title">${esc(c.title)}</h3>
    <p class="case-summary">${esc(c.summary)}</p>
    <div class="case-foot">
      <div class="byline">
        <span class="av">${initials(c.author.name)}</span>
        <span>
          <span class="byline-name">${esc(c.author.name)} ${c.author.verified ? verifiedBadge() : ""}</span>
          <span class="byline-sub">${esc(c.author.credential)} · ${fmtDate(c.date)}</span>
        </span>
      </div>
      <div class="stat-row">
        <span>${ICON.eye}${fmtNum(c.reads)}</span>
        <span>${ICON.bookmk}${fmtNum(c.saves)}</span>
        <span>${ICON.msg}${(c.comments || []).length}</span>
        ${(c.media || []).length ? `<span>${ICON.image}${c.media.length}</span>` : ""}
      </div>
    </div>
  </a>`);
}

/* ── Case detail view ────────────────────────────────────── */
function renderCase(id) {
  const c = CASES.find((x) => x.id === id);
  if (!c) return el(`<div class="empty"><h3>Case not found</h3><p><a href="#/browse">Back to browse</a></p></div>`);

  const view = el(`<div>
    <a class="back-link" href="#/browse">${ICON.left} All cases</a>
    <div class="detail">
      <article>
        <header class="detail-hero">
          <div class="case-meta-top">
            <span class="tag tag-proc">${esc(c.procedurePath)}</span>
            ${(c.complications || []).map((x) => `<span class="tag">${esc(x)}</span>`).join("")}
            ${diffMeter(c.difficulty)}
          </div>
          <h1>${esc(c.title)}</h1>
          <div class="detail-byline">
            <span class="av">${initials(c.author.name)}</span>
            <div>
              <div class="byline-name">${esc(c.author.name)} ${c.author.verified ? verifiedBadge() : ""}</div>
              <div class="byline-sub">${esc(c.author.credential)} · ${esc(c.author.location)} · ${fmtDate(c.date)}</div>
            </div>
          </div>
        </header>

        <div class="section">
          <p class="section-label">Presentation</p>
          <p>${esc(c.presentation)}</p>
        </div>
        <div class="section">
          <p class="section-label">What was unusual</p>
          <p>${esc(c.unusual)}</p>
        </div>
        <div class="section">
          <p class="section-label">How it was resolved</p>
          <ol class="steps">${(c.resolution || []).map((s) => `<li>${esc(s)}</li>`).join("")}</ol>
        </div>
        <div class="section">
          <p class="section-label">Outcome</p>
          <div class="outcome"><p>${esc(c.outcome)}</p></div>
        </div>
        <div class="section">
          <p class="section-label">Clinical takeaways</p>
          <div class="callout"><ul>${(c.takeaways || []).map((t) => `<li>${esc(t)}</li>`).join("")}</ul></div>
        </div>
        ${(c.media || []).length ? `<div class="section">
          <p class="section-label">Media (${c.media.length})</p>
          <div class="media-grid">${(c.media || []).map((m) => `<div class="media-item">
            <span class="badge">${m.type}</span>${m.type === "video" ? ICON.video : ICON.image}<span>${esc(m.label)}</span>
          </div>`).join("")}</div>
        </div>` : ""}

        <div class="section">
          <p class="section-label">Peer discussion (${(c.comments || []).length})</p>
          ${(c.comments || []).length
            ? c.comments.map((m) => `<div class="comment">
                <span class="av">${initials(m.author)}</span>
                <div class="comment-body">
                  <div class="comment-head"><span class="nm">${esc(m.author)}</span>
                    ${m.verified ? verifiedBadge() : ""}<span class="dt">${fmtDate(m.date)}</span></div>
                  <div class="comment-text">${esc(m.text)}</div>
                </div></div>`).join("")
            : `<p style="color:var(--ink-4);font-size:13.5px">No discussion yet.</p>`}
          <div style="margin-top:18px">
            ${canComment()
              ? `<button class="btn btn-ghost btn-sm" id="addComment">${ICON.msg} Add a clinical comment</button>`
              : `<div style="display:flex;align-items:center;gap:9px;color:var(--ink-4);font-size:13px">
                   <span style="width:15px;height:15px;display:inline-block;flex-shrink:0">${ICON.lock}</span>
                   Only verified dentists can comment. <a href="#/verify" style="color:var(--accent);font-weight:600">Get verified</a>
                 </div>`}
          </div>
        </div>
      </article>

      <aside class="sidecar">
        <div class="panel">
          <button class="btn btn-primary btn-block" id="saveCase">${ICON.bookmk} Save to library</button>
          ${canEdit(c) ? `<a class="btn btn-ghost btn-block" style="margin-top:9px"
             href="#/edit/${esc(c.id)}">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>
            Edit case</a>` : ""}
          ${canDelete(c) ? `
          <button class="btn btn-danger btn-block" id="deleteCase" style="margin-top:9px">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h16"/><path d="M9 7V4h6v3"/><path d="M6 7l1 13h10l1-13"/></svg>
            Delete case
          </button>
          <p class="panel-note">${!isMine(c)
            ? "You are deleting another dentist’s case as an admin."
            : "This is your case. Deleting removes it permanently."}</p>` : ""}
        </div>
        <div class="panel">
          <h4>Tools &amp; materials used</h4>
          <div class="tool-list">${(c.tools || []).map((t) => `<span class="tag tag-tool">${esc(t)}</span>`).join("")}</div>
        </div>
        <div class="panel">
          <h4>Case record</h4>
          <div class="kv">
            <div class="kv-row"><span class="k">Case ID</span><span class="v">${esc(c.id.toUpperCase())}</span></div>
            <div class="kv-row"><span class="k">Procedure</span><span class="v">${esc(c.procedurePath)}</span></div>
            <div class="kv-row"><span class="k">Published</span><span class="v">${fmtDate(c.date)}</span></div>
            <div class="kv-row"><span class="k">Engagement</span><span class="v">${fmtNum(c.reads)} reads · ${fmtNum(c.saves)} saves</span></div>
          </div>
        </div>
        <div class="panel" style="background:var(--accent-wash);border-color:var(--t-300)">
          <h4 style="color:var(--accent-ink)">Peer-contributed content</h4>
          <p style="margin:0;font-size:12.5px;color:var(--ink-2);line-height:1.6">
            Written by a verified dentist for professional discussion. Not a clinical guideline and
            not a substitute for your own judgement.</p>
        </div>
      </aside>
    </div>
  </div>`);

  $("#saveCase", view).onclick = () => toast("Saving to a library is not wired up yet");
  const ac = $("#addComment", view);
  if (ac) ac.onclick = () => toast("Comment composer is not wired up yet");

  const del = $("#deleteCase", view);
  if (del) {
    del.onclick = async () => {
      if (!confirm(`Delete this case permanently?\n\n“${c.title}”\n\nThis cannot be undone.`)) return;
      del.disabled = true;
      del.textContent = "Deleting…";
      try {
        await API.deletePost(c.id);
        await refresh({ session: false });
        toast("Case deleted");
        location.hash = "#/browse";
        render();
      } catch (err) {
        toast(err.message);
        del.disabled = false;
        del.textContent = "Delete case";
      }
    };
  }
  return view;
}

/* ── Contribute view (role-gated) ────────────────────────── */
function renderContribute(editing) {
  if (editing && !canEdit(editing)) {
    return el(`<div class="gate">
      <div class="gate-icon">${ICON.lock}</div>
      <h2>You cannot edit this case</h2>
      <p>${signedIn() ? "Cases can only be edited by their author, or by an admin."
                      : "Sign in to edit your own cases."}</p>
      <div class="gate-actions">
        <a class="btn btn-primary" href="#/case/${esc(editing.id)}">Back to the case</a>
      </div>
    </div>`);
  }

  if (!signedIn()) {
    return el(`<div class="gate">
      <div class="gate-icon">${ICON.lock}</div>
      <h2>Sign in to contribute</h2>
      <p>Anyone can read Dental Info, but publishing a case needs an account.</p>
      <div class="gate-actions">
        <a class="btn btn-primary" href="#/login">Sign in</a>
        <a class="btn btn-ghost" href="#/browse">Keep browsing</a>
      </div>
    </div>`);
  }

  if (!canPost()) {
    const pending = SESSION.verification_status === "pending";
    return el(`<div class="gate">
      <div class="gate-icon">${pending ? ICON.clock : ICON.lock}</div>
      <h2>${pending ? "Your verification is still in review" : "Only verified dentists can contribute"}</h2>
      <p>${pending
        ? "We are cross-checking your license against the issuing board. You will get posting rights as soon as it clears — usually within two business days."
        : "Your account is read-only. Publishing a case requires a verified dental license."}</p>
      <div class="gate-actions">
        <a class="btn btn-primary" href="#/verify">${ICON.shield} ${pending ? "View verification status" : "Start verification"}</a>
        <a class="btn btn-ghost" href="#/browse">Keep browsing</a>
      </div>
    </div>`);
  }

  const view = el(`<div class="form-wrap">
    <section class="hero compact" style="margin-bottom:24px">
      <div class="hero-inner">
        <h1>${editing ? "Edit case" : "Document a case"}</h1>
        <p>${editing
          ? "Corrections are expected — a clinical write-up you cannot fix is worse than none."
          : "Structured entry is what makes cases searchable later. Aim for what you would want to read at 8am before a difficult appointment."}</p>
      </div>
    </section>
    <div class="form-steps">
      <div class="form-step on">1 · Classification</div>
      <div class="form-step on">2 · Clinical narrative</div>
      <div class="form-step${editing ? " on" : ""}">3 · Media &amp; review</div>
    </div>
    <form class="form-card" id="caseForm">
      <div class="field">
        <label for="f-title">Case title</label>
        <input id="f-title" type="text" placeholder="e.g. Separated rotary file in the apical third of a curved MB canal" required value="${editing ? esc(editing.title) : ""}">
        <p class="hint">Describe the complication, not the treatment. Specific beats catchy.</p>
      </div>
      <div class="field-row">
        <div class="field">
          <label for="f-proc">Procedure type</label>
          <select id="f-proc" required>
            <option value="">Select a procedure…</option>
            ${PROCEDURES.map((g) => `<optgroup label="${esc(g.label)}">
              ${g.children.map((c) => `<option value="${c.id}"${editing && editing.procedure === c.id ? " selected" : ""}>${esc(c.label)}</option>`).join("")}</optgroup>`).join("")}
            <option value="__other">Other — not listed</option>
          </select>
          <div class="field other-field" id="f-proc-other-wrap" hidden>
            <label for="f-proc-other">Which procedure?</label>
            <input id="f-proc-other" type="text" placeholder="e.g. Apexification">
            <p class="hint">New procedure types go to an editor before they join the taxonomy.</p>
          </div>
        </div>
        <div class="field">
          <label for="f-comp">Primary complication</label>
          <select id="f-comp" required>
            <option value="">Select a complication…</option>
            ${COMPLICATIONS.map((c) => `<option${editing && (editing.complications || []).includes(c) ? " selected" : ""}>${esc(c)}</option>`).join("")}
            <option value="__other">Other — not listed</option>
          </select>
          <div class="field other-field" id="f-comp-other-wrap" hidden>
            <label for="f-comp-other">Which complication?</label>
            <input id="f-comp-other" type="text" placeholder="e.g. Emphysema from air-driven handpiece">
            <p class="hint">Describe it in a few words, the way you would search for it later.</p>
          </div>
        </div>
      </div>
      <div class="field">
        <label for="f-diff">Difficulty</label>
        <select id="f-diff">
          ${["Low","Medium","High"].map((d) => `<option${(editing ? editing.difficulty === d : d === "Medium") ? " selected" : ""}>${d}</option>`).join("")}
        </select>
        <p class="hint">How much judgement or specialist kit this needed beyond routine practice.</p>
      </div>
      <div class="field">
        <label for="f-tools">Tools &amp; materials used</label>
        <input id="f-tools" type="text" placeholder="Comma separated — e.g. ProTaper Gold F2, ultrasonic tip, 17% EDTA" value="${editing ? esc((editing.tools || []).join(", ")) : ""}">
        <p class="hint">Brand and size where it mattered to the outcome.</p>
      </div>
      <div class="field">
        <label for="f-pres">Presentation</label>
        <textarea id="f-pres" placeholder="Age, sex, tooth, history, findings. No identifying details.">${editing ? esc(editing.presentation || "") : ""}</textarea>
      </div>
      <div class="field">
        <label for="f-unusual">What was unusual or difficult</label>
        <textarea id="f-unusual" placeholder="The specific thing that made this case not routine.">${editing ? esc(editing.unusual || "") : ""}</textarea>
      </div>
      <div class="field">
        <label for="f-res">How you resolved it</label>
        <textarea id="f-res" style="min-height:132px" placeholder="One step per line. Include what you tried that did not work.">${editing ? esc((editing.resolution || []).join("\n")) : ""}</textarea>
      </div>
      <div class="field">
        <label for="f-out">Outcome &amp; follow-up</label>
        <textarea id="f-out" style="min-height:76px" placeholder="Review interval and what you found.">${editing ? esc(editing.outcome || "") : ""}</textarea>
      </div>
      <div class="field">
        <label>Supporting media</label>
        <div class="dropzone">${ICON.upload}
          <div>Drop radiographs, clinical photos or video here</div>
          <div class="hint" style="margin-top:6px">Faces and identifiers are auto-flagged before publishing</div>
        </div>
      </div>
      <div class="form-actions">
        ${editing
          ? `<a class="btn btn-ghost" href="#/case/${esc(editing.id)}">Cancel</a>`
          : `<button type="button" class="btn btn-ghost" id="saveDraft">Save draft</button>`}
        <button type="submit" class="btn btn-primary" id="publishBtn">${ICON.check} ${editing ? "Save changes" : "Publish case"}</button>
      </div>
    </form>
  </div>`);

  /* Choosing "Other" reveals a free-text field, and that field is only
     required while it is visible — otherwise a hidden empty input would
     block submission. */
  const wireOther = (selectId, wrapId, inputId) => {
    const sel = $("#" + selectId, view);
    const wrap = $("#" + wrapId, view);
    const input = $("#" + inputId, view);
    const sync = (moveFocus) => {
      const on = sel.value === "__other";
      wrap.hidden = !on;
      input.required = on;
      if (!on) input.value = "";
      else if (moveFocus) input.focus();
    };
    sel.addEventListener("change", () => sync(true));
    sync(false);
  };
  wireOther("f-proc", "f-proc-other-wrap", "f-proc-other");
  wireOther("f-comp", "f-comp-other-wrap", "f-comp-other");

  /* What the entry would be filed under, honouring an "Other" write-in */
  const chosen = (selectId, otherId) => {
    const sel = $("#" + selectId, view);
    if (sel.value === "__other") return $("#" + otherId, view).value.trim();
    return sel.selectedOptions[0] ? sel.selectedOptions[0].textContent.trim() : "";
  };

  /* Split a textarea into a list, one item per non-empty line. */
  const lines = (id) => $("#" + id, view).value
    .split("\n").map((s) => s.trim()).filter(Boolean);
  const val = (id) => $("#" + id, view).value.trim();

  const submitBtn = $("#publishBtn", view);

  const draftBtn = $("#saveDraft", view);
  if (draftBtn) draftBtn.onclick = () => toast("Draft saving is not wired up yet");

  $("#caseForm", view).onsubmit = async (e) => {
    e.preventDefault();

    const procSel = $("#f-proc", view);
    const writeIn = procSel.value === "__other";
    const procLeaf = chosen("f-proc", "f-proc-other");
    const compLabel = chosen("f-comp", "f-comp-other");

    if (!val("f-title")) { toast("A title is required"); return; }
    if (!procLeaf)       { toast("Choose a procedure type"); return; }

    // build the display path: "Group › Leaf", or "Proposed › <write-in>"
    let path = procLeaf;
    if (!writeIn) {
      const p = procLabel(procSel.value);
      if (p) path = `${p.group} › ${p.leaf}`;
    } else {
      path = `Proposed › ${procLeaf}`;
    }

    const payload = {
      title: val("f-title"),
      procedure: writeIn ? "__other" : procSel.value,
      procedurePath: path,
      difficulty: $("#f-diff", view).value,
      summary: val("f-unusual").slice(0, 220) || val("f-title"),
      complications: compLabel ? [compLabel] : [],
      tools: val("f-tools").split(",").map((s) => s.trim()).filter(Boolean),
      resolution: lines("f-res"),
      takeaways: [],
      media: [],
      presentation: val("f-pres"),
      unusual: val("f-unusual"),
      outcome: val("f-out"),
    };

    submitBtn.disabled = true;
    submitBtn.textContent = editing ? "Saving…" : "Publishing…";
    try {
      const { post } = editing
        ? await API.updatePost(editing.id, payload)
        : await API.createPost(payload);
      await refresh({ session: false });
      toast(editing ? "Changes saved" : `Published — “${post.title.slice(0, 40)}”`);
      location.hash = "#/case/" + post.id;
      render();
    } catch (err) {
      toast(err.message);
      submitBtn.disabled = false;
      submitBtn.innerHTML = `${ICON.check} ${editing ? "Save changes" : "Publish case"}`;
    }
  };
  return view;
}

/* ── Verification view ───────────────────────────────────── */
function renderVerify() {
  if (!signedIn()) {
    return el(`<div class="gate">
      <div class="gate-icon">${ICON.shield}</div>
      <h2>Sign in to see your verification status</h2>
      <p>Verification is tied to your account.</p>
      <div class="gate-actions">
        <a class="btn btn-primary" href="#/login">Sign in</a>
        <a class="btn btn-ghost" href="#/browse">Keep browsing</a>
      </div>
    </div>`);
  }

  const status = SESSION.verification_status;   // unverified | pending | verified | lapsed
  const banner = {
    verified: { cls: "sb-ok", icon: ICON.shield, h: "Your license is verified",
      p: SESSION.reverify_due
         ? `Confirmed against the issuing board. Next re-verification due ${fmtDate(SESSION.reverify_due.slice(0,10))}.`
         : "Confirmed against the issuing board." },
    pending: { cls: "sb-pending", icon: ICON.clock, h: "Verification in review",
      p: "Registry cross-check is running. Most submissions clear within two business days." },
    lapsed: { cls: "sb-pending", icon: ICON.clock, h: "Your verification has lapsed",
      p: "Re-submit your license to restore posting rights." },
    unverified: { cls: "sb-none", icon: ICON.lock, h: "Not yet verified",
      p: "You can browse and search every case. Submit your license to get posting rights." },
  }[status] || { cls: "sb-none", icon: ICON.lock, h: status, p: "" };

  /* The step list is derived from the account record rather than stored
     per-user: each step is a fact we can already answer. */
  const has = (v) => !!(v && String(v).trim());
  const steps = [
    { label: "Account created", state: "done" },
    { label: "License number & board submitted",
      state: has(SESSION.license.number) && has(SESSION.license.board) ? "done" : "todo" },
    { label: "Automatic registry cross-check",
      state: status === "verified" ? "done" : (status === "pending" ? "pending" : "todo") },
    { label: "Degree & license certificate upload",
      state: status === "verified" ? "done" : "todo" },
    { label: "Admin review",
      state: status === "verified" ? "done" : (status === "pending" ? "pending" : "todo") },
  ];
  const stepText = { done: "Complete", pending: "In progress", todo: "Not started" };

  const roleRow = (name, granted, detail) => `<div class="kv-row">
    <span class="k">${esc(name)} ${granted ? '<span class="yes">you</span>' : ""}</span>
    <span class="v">${esc(detail)}</span></div>`;

  const view = el(`<div class="verify-wrap">
    <section class="hero compact" style="margin-bottom:24px">
      <div class="hero-inner">
        <h1>Contributor verification</h1>
        <p>Every contributor on Dental Info is a licensed dentist, checked against the issuing
           board. This is what makes the content worth reading.</p>
      </div>
    </section>

    <div class="status-banner ${banner.cls}">
      <span class="si">${banner.icon}</span>
      <div><h3>${banner.h}</h3><p>${banner.p}</p></div>
    </div>

    ${SESSION.permissive_mode ? `<div class="panel dev-note">
      <h4>Development mode</h4>
      <p>Permissions are currently <b>permissive</b> — every signed-in account has full
         access regardless of the role below, as requested. Roles are still recorded per
         account and every check is in place; they are short-circuited by one flag.
         Start the server with <code>DENTAL_INFO_STRICT=1</code> to enforce them.</p>
    </div>` : ""}

    <div class="panel" style="margin-bottom:14px">
      <h4>Your account</h4>
      <div class="kv">
        <div class="kv-row"><span class="k">Name</span><span class="v">${esc(SESSION.name)}</span></div>
        <div class="kv-row"><span class="k">Email</span><span class="v">${esc(SESSION.email)}</span></div>
        <div class="kv-row"><span class="k">Role</span><span class="v">${esc(SESSION.role)}</span></div>
        <div class="kv-row"><span class="k">Credential</span><span class="v">${esc(SESSION.credential || "—")}</span></div>
        <div class="kv-row"><span class="k">Effective rights</span><span class="v">${
          [SESSION.can_post ? "publish cases" : "browse only",
           SESSION.can_delete_any ? "delete any case"
             : (SESSION.can_delete_own ? "delete own cases" : null),
           SESSION.can_comment ? "comment" : null,
           SESSION.is_admin ? "manage users · read audit log" : null
          ].filter(Boolean).join(" · ")
        }</span></div>
      </div>
    </div>

    <div class="panel" style="margin-bottom:14px">
      <h4>Verification steps</h4>
      <ul class="vsteps">
        ${steps.map((s) => `<li class="vstep ${s.state}">
            <span class="dot">${s.state === "done" ? ICON.check : ""}</span>
            <span class="txt">${esc(s.label)}<span class="st">${stepText[s.state]}</span></span>
            ${s.state === "todo" && s.label.indexOf("certificate") > -1
              ? `<span class="act"><button class="btn btn-ghost btn-sm" data-act="upload">Upload</button></span>` : ""}
          </li>`).join("")}
      </ul>
    </div>

    <div id="licenseSlot"></div>

    <div class="panel">
      <h4>What each role can do</h4>
      <div class="kv">
        ${roleRow("Read-only user", SESSION.role === "reader", "Browse, search and filter every published case.")}
        ${roleRow("Verified contributor", SESSION.role === "contributor", "Everything above, plus publishing cases and deleting their own.")}
        ${roleRow("Admin", SESSION.role === "admin", "Everything above, plus deleting any case, managing users and reading the audit log.")}
        <div class="kv-row"><span class="k">Re-verification</span><span class="v">Annual, to catch lapsed or revoked licenses.</span></div>
      </div>
    </div>
  </div>`);

  view.querySelectorAll("[data-act]").forEach((b) => { b.onclick = () => toast("Document upload is not wired up yet"); });

  /* The license section is live: submit when unverified, status when pending. */
  const slot = $("#licenseSlot", view);
  slot.appendChild(el(`<div class="panel"><h4>License</h4>
    <p style="margin:0;font-size:13px;color:var(--ink-4)">Loading…</p></div>`));

  (async () => {
    let request = null;
    try { ({ request } = await API.myRequest()); } catch (e) { /* show the form anyway */ }
    slot.innerHTML = "";

    if (status === "pending") {
      slot.appendChild(el(`<div class="panel" style="margin-bottom:14px">
        <h4>Submitted for review</h4>
        <div class="kv">
          <div class="kv-row"><span class="k">License number</span><span class="v">${esc(SESSION.license.number || "—")}</span></div>
          <div class="kv-row"><span class="k">Issuing board</span><span class="v">${esc(SESSION.license.board || "—")}</span></div>
          <div class="kv-row"><span class="k">Country</span><span class="v">${esc(SESSION.license.country || "—")}</span></div>
          ${request ? `<div class="kv-row"><span class="k">Submitted</span><span class="v">${fmtDate((request.created_at || "").slice(0,10))}</span></div>` : ""}
          ${request ? `<div class="kv-row"><span class="k">Registry check</span><span class="v">${esc(request.registry_check)}${request.registry_detail ? " — " + esc(request.registry_detail) : ""}</span></div>` : ""}
        </div>
        <p class="panel-note">An administrator reviews each submission against the
           issuing board. You keep read access in the meantime.</p>
      </div>`));
      return;
    }

    if (status === "verified") {
      slot.appendChild(el(`<div class="panel" style="margin-bottom:14px">
        <h4>License on file</h4>
        <div class="kv">
          <div class="kv-row"><span class="k">License number</span><span class="v">${esc(SESSION.license.number || "—")}</span></div>
          <div class="kv-row"><span class="k">Issuing board</span><span class="v">${esc(SESSION.license.board || "—")}</span></div>
          <div class="kv-row"><span class="k">Country</span><span class="v">${esc(SESSION.license.country || "—")}</span></div>
          ${request && request.reviewer_note ? `<div class="kv-row"><span class="k">Reviewer note</span><span class="v">${esc(request.reviewer_note)}</span></div>` : ""}
        </div>
      </div>`));
      return;
    }

    /* unverified, lapsed, or previously rejected -> let them apply */
    const rejected = request && request.status === "rejected";
    const form = el(`<div class="panel" style="margin-bottom:14px">
      <h4>${rejected ? "Re-submit your license" : "Submit your license for verification"}</h4>
      ${rejected ? `<div class="login-error" style="margin-bottom:14px">
        <b>Previously rejected.</b> ${esc(request.reviewer_note || "No reason recorded.")}
      </div>` : ""}
      <form id="licForm">
        <div class="field">
          <label for="v-num">License / registration number</label>
          <input id="v-num" type="text" placeholder="e.g. KA-DEN-48291"
                 value="${esc(SESSION.license.number || "")}">
        </div>
        <div class="field">
          <label for="v-board">Issuing board or council</label>
          <input id="v-board" type="text" placeholder="e.g. Karnataka State Dental Council"
                 value="${esc(SESSION.license.board || "")}">
        </div>
        <div class="field-row">
          <div class="field">
            <label for="v-country">Country of registration</label>
            <input id="v-country" type="text" placeholder="India"
                   value="${esc(SESSION.license.country || "")}">
          </div>
          <div class="field">
            <label for="v-cred">Credential <span class="opt">optional</span></label>
            <input id="v-cred" type="text" placeholder="MDS Endodontics"
                   value="${esc(SESSION.credential || "")}">
          </div>
        </div>
        <div class="field">
          <label for="v-note">Anything the reviewer should know <span class="opt">optional</span></label>
          <input id="v-note" type="text" placeholder="e.g. Registered under my maiden name, Nair">
          <p class="hint">Certificate upload is not built yet, so add anything that
             helps a human confirm your registration.</p>
        </div>
        <p class="login-error" id="licError" hidden></p>
        <button type="submit" class="btn btn-primary" id="licBtn">${ICON.shield} Submit for review</button>
      </form>
    </div>`);
    slot.appendChild(form);

    const errBox = $("#licError", form);
    const btn = $("#licBtn", form);
    $("#licForm", form).onsubmit = async (e) => {
      e.preventDefault();
      errBox.hidden = true;
      btn.disabled = true; btn.textContent = "Submitting…";
      try {
        await API.submitLicense({
          license_number: $("#v-num", form).value.trim(),
          license_board: $("#v-board", form).value.trim(),
          license_country: $("#v-country", form).value.trim(),
          credential: $("#v-cred", form).value.trim(),
          document_note: $("#v-note", form).value.trim(),
        });
        await refresh();
        renderAuthSlot();
        toast("Submitted — an administrator will review it");
        render();
      } catch (err) {
        errBox.textContent = err.message;
        errBox.hidden = false;
        btn.disabled = false;
        btn.innerHTML = `${ICON.shield} Submit for review`;
      }
    };
  })();

  return view;
}

/* ── Login view ──────────────────────────────────────────── */
function renderLogin() {
  const view = el(`<div class="login-wrap">
    <div class="login-card">
      <div class="login-mark">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
          <path d="M12 3c-2.2 0-3.2 1-5 1S4 3.4 4 6.5c0 3 1 4.6 1.7 7.2.6 2.2.8 6.3 2.6 6.3 1.6 0 1.3-4.5 3.7-4.5s2.1 4.5 3.7 4.5c1.8 0 2-4.1 2.6-6.3C19 11.1 20 9.5 20 6.5 20 3.4 18.8 4 17 4s-2.8-1-5-1z"/>
        </svg>
      </div>
      <h1>Sign in</h1>
      <p class="login-sub">Clinical case library for verified dentists.</p>

      <form id="loginForm" novalidate>
        <div class="field">
          <label for="l-email">Email</label>
          <input id="l-email" type="text" autocomplete="username"
                 placeholder="you@example.com" value="admin@dentalinfo.test">
        </div>
        <div class="field">
          <label for="l-pw">Password</label>
          <input id="l-pw" type="password" autocomplete="current-password"
                 placeholder="••••••••" value="Admin@12345">
        </div>
        <p class="login-error" id="loginError" hidden></p>
        <button type="submit" class="btn btn-primary btn-block" id="loginBtn">Sign in</button>
      </form>

      <div class="login-hint">
        <p style="margin-bottom:12px">No account?
           <a href="#/signup" style="color:var(--accent);font-weight:600">Create one</a></p>
        <p><b>Local test accounts</b> — full credentials are in <code>ACCOUNTS.txt</code></p>
        <div class="login-accounts">
          <button class="acct-chip" data-e="admin@dentalinfo.test"  data-p="Admin@12345">Admin</button>
          <button class="acct-chip" data-e="test@dentalinfo.test"   data-p="Test@12345">Contributor</button>
          <button class="acct-chip" data-e="reader@dentalinfo.test" data-p="Reader@12345">Read-only</button>
        </div>
      </div>
    </div>
  </div>`);

  const errBox = $("#loginError", view);
  const btn = $("#loginBtn", view);

  view.querySelectorAll(".acct-chip").forEach((c) => {
    c.onclick = () => {
      $("#l-email", view).value = c.dataset.e;
      $("#l-pw", view).value = c.dataset.p;
      errBox.hidden = true;
    };
  });

  $("#loginForm", view).onsubmit = async (e) => {
    e.preventDefault();
    const email = $("#l-email", view).value.trim();
    const pw = $("#l-pw", view).value;
    if (!email || !pw) {
      errBox.textContent = "Enter an email and password.";
      errBox.hidden = false;
      return;
    }
    btn.disabled = true;
    btn.textContent = "Signing in…";
    errBox.hidden = true;
    try {
      await API.login(email, pw);
      await refresh();
      renderAuthSlot();
      toast(`Signed in as ${SESSION.name}`);
      location.hash = "#/browse";
      render();
    } catch (err) {
      errBox.textContent = err.message;
      errBox.hidden = false;
      btn.disabled = false;
      btn.textContent = "Sign in";
    }
  };

  return view;
}

/* ── Signup view ─────────────────────────────────────────── */
function renderSignup() {
  const view = el(`<div class="login-wrap">
    <div class="login-card" style="max-width:452px">
      <div class="login-mark">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
          <path d="M12 3c-2.2 0-3.2 1-5 1S4 3.4 4 6.5c0 3 1 4.6 1.7 7.2.6 2.2.8 6.3 2.6 6.3 1.6 0 1.3-4.5 3.7-4.5s2.1 4.5 3.7 4.5c1.8 0 2-4.1 2.6-6.3C19 11.1 20 9.5 20 6.5 20 3.4 18.8 4 17 4s-2.8-1-5-1z"/>
        </svg>
      </div>
      <h1>Create an account</h1>
      <p class="login-sub">Anyone can read Dental Info. Publishing requires a
         verified dental license, which you can submit after signing up.</p>

      <form id="signupForm" novalidate>
        <div class="field">
          <label for="s-name">Full name</label>
          <input id="s-name" type="text" autocomplete="name" placeholder="Dr. Priya Nair">
        </div>
        <div class="field">
          <label for="s-email">Email</label>
          <input id="s-email" type="text" autocomplete="username" placeholder="you@clinic.com">
        </div>
        <div class="field">
          <label for="s-pw">Password</label>
          <input id="s-pw" type="password" autocomplete="new-password" placeholder="At least 10 characters">
          <p class="hint">At least 10 characters, and not your name or email.</p>
        </div>
        <div class="field-row">
          <div class="field">
            <label for="s-cred">Credential <span class="opt">optional</span></label>
            <input id="s-cred" type="text" placeholder="MDS Endodontics">
          </div>
          <div class="field">
            <label for="s-loc">Location <span class="opt">optional</span></label>
            <input id="s-loc" type="text" placeholder="Bengaluru, IN">
          </div>
        </div>
        <p class="login-error" id="signupError" hidden></p>
        <button type="submit" class="btn btn-primary btn-block" id="signupBtn">Create account</button>
      </form>

      <div class="login-hint">
        <p>Already have an account? <a href="#/login" style="color:var(--accent);font-weight:600">Sign in</a></p>
      </div>
    </div>
  </div>`);

  const errBox = $("#signupError", view);
  const btn = $("#signupBtn", view);

  $("#signupForm", view).onsubmit = async (e) => {
    e.preventDefault();
    errBox.hidden = true;
    btn.disabled = true; btn.textContent = "Creating…";
    try {
      await API.signup({
        name: $("#s-name", view).value.trim(),
        email: $("#s-email", view).value.trim(),
        password: $("#s-pw", view).value,
        credential: $("#s-cred", view).value.trim(),
        location: $("#s-loc", view).value.trim(),
      });
      await refresh();
      renderAuthSlot();
      toast(`Welcome, ${SESSION.name}`);
      location.hash = "#/verify";   // straight to the next real step
      render();
    } catch (err) {
      errBox.textContent = err.message;
      errBox.hidden = false;
      btn.disabled = false; btn.textContent = "Create account";
    }
  };
  return view;
}

/* ── Admin: verification review queue ────────────────────── */
function renderAdmin() {
  if (!signedIn()) {
    return el(`<div class="gate">
      <div class="gate-icon">${ICON.lock}</div>
      <h2>Sign in required</h2>
      <div class="gate-actions"><a class="btn btn-primary" href="#/login">Sign in</a></div>
    </div>`);
  }
  if (!isAdmin()) {
    return el(`<div class="gate">
      <div class="gate-icon">${ICON.lock}</div>
      <h2>Admins only</h2>
      <p>The verification queue is restricted to administrators.</p>
      <div class="gate-actions"><a class="btn btn-ghost" href="#/browse">Back to browse</a></div>
    </div>`);
  }

  const view = el(`<div class="verify-wrap">
    <section class="hero compact" style="margin-bottom:24px">
      <div class="hero-inner">
        <h1>Verification queue</h1>
        <p>Every approval grants posting rights. Check the license number against
           the issuing board before approving — the automatic registry check is
           not yet integrated.</p>
      </div>
    </section>
    <div id="queueBody"><div class="empty"><h3>Loading…</h3></div></div>
  </div>`);

  const body = $("#queueBody", view);

  const load = async () => {
    body.innerHTML = `<div class="empty"><h3>Loading…</h3></div>`;
    let requests;
    try {
      ({ requests } = await API.vQueue());
    } catch (err) {
      body.innerHTML = "";
      body.appendChild(el(`<div class="empty"><h3>Could not load the queue</h3>
        <p>${esc(err.message)}</p></div>`));
      return;
    }
    body.innerHTML = "";
    if (!requests.length) {
      body.appendChild(el(`<div class="empty"><h3>Nothing waiting</h3>
        <p>New license submissions will appear here.</p></div>`));
      return;
    }
    requests.forEach((r) => body.appendChild(queueCard(r, load)));
  };

  load();
  return view;
}

function queueCard(r, reload) {
  const card = el(`<div class="panel vreq">
    <div class="vreq-head">
      <div>
        <div class="vreq-name">${esc(r.user_name)}</div>
        <div class="vreq-sub">${esc(r.user_email)}${r.user_location ? " · " + esc(r.user_location) : ""}</div>
      </div>
      <span class="tag">${esc(r.user_role)}</span>
    </div>
    <div class="kv" style="margin:14px 0">
      <div class="kv-row"><span class="k">License number</span><span class="v">${esc(r.license_number)}</span></div>
      <div class="kv-row"><span class="k">Issuing board</span><span class="v">${esc(r.license_board)}</span></div>
      <div class="kv-row"><span class="k">Country</span><span class="v">${esc(r.license_country)}</span></div>
      ${r.credential ? `<div class="kv-row"><span class="k">Credential</span><span class="v">${esc(r.credential)}</span></div>` : ""}
      ${r.document_note ? `<div class="kv-row"><span class="k">Applicant note</span><span class="v">${esc(r.document_note)}</span></div>` : ""}
      <div class="kv-row"><span class="k">Submitted</span><span class="v">${fmtDate((r.created_at || "").slice(0,10))}</span></div>
    </div>
    <div class="vreq-registry">
      <b>Registry check: ${esc(r.registry_check)}</b>
      ${r.registry_detail ? `<span>${esc(r.registry_detail)}</span>` : ""}
    </div>
    <div class="field" style="margin:14px 0 0">
      <label for="note-${esc(r.id)}">Decision note <span class="opt">required to reject</span></label>
      <input id="note-${esc(r.id)}" type="text" placeholder="e.g. Confirmed against KSDC register, 11 Sep 2026">
    </div>
    <div class="vreq-actions">
      <button class="btn btn-ghost btn-sm" data-act="reject">Reject</button>
      <button class="btn btn-primary btn-sm" data-act="approve">${ICON.check} Approve &amp; grant posting rights</button>
    </div>
  </div>`);

  const noteEl = $(`#note-${r.id}`, card);
  card.querySelectorAll("[data-act]").forEach((b) => {
    b.onclick = async () => {
      const decision = b.dataset.act;
      const note = noteEl.value.trim();
      if (decision === "reject" && !note) {
        toast("Give a reason the applicant can act on");
        noteEl.focus();
        return;
      }
      if (decision === "approve" &&
          !confirm(`Approve ${r.user_name}?\n\nThis grants posting rights. Confirm the license number against ${r.license_board} first.`)) return;
      card.querySelectorAll("button").forEach((x) => { x.disabled = true; });
      try {
        await API.decide(r.id, decision, note);
        toast(decision === "approve" ? `${r.user_name} is now a verified contributor`
                                     : `${r.user_name}'s request was rejected`);
        reload();
      } catch (err) {
        toast(err.message);
        card.querySelectorAll("button").forEach((x) => { x.disabled = false; });
      }
    };
  });
  return card;
}

/* ── Router ──────────────────────────────────────────────── */
function render() {
  const hash = location.hash || "#/browse";
  const host = $("#view");
  host.innerHTML = "";
  renderAuthSlot();

  let node, navKey;
  try {
    if (hash.startsWith("#/login"))           { node = renderLogin();            navKey = null; }
    else if (hash.startsWith("#/signup"))     { node = renderSignup();           navKey = null; }
    else if (hash.startsWith("#/admin"))      { node = renderAdmin();            navKey = "#/admin"; }
    else if (hash.startsWith("#/case/"))      { node = renderCase(hash.slice(7)); navKey = "#/browse"; }
    else if (hash.startsWith("#/contribute")) { node = renderContribute();        navKey = "#/contribute"; }
    else if (hash.startsWith("#/edit/")) {
      const target = CASES.find((x) => x.id === hash.slice(7));
      node = target
        ? renderContribute(target)
        : el(`<div class="empty"><h3>Case not found</h3>
            <p><a href="#/browse">Back to browse</a></p></div>`);
      navKey = null;
    }
    else if (hash.startsWith("#/verify"))     { node = renderVerify();            navKey = "#/verify"; }
    else                                      { node = renderBrowse();            navKey = "#/browse"; }
  } catch (err) {
    // Without this, one unexpected field shape blanks the entire page with no
    // clue why — which is exactly how the missing `comments` field presented.
    console.error("Render failed:", err);
    node = el(`<div class="gate">
      <div class="gate-icon">${ICON.lock}</div>
      <h2>Something failed to render</h2>
      <p>The data loaded, but this view could not be drawn. This is a bug, not a
         configuration problem.</p>
      <p style="font-size:12.5px;color:var(--ink-4);word-break:break-word">
        <b>${esc(err && err.name || "Error")}:</b> ${esc(err && err.message || String(err))}</p>
      <div class="gate-actions">
        <a class="btn btn-primary" href="#/browse">Back to browse</a>
        <button class="btn btn-ghost" onclick="location.reload()">Reload</button>
      </div>
    </div>`);
    navKey = null;
  }

  host.appendChild(node);
  document.querySelectorAll(".topnav-link").forEach((a) => {
    a.classList.toggle("active", a.getAttribute("href") === navKey);
  });
  if (!hash.startsWith("#/case/")) window.scrollTo(0, 0);
}

/* ── Theme ───────────────────────────────────────────────── */
function currentTheme() {
  const stamped = document.documentElement.getAttribute("data-theme");
  if (stamped) return stamped;
  return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}
$("#themeToggle").addEventListener("click", () => {
  const next = currentTheme() === "dark" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", next);
  try { localStorage.setItem("dentalinfo-theme", next); } catch (e) {}
  toast(next === "dark" ? "Dark theme" : "Light theme");
});

/* ── Global wiring ───────────────────────────────────────── */
window.addEventListener("hashchange", render);

$("#globalSearch").addEventListener("input", (e) => {
  state.query = e.target.value;
  if (!location.hash.startsWith("#/browse") && location.hash !== "") { location.hash = "#/browse"; return; }
  render();
  $("#globalSearch").focus();
});

document.addEventListener("keydown", (e) => {
  const tag = document.activeElement.tagName;
  if (e.key === "/" && tag !== "INPUT" && tag !== "TEXTAREA" && tag !== "SELECT") {
    e.preventDefault(); $("#globalSearch").focus();
  }
  if (e.key === "Escape" && document.activeElement === $("#globalSearch")) {
    $("#globalSearch").value = ""; state.query = ""; render();
  }
});

/* ── Top-bar auth control ────────────────────────────────── */
function renderAuthSlot() {
  const slot = $("#authSlot");
  slot.innerHTML = "";

  /* the admin queue link only exists for admins */
  const nav = document.querySelector(".topnav");
  let adminLink = nav.querySelector('[href="#/admin"]');
  if (isAdmin() && !adminLink) {
    adminLink = el('<a class="topnav-link" href="#/admin">Queue</a>');
    nav.appendChild(adminLink);
  } else if (!isAdmin() && adminLink) {
    adminLink.remove();
  }

  if (!signedIn()) {
    const btn = el(`<a class="btn btn-primary btn-sm" href="#/login">Sign in</a>`);
    slot.appendChild(btn);
    return;
  }

  const roleLabel = { admin: "Admin", contributor: "Contributor", reader: "Read-only" }[SESSION.role]
                    || SESSION.role;
  const wrap = el(`<div class="account">
    <div class="account-meta">
      <span class="account-name">${esc(SESSION.name)}</span>
      <span class="account-role${SESSION.is_admin ? " is-admin" : ""}">${esc(roleLabel)}</span>
    </div>
    <div class="avatar" title="${esc(SESSION.email)}">${initials(SESSION.name)}</div>
    <button class="icon-btn" id="signOutBtn" title="Sign out" aria-label="Sign out">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M15 17l5-5-5-5"/><path d="M20 12H9"/><path d="M13 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h7"/>
      </svg>
    </button>
  </div>`);
  slot.appendChild(wrap);

  $("#signOutBtn", wrap).onclick = async () => {
    try {
      await API.logout();
      await refresh();
      toast("Signed out");
      location.hash = "#/browse";
      render();
    } catch (err) { toast(err.message); }
  };
}

/* ── Boot ────────────────────────────────────────────────── */
(async function boot() {
  const host = $("#view");
  host.innerHTML = `<div class="empty"><h3>Loading…</h3></div>`;
  try {
    await refresh();
  } catch (err) {
    host.innerHTML = "";
    const where = err && err.crashed
      ? `The API on <code>${esc(location.host)}</code> is deployed but returned
         <b>HTTP ${err.status}</b> before it could reply.`
      : location.protocol === "file:"
        ? "You opened this file directly from disk (<code>file://</code>)."
        : `Nothing is answering <code>/api</code> on <code>${esc(location.host)}</code>.`;
    host.appendChild(el(`<div class="gate">
      <div class="gate-icon">${ICON.lock}</div>
      <h2>${err && err.crashed ? "The backend is failing" : "This build needs its backend"}</h2>
      <p>${where} Since moving onto a database, the app loads cases and accounts
         from its own API, so the static files alone cannot sign you in.</p>
      ${err && err.crashed ? `<p style="font-size:13px">
        <a href="/api/health" style="color:var(--accent);font-weight:600">Open /api/health</a>
        — it reports whether the driver loaded, whether <code>DATABASE_URL</code> is
        set, and whether the tables exist.</p>` : ""}
      <div class="panel" style="text-align:left;margin:18px 0 4px">
        <h4>Run it locally</h4>
        <p style="margin:0;font-size:12.5px;line-height:1.7">
          <code>python server/seed.py</code> &nbsp;— once, creates the accounts<br>
          <code>python server/app.py</code> &nbsp;— starts the server<br>
          then open <b>http://localhost:8000</b>
        </p>
      </div>
      <p style="font-size:12.5px;color:var(--ink-4)">Credentials are in
         <code>ACCOUNTS.txt</code> at the repo root.</p>
      <div class="gate-actions">
        <button class="btn btn-primary" onclick="location.reload()">Retry</button>
        <a class="btn btn-ghost" href="http://localhost:8000">Open localhost:8000</a>
      </div>
    </div>`));
    return;
  }
  render();
})();
