"""Tests for the interactive glossary and learning-aid layer.

The decisive test here is that adding tooltips, explainers and colour coding did
not perturb a single computed value. Run with ``python test_glossary.py``.
"""

from __future__ import annotations

import math
import sys

import pandas as pd

import components as ui
import glossary
from case_studies import CASE_LIBRARY, answer_key_adjustments, build_case_engagement
from config import DEBT_LIKE, NON_OPERATING_ASSET, OPERATING_NEUTRAL
from finance_logic import (
    adjusted_ebitda,
    build_efficiency_metrics,
    build_nwc_schedule,
    build_qoe_bridge,
    margin_summary,
    net_working_capital,
    reported_ebitda,
)

FAILURES: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {msg}")
    if not cond:
        FAILURES.append(msg)


def section(t: str) -> None:
    print(f"\n{t}\n{'-'*len(t)}")


def main() -> int:
    section("1. Glossary contract — every term has all three parts")
    terms = glossary.all_terms()
    check(len(terms) >= 60, f"glossary is comprehensive ({len(terms)} terms)")
    no_def = [t.name for t in terms if not t.definition.strip()]
    check(not no_def, "every term has a plain-English definition")
    short = [t.name for t in terms if len(t.definition) < 60]
    check(not short, f"no definition is a stub{'' if not short else ': ' + ', '.join(short[:3])}")
    no_example = [t.name for t in terms if not t.example.strip()]
    check(not no_example, f"every term has a worked example{'' if not no_example else ': ' + ', '.join(no_example[:5])}")

    # Formula is required only where a calculation exists.
    formula_required = [
        "EBITDA", "Adjusted EBITDA", "EBITDA Margin", "Net Working Capital",
        "Days Sales Outstanding", "Days Inventory Outstanding", "Days Payable Outstanding",
        "Cash Conversion Cycle", "Enterprise Value", "Equity Value", "Goodwill",
        "Deferred Tax Liability", "NOPAT", "Unlevered Free Cash Flow", "Terminal Value",
        "Fair Value Step-Up", "Accrual Gap", "Impact Score", "Net Debt", "Funded Debt",
    ]
    missing_formula = [
        name for name in formula_required
        if not (glossary.lookup(name) and glossary.lookup(name).formula.strip())
    ]
    check(not missing_formula, f"calculable terms state a formula{'' if not missing_formula else ': ' + ', '.join(missing_formula)}")

    section("2. Tooltip structure")
    t = glossary.lookup("Net Working Capital")
    tip = t.tooltip()
    check("**Net Working Capital**" in tip, "tooltip leads with the term name")
    check("**Formula:**" in tip, "tooltip states the formula")
    check("**Example:**" in tip, "tooltip gives a numerical example")
    check("$100" in tip and "$80" in tip and "$20" in tip, "example uses concrete numbers")

    section("3. Label resolution across the UI")
    for label, expected in [
        ("Adjusted EBITDA", "Adjusted EBITDA"),
        ("EBITDA (as reported)", "EBITDA"),
        ("Adjusted EBITDA Margin %", "EBITDA Margin"),
        ("Days Sales Outstanding (DSO)", "Days Sales Outstanding"),
        ("Total Debt-Like Items", "Debt-Like Items"),
        ("Net Funded Debt", "Net Debt"),
        ("Proposed NWC Peg", "NWC Peg"),
        ("WACC (%)", "WACC"),
        ("Implied Equity Value", "Equity Value"),
        ("Goodwill", "Goodwill"),
        ("Deferred Tax Liability", "Deferred Tax Liability"),
        ("Impact Score", "Impact Score"),
    ]:
        got = glossary.lookup(label)
        check(got is not None and got.name == expected, f"'{label}' resolves to {expected}")
    check(glossary.lookup("FY2024") is None, "period headers resolve to nothing (no stray tooltip)")
    check(glossary.lookup("") is None, "empty label is handled")
    check(glossary.help_for("FY2024") is None, "help_for returns None when unresolved")

    section("4. Explainers")
    for key in ("qoe_bridge", "nwc", "efficiency", "value_bridge", "dcf", "ppa",
                "opening_bs", "risk_scan", "narrative_delta", "comparison"):
        body = glossary.explainer(key)
        check(len(body) > 400, f"explainer '{key}' is substantive ({len(body)} chars)")
    check(glossary.explainer("nonexistent") == "", "unknown explainer returns empty, not an error")

    section("5. Semantic colour coding leaves data untouched")
    frame = pd.DataFrame({
        "Item": ["Deferred revenue", "Aircraft", "Prepaid"],
        "Amount": [2_650_000.123456789, 2_400_000.987654321, 1_480_000.5],
        "Classification": [DEBT_LIKE, NON_OPERATING_ASSET, OPERATING_NEUTRAL],
    })
    before = list(frame["Amount"])
    styler = ui.style_classifications(frame, "Classification")
    html = styler.to_html()
    check("179, 53, 44" in html, "debt-like rows tinted red")
    check("27, 127, 94" in html, "non-operating rows tinted green")
    check(list(styler.data["Amount"]) == before, "styling does not mutate values")
    check(list(frame["Amount"]) == before, "source frame is untouched")
    # Compare values, not reprs: pandas 3.0 renders numpy scalars as
    # np.float64(...) where pandas 2.x rendered a bare float.
    styled_value = float(styler.data["Amount"].iloc[0])
    check(
        styled_value == 2_650_000.123456789
        and styled_value.hex() == (2_650_000.123456789).hex(),
        f"full precision survives styling ({styled_value!r})",
    )
    sev = ui.style_severity(
        pd.DataFrame({"Risk": ["a", "b"], "Severity": ["Critical", "Low"]}), "Severity"
    )
    check("font-weight: 600" in sev.to_html(), "critical severity is emphasised")
    check(ui.style_classifications(pd.DataFrame(), "Classification") is not None,
          "empty frame does not raise")

    section("6. NUMERICAL INTEGRITY — the UI layer changed nothing")
    for case_key in CASE_LIBRARY:
        eng = build_case_engagement(case_key)
        adj = answer_key_adjustments(case_key)
        latest = eng.latest_period

        bridge = build_qoe_bridge(eng, adj)
        direct = adjusted_ebitda(eng, adj, latest)
        check(bridge.loc["Adjusted EBITDA", latest] == direct,
              f"{case_key}: bridge Adjusted EBITDA bit-identical ({direct!r})")

        summary = margin_summary(eng, adj)
        margin = summary.loc["Adjusted EBITDA Margin %", latest]
        expected = direct / eng.fact("revenue", latest) * 100.0
        check(margin == expected, f"{case_key}: margin bit-identical ({margin!r})")
        check(len(repr(margin).split(".")[-1]) > 4,
              f"{case_key}: margin retains full mantissa")

        nwc = build_nwc_schedule(eng)
        check(nwc.loc["Net Working Capital", latest] == net_working_capital(eng, latest),
              f"{case_key}: NWC schedule bit-identical")

        eff = build_efficiency_metrics(eng)
        dso = eff.loc["Days Sales Outstanding (DSO)", latest]
        check(len(repr(dso).split(".")[-1]) > 4, f"{case_key}: DSO unrounded ({dso!r})")

        check(reported_ebitda(eng, latest) + math.fsum(
            a.impact(latest) for a in adj if a.is_accepted) == direct,
            f"{case_key}: adjustments sum exactly")

    section("7. The glossary never touches numbers")
    import pathlib, io, tokenize, re
    banned = re.compile(r"^(round|quantize)$")
    offenders = []
    for path in (pathlib.Path("glossary.py"),):
        with io.StringIO(path.read_text()) as fh:
            for tok in tokenize.generate_tokens(fh.readline):
                if tok.type == tokenize.NAME and banned.match(tok.string):
                    offenders.append(f"{path.name}:{tok.start[0]}")
    check(not offenders, "glossary.py contains no rounding call")

    print()
    if FAILURES:
        print(f"{len(FAILURES)} CHECK(S) FAILED")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("All glossary checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
