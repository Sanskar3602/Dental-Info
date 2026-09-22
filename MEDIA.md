# Media uploads

Cases can now carry real photos. This documents why the implementation looks
the way it does — every number here traces back to a platform constraint, not
a preference.

## The two limits, and why they're different numbers

| Limit | Value | Why |
|---|---|---|
| Per file | **4 MB** | A Vercel Serverless Function's request **and response** body is hard-capped at 4.5 MB — infrastructure-level, not configurable ([Vercel docs](https://vercel.com/docs/functions/limitations)). We serve a complete file in one response, so it has to fit under that cap with headroom. |
| Per case | **50 MB** | This is the number you asked for, sized against the Neon free plan's 0.5 GB storage budget. |

50 MB per case, in ≤4 MB files, means a case can carry up to ~12 photos —
comfortable for real clinical documentation (typically 2–6 images: pre-op,
intra-op, post-op).

**Video is not supported yet.** A clip that says anything useful will not fit
in 4 MB, and there's no separate video pipeline. The upload form only accepts
images for now.

## Why chunked upload

A 4 MB file can't go up in one request either — base64 inflates it to ~5.3 MB,
over the 4.5 MB cap before you even count the JSON wrapper. So the browser
splits each file into ~2 MB pieces and posts them one at a time:

```
POST /api/media/start   {filename, mime, size_bytes}     -> {id, chunk_size, chunks_total}
POST /api/media/chunk   {id, index, data_base64}   × N   -> {done: false, chunks_received} | {done: true}
GET  /api/media/:id                                       -> the file, once complete
```

Chunks must arrive **in order** (the server checks `index` against how many
it's received) and are sized to a multiple of 3 bytes, so each chunk's
base64 encoding can be concatenated directly without re-encoding a growing
buffer on every request — that would be O(n²) bandwidth for a 50 MB case.

On the last chunk, the server decodes the full stored blob and checks its
length against the declared `size_bytes`. A mismatch marks the upload
`abandoned` instead of silently serving a corrupt file.

## Why bytes live in Postgres, not object storage

The obvious alternative is Vercel Blob or S3: upload goes straight from the
browser to the store, bypassing the function body limit entirely on both
directions. That's the architecturally cleaner answer.

It's not what's built, because the 50 MB figure came from your Neon plan —
the intent was clearly for media to count against that budget, not a second
storage bill. Postgres `bytea` (SQLite `BLOB` locally) makes that literal:
50 MB of images costs 50 MB of budget. Base64 text would have cost ~66 MB for
the same content; storing raw bytes avoids that ~33% tax.

If usage ever outgrows 0.5 GB, moving to object storage is the next step —
the `media` column already stores a URL per item, so migrating means writing
new URLs, not touching the schema.

## A bug this surfaced: SQLite's `||` corrupts large blobs

While wiring the **local** upload path, chunk 2 of a multi-chunk file kept
coming back a few hundred bytes long instead of megabytes. Isolated with a
minimal repro, independent of any of this project's code:

```python
db.execute("UPDATE t SET data = data || ? WHERE id='a'", (raw_2mb_bytes,))
# stored length: 208        (should be 2,097,150)

db.execute("INSERT INTO t2 (data) VALUES (?)", (raw_2mb_bytes,))
# stored length: 2,097,150  (correct)
```

SQLite's `||` operator, concatenating a `BLOB` column with a bound Python
`bytes` parameter, silently truncates. A plain parameterized `INSERT`/`UPDATE`
with the same bytes stores it correctly. The fix (`server/store.py`,
`append_chunk`) reads the current blob, concatenates in Python, and writes the
whole thing back with a bound parameter — never `||` on a growing blob.

Postgres's `bytea || bytea` (used in `api/index.py`) does not share this bug;
it's a well-established, correct operation there, and was verified against
real Neon (byte-exact SHA-256 on a 3 MB round-trip).

## Seed images: real, licensed, attributed

The 8 example cases now carry real photographs and radiographs from Wikimedia
Commons, sourced and license-checked individually — not stock filler. All are
CC BY / CC BY-SA / public domain, verified by fetching each file's own
Commons page rather than trusting a search snippet.

They're deliberately labelled **"representative image"** everywhere in the
UI and data: these are real clinical photos of real (anonymized) patients on
Commons, reused here to make the demo readable — not literally the patient
described in that case's write-up. Claiming otherwise would misrepresent
both the case and the original photographer's work.

CC BY-SA requires attribution; each item carries a `credit` field
(photographer + license) and the frontend renders it as a caption on the
image and in the lightbox — not just logged in this file.

| Case | Image | Author | License |
|---|---|---|---|
| c-1041 | Periapical radiograph | Coronation Dental Specialty Group | CC BY-SA 3.0 |
| c-1039 | Sinus lift with implant | DRosenbach | CC BY-SA 3.0 |
| c-1036 | Periapical radiograph | Werneuchen | Public domain |
| c-1030 | Extracted tooth | David M. Jensen | CC BY-SA 3.0 |
| c-1024 | Panoramic radiograph | Coronation Dental Specialty Group | CC BY 3.0 |
| c-1019 | Implant radiograph | Racrat30 | CC BY-SA 4.0 |
| c-1012 | Panoramic, impacted third molar | Coronation Dental Specialty Group | CC BY-SA 3.0 |
| c-1007 | Clear aligner | Smikey Io | CC BY-SA 3.0 |

External links like these cost nothing from the Neon budget and are not
policed by the 50 MB cap — only items whose `url` points at our own
`/api/media/<id>` are checked and counted (`validate_media` in both backends).

## Known limitations

- **No orphan cleanup.** An upload that finishes but is never attached to a
  case (user abandons the form) stays in `media_assets` forever, consuming
  budget. Fine for the current use, worth a sweep job before real traffic.
- **No image resizing/compression.** A 4 MB photo is stored and served at
  full size. Thumbnailing would stretch the same budget further.
- **No de-identification.** The dropzone says cropping faces/identifiers is
  the uploader's responsibility — there is no automatic redaction. Flagged in
  the original feature audit; still true.
- **Video is unsupported**, as above.
