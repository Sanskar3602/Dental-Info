# Dental Info — clickable prototype

A **UI/UX prototype only**. No backend, no build step, no dependencies — mock data
lives in `data.js`. The point is to see whether the structure of the product works
before committing to a stack.

## Run it

Open `index.html` in a browser. That's it.

(If you'd rather serve it: `python -m http.server 8000` in this folder, then
visit `http://localhost:8000`.)

## What to click

| Where | What it demonstrates |
|---|---|
| Left rail | Procedure taxonomy (parent → leaf), with live case counts. This is the "not a forum" differentiator. |
| Right rail | Filter by complication type and by tools/materials — stacks with search and procedure. |
| Search box | Multi-word search across title, narrative, complication and tools. Press `/` to focus, `Esc` to clear. |
| Any case card | The structured case record: Presentation → What was unusual → Resolution steps → Outcome → Takeaways → Media → Peer discussion. |
| **Demo role** dropdown (top right) | Switches between read-only user / verification pending / verified contributor. Watch Contribute and the comment box change. |
| Contribute | Gated. As a reader you get a verification gate; as a contributor you get the structured submission form. |
| Verification | Multi-step verification status: license submitted → registry cross-check → document upload → admin review. |

## Deliberate design choices

- **Structured record, not a feed.** Every case renders the same labelled
  sections in the same order, so it reads like a reference entry rather than
  a thread. That's the whole differentiation from Dentaltown.
- **Role is visible everywhere.** Contributor vs read-only is enforced in the
  UI at the Contribute route and the comment composer, and the verified badge
  appears on every byline. Per CLAUDE.md, this is treated as first-class, not
  bolted on.
- **Serif for case titles and body, sans for UI chrome.** Clinical content
  should read like something you'd trust; navigation should read like a tool.
- **Media is a labelled grid, not a gallery.** Radiographs and intra-op photos
  are evidence attached to a step, so they're captioned placeholders here.
- **A liability note sits on every case page.** Peer-to-peer clinical content
  needs it, and CLAUDE.md flags legal review as pre-launch.

## Not built (intentionally)

Auth, real upload, real search index, comment composer, saved library,
peer vouching, admin review queue, mobile app. Placeholders show where they go.

## Files

- `index.html` — shell, top bar, search, role switcher
- `styles.css` — design tokens + all component styles
- `app.js` — hash router, filtering, four views
- `data.js` — mock taxonomy, 8 mock cases, demo session

## Configuration

The prototype itself needs **no configuration** — it has no backend and reads no
secrets. Open `index.html` and it runs.

Project-level config lives at the repo root:

- `.env.example` — committed template, no real values
- `.env` — your real secrets, gitignored, never committed
- `.gitignore` — keeps `.env` and stray secret files out of git

Anything in `.env` is for build scripts, CI, or a future backend only. Never
reference it from `index.html`, `app.js`, or `data.js`: this is a static site,
so any value the browser can load is public.
