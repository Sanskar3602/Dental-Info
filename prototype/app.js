/* ============================================================
   Cuspid prototype — hash-routed SPA, no build step, no backend.
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
  book:   '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h12v18z"/><path d="M5 17h14"/></svg>',
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
};

const verifiedBadge = () => `<span class="verified">${ICON.check}Verified</span>`;
const canPost = () => SESSION.role === "contributor";

/* All distinct tools, for the filter rail */
const ALL_TOOLS = [...new Set(CASES.flatMap((c) => c.tools))].sort();

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

function filteredCases() {
  const q = state.query.trim().toLowerCase();
  let out = CASES.filter((c) => {
    if (state.procedure && c.procedure !== state.procedure) return false;
    if (state.complications.size && !c.complications.some((x) => state.complications.has(x))) return false;
    if (state.tools.size && !c.tools.some((x) => state.tools.has(x))) return false;
    if (!q) return true;
    const hay = [c.title, c.summary, c.presentation, c.unusual, c.procedurePath,
                 c.complications.join(" "), c.tools.join(" "), c.author.name].join(" ").toLowerCase();
    return q.split(/\s+/).every((w) => hay.includes(w));
  });
  const by = { recent: (a, b) => b.date.localeCompare(a.date),
               reads:  (a, b) => b.reads - a.reads,
               saves:  (a, b) => b.saves - a.saves };
  return out.sort(by[state.sort]);
}

/* ── Browse view ─────────────────────────────────────────── */
function renderBrowse() {
  const results = filteredCases();
  const p = state.procedure ? procLabel(state.procedure) : null;

  const view = el(`<div class="layout">
    <aside class="rail rail-left">
      <p class="rail-title">Procedures</p>
      <div id="taxonomy"></div>
      <div class="rail-divider"></div>
      <button class="btn btn-primary btn-block btn-sm" id="newCaseBtn">${ICON.plus} Document a case</button>
    </aside>

    <section>
      <div class="page-head">
        ${p ? `<p class="crumbs">Procedures › ${esc(p.group)}</p>` : ""}
        <h1>${p ? esc(p.leaf) : "All documented cases"}</h1>
        <p class="sub">${p
          ? "Uncommon and difficult cases in this procedure, contributed by verified dentists."
          : "A structured, procedure-indexed record of real complications and how they were resolved."}</p>
      </div>
      <div class="results-bar">
        <span class="results-count"><b>${results.length}</b> case${results.length === 1 ? "" : "s"}${state.query ? ` for “${esc(state.query)}”` : ""}</span>
        <div class="sort-wrap">
          <span>Sort</span>
          <select id="sortSelect">
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
  </div>`);

  /* taxonomy */
  const tax = $("#taxonomy", view);
  PROCEDURES.forEach((g) => {
    const openGroup = g.children.some((c) => c.id === state.procedure);
    const grp = el(`<div class="tax-group${openGroup ? " open" : ""}">
      <button class="tax-parent">${ICON.chev}<span>${esc(g.label)}</span><span class="cnt">${countForGroup(g)}</span></button>
      <div class="tax-children"></div>
    </div>`);
    $(".tax-parent", grp).onclick = () => grp.classList.toggle("open");
    const kids = $(".tax-children", grp);
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
  chipRow($("#toolChips", view), ALL_TOOLS.slice(0, 14), state.tools);

  /* results */
  const list = $("#caseList", view);
  if (!results.length) {
    list.appendChild(el(`<div class="empty"><h3>No cases match these filters</h3>
      <p>Try clearing a filter or broadening the search.</p></div>`));
  } else {
    results.forEach((c) => list.appendChild(caseCard(c)));
  }

  /* controls */
  const sort = $("#sortSelect", view);
  sort.value = state.sort;
  sort.onchange = (e) => { state.sort = e.target.value; render(); };
  $("#clearFilters", view).onclick = () => {
    state.complications.clear(); state.tools.clear();
    state.procedure = null; state.query = ""; $("#globalSearch").value = ""; render();
  };
  $("#newCaseBtn", view).onclick = () => { location.hash = "#/contribute"; };

  return view;
}

function caseCard(c) {
  const a = el(`<a class="case-card" href="#/case/${c.id}">
    <div class="case-meta-top">
      <span class="tag tag-proc">${esc(c.procedurePath)}</span>
      ${c.complications.map((x) => `<span class="tag tag-comp">${esc(x)}</span>`).join("")}
      <span class="diff diff-${c.difficulty}">${c.difficulty}</span>
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
        <span>${ICON.msg}${c.comments.length}</span>
        ${c.media.length ? `<span>${ICON.image}${c.media.length}</span>` : ""}
      </div>
    </div>
  </a>`);
  return a;
}

/* ── Case detail view ────────────────────────────────────── */
function renderCase(id) {
  const c = CASES.find((x) => x.id === id);
  if (!c) return el(`<div class="empty"><h3>Case not found</h3><p><a href="#/browse">Back to browse</a></p></div>`);

  const view = el(`<div>
    <a class="back-link" href="#/browse">${ICON.left} All cases</a>
    <div class="detail">
      <article>
        <div class="case-meta-top">
          <span class="tag tag-proc">${esc(c.procedurePath)}</span>
          ${c.complications.map((x) => `<span class="tag tag-comp">${esc(x)}</span>`).join("")}
          <span class="diff diff-${c.difficulty}">${c.difficulty} difficulty</span>
        </div>
        <h1>${esc(c.title)}</h1>
        <div class="detail-byline">
          <span class="av byline-av" style="width:38px;height:38px;border-radius:50%;display:grid;place-items:center;background:var(--teal-100);color:var(--teal-800);font-size:13px;font-weight:700">${initials(c.author.name)}</span>
          <div>
            <div class="byline-name">${esc(c.author.name)} ${c.author.verified ? verifiedBadge() : ""}</div>
            <div class="byline-sub">${esc(c.author.credential)} · ${esc(c.author.location)} · ${fmtDate(c.date)}</div>
          </div>
        </div>

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
          <ol class="steps">${c.resolution.map((s) => `<li>${esc(s)}</li>`).join("")}</ol>
        </div>
        <div class="section">
          <p class="section-label">Outcome</p>
          <div class="outcome"><p>${esc(c.outcome)}</p></div>
        </div>
        <div class="section callout">
          <p class="section-label">Clinical takeaways</p>
          <ul>${c.takeaways.map((t) => `<li>${esc(t)}</li>`).join("")}</ul>
        </div>
        ${c.media.length ? `<div class="section">
          <p class="section-label">Media (${c.media.length})</p>
          <div class="media-grid">${c.media.map((m) => `<div class="media-item">
            <span class="badge">${m.type}</span>${m.type === "video" ? ICON.video : ICON.image}<span>${esc(m.label)}</span>
          </div>`).join("")}</div>
        </div>` : ""}

        <div class="section">
          <p class="section-label">Peer discussion (${c.comments.length})</p>
          ${c.comments.length
            ? c.comments.map((m) => `<div class="comment">
                <span class="av">${initials(m.author)}</span>
                <div class="comment-body">
                  <div class="comment-head"><span class="nm">${esc(m.author)}</span>
                    ${m.verified ? verifiedBadge() : ""}<span class="dt">${fmtDate(m.date)}</span></div>
                  <div class="comment-text">${esc(m.text)}</div>
                </div></div>`).join("")
            : `<p style="color:var(--ink-400);font-size:13.5px">No discussion yet.</p>`}
          <div style="margin-top:16px">
            ${canPost()
              ? `<button class="btn btn-ghost btn-sm" id="addComment">${ICON.msg} Add a clinical comment</button>`
              : `<div style="display:flex;align-items:center;gap:9px;color:var(--ink-400);font-size:13px">
                   <span style="width:15px;height:15px;display:inline-block">${ICON.lock}</span>
                   Only verified dentists can comment. <a href="#/verify" style="color:var(--teal-700);font-weight:600">Get verified</a>
                 </div>`}
          </div>
        </div>
      </article>

      <aside class="sidecar">
        <div class="panel">
          <button class="btn btn-primary btn-block" id="saveCase">${ICON.bookmk} Save to library</button>
        </div>
        <div class="panel">
          <h4>Tools &amp; materials used</h4>
          <div class="tool-list">${c.tools.map((t) => `<span class="tag tag-tool">${esc(t)}</span>`).join("")}</div>
        </div>
        <div class="panel">
          <h4>Case record</h4>
          <div class="kv">
            <div class="kv-row"><span class="k">Case ID</span><span class="v">${esc(c.id.toUpperCase())}</span></div>
            <div class="kv-row"><span class="k">Procedure</span><span class="v">${esc(c.procedurePath)}</span></div>
            <div class="kv-row"><span class="k">Published</span><span class="v">${fmtDate(c.date)}</span></div>
            <div class="kv-row"><span class="k">Reads</span><span class="v">${fmtNum(c.reads)} · ${fmtNum(c.saves)} saves</span></div>
          </div>
        </div>
        <div class="panel" style="background:var(--teal-50);border-color:var(--teal-100)">
          <h4 style="color:var(--teal-700)">Peer-contributed content</h4>
          <p style="margin:0;font-size:12.5px;color:var(--ink-700);line-height:1.55">
            Written by a verified dentist for professional discussion. Not a clinical guideline and
            not a substitute for your own judgement.</p>
        </div>
      </aside>
    </div>
  </div>`);

  $("#saveCase", view).onclick = () => toast("Saved to your library");
  const ac = $("#addComment", view);
  if (ac) ac.onclick = () => toast("Comment composer — not built in this prototype");
  return view;
}

/* ── Contribute view (role-gated) ────────────────────────── */
function renderContribute() {
  if (!canPost()) {
    const pending = SESSION.role === "pending";
    const gate = el(`<div class="gate">
      <div class="gate-icon">${pending ? ICON.clock : ICON.lock}</div>
      <h2>${pending ? "Your verification is still in review" : "Only verified dentists can contribute"}</h2>
      <p>${pending
        ? "We are cross-checking your license against the issuing board. You will get posting rights as soon as it clears — usually within two business days."
        : "Anyone can read Cuspid, but posting a case requires a verified dental license. Verification takes a few minutes to submit."}</p>
      <div class="gate-actions">
        <a class="btn btn-primary" href="#/verify">${ICON.shield} ${pending ? "View verification status" : "Start verification"}</a>
        <a class="btn btn-ghost" href="#/browse">Keep browsing</a>
      </div>
    </div>`);
    return gate;
  }

  const view = el(`<div class="form-wrap">
    <div class="page-head">
      <h1>Document a case</h1>
      <p class="sub">Structured entry — this is what makes cases searchable later. Aim for what you would want to read at 8am before a difficult appointment.</p>
    </div>
    <div class="form-steps">
      <div class="form-step on">1 · Classification</div>
      <div class="form-step on">2 · Clinical narrative</div>
      <div class="form-step">3 · Media &amp; review</div>
    </div>
    <form class="form-card" id="caseForm">
      <div class="field">
        <label for="f-title">Case title</label>
        <input id="f-title" type="text" placeholder="e.g. Separated rotary file in the apical third of a curved MB canal" required>
        <p class="hint">Describe the complication, not the treatment. Specific beats catchy.</p>
      </div>
      <div class="field-row">
        <div class="field">
          <label for="f-proc">Procedure type</label>
          <select id="f-proc" required>
            <option value="">Select a procedure…</option>
            ${PROCEDURES.map((g) => `<optgroup label="${esc(g.label)}">
              ${g.children.map((c) => `<option value="${c.id}">${esc(c.label)}</option>`).join("")}</optgroup>`).join("")}
          </select>
        </div>
        <div class="field">
          <label for="f-comp">Primary complication</label>
          <select id="f-comp" required>
            <option value="">Select a complication…</option>
            ${COMPLICATIONS.map((c) => `<option>${esc(c)}</option>`).join("")}
          </select>
        </div>
      </div>
      <div class="field">
        <label for="f-tools">Tools &amp; materials used</label>
        <input id="f-tools" type="text" placeholder="Comma separated — e.g. ProTaper Gold F2, ultrasonic tip, 17% EDTA">
        <p class="hint">Brand and size where it mattered to the outcome.</p>
      </div>
      <div class="field">
        <label for="f-pres">Presentation</label>
        <textarea id="f-pres" placeholder="Age, sex, tooth, history, findings. No identifying details."></textarea>
      </div>
      <div class="field">
        <label for="f-unusual">What was unusual or difficult</label>
        <textarea id="f-unusual" placeholder="The specific thing that made this case not routine."></textarea>
      </div>
      <div class="field">
        <label for="f-res">How you resolved it</label>
        <textarea id="f-res" style="min-height:130px" placeholder="One step per line. Include what you tried that did not work."></textarea>
      </div>
      <div class="field">
        <label for="f-out">Outcome &amp; follow-up</label>
        <textarea id="f-out" style="min-height:74px" placeholder="Review interval and what you found."></textarea>
      </div>
      <div class="field">
        <label>Supporting media</label>
        <div class="dropzone">${ICON.upload}
          <div>Drop radiographs, clinical photos or video here</div>
          <div class="hint" style="margin-top:5px">Faces and identifiers are auto-flagged before publishing</div>
        </div>
      </div>
      <div class="form-actions">
        <button type="button" class="btn btn-ghost" id="saveDraft">Save draft</button>
        <button type="submit" class="btn btn-primary">${ICON.check} Publish case</button>
      </div>
    </form>
  </div>`);

  $("#saveDraft", view).onclick = () => toast("Draft saved");
  $("#caseForm", view).onsubmit = (e) => {
    e.preventDefault();
    toast("Case published — visible under its procedure category");
    setTimeout(() => { location.hash = "#/browse"; }, 900);
  };
  return view;
}

/* ── Verification view ───────────────────────────────────── */
function renderVerify() {
  const role = SESSION.role;
  const banner = {
    contributor: { cls: "sb-ok", icon: ICON.shield, h: "You are a verified contributor",
      p: "License confirmed against the issuing board. You can publish cases and comment. Next re-verification due Aug 2027." },
    pending: { cls: "sb-pending", icon: ICON.clock, h: "Verification in review",
      p: "Registry cross-check is running. Most submissions clear within two business days." },
    reader: { cls: "sb-none", icon: ICON.lock, h: "Read-only account",
      p: "You can browse and search every case. Submit your license to get posting rights." },
  }[role];

  const stepState = (s) => {
    if (role === "contributor") return "done";
    if (role === "reader") return s.key === "account" ? "done" : "todo";
    return s.state;
  };
  const stepText = { done: "Complete", pending: "In progress", todo: "Not started" };

  const view = el(`<div class="verify-wrap">
    <div class="page-head">
      <h1>Contributor verification</h1>
      <p class="sub">Every contributor on Cuspid is a licensed dentist, checked against the issuing board. This is what makes the content worth reading.</p>
    </div>

    <div class="status-banner ${banner.cls}">
      <span class="si">${banner.icon}</span>
      <div><h3>${banner.h}</h3><p>${banner.p}</p></div>
    </div>

    <div class="panel" style="margin-bottom:14px">
      <h4>Verification steps</h4>
      <ul class="vsteps">
        ${SESSION.verification.steps.map((s) => {
          const st = stepState(s);
          return `<li class="vstep ${st}">
            <span class="dot">${st === "done" ? ICON.check : ""}</span>
            <span class="txt">${esc(s.label)}<span class="st">${stepText[st]}</span></span>
            ${st === "todo" && s.key === "docs" ? `<span class="act"><button class="btn btn-ghost btn-sm" data-act="upload">Upload</button></span>` : ""}
          </li>`;
        }).join("")}
      </ul>
    </div>

    <div class="panel" style="margin-bottom:14px">
      <h4>License on file</h4>
      <div class="kv">
        <div class="kv-row"><span class="k">License number</span><span class="v">${esc(SESSION.license.number)}</span></div>
        <div class="kv-row"><span class="k">Issuing board</span><span class="v">${esc(SESSION.license.board)}</span></div>
        <div class="kv-row"><span class="k">Country</span><span class="v">${esc(SESSION.license.country)}</span></div>
      </div>
    </div>

    <div class="panel">
      <h4>What each role can do</h4>
      <div class="kv">
        <div class="kv-row"><span class="k">Read-only user</span><span class="v">Browse, search, filter and save every published case.</span></div>
        <div class="kv-row"><span class="k">Verified contributor</span><span class="v">Everything above, plus publishing cases, commenting, and vouching for peers.</span></div>
        <div class="kv-row"><span class="k">Re-verification</span><span class="v">Annual, to catch lapsed or revoked licenses.</span></div>
      </div>
    </div>
  </div>`);

  view.querySelectorAll("[data-act]").forEach((b) => { b.onclick = () => toast("Document upload — not built in this prototype"); });
  return view;
}

/* ── Router ──────────────────────────────────────────────── */
function render() {
  const hash = location.hash || "#/browse";
  const host = $("#view");
  host.innerHTML = "";

  let node, navKey;
  if (hash.startsWith("#/case/"))          { node = renderCase(hash.slice(7));  navKey = "#/browse"; }
  else if (hash.startsWith("#/contribute")){ node = renderContribute();          navKey = "#/contribute"; }
  else if (hash.startsWith("#/verify"))    { node = renderVerify();              navKey = "#/verify"; }
  else                                     { node = renderBrowse();              navKey = "#/browse"; }

  host.appendChild(node);
  document.querySelectorAll(".topnav-link").forEach((a) => {
    a.classList.toggle("active", a.getAttribute("href") === navKey);
  });
  if (!hash.startsWith("#/case/")) window.scrollTo(0, 0);
}

/* ── Global wiring ───────────────────────────────────────── */
window.addEventListener("hashchange", render);

$("#globalSearch").addEventListener("input", (e) => {
  state.query = e.target.value;
  if (!location.hash.startsWith("#/browse")) { location.hash = "#/browse"; return; }
  render();
  $("#globalSearch").focus();
});

document.addEventListener("keydown", (e) => {
  if (e.key === "/" && document.activeElement.tagName !== "INPUT" && document.activeElement.tagName !== "TEXTAREA") {
    e.preventDefault(); $("#globalSearch").focus();
  }
  if (e.key === "Escape" && document.activeElement === $("#globalSearch")) {
    $("#globalSearch").value = ""; state.query = ""; render();
  }
});

$("#roleSelect").addEventListener("change", (e) => {
  SESSION.role = e.target.value;
  const label = { reader: "read-only user", pending: "verification pending", contributor: "verified contributor" }[SESSION.role];
  toast(`Now viewing as: ${label}`);
  render();
});

$("#avatar").textContent = SESSION.initials;
$("#roleSelect").value = SESSION.role;
render();
