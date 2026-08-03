"""Integrity harness for the sandbox case library.

Run directly (``python validate_cases.py``) to assert that every case's three
statements articulate and to print the derived diagnostics used to sanity-check
the hardcoded inputs.
"""

from __future__ import annotations

import sys

from case_studies import (
    CASE_LIBRARY,
    answer_key_adjustments,
    build_case_engagement,
    validate_all_cases,
)
from finance_logic import (
    adjusted_ebitda,
    build_efficiency_metrics,
    net_working_capital,
    reported_ebitda,
    safe_divide,
    total_funded_debt,
)


def main() -> int:
    failures = validate_all_cases()

    for key, case in CASE_LIBRARY.items():
        engagement = build_case_engagement(key)
        adjustments = answer_key_adjustments(key)
        efficiency = build_efficiency_metrics(engagement)

        print("=" * 78)
        print(f"{case['name']} — {case['target']}")
        print("=" * 78)

        for period in engagement.periods:
            revenue = engagement.fact("revenue", period)
            ebitda = reported_ebitda(engagement, period)
            adjusted = adjusted_ebitda(engagement, adjustments, period)
            cash = engagement.fact("cash_and_equivalents", period)
            debt = total_funded_debt(engagement, period)
            interest = engagement.fact("interest_expense", period)
            assets = engagement.fact("total_assets", period)
            check = assets - (
                engagement.fact("total_liabilities", period)
                + engagement.fact("total_equity", period)
            )
            print(
                f"  {period}: revenue={revenue:>16,.2f}  EBITDA={ebitda:>14,.2f}  "
                f"adjEBITDA={adjusted:>14,.2f}"
            )
            print(
                f"          cash={cash:>16,.2f}  funded debt={debt:>12,.2f}  "
                f"net debt/adjEBITDA={safe_divide(debt - cash, adjusted):>7.2f}x"
            )
            print(
                f"          NWC={net_working_capital(engagement, period):>16,.2f}  "
                f"interest rate on avg debt={interest / max(debt, 1.0) * 100.0:>6.2f}%  "
                f"A-L-E={check:.10f}"
            )
            print(
                f"          DSO={efficiency.loc['Days Sales Outstanding (DSO)', period]:>7.2f}  "
                f"DIO={efficiency.loc['Days Inventory Outstanding (DIO)', period]:>7.2f}  "
                f"DPO={efficiency.loc['Days Payable Outstanding (DPO)', period]:>7.2f}"
            )
        latest = engagement.latest_period
        management = case.get("management_adjusted_ebitda", {}).get(latest)
        actual = adjusted_ebitda(engagement, adjustments, latest)
        if management is not None:
            print(
                f"  Management represented {management:,.2f} vs. engagement team "
                f"{actual:,.2f} (gap {management - actual:,.2f}, "
                f"{safe_divide(management - actual, management) * 100.0:.1f}%)"
            )
        print()

    if failures:
        print("INTEGRITY FAILURES")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("All cases articulate: balance sheets balance and cash flows foot.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
