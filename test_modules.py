"""Tests for Modules 2 and 3 and their integration with Module 1.

Run with ``python test_modules.py``. Network-dependent EDGAR checks degrade to
SKIP when SEC is unreachable so the suite stays usable offline.
"""

from __future__ import annotations

import math
import sys

import pandas as pd

import ppa_engine as ppa
import risk_engine as risk
import text_analysis as nlp
from case_studies import build_case_engagement
from export import ReportPayload, ReportSection, build_csv_bundle, build_markdown, build_pdf
from finance_logic import is_missing, net_working_capital

FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    print(f"  {'PASS' if condition else 'FAIL'}  {message}")
    if not condition:
        FAILURES.append(message)


def section(title: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}")


SAMPLE = """
Item 1A. Risk Factors
Our revenue recognition involves significant judgment under ASC 606, particularly for
performance obligations satisfied over time. We have identified a material weakness in our
internal control over financial reporting relating to the review of manual journal entries.
A single customer accounted for 22% of our revenue in the period. We are subject to an
ongoing SEC investigation regarding our historical disclosures. Our allowance for credit
losses may prove insufficient if collection experience deteriorates. We rely on a sole source
supplier for a critical component. There is substantial doubt about our ability to continue
as a going concern absent additional financing.
Item 1B. Unresolved Staff Comments
"""

PRIOR = """
Item 1A. Risk Factors
Our revenue recognition involves significant judgment under ASC 606, particularly for
performance obligations satisfied over time. A single customer accounted for 19% of our
revenue in the period. Our allowance for credit losses may prove insufficient if collection
experience deteriorates.
Item 1B. Unresolved Staff Comments
"""


def main() -> int:
    section("1. NLP risk scanner")
    hits = nlp.scan_text(SAMPLE, "item_1a_risk_factors", "FY2025", "10-K")
    labels = {hit.label for hit in hits}
    check(bool(hits), f"scanner fires on seeded text ({len(hits)} hits)")
    for expected in (
        "Material weakness in internal control",
        "Going concern / substantial doubt",
        "Government investigation or enforcement",
        "Customer concentration",
        "Receivable collectability and credit losses",
        "Supplier or single-source dependency",
    ):
        check(expected in labels, f"detects: {expected}")
    check(
        all(hit.evidence for hit in hits if hit.occurrences),
        "every hit carries the sentence that triggered it",
    )
    check(nlp.scan_text("") == [], "empty text yields no hits rather than raising")

    metrics = nlp.readability(SAMPLE)
    check(metrics.word_count > 0 and metrics.sentence_count > 0, "readability computes")
    check(
        len(repr(metrics.flesch_kincaid_grade).split(".")[-1]) > 4,
        f"readability retains full precision ({metrics.flesch_kincaid_grade!r})",
    )
    empty_metrics = nlp.readability("")
    check(is_missing(empty_metrics.average_sentence_length), "empty text yields NaN, not a crash")

    section("1b. HTML parsing without the optional lxml accelerator")
    import edgar_client as ec

    sample_html = (
        "<html><head><style>p{color:red}</style><script>var x=1;</script></head>"
        "<body><div>Item&nbsp;1A. Risk Factors</div><p>Our revenue recognition "
        "involves significant judgment under ASC 606.</p>"
        "<span>Item 1B. Unresolved Staff Comments</span></body></html>"
    )
    original = ec.LXML_AVAILABLE
    try:
        ec.LXML_AVAILABLE = True
        with_lxml = ec.html_to_text(sample_html)
        ec.LXML_AVAILABLE = False
        without_lxml = ec.html_to_text(sample_html)
    finally:
        ec.LXML_AVAILABLE = original

    check(bool(without_lxml), "the regex fallback produces text when lxml is absent")
    check(
        "var x" not in without_lxml and "color:red" not in without_lxml,
        "the fallback strips script and style content",
    )
    check("Risk Factors" in without_lxml, "the fallback preserves narrative text")
    check(
        "&nbsp;" not in without_lxml,
        "the fallback decodes HTML entities",
    )
    check(
        "ASC 606" in with_lxml and "ASC 606" in without_lxml,
        "both parsers preserve the language the scanner keys on",
    )

    section("2. Year-over-year delta")
    delta = risk.compare_sections(SAMPLE, PRIOR, "item_1a_risk_factors", "FY2025", "FY2024")
    check(0.0 < delta.similarity < 1.0, f"similarity computes ({delta.similarity:.6f})")
    check(bool(delta.added_sentences), f"detects added language ({len(delta.added_sentences)})")
    check(
        delta.current_word_count > delta.prior_word_count,
        "word count growth detected",
    )
    html = risk.render_delta_html(delta)
    check("nd-added" in html and "nd-removed" in html, "diff HTML marks added and removed")
    check("<script" not in html.lower(), "diff HTML escapes filing content (no script injection)")
    identical = risk.compare_sections(SAMPLE, SAMPLE)
    check(identical.similarity == 1.0, "identical sections score 1.0 similarity")
    check(not identical.added_sentences, "identical sections report no additions")

    section("3. Scoring and the QoE hand-off")
    prior_hits = nlp.scan_text(PRIOR, "item_1a_risk_factors", "FY2024", "10-K")
    scored = risk.score_risks(hits, prior_hits, "FY2025")
    check(bool(scored), f"risks scored ({len(scored)})")
    check(
        all(scored[i].score >= scored[i + 1].score for i in range(len(scored) - 1)),
        "scored risks are ranked by impact score",
    )
    new_ones = [item for item in scored if item.is_new]
    check(bool(new_ones), f"new-this-year risks identified ({len(new_ones)})")
    check(
        all(item.emphasis_multiplier == 2.0 for item in new_ones),
        "new risks receive the doubled emphasis multiplier",
    )

    matrix = risk.risks_to_matrix(scored)
    check(len(matrix) == len(scored), "matrix has one row per scored risk")
    check(
        not matrix[risk.COL_ACCEPT].any(),
        "no flag is accepted by default — the analyst must decide",
    )
    check(
        (matrix[risk.COL_HAIRCUT] == 0.0).all(),
        "no haircut is pre-populated",
    )

    periods = ["FY2023", "FY2024", "FY2025"]
    check(
        risk.matrix_to_ledger_rows(matrix, periods, "FY2025").empty,
        "an untouched matrix pushes nothing",
    )
    matrix.loc[0, risk.COL_ACCEPT] = True
    matrix.loc[0, risk.COL_HAIRCUT] = 750_000.0
    matrix.loc[1, risk.COL_ACCEPT] = True  # accepted but zero haircut
    rows = risk.matrix_to_ledger_rows(matrix, periods, "FY2025")
    check(len(rows) == 1, "accepted rows with a zero haircut are excluded")
    check(
        list(rows.columns)
        == ["Adjustment", "Category", "Treatment", *periods, "Rationale / Support"],
        "ledger rows match the Module 1 editor schema exactly",
    )
    check(rows.iloc[0]["FY2025"] == -750_000.0, "haircut is applied as a negative impact")
    check(
        rows.iloc[0]["FY2023"] == 0.0 and rows.iloc[0]["FY2024"] == 0.0,
        "non-target periods are zero",
    )
    from config import ADJUSTMENT_CATEGORIES, ADJUSTMENT_STATUSES

    check(
        rows.iloc[0]["Category"] in ADJUSTMENT_CATEGORIES,
        "mapped category is valid in the Module 1 taxonomy",
    )
    check(
        rows.iloc[0]["Treatment"] in ADJUSTMENT_STATUSES,
        "mapped treatment is valid in the Module 1 taxonomy",
    )

    import components as ui

    restored = ui.frame_to_adjustments(rows, periods)
    check(len(restored) == 1, "pushed rows deserialize as Module 1 Adjustment objects")
    check(
        restored[0].impact("FY2025") == -750_000.0,
        "deserialized impact is bit-identical to the haircut",
    )

    section("4. ASC 805 PPA engine")
    engagement = build_case_engagement("helios")
    tangible = ppa.seed_tangible_frame(engagement)
    intangibles = ppa.seed_intangible_frame()
    intangibles.loc[0, ppa.COL_INT_FAIR] = 14_000_000.0
    intangibles.loc[1, ppa.COL_INT_FAIR] = 9_000_000.0
    tangible.loc[0, ppa.COL_STEP] = 1_200_000.0

    stock = ppa.run_ppa(
        engagement, tangible, intangibles,
        ppa.PPAAssumptions(48_600_000.0, 0.25, ppa.STRUCTURE_STOCK),
    )
    asset = ppa.run_ppa(
        engagement, tangible, intangibles,
        ppa.PPAAssumptions(48_600_000.0, 0.25, ppa.STRUCTURE_ASSET),
    )

    check(
        abs(
            (stock.book_net_assets + stock.tangible_step_up + stock.intangible_fair_value
             - stock.deferred_tax_liability)
            - stock.fair_value_identifiable_net_assets
        ) < 1e-9,
        "identifiable net assets tie to their components",
    )
    check(
        abs((stock.consideration - stock.fair_value_identifiable_net_assets) - stock.goodwill) < 1e-9,
        f"goodwill is the residual ({stock.goodwill:,.2f})",
    )
    expected_dtl = (stock.tangible_step_up + stock.intangible_fair_value) * 0.25
    check(
        abs(stock.deferred_tax_liability - expected_dtl) < 1e-9,
        "DTL equals the basis difference times the marginal rate",
    )
    check(asset.deferred_tax_liability == 0.0, "a taxable asset acquisition records no DTL")
    check(
        abs((stock.goodwill - asset.goodwill) - stock.deferred_tax_liability) < 1e-9,
        "the DTL increases goodwill dollar for dollar",
    )
    check(
        abs(math.fsum(amount for _, amount in stock.steps[:-1]) - stock.goodwill) < 1e-9,
        "the allocation bridge steps sum to goodwill",
    )

    bargain = ppa.run_ppa(
        engagement, tangible, intangibles,
        ppa.PPAAssumptions(5_000_000.0, 0.25, ppa.STRUCTURE_STOCK),
    )
    check(
        bargain.is_bargain_purchase and bargain.goodwill == 0.0,
        f"a low price produces a bargain purchase gain ({bargain.bargain_purchase_gain:,.2f})",
    )
    check(
        any("805-30-25-4" in note for note in bargain.notes),
        "the bargain purchase reassessment requirement is cited",
    )

    zero = ppa.run_ppa(
        engagement, ppa.seed_tangible_frame(engagement), ppa.seed_intangible_frame(),
        ppa.PPAAssumptions(0.0, 0.25, ppa.STRUCTURE_STOCK),
    )
    check(not is_missing(zero.goodwill), "zero consideration does not raise")

    amortization = ppa.annual_amortization(intangibles)
    check(
        abs(amortization - (14_000_000.0 / 10.0 + 9_000_000.0 / 7.0)) < 1e-9,
        f"straight-line amortization is exact ({amortization!r})",
    )
    indefinite = ppa.seed_intangible_frame()
    indefinite.loc[5, ppa.COL_INT_FAIR] = 5_000_000.0  # in-process R&D, zero life
    check(
        ppa.annual_amortization(indefinite) == 0.0,
        "indefinite-lived intangibles are not amortized",
    )

    opening = ppa.opening_balance_sheet(engagement, tangible, intangibles, stock)
    check(not opening.empty, f"opening balance sheet builds ({len(opening)} rows)")
    book_total = opening.loc["Net assets acquired", "Historical Book Value"]
    fair_total = opening.loc["Net assets acquired", "Day 1 Fair Value"]
    check(
        abs(fair_total - (book_total + math.fsum(
            opening["Fair Value Adjustment"].tolist()[:-1]))) < 1e-6,
        "opening balance sheet adjustments reconcile book to fair value",
    )
    schedule = ppa.amortization_schedule(intangibles, horizon_years=12)
    check(not schedule.empty, "amortization schedule builds")
    check(
        abs(schedule.loc["Total annual amortization", "Year 1"] - amortization) < 1e-9,
        "year one of the schedule equals annual amortization",
    )

    nwc = net_working_capital(engagement, engagement.latest_period)
    imported_ev, imported_nwc = ppa.import_from_workspace(
        {"enterprise_value": 62_000_000.0, "equity_value": 55_000_000.0}, nwc
    )
    check(imported_ev == 62_000_000.0, "PPA imports enterprise value from Module 1")
    check(imported_nwc == nwc, "PPA imports net working capital from Module 1")
    fallback_ev, _ = ppa.import_from_workspace({"equity_value": 55_000_000.0}, nwc)
    check(fallback_ev == 55_000_000.0, "falls back to equity value when EV is absent")
    none_ev, _ = ppa.import_from_workspace(None, nwc)
    check(is_missing(none_ev), "handles an absent Module 1 valuation")

    section("5. Report export")
    payload = ReportPayload(
        title="Quality of Earnings Report",
        entity="Helios Practice Systems, Inc. — em dash — “curly quotes” … →",
        subtitle="Unicode transliteration probe",
        sections=[
            ReportSection("Narrative", "Adjusted EBITDA fell 21.7% — a material finding."),
            ReportSection("Allocation", "", stock.bridge),
            ReportSection("Opening Balance Sheet", "", opening),
            ReportSection("Empty table", "", pd.DataFrame()),
        ],
    )
    pdf_bytes = build_pdf(payload)
    check(pdf_bytes.startswith(b"%PDF"), f"PDF generates ({len(pdf_bytes):,} bytes)")
    check(len(pdf_bytes) > 3000, "PDF has substantive content")
    markdown = build_markdown(payload)
    check("# Quality of Earnings Report" in markdown, "Markdown export builds")
    check(
        "| ---" in markdown and "Consideration transferred" in markdown,
        "Markdown tables render without the optional tabulate dependency",
    )
    import sys as _sys

    check(
        "tabulate" not in _sys.modules,
        "the export path never imports tabulate",
    )
    piped = pd.DataFrame({"Item": ["a|b"], "Value": [1.5]})
    from export import markdown_table

    check(
        "a\\|b" in markdown_table(piped, include_index=False),
        "pipes inside cell values are escaped",
    )
    csv_bundle = build_csv_bundle(payload)
    check("Allocation" in csv_bundle, "CSV bundle includes tables")

    print()
    if FAILURES:
        print(f"{len(FAILURES)} CHECK(S) FAILED")
        for failure in FAILURES:
            print(f"  - {failure}")
        return 1
    print("All module checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
