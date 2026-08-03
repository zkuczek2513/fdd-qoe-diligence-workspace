"""End-to-end exercise of the analytical pipeline, without Streamlit.

Run with ``python test_pipeline.py``. Covers the QoE bridge, the working capital
schedule, the DCF, the value bridge, the comparison engine and the heuristic
review engine, and asserts that no computed value has been silently rounded.
"""

from __future__ import annotations

import math
import sys

import pandas as pd

import ai_reviewer
import components as ui
from case_studies import (
    CASE_LIBRARY,
    answer_key_adjustments,
    answer_key_classifications,
    answer_key_risks,
    build_case_engagement,
    compare_to_answer_key,
    validate_all_cases,
)
from config import TERMINAL_METHOD_GORDON, TERMINAL_METHOD_MULTIPLE
from finance_logic import (
    Adjustment,
    DCFAssumptions,
    adjusted_ebitda,
    build_efficiency_metrics,
    build_nwc_schedule,
    build_qoe_bridge,
    build_value_bridge,
    classification_totals,
    compute_diagnostics,
    is_missing,
    margin_summary,
    net_working_capital,
    normalized_nwc_peg,
    reported_ebitda,
    run_dcf,
    total_funded_debt,
)

FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    if condition:
        print(f"  PASS  {message}")
    else:
        print(f"  FAIL  {message}")
        FAILURES.append(message)


def section(title: str) -> None:
    print()
    print(title)
    print("-" * len(title))


def main() -> int:
    section("1. Case library integrity")
    failures = validate_all_cases()
    check(not failures, "every case balances and foots")
    for failure in failures:
        print(f"        {failure}")

    for case_key, case in CASE_LIBRARY.items():
        section(f"2. Pipeline — {case['name']}")
        engagement = build_case_engagement(case_key)
        key_adjustments = answer_key_adjustments(case_key)
        latest = engagement.latest_period

        # --- QoE bridge ---------------------------------------------------
        bridge = build_qoe_bridge(engagement, key_adjustments)
        bridge_total = bridge.loc["Adjusted EBITDA", latest]
        direct_total = adjusted_ebitda(engagement, key_adjustments, latest)
        check(
            abs(bridge_total - direct_total) < 1e-9,
            f"bridge Adjusted EBITDA ties to the direct computation ({bridge_total:,.2f})",
        )

        walk = math.fsum(
            [
                engagement.fact("net_income", latest),
                engagement.fact("income_tax_expense", latest),
                engagement.fact("interest_expense", latest),
                -engagement.fact("other_income_expense", latest),
                engagement.fact("depreciation", latest),
                engagement.fact("amortization_software", latest),
                engagement.fact("amortization_intangibles", latest),
            ]
        )
        check(
            abs(walk - reported_ebitda(engagement, latest)) < 1e-9,
            "net income walks to reported EBITDA through tax, interest and D&A",
        )

        # --- Working capital ----------------------------------------------
        schedule = build_nwc_schedule(engagement)
        check(
            abs(schedule.loc["Net Working Capital", latest]
                - net_working_capital(engagement, latest)) < 1e-9,
            "NWC schedule ties to the direct computation",
        )
        metrics = build_efficiency_metrics(engagement)
        check(
            not metrics.empty and not is_missing(metrics.loc["Days Sales Outstanding (DSO)", latest]),
            f"DSO computes ({metrics.loc['Days Sales Outstanding (DSO)', latest]:,.2f} days)",
        )
        peg = normalized_nwc_peg(engagement, 3)
        check(not is_missing(peg), f"working capital peg computes ({peg:,.2f})")

        # --- DCF ------------------------------------------------------------
        base_revenue = engagement.fact("revenue", latest)
        base_adjusted = adjusted_ebitda(engagement, key_adjustments, latest)
        assumptions = DCFAssumptions(
            horizon_years=5,
            revenue_cagr=0.06,
            target_ebitda_margin=0.18,
            capex_pct_revenue=0.035,
            da_pct_revenue=0.030,
            nwc_pct_revenue=0.10,
            wacc=0.115,
            tax_rate=0.25,
            terminal_method=TERMINAL_METHOD_MULTIPLE,
            terminal_multiple=9.0,
            terminal_growth=0.025,
            mid_year_convention=True,
            ramp_margin=True,
        )
        result = run_dcf(base_revenue, base_adjusted, assumptions)
        check(
            not is_missing(result.enterprise_value) and result.enterprise_value > 0.0,
            f"DCF produces an enterprise value ({result.enterprise_value:,.2f})",
        )
        pv_sum = math.fsum(result.schedule.loc["Present Value of UFCF"].tolist())
        check(
            abs(pv_sum - result.pv_of_forecast) < 1e-6,
            "sum of discounted cash flows ties to the reported PV of the forecast",
        )
        check(
            abs((result.pv_of_forecast + result.pv_of_terminal_value) - result.enterprise_value)
            < 1e-6,
            "PV of forecast plus PV of terminal value equals enterprise value",
        )

        gordon = run_dcf(
            base_revenue,
            base_adjusted,
            DCFAssumptions(**{**assumptions.__dict__, "terminal_method": TERMINAL_METHOD_GORDON}),
        )
        check(
            not is_missing(gordon.enterprise_value),
            f"Gordon Growth terminal value converges ({gordon.enterprise_value:,.2f})",
        )
        degenerate = run_dcf(
            base_revenue,
            base_adjusted,
            DCFAssumptions(
                **{
                    **assumptions.__dict__,
                    "terminal_method": TERMINAL_METHOD_GORDON,
                    "terminal_growth": 0.20,
                }
            ),
        )
        check(
            is_missing(degenerate.enterprise_value) and bool(degenerate.note),
            "terminal growth above WACC is refused rather than producing a negative value",
        )

        # --- Value bridge ---------------------------------------------------
        key_items = answer_key_classifications(case_key)
        debt_like_total, non_operating_total = classification_totals(key_items)
        bridge_result = build_value_bridge(
            enterprise_value=result.enterprise_value,
            cash=engagement.fact("cash_and_equivalents", latest),
            funded_debt=total_funded_debt(engagement, latest),
            debt_like_total=debt_like_total,
            non_operating_total=non_operating_total,
            nwc_actual=net_working_capital(engagement, latest),
            nwc_peg=peg,
            include_nwc_true_up=True,
        )
        recomputed = math.fsum(amount for _, amount in bridge_result.steps[:-1])
        check(
            abs(recomputed - bridge_result.equity_value) < 1e-6,
            f"value bridge steps sum to implied equity value ({bridge_result.equity_value:,.2f})",
        )

        # --- Comparison engine ----------------------------------------------
        perfect = compare_to_answer_key(engagement, key_adjustments, case_key, latest)
        check(
            abs(perfect.variance) < 1e-9,
            "submitting the answer key produces zero variance",
        )
        check(
            abs(perfect.coverage_percent - 100.0) < 1e-9,
            f"submitting the answer key scores full coverage ({perfect.coverage_percent:,.2f}%)",
        )
        check(
            not perfect.missed and not perfect.unsupported,
            "submitting the answer key leaves nothing missed or unsupported",
        )

        empty = compare_to_answer_key(engagement, [], case_key, latest)
        check(
            len(empty.missed) == len(key_adjustments),
            "an empty schedule reports every answer-key adjustment as missed",
        )
        check(
            abs(empty.user_adjusted_ebitda - reported_ebitda(engagement, latest)) < 1e-9,
            "an empty schedule leaves Adjusted EBITDA equal to reported EBITDA",
        )

        partial = compare_to_answer_key(
            engagement, key_adjustments[:2], case_key, latest
        )
        check(
            len(partial.matched) == 2 and len(partial.missed) == len(key_adjustments) - 2,
            "a partial schedule matches only what was submitted",
        )

        paraphrased = [
            Adjustment(
                label="Owner compensation normalised to market rates",
                category="Owner / Management Compensation",
                period_impacts={latest: 1_000_000.0},
                rationale="Benchmarked to a market study.",
            )
        ]
        loose = compare_to_answer_key(engagement, paraphrased, case_key, latest)
        matched_labels = [key.label for _, key, _ in loose.matched]
        check(
            any("compensation" in label.lower() for label in matched_labels),
            "paraphrased labels still pair with the answer key",
        )

        # --- Round-trip through the editor serializers ----------------------
        frame = ui.adjustments_to_frame(key_adjustments, engagement.periods)
        restored = ui.frame_to_adjustments(frame, engagement.periods)
        check(
            len(restored) == len(key_adjustments),
            "adjustments survive a round trip through the editor frame",
        )
        check(
            all(
                abs(a.impact(latest) - b.impact(latest)) < 1e-12
                for a, b in zip(restored, key_adjustments)
            ),
            "round-tripped adjustment impacts are bit-identical",
        )

        blank = pd.DataFrame(
            [{"Adjustment": "", "Category": None, "Treatment": None, latest: None,
              "Rationale / Support": None}]
        )
        for period in engagement.periods:
            if period not in blank.columns:
                blank[period] = None
        check(
            ui.frame_to_adjustments(blank, engagement.periods) == [],
            "blank editor rows are ignored rather than booked as zero adjustments",
        )

        # --- Heuristic reviewer ---------------------------------------------
        diagnostics = compute_diagnostics(engagement, key_adjustments)
        review = ai_reviewer.heuristic_review(
            engagement,
            diagnostics,
            key_adjustments,
            key_items,
            answer_key_risks(case_key),
            {
                "enterprise_value": result.enterprise_value,
                "equity_value": bridge_result.equity_value,
            },
            perfect,
        )
        check(
            len(review.body) > 1500 and "### Diligence Summary Memo" in review.body,
            f"heuristic review produces a full memo ({len(review.body):,} characters)",
        )

        empty_review = ai_reviewer.heuristic_review(
            engagement, compute_diagnostics(engagement, []), [], [], [], {}
        )
        check(
            "No accepted adjustments have been booked" in empty_review.body
            and "No debt-like items have been identified" in empty_review.body,
            "heuristic review calls out empty working papers",
        )

        add_backs_only = [
            Adjustment(
                label="Owner compensation add-back",
                category="Owner / Management Compensation",
                period_impacts={period: 500_000.0 for period in engagement.periods},
                rationale="Market study.",
            )
        ]
        biased = ai_reviewer.heuristic_review(
            engagement,
            compute_diagnostics(engagement, add_backs_only),
            add_backs_only,
            [],
            [],
            {},
        )
        check(
            "Every accepted adjustment is an add-back" in biased.body,
            "heuristic review flags a one-directional adjustment schedule",
        )

        # --- Prompt assembly ------------------------------------------------
        sandbox_prompt = ai_reviewer.build_sandbox_prompt(
            engagement,
            diagnostics,
            key_adjustments,
            key_items,
            answer_key_risks(case_key),
            perfect,
            case,
            key_items,
            answer_key_risks(case_key),
        )
        check(
            "<variance_analysis>" in sandbox_prompt and "<working_papers>" in sandbox_prompt,
            f"sandbox prompt assembles ({len(sandbox_prompt):,} characters)",
        )
        live_prompt = ai_reviewer.build_live_prompt(
            engagement, diagnostics, key_adjustments, key_items, [], {"enterprise_value": 1.0}
        )
        check("<engagement_data>" in live_prompt, "live prompt assembles")

    # --- No-rounding audit --------------------------------------------------
    section("3. Numerical integrity audit")
    import io
    import pathlib
    import re
    import tokenize

    # Tokenize rather than grep so that prose in docstrings and comments — which
    # legitimately names round() when describing the policy — is not flagged.
    banned = re.compile(r"^(round|quantize)$")
    offenders: list[str] = []
    for path in sorted(pathlib.Path(".").glob("*.py")):
        source = path.read_text()
        with io.StringIO(source) as handle:
            for token in tokenize.generate_tokens(handle.readline):
                if token.type == tokenize.NAME and banned.match(token.string):
                    offenders.append(
                        f"{path.name}:{token.start[0]}: {token.line.strip()}"
                    )
    check(not offenders, "no rounding call appears in executable code anywhere")
    for offender in offenders:
        print(f"        {offender}")

    engagement = build_case_engagement("helios")
    adjustments = answer_key_adjustments("helios")
    irrational = [
        Adjustment(
            label="Irrational precision probe",
            category="Other Normalization",
            period_impacts={period: math.pi * 1_000.0 for period in engagement.periods},
        )
    ]
    probed = adjusted_ebitda(engagement, irrational, engagement.latest_period)
    expected = reported_ebitda(engagement, engagement.latest_period) + math.pi * 1_000.0
    check(
        probed == expected,
        f"an irrational adjustment survives the bridge bit-for-bit ({probed!r})",
    )

    summary = margin_summary(engagement, adjustments)
    margin = summary.loc["Adjusted EBITDA Margin %", engagement.latest_period]
    check(
        len(repr(margin).split(".")[-1]) > 6,
        f"computed margins retain full precision ({margin!r})",
    )

    print()
    if FAILURES:
        print(f"{len(FAILURES)} CHECK(S) FAILED")
        for failure in FAILURES:
            print(f"  - {failure}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
