# CLAUDE.md — working notes for this repository

Read this before making changes. It records decisions and traps that are not obvious from the
code and that have already cost real debugging time.

## What this is

An FDD / QoE diligence **training platform** — a Streamlit app that teaches financial due
diligence by making the user do it, then grading them against a hidden answer key.

Three modules behind one sidebar selector:

1. **FDD / QoE Workspace** — three-statement model, QoE bridge, NWC + peg, DCF, value bridge,
   sandbox case comparison, manager review
2. **SEC Narrative Risk Scanner** — EDGAR ingestion, regex risk detectors, year-over-year filing
   diff, pushes quantified haircuts into Module 1
3. **ASC 805 PPA Engine** — purchase price allocation, deferred tax, goodwill, opening balance sheet

Live: `zachary-kuczek-qoe.streamlit.app` (Streamlit Community Cloud, auto-deploys on push to `main`).

## Who the owner is

An accounting student targeting Transaction Advisory Services — **not a developer**. He directs
the build with AI tooling and owns the accounting content.

**This matters for how you write and talk about the project.** He has explicitly asked that
nothing imply he writes code. On his résumé and in any user-facing copy, the framing is *"designed
with AI development tools"*, never *"built in Python"*. Explain changes in terms of what they do,
not how they were implemented, unless he asks.

## Hard constraints

**Never round any number.** No `round()`, `numpy.round`, `DataFrame.round`, or `Decimal.quantize`
anywhere in executable code. `test_pipeline.py` enforces this with a tokenizer audit that ignores
prose in docstrings. Display formatting is printf masks applied by Streamlit and Plotly at render
time; the stored value is never touched. A pandas `Styler` is acceptable because it only emits CSS.

**The suite must pass on both ends of the dependency range.** Streamlit 1.37 / pandas 2.2 /
plotly 5.24 *and* Streamlit 1.60 / pandas 3.0 / plotly 6.9. There is a venv at `/tmp/st_latest`
for the newer stack; recreate it if missing. Testing only one end has let two bugs reach
production already.

**Pushing is done by the owner through GitHub Desktop.** Terminal git has no credentials —
`git push` fails with "could not read Username". Commit locally, then tell him to push.

## Traps already hit — do not rediscover these

**`st.data_editor` stores deltas, not values.** It records added/edited/deleted rows *against the
frame it was given*. Writing the returned frame back over its own seed re-applies those deltas on
the next run and duplicates rows. Seeds in `st.session_state` are written once and never mutated;
the widget owns the edits. `workspace.push_to_qoe_ledger` rebuilds the combined frame, writes the
seed, and **drops the widget key** so the editor re-seeds cleanly.

**Expanders cannot nest, and Streamlit enforces it inconsistently.** 1.37 raises
`StreamlitAPIException`; 1.60 does not. `components._collapsible` attempts the expander and renders
inline if refused. Never assume nesting works because it worked locally.

**`height=None` is invalid on Streamlit 1.4x+.** Use `components.sizing()`, which omits the
argument entirely rather than passing `None`. Same helper picks `width="stretch"` vs
`use_container_width=True` from the installed signature, so there are zero deprecation warnings on
either stack.

**`DataFrame.to_markdown()` requires `tabulate`,** which pandas 2.x bundles and 3.x does not. Use
`export.markdown_table()` instead. This broke the deployed report tab.

**Streamlit renders paired `$` as LaTeX.** Currency-dense text like "$96.0 million … $16.0 million"
silently becomes an equation. All case narratives and review memos go through `ui.markdown()`,
which escapes unescaped `$`. Never call `st.markdown` directly on financial prose.

**SEC EDGAR returns 403 without a contact email in the User-Agent.** See `SEC_DEFAULT_USER_AGENT`
in `config.py`; override with the `SEC_USER_AGENT` env var. `urllib` also does not auto-decompress
gzip the way `aiohttp` does — `edgar_client` handles that explicitly.

**`lxml` is deliberately not a dependency.** No 5.x release publishes a `cp314` wheel, and
Streamlit Cloud runs Python 3.14, so a capped pin forces a source build that fails. `edgar_client`
uses lxml when present and falls back to a regex stripper — verified byte-identical output on a
6.1 MB 10-K. **Do not add it back.** Same caution applies to `matplotlib`: pandas `Styler` works
without it and it is not installed on the deployment image.

**EDGAR item extraction scores boundary pairs, it does not take the last heading match.** Item
headings appear in the table of contents, as the real heading, and as cross-references in later
prose. Taking the last match returned 304k characters of a 557k-character 10-K. See
`edgar_client._locate_section`.

**EDGAR throttles datacenter IPs.** Module 2 is reliable locally, flaky on Streamlit Cloud.
Modules 1 and 3 are unaffected. Send recruiters the sandbox deep link:
`?mode=sandbox&case=helios`.

## State namespaces

Only the active module renders, and Streamlit garbage-collects widget state for widgets it did not
render — so `editor::` keys cannot be read across modules. Each module publishes its materialised
outputs to `shared::` on every run.

```
seed::<engagement>::<name>      immutable editor seeds (Module 1)
editor::<engagement>::<name>    Streamlit-owned widget state
shared::<engagement>::<name>    published cross-module outputs
sec::<engagement>::<name>       Module 2 private
ppa::<engagement>::<name>       Module 3 private
```

## Sandbox case data

Cases hardcode primitive line items only. The builder derives subtotals, rolls retained earnings
forward, derives **cash as the balancing plug**, and derives the cash flow statement by the
indirect method from balance sheet movements. The statements therefore articulate *by
construction* — `validate_cases.py` asserts `A − L − E = 0.0000000000`.

If you change a case's numbers, re-run `validate_cases.py` and check the narrative text still
matches the computed figures. The FDD report summaries quote specific numbers.

## Tests

```bash
python validate_cases.py   # statement articulation + derived ratios
python test_pipeline.py    # analytical pipeline, comparison engine, no-rounding audit
python test_modules.py     # Modules 2 and 3 engines and their Module 1 integration
python test_glossary.py    # glossary contract, colour coding, numerical integrity
python test_app.py         # headless UI via streamlit.testing.v1.AppTest
```

`test_app.py` uses `AppTest`, which has no `data_editor` accessor and surfaces Plotly as
`UnknownElement` — hence the tree walk in `element_counts`. The sidebar module selector is
`radio[0]`; workspace mode is `radio[1]`.

## Module map

| File | Responsibility |
|---|---|
| `app.py` | Router, sidebar, Module 1 tabs, working-paper state |
| `config.py` | Canonical taxonomy, layouts, DCF defaults, display masks |
| `glossary.py` | 76 term definitions + 10 explainers; attached by label lookup |
| `finance_logic.py` | QoE bridge, NWC, efficiency, DCF, value bridge, diagnostics |
| `case_studies.py` | Case library, three-statement builder, answer keys, comparison engine |
| `components.py` | Widgets, Plotly renderers, tooltips, Styler colour coding |
| `api_client.py` | yfinance ingestion and vendor-label normalisation |
| `edgar_client.py` | SEC EDGAR async ingestion and item extraction |
| `text_analysis.py` | Regex risk detectors, readability metrics |
| `risk_engine.py` | YoY diff, impact scoring, QoE ledger translation |
| `ppa_engine.py` | ASC 805 allocation, deferred tax, opening balance sheet |
| `workspace.py` | Cross-module state bus, QoE ledger hand-off |
| `ui_sec.py` / `ui_ppa.py` | Module 2 and 3 interfaces |
| `export.py` | PDF / Markdown / CSV reports |

## Open items

- The owner has worked the **Helios** case with guidance but not **Anvil** or **Cascade** cold.
  Those are meant to be done unaided — do not reveal their answer keys unless he explicitly asks.
- Module 2 needs a live US-registrant ticker; sandbox cases are fictional and have no EDGAR presence.
