# TEN Capital — Startup Valuation Analyzer

Reads an investor pitch deck, combines what it says with anything you supply,
and produces a defensible **pre-money valuation range** as a one-page PDF plus a
machine-readable JSON record.

The point is not a single number. The report shows how the range was derived,
which assumptions drive it, what would justify a premium or a discount, and how
the proposed round dilutes existing holders — so an investor can challenge it,
understand it, and reproduce it.

---

## Install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

copy .env.example .env      # then add your ANTHROPIC_API_KEY
```

Python 3.11 or later. Get a key at <https://console.anthropic.com/settings/keys>.

## Run

### As a web app

```powershell
python web/server.py          # http://localhost:8030
```

Upload a deck, watch the six stages, download the PDF and the JSON. Optional
panels let you attach comparable financings, attach a user-inputs file, override
individual figures, and record consent. A run takes a few minutes, so the upload
starts a background job and the browser polls it — leave the tab open.

The interface follows the TEN Capital Network design language: navy card on an
ambient tri-colour ground, the mark's three figures drawn as a single rule across
the top of every card, a status dot that changes colour as a run moves from
analyzing to complete or failed, and the coral-to-amber gradient on the headline
and the primary action.

**Icons.** `web/static/brand-mark.png` is the TEN Capital mark and the source of
every icon the app serves — tab favicons, the multi-resolution `.ico`, the
apple-touch icon and the PWA icons. Replace that one file and run
`python tools_build_icons.py` to regenerate the set; the tab icons stay
transparent so they read on light and dark browser chrome, and the installed-app
icons get the navy ground and the safe zone Android's circular crop needs.

### As a CLI

```powershell
python -m app.main pitchdeck.pdf
python -m app.main deck.pptx --inputs inputs\company.json --comparables inputs\comps.csv
python -m app.main deck.pdf --output output\northwind_valuation.pdf
```

Two files are written, sharing a stem: `<company>_valuation.pdf` and
`<company>_valuation.json`. When Resend is configured, both are also emailed to
the desk inbox.

### Options

| Flag | Purpose |
| --- | --- |
| `--inputs`, `-i` | JSON file of user-supplied values, which outrank the deck. Start from `templates/user_inputs.json`. |
| `--comparables`, `-c` | JSON or CSV of comparable financings. Start from `templates/comparables.csv`. |
| `--output`, `-o` | Output PDF path. The JSON is written beside it. |
| `--company`, `--raise-amount`, `--pre-money` | One-off overrides without an input file. |
| `--user-name`, `--user-email` | Who requested the analysis. |
| `--consent-analysis`, `--consent-storage`, `--consent-contact` | Record explicit consent. Never inferred; absent flags stay `false`. |
| `--no-interpretation` | Skip the written interpretation pass. Every number is unchanged. |
| `--no-email` | Do not email the result, even when Resend is configured. |
| `--date` | Analysis date as `YYYY-MM-DD`. Also decides which years count as forecasts. |

**Deck formats.** `.pdf` and `.pptx`. Legacy binary `.ppt` cannot be read
directly — the application says so and asks you to save as `.pptx` or export to
PDF first.

---

## Automated email

Every completed analysis is emailed to the desk inbox with the one-page PDF and
the JSON audit trail attached. The body carries the answer rather than a link to
it — the range, the ask, the gap and the verdict, the methods and their weights,
the premium and discount drivers, the dilution table, the key milestone, the
analyst flags and what was never disclosed — so a partner can read the conclusion
on a phone and open the PDF only when they want the page itself.

Its figures come from the same formatting helpers the PDF uses, so the email and
the page cannot quote different numbers. A test asserts it.

```powershell
python -m app.main deck.pdf                 # analysed and emailed
python -m app.main deck.pdf --no-email      # analysed, not emailed
```

In the web app it is a checkbox, on by default, and the result panel reports
whether delivery succeeded.

### Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `RESEND_API_KEY` | — | Enables delivery. Leave blank and nothing is sent. |
| `REPORT_EMAIL_TO` | `Info@tencapital.group` | Recipients, comma-separated. |
| `RESEND_FROM` | Resend sandbox sender | From address. **Its domain must be verified at resend.com/domains.** |
| `EMAIL_REPORTS` | `1` | Set to `0` to disable delivery without removing the key. |

The sandbox sender `onboarding@resend.dev` only delivers to the Resend account
owner, so a real deploy must set `RESEND_FROM` to a verified domain — otherwise
mail to the desk inbox is accepted and quietly dropped.

**Delivery is best-effort.** A Resend outage, a bad key or a rejected sender
costs a warning line, never the analysis. `send_analysis` returns
`{"sent", "id", "error", "skipped"}` and never raises; the CLI prints the outcome
and the web app shows it as a banner beside the download buttons.

`GET /healthz` reports whether delivery is enabled, the recipients and the sender.

---

## Deploying to Railway

The repository ships everything Railway needs: `Procfile`, `railway.json`,
`nixpacks.toml`, `.python-version` and `.railwayignore`.

```powershell
railway login
railway init                 # or: railway link   to attach an existing project
railway variables --set "ANTHROPIC_API_KEY=sk-ant-..."
railway up
railway domain               # generates the public URL
```

Or from the dashboard: **New Project → Deploy from GitHub repo**, then add
`ANTHROPIC_API_KEY` under **Variables**. Nixpacks detects Python, installs
`requirements.txt`, and starts the command in `railway.json`.

### Service variables

| Variable | Required | Purpose |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | **yes** | Server-side key. Set it in Railway variables — never in a committed file. |
| `RESEND_API_KEY` | no | Enables automated email. Without it the app runs and reports delivery as skipped. |
| `REPORT_EMAIL_TO` | no | Recipients, default `Info@tencapital.group`. |
| `RESEND_FROM` | no | From address on a domain verified in Resend. |
| `EMAIL_REPORTS` | no | `0` disables delivery without removing the key. |
| `APP_PASSWORD` | no | Gates the public URL with a password box. Leave unset for an open deploy. |
| `VALUATION_MODEL` | no | Defaults to `claude-opus-5`. |
| `VALUATION_EFFORT` | no | Defaults to `high`. |
| `MAX_UPLOAD_MB` | no | Deck size ceiling, default 40. |
| `MAX_CONCURRENT_JOBS` | no | Concurrent runs, default 2. Each spends a deck-sized model call. |
| `JOB_TTL_MINUTES` | no | How long a finished job stays downloadable, default 60. |

`PORT` is injected by Railway; the start command reads it.

### Two things the deploy depends on

**One worker, several threads.** The job registry is process-local, so a poll
must land on the process that started the job. `Procfile` and `railway.json`
both specify `--workers 1 --threads 8`. Scaling workers up would break polling;
to serve more load, scale replicas only behind a sticky-session router, or move
the registry to Redis.

**A 600-second worker timeout.** A run takes minutes. The default 30-second
gunicorn timeout would kill it mid-analysis. `test_web.py` asserts the configured
timeout stays above 300s so it cannot be lowered by accident.

Health check: `GET /healthz` — reports whether the key is configured and how many
jobs are running, and spends no tokens. `GET /healthz?deep=1` additionally
round-trips the Anthropic API to confirm the key and model actually resolve.
Railway probes the shallow path.

### Local secrets

`.env` holds the key locally and is gitignored *and* railwayignored, so it never
reaches an image. `.env.example` is the committed copy and carries no key.

---

## What it does

```
Deck ─▶ Extract ─▶ Normalize ─▶ Select methods ─▶ Value ─▶ Adjust once ─▶ Validate ─▶ Render
```

**The model reads. Python calculates.** The model is used exactly twice: once to
read the deck into quoted, slide-cited fields and classify the qualitative
factors, and once to phrase the interpretation lines *after* the valuation is
fixed. Every figure, multiple, weight, blend, bridge, sensitivity cell and
dilution percentage is computed in Python. The model is never asked to do
arithmetic, and the second pass is optional — without an API key the
deterministic interpretation stands and the numbers are identical.

### Source precedence

When the same metric appears in more than one place, the ledger keeps the
highest-authority value and remembers the ones it displaced:

1. Explicit user-entered value
2. Most recent value disclosed in the deck
3. Value calculated from disclosed inputs
4. Analyst assumption

Every material input reaches the JSON record with its type, its citation and its
confidence — `slide 8`, `user input`, `MRR x 12`, `stage prior for Series A`.

### Method selection

Methods are chosen by stage, and stage is decided by revenue rather than by the
word on the cover. A deck calling itself Series A with nothing earned is priced
as a seed, and the revenue methods exclude themselves regardless.

| Stage | Methods |
| --- | --- |
| Pre-revenue | Comparable financings, scorecard, VC method |
| Seed | Comparable financings, revenue multiple, scorecard, VC method |
| Series A | Revenue multiple, comparable financings, VC method |
| Growth | Revenue multiple, comparable financings, VC method |

A method that cannot be grounded excludes itself and says why, in the report and
in the record. Weights renormalise across whatever ran, except that the VC
method never rises above its nominal weight — it stacks four assumptions, and
the absence of comparables should not promote the most fragile method in the set.

### One adjustment, applied once

The methods produce market anchors. Company quality moves that anchor exactly
once, in `valuation/adjustments.py`, so the section 8 bridge reconciles rather
than approximately reconciles:

```
weighted method baseline  $12.9M
94% revenue growth          +15%  $14.9M
largest customer = 31%      −10%  $13.6M
112% net revenue retention   +6%  $14.4M
...
adjusted base valuation           $16.0M
```

Growth, gross margin and retention are continuous and sized by formula; the rest
use a three-step scale. A factor with no evidenced trigger is not applied in
either direction — silence is not a weakness. A method that already prices
quality into its own answer (the scorecard) is held out of the adjustment, and
the share of the blend the factors reach is reported.

One factor from the standard's discount list is deliberately absent: *unusually
high requested valuation*. Discounting the estimate because management asked
high would push the estimate away from the ask and make the gap partly
self-inflicted. It is reported as a validation finding instead.

---

## The document template

`templates/valuation_report_template.json` is the canonical structure of the
one-pager. It describes the document and never a company: zone geometry, section
order, every field with its placeholder and length budget, the input catalogue to
be analyzed, the four valuation methods and when each excludes itself, the
provenance and precedence rules, the validation rules to be run, the ten reader
questions each section has to answer, and the palette and number formats.

It is a contract, not documentation. `tests/test_template.py` renders a page and
checks the code against the template — the section headings, the table column
labels, the extracted field catalogue, the source-precedence ranks, the palette,
the type-size range, the status values and the validation rule ids all have to
agree, or the suite fails. If the renderer grows a section or the extractor gains
a field, the template moves with it.

Use it to see what the generator will produce before running it, to review what
gets analyzed without reading the code, or as the specification for a variant
one-pager.

---

## Supplying your own data

### User inputs

Copy `templates/user_inputs.json`, fill in what you know, delete the rest. Any
key you supply outranks the deck. Unrecognised keys are reported rather than
silently ignored.

Two keys are worth knowing about:

- `revenue_basis` — one of `current`, `run_rate`, `contracted`, `historical`,
  `projected`, `pipeline`, `aspirational`. Use it to correct the classifier when
  you know what a figure actually is.
- `comparables` — an inline list, as an alternative to `--comparables`.

### Comparable financings

Supplied, never fetched and never invented. Copy `templates/comparables.csv` or
supply JSON. Column headings may be prose (`Pre-money valuation`, `ARR at
financing`); money may be written `24000000` or `$24M`; growth `0.85` or `85%`.
A row without a pre-money valuation is kept but marked unusable.

**With no comparables the report says so** — `Comparable financing data not
supplied.` — the comparable method excludes itself, the multiple falls back to a
stage prior labelled `ANALYST ASSUMPTION`, and confidence drops accordingly.

---

## Output

### The PDF

One portrait page: company snapshot, the valuation range as the visual focal
point with the ask marked on it, the methods and their weights, the premium and
discount drivers, the round and its dilution at four valuations, and a
sensitivity grid whose centre cell reproduces the headline base valuation. The
footer carries the confidence score with its reason, the analyst flags, what was
never disclosed, and the disclaimer.

One page is a hard constraint. The renderer retries at progressively tighter type
profiles and reduces content before dropping type below a readable size.

**Fonts.** Open Sans is the house face and is used automatically if
`OpenSans-Regular.ttf`, `OpenSans-Bold.ttf` and `OpenSans-Italic.ttf` are placed
in `assets/fonts/`. Otherwise the report falls back to the ReportLab core fonts,
which need no files and render identically on any host.

### The JSON record

The schema in the TEN Capital standard is emitted exactly, key for key, in the
order specified. Five keys are added — `valuation_bridge`, `sensitivity`,
`validation`, `source_ledger`, `deck` — carrying what a reader needs to
reproduce the number. They are additions, never substitutions: a consumer written
against the specified schema reads the file unchanged. `output/json_export.py`
ships `verify_schema()`, and the suite asserts conformance on all ten scenarios.

**Consent is never inferred.** All three flags default to `false` and are set
only by an explicit CLI flag or a literal `true` in the input file. A timestamp
present without consent is a validation error.

---

## Data-quality rules

The application will not fabricate revenue, comparable financings or valuations;
will not treat projected or pipeline revenue as current revenue; will not
silently substitute an industry average; will not infer retention, IP strength,
investor demand or consent. Where information is missing it prints **Not
disclosed** and lists the gap. Where an assumption is required it is labelled
**ANALYST ASSUMPTION** and itemised.

Contradictions in the deck are reported, not resolved. If slide 4 says $2.4M ARR
and slide 11 says $3.1M, both appear in the flags.

Two failure modes are guarded specifically because they are the expensive ones:

- **Projection tables.** A five-year model's columns are bare years. A revenue
  label naming a year later than the analysis date is classified `projected` and
  excluded from every multiple.
- **Units.** Financial tables state their scale once, in a header. Revenue that
  is implausibly small against the round size raises a flag naming the likely
  thousandfold understatement. Accounting parentheses — `(1,377)` — read as
  negative.

---

## Layout

```
app/
  main.py                     CLI entry point and pipeline
  config.py                   Environment, firm identity, valuation priors, style
  llm.py                      Anthropic client and structured-output helper
  mailer.py                   Resend delivery: the answer in the body, artefacts attached
  narrative.py                Optional interpretation pass, after the numbers are fixed
  ingestion/
    deck_reader.py            Format dispatch, slide-labelled transcript
    pdf_parser.py             PDF, page by page
    ppt_parser.py             PPTX, shape tree, tables and speaker notes
  extraction/
    engine.py                 One deck, one merged extraction request
    company_extractor.py      Identity, quality factors, scorecard classification
    financial_extractor.py    Operating metrics, units, revenue basis
    financing_extractor.py    The round, and supplied comparable financings
    profile_builder.py        Composition under source precedence
  valuation/
    revenue_multiple.py       ARR / revenue multiple
    comparable_financing.py   Percentiles over supplied transactions
    venture_method.py         Exit value discounted to a present pre-money
    scorecard.py              Weighted quality factors against a stage baseline
    adjustments.py            Premium / discount framework and the bridge
    sensitivity.py            The two assumptions that matter most
    dilution.py               Round arithmetic at four valuations
    valuation_engine.py       Selection, weighting, gap, confidence
  reporting/pdf_report.py     The one-page PDF
  models/                     Typed company and valuation models
  output/json_export.py       The structured record and its schema verifier
  utils/                      Normalization, formatting, validation
web/
  server.py                   Flask front end: upload, background job, poll, download
  templates/index.html        The single page
  static/brand-mark.png       The TEN Capital mark — source of every icon below
  static/                     Favicons, PWA icons and the web manifest
tools_build_icons.py            Rebuilds the icon set from the brand mark
templates/
  valuation_report_template.json   The document structure, enforced by tests
  user_inputs.json                 Starting point for user-supplied values
  comparables.csv                  Starting point for comparable financings
tests/                          516 tests across ten company scenarios
Procfile, railway.json,
nixpacks.toml, .railwayignore   Railway deployment
```

## Tests

```powershell
pip install pytest
python -m pytest
```

The suite runs entirely offline — no API key, no deck files. Ten company
scenarios are built as profiles directly and carried through every check:

pre-revenue seed · SaaS with ARR and high growth · hardware with a low gross
margin · life science awaiting approval · high customer concentration · an ask
materially above the range · no comparable data · SAFE financing · priced equity
with a full comp set · a deck with almost nothing disclosed.

The checks verify arithmetic rather than answers: the blend equals the weighted
average of the methods that ran, the bridge closes on the reported base, ownership
equals new capital over post-money, no forbidden method runs, the JSON conforms
to the specified schema, and every scenario renders on one page with the range,
the ask and the verdict actually present on it. A further group checks the
rendered document against `templates/valuation_report_template.json`, so the
template and the code cannot drift apart, and a last group covers the web layer —
what it accepts, what it refuses, the job lifecycle, and that a blank form field
never overwrites the deck and an unticked box never grants consent. Delivery is
covered with the Resend SDK stubbed, so the suite sends no mail.

---

*Confidential – for recipients only.*
