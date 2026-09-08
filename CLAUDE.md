# Project: Dentist Clinical Knowledge-Sharing Platform

## Concept
A web app / mobile app built specifically for dentists. Only verified, licensed
dentists can contribute content. Contributors document how they handled
uncommon or difficult cases: what the issue was, what tools/materials they
used, and how they resolved it. Content is organized by procedure type and
can include text, images, and video.

Long-term: everyone (dental students, hygienists, etc.) may be able to browse
as read-only users, but only verified dentists can post. Possible future
expansion beyond dentistry into medicine generally — but the MVP is
dentistry-only by design, to keep verification and taxonomy manageable.

## Why this exists / competitive context
- **MedShr** — general-medicine case-sharing platform for verified doctors.
  Closest structural analog (verified pros, case-based, media-rich), but not
  dentistry-specific.
- **Dentaltown / Hygienetown / OrthoTown** — the big incumbent dental
  community. Verified-member forums with clinical case discussion. Valuable
  content but buried in unstructured forum threads, mixed with vendor
  promotion.
- **Dental AI** — AI-assistant app for dentists (imaging analysis, specialty
  Q&A). Different model: AI-generated answers, not peer-sourced case
  knowledge.
- **Gap this project fills**: a structured, searchable, procedure-indexed
  knowledge base of real-world complications and solutions, contributed only
  by verified dentists — closer to a clean clinical knowledge base than a
  forum.

## Core requirements

### 1. Contributor verification (only legitimate dentists can post)
Everyone can sign up and browse; only verified accounts get a "contributor"
badge and posting rights. Verification approach (to be finalized):
- License number + issuing board/state entered at signup
- Cross-check against public licensing registries where available
  (e.g. state dental boards, Dental Council of India / state dental councils,
  UK GDC, or NPI registry for the US as a fast first-pass check)
- Document upload (degree + license certificate) + manual admin review for
  anything the registry can't auto-confirm
- Peer vouching once there's a base of verified dentists
- Periodic re-verification (e.g. annually) to catch lapsed/revoked licenses

### 2. Content structure
- Organized by procedure type (e.g. root canal, extraction, implant, etc.),
  not a flat forum feed
- Each entry: what happened / what was unusual, tools & materials used, how
  it was resolved, supporting media (photos, video, notes)
- Searchable/filterable by procedure type, complication type, tools used

### 3. Open questions / not yet decided
- Tech stack (frontend, backend, database) — not chosen yet
- Web app, mobile app, or both — not chosen yet
- Monetization / incentive model for contributors (CE credit? reputation?
  paid?) — not decided
- Legal/liability review for published clinical advice between professionals
  — needs to happen before launch, not after
- Which region(s) to launch in first (affects which licensing registry to
  integrate with first)

## Working notes for Claude Code
- This is early-stage / pre-architecture. Don't assume a stack has been
  chosen — ask or propose options rather than defaulting silently.
- Verification logic is a first-class feature, not an afterthought — treat
  "contributor" as a distinct role from "read-only user" everywhere in the
  data model and permissions.
- Favor a clean, searchable, structured UI over a forum/feed UI — that's the
  whole differentiation from Dentaltown.
- Keep dentistry-only scope for now; don't generalize the schema to "doctors"
  prematurely even though that's a stated future direction.
