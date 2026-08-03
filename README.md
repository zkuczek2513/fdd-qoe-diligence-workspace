# FDD / QoE Diligence Workspace

**An interactive Financial Due Diligence and Quality of Earnings learning platform and case study workspace.**

Transaction Advisory Services (TAS) analysts learn Quality of Earnings by doing it and then having a
Senior Associate tear the work apart. This application reproduces that loop. You are handed a target
and a blank set of working papers, you build an adjustment schedule and defend it, and then you find
out what the engagement team actually concluded — and why.

Built with Python, Streamlit and Plotly, with an optional Claude-powered review panel.

---

## Contents

- [What it does](#what-it-does)
- [Quick start](#quick-start)
- [The simulation loop](#the-simulation-loop)
- [Architecture](#architecture)
- [Financial methodology](#financial-methodology)
- [Case study library](#case-study-library)
- [Numerical integrity policy](#numerical-integrity-policy)
- [The manager review panel](#the-manager-review-panel)
- [Testing](#testing)
- [Deployment](#deployment)
- [Limitations and disclaimers](#limitations-and-disclaimers)

---

## What it does

The workspace runs in two modes.

**Live Deal Mode** pulls annual income statement, balance sheet and cash flow data for any public
issuer through `yfinance`, normalizes several hundred vendor line-item labels onto a single canonical
diligence taxonomy, and presents it as a collapsible three-statement model. From there it is an
open-ended practice engagement: you write your own adjustments, classify the balance sheet, run the
DCF and take the result to review.

**TAS Analyst Sandbox Mode** loads a complete mock transaction — three years of articulated
financial statements, a deal context memorandum, and a hidden answer key containing the adjustments
the engagement team actually booked, the items they classified as debt-like or non-operating, their
risk register, and the executive summary of the issued report. You work the case blind. When you are
ready, **Compare to Actual Deal** produces a side-by-side variance analysis of your adjustment
ledger against the Senior Associate's, computes the exact difference between your implied Adjusted
EBITDA and theirs, and explains every adjustment you missed.

Both modes feed the same downstream analytics: the QoE bridge and waterfall, the net working capital
schedule with DSO/DIO/DPO trending and a proposed peg, an integrated DCF anchored on *your* Adjusted
EBITDA, and a transaction value bridge that walks enterprise value to implied equity value.

---

## Quick start

```bash
git clone <your-repo-url>
cd fdd_qoe_platform
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

The app opens at `http://localhost:8501`. **Sandbox Mode needs no network access and no API key** —
select it in the sidebar and start working a case immediately.

Deep links are supported, so you can share a specific case:

```
http://localhost:8501/?mode=sandbox&case=anvil
http://localhost:8501/?mode=live&ticker=CAT
```

Optional, for the Claude review panel:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

Without a key the review panel runs a deterministic local heuristic engine instead (see
[The manager review panel](#the-manager-review-panel)).

**Requirements:** Python 3.10+. Dependencies are pinned in `requirements.txt`.

---

## The simulation loop

The pedagogy is the architecture. Every mode produces the same `Engagement` object, so the analytics
never know or care whether they are looking at a public issuer or a mock target.

```
                 ┌──────────────────────┐        ┌──────────────────────┐
                 │   Live Deal Mode     │        │  TAS Analyst Sandbox │
                 │  yfinance ingestion  │        │   hardcoded case     │
                 └──────────┬───────────┘        └──────────┬───────────┘
                            │  normalize to canonical taxonomy │
                            └────────────────┬─────────────────┘
                                             ▼
                                    ┌─────────────────┐
                                    │   Engagement    │  periods, facts, context
                                    └────────┬────────┘
                                             ▼
                        ╔════════════════════════════════════════╗
                        ║        ANALYST WORKING PAPERS          ║
                        ║  adjustments · classifications · risks ║
                        ╚════════════════════╤═══════════════════╝
                                             ▼
        ┌──────────────┬──────────────┬──────────────┬────────────────────┐
        ▼              ▼              ▼              ▼                    ▼
   QoE bridge      NWC schedule   DCF model    Value bridge        Diagnostics
   + waterfall     + DSO/DIO/DPO  (anchored    (EV → equity)       (accrual gap,
                   + peg          on Adj.                          efficiency
                                  EBITDA)                          trends, leverage)
        │              │              │              │                    │
        └──────────────┴──────────────┴──────┬───────┴────────────────────┘
                                             ▼
                              ┌──────────────────────────────┐
                              │   TAS Manager Review Panel   │
                              │  Claude  ·  or heuristic     │
                              └──────────────┬───────────────┘
                                             │
                    ┌────────────────────────┴───────────────────────┐
                    ▼ (Live)                                         ▼ (Sandbox)
        Red flags in the reported                       Variance vs. the issued report,
        financials; critique of your                    coaching on what you missed,
        adjustment logic                                formal diligence summary memo
```

The critical property is that **the analyst's working papers sit upstream of everything**. Change one
adjustment and the waterfall, the DCF baseline, the implied entry multiple, the equity value and the
reviewer's assessment all move together. That is what makes it a simulation rather than a calculator.

---

## Architecture

| Module | Responsibility |
|---|---|
| `app.py` | Streamlit entry point: sidebar, tab orchestration, working-paper state, deep links |
| `config.py` | Canonical line-item taxonomy, statement layouts, adjustment categories, DCF defaults, display masks |
| `api_client.py` | `yfinance` ingestion, vendor-label synonym resolution, statement gap derivation |
| `finance_logic.py` | The financial engine: `Engagement`, `Adjustment`, QoE bridge, NWC, efficiency metrics, DCF, value bridge, diagnostics |
| `case_studies.py` | Case library, three-statement builder, answer keys, work comparison engine |
| `components.py` | Streamlit components, editors and Plotly renderers — the only layer that touches precision |
| `ai_reviewer.py` | Claude review panel, prompt assembly, and the deterministic heuristic fallback |

Supporting: `validate_cases.py` (statement articulation harness), `test_pipeline.py` (analytical
pipeline tests), `test_app.py` (headless UI tests via `streamlit.testing`).

### The canonical taxonomy

Both data sources converge on one schema defined in `config.py`, so the analytics have a single
contract. Live ingestion resolves each canonical key against an ordered list of vendor synonyms —
`yfinance` label sets drift between releases and across issuers — and derives what the vendor leaves
implicit (gross profit from revenue less cost of sales, pretax income from net income plus tax,
long-term debt as the residual of total debt).

### State management

The three editable working papers are rendered from *seed* frames in `st.session_state`, namespaced
per engagement. The seed is written once and never mutated: `st.data_editor` owns the analyst's edits
and returns the current truth on every run. Writing the returned frame back over the seed would
re-apply the widget's added- and deleted-row deltas on the next run and silently duplicate rows.

Because downstream tabs need the edited values, the editors execute first into containers reserved
inside their target tabs. Streamlit places content in container-creation order rather than execution
order, so the editors appear in the right place on screen while running early enough for every other
tab to read from them.

---

## Financial methodology

### Quality of Earnings bridge

The working paper walks GAAP net income to Adjusted EBITDA in the standard order:

```
Net Income (GAAP)
  + Income tax expense
  + Interest expense
  − Other income / (expense), net
  + Depreciation
  + Amortization — capitalized software
  + Amortization — acquired intangibles
  ────────────────────────────────────
  = EBITDA (as reported)
  ± Quality of Earnings adjustments
  ────────────────────────────────────
  = Adjusted EBITDA
```

Adjustments are classified across nine categories (non-recurring, GAAP correction, owner
compensation, related-party, run-rate/pro forma, cut-off and revenue recognition, reserve adequacy,
carve-out/standalone cost, other) and carry a **treatment**: accepted, or *rejected — proposed by
management*. Rejected adjustments stay in the working papers and are excluded from Adjusted EBITDA,
which is how a management proposal is documented and declined in a real databook.

The Plotly waterfall bridges reported EBITDA to Adjusted EBITDA for any selected period, colouring
add-backs and deductions separately and carrying the category into the hover.

### Net working capital

```
NWC = (Current assets − cash) − (Current liabilities − short-term debt − current maturities)
```

Reported alongside NWC as a percentage of revenue, period-over-period movement, and a **peg** set on
a trailing average over an adjustable lookback. The peg matters: setting it on the year-end balance
alone flatters a seller whose position has been managed into the close, and the surplus or deficit
against it flows into the value bridge.

### Efficiency metrics

```
DSO = (average accounts receivable / revenue)      × 365
DIO = (average inventory          / cost of sales) × 365
DPO = (average accounts payable   / cost of sales) × 365
Cash conversion cycle = DSO + DIO − DPO
```

Balances are **trailing two-period averages**, the convention used in TAS working papers. Sandbox
cases carry an opening comparative balance sheet outside the presented window so the first diligence
year is computed on the same basis as the rest; live ingestion promotes a leading balance-sheet-only
period to the same role. Where an opening balance is genuinely unavailable the metric falls back to
the ending balance rather than averaging against zero — an easy way to make a first period look
dramatically more efficient than it was.

### Debt-like items and non-operating assets

The classification schedule is seeded from the closing balance sheet with every candidate marked
*operating*, because deciding what is debt-like is the analyst's job. Debt-like items are obligations
the buyer inherits and settles in cash and reduce equity value; non-operating assets are not required
to run the business and increase it.

### Discounted cash flow

Anchored on the **active** Adjusted EBITDA — the number your working papers produce, not a reported
figure. For each projection year:

```
Revenue_t   = Revenue_0 × (1 + g)^t
Margin_t    = current margin + (target − current) × t/N        [linear ramp, optional]
EBITDA_t    = Revenue_t × Margin_t
EBIT_t      = EBITDA_t − D&A_t
NOPAT_t     = EBIT_t × (1 − tax rate)
ΔNWC_t      = NWC%×Revenue_t − NWC%×Revenue_(t−1)
UFCF_t      = NOPAT_t + D&A_t − Capex_t − ΔNWC_t
PV_t        = UFCF_t / (1 + WACC)^(t − 0.5)                    [mid-year convention, optional]
```

Terminal value by exit EBITDA multiple or Gordon Growth, discounted at the end of the final year.
Gordon Growth is refused with an explanatory message when terminal growth meets or exceeds WACC
rather than silently returning a negative value.

Controls: projection horizon, revenue CAGR, target EBITDA margin, margin ramp, capex % of revenue,
D&A % of revenue, NWC % of revenue, cash tax rate, WACC, terminal method and parameter, and the
mid-year convention.

### Transaction value bridge

```
Enterprise Value
  + Cash and cash equivalents
  − Total funded debt
  − Debt-like items                              (your classifications)
  + Non-operating assets                         (your classifications)
  ± Working capital surplus / (deficit) vs. peg  (optional)
  ─────────────────────────────────────────────
  = Implied Equity Value
```

---

## Case study library

Three engagements, each with a distinct earnings-quality pathology. Deal contexts and management's
represented figures are visible from the start; adjustment ledgers, classifications, risk registers
and the issued report stay hidden until you press **Compare to Actual Deal**.

| Case | Sector | Transaction | The diligence question |
|---|---|---|---|
| **Project Helios** | Vertical SaaS — dental practice management | Growth equity recapitalization | Revenue recognition on multi-year prepaid contracts, and a capitalized software balance growing far faster than engineering headcount |
| **Project Anvil** | Industrial manufacturing — precision components | Control buyout of a family-owned business | An inventory build during a destocking cycle, related-party facility leases, and whether the peak year or the current year represents the go-forward business |
| **Project Cascade** | Healthcare services — multi-site optometry MSO | Secondary buyout of a platform roll-up | Which pro forma acquisition adjustments are evidenced and which are forecasts, and a receivables ledger aging faster than revenue is growing |

Each case teaches something the reported financial statements will not tell you directly. Between
them they cover positive and negative adjustments, adjustments whose sign is counter-intuitive,
adjustments that are invisible without reading the deal context, and management proposals that should
be rejected outright.

### How the case financials are constructed

The cases are hardcoded, but they are not hand-tuned into balance. Each case declares its primitive
line items and the builder derives everything else:

1. **Income statement subtotals** are computed from the primitives (gross profit, operating income,
   pretax income, net income).
2. **Retained earnings** rolls forward: `RE_t = RE_(t−1) + Net income_t − Distributions_t`.
3. **Cash is derived as the balancing plug** from the accounting identity:
   `Cash_t = Liabilities_t + Contributed capital_t + RE_t − Non-cash assets_t`.
4. **The cash flow statement is derived** by the indirect method from the period-over-period balance
   sheet movements, with capital expenditure recovered as `ΔPP&E + depreciation` and the equivalent
   for capitalized software and acquired intangibles.

Because cash is the plug and retained earnings rolls forward on net income, the balance sheet
balances and the cash flow statement foots **by construction** rather than by arithmetic luck.
`validate_cases.py` asserts both to a tolerance of 1e-6 and reports the derived leverage, coverage
and efficiency ratios so the inputs can be sanity-checked against the narrative.

### The work comparison engine

Your adjustments are paired against the answer key by token-overlap similarity on the labels
(Jaccard over significant tokens, greedy best-first above a confidence threshold), so a paraphrased
label still matches. Every entry lands in one of three buckets:

- **Identified** — paired with an answer-key item, showing both impacts, both treatments, the
  variance and the match confidence
- **Missed** — in the issued report but not in your papers, with the full rationale and the
  technical authority behind it
- **Not in the issued report** — in your papers but not the key, which is *not automatically wrong*;
  the answer key is one team's judgement

The engine reports your implied Adjusted EBITDA, the engagement team's, the exact variance in
currency and percentage terms, and your coverage of the accepted adjustments.

---

## Numerical integrity policy

**No value is rounded anywhere in this codebase.** `round()`, `numpy.round`, `DataFrame.round` and
`Decimal.quantize` do not appear in any executable line — `test_pipeline.py` enforces this with a
tokenizer-based audit that inspects code while ignoring prose in docstrings and comments.

Every figure is carried as an IEEE-754 double at full precision from ingestion through the QoE
bridge, the working capital schedule, the DCF and the value bridge. The only place precision is
reduced is the rendering layer, where Streamlit column formats and Plotly hover templates apply
printf-style **display masks** to values that remain unmodified in session state. The sidebar's
**Full raw precision** toggle swaps the mask from two decimals to ten; it never touches the datum.

The test suite proves the property rather than asserting it: it pushes an irrational adjustment
(`π × 1000`) through the bridge and checks the result is bit-identical to the direct computation, and
checks that computed margins retain their full mantissa.

Float64 is used rather than `decimal.Decimal` deliberately — it is the standard for financial
modelling, interoperates with pandas and Plotly without lossy conversion, and carries roughly 15–17
significant decimal digits, far beyond materiality on any transaction this application models. The
guarantee here is *no rounding operations*, not arbitrary precision.

---

## The manager review panel

An internal prompt casts Claude as a demanding TAS Senior Associate or Manager reviewing your work
before it reaches the engagement Partner. The system prompt encodes how a reviewer actually thinks:
an adjustment is only as good as its support; watch the *direction* of adjustments, because an
all-positive schedule says more about the analyst than about the target; distinguish a pro forma
adjustment that annualises an evidenced historical result from one that annualises a plan; tie
earnings to cash; cite the authoritative reference and the criterion, not just the number.

**In Live Deal Mode** the reviewer evaluates the red flags visible in the reported financials —
accrual gaps between net income and operating cash flow, working capital efficiency trends, margin
movements that do not tie to the revenue story, leverage — and challenges your adjustment logic.

**In Sandbox Mode** the reviewer receives your working papers, the engagement team's conclusions and
the computed variance analysis, and coaches you: why the professionals booked what they booked, what
evidence should have prompted you to look, which procedure would have found it, and which accounting
standard governs the treatment. It closes with a formal Diligence Summary Memo, downloadable as
Markdown.

Analyst-authored text is passed inside delimited blocks and the system prompt instructs the model to
treat it strictly as data under review, so content in a working paper cannot redirect the reviewer.

Requests use `claude-opus-5` by default (Sonnet 5, Fable 5 and Haiku 4.5 are selectable) with
adaptive thinking, a configurable effort level, and streaming — adaptive thinking is on by default on
Opus 5 and the combined thinking-plus-response budget is large enough that a non-streaming request
risks an HTTP timeout. Responses are checked for a `refusal` stop reason before the content is read,
and the server-side refusal fallback is requested so a policy decline is rescued within the same
call.

### The local heuristic engine

With no API key configured the panel runs a deterministic rule-based reviewer. It is not a stub — it
runs the analytical tests a manager runs first:

- Direction analysis of the adjustment schedule, flagging one-directional add-back-only ledgers
- Pro forma and run-rate adjustments as a proportion of Adjusted EBITDA, flagged above a fifth
- Adjustments with no written rationale, and recurring normalizations booked in a single period only
- DSO / DIO / DPO trend movement across the diligence window
- Receivables growth outrunning revenue growth
- Accrual gap between net income and operating cash flow, as a proportion of EBITDA
- Net leverage against Adjusted EBITDA, with a refinancing call above 5.0x
- Coverage of the debt-like item and non-operating asset sweep, and the completeness of the risk
  register
- In Sandbox Mode, the variance against the issued report and an itemised list of what was missed

It closes with the same Partner-addressed memo structure. The application is fully demonstrable
offline.

---

## Testing

```bash
python validate_cases.py   # statement articulation + derived ratio diagnostics
python test_pipeline.py    # analytical pipeline, comparison engine, precision audit
python test_app.py         # headless UI via streamlit.testing.v1.AppTest
```

`validate_cases.py` asserts that every case balances and foots, and prints revenue, EBITDA, Adjusted
EBITDA, cash, funded debt, net leverage, NWC, implied interest rate and the efficiency metrics for
each period so the hardcoded inputs can be checked against the narrative.

`test_pipeline.py` covers the QoE bridge tying to the direct computation, the net income walk to
reported EBITDA, the NWC schedule, DCF component sums reconciling to enterprise value, Gordon Growth
degeneracy handling, value bridge steps summing to equity value, the comparison engine against
perfect / empty / partial / paraphrased submissions, editor serializer round trips, the heuristic
reviewer's findings, prompt assembly, and the no-rounding audit.

`test_app.py` drives the real Streamlit render path without a browser: the landing page, every case
in Sandbox Mode (asserting seven tabs, all six Plotly figures and the full table set render), the
compare-to-actual-deal workflow, the review panel, the precision toggle, the DCF controls, clearing
the workspace, and the empty-ticker guard rail.

### Dependency compatibility

Streamlit's layout API changed materially across the supported range: 1.4x replaced
`use_container_width=True` with `width="stretch"` and made `height=None` an error rather than
"size to content". Rather than pin to one release, `components.py` selects the correct keyword from
the installed signature, so a single codebase runs clean on both. The suite is run at both ends of
every dependency range before release:

| | Older | Newer |
|---|---|---|
| Streamlit | 1.37 | 1.60 |
| pandas | 2.2 | 3.0 |
| Plotly | 5.24 | 6.9 |

All three suites pass on both, with zero deprecation warnings.

---

## Deployment

### Streamlit Community Cloud

1. Push the repository to GitHub.
2. At [share.streamlit.io](https://share.streamlit.io), create an app pointing at `app.py`.
3. Optionally add `ANTHROPIC_API_KEY` under **Advanced settings → Secrets** to enable the Claude
   review panel:
   ```toml
   ANTHROPIC_API_KEY = "sk-ant-..."
   ```

The app runs without secrets — Sandbox Mode and the heuristic review engine need neither network
access nor credentials.

### Docker

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8501
CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
```

```bash
docker build -t fdd-qoe-workspace .
docker run -p 8501:8501 -e ANTHROPIC_API_KEY="sk-ant-..." fdd-qoe-workspace
```

---

## Limitations and disclaimers

**This is an educational tool. It is not investment advice and it is not a substitute for a
professional due diligence engagement.**

- The three case studies are **fictional**. Company names, sponsors, figures and findings were
  constructed to illustrate specific earnings-quality patterns. Any resemblance to a real
  transaction is coincidental.
- Live Deal Mode consumes an unofficial, community-maintained data source. Vendor coverage varies by
  issuer, label sets drift between releases, and most public issuers expose four rather than five
  years of annual detail through this feed. Where the taxonomy cannot resolve a line item the
  application surfaces a warning and leaves the value unknown rather than defaulting it to zero — the
  raw provider frames are available in an expander for inspection.
- A public issuer's reported statements will not contain the population of owner compensation,
  related-party and reserve-adequacy findings that make private-company diligence what it is. Live
  Deal Mode is for practising analytical review and adjustment logic; Sandbox Mode is where the
  full QoE discipline is exercised.
- The DCF is a foundational model. It does not build a full three-statement projection, model debt
  schedules or circular interest, or compute WACC from a capital structure build-up.
- Switching target resets the workspace. Working papers are held per engagement in session state and
  are not persisted across sessions.

---

## Licence

MIT.
