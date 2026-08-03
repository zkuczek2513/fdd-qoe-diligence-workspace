"""Financial engine: QoE bridge, net working capital, efficiency metrics, DCF.

Every routine in this module is pure — it takes an :class:`Engagement` plus user
inputs and returns dataclasses or DataFrames. No value is rounded at any stage;
see the numerical integrity policy in :mod:`config`.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

import pandas as pd

from config import (
    ADJUSTMENT_STATUSES,
    DAYS_IN_YEAR,
    DEBT_LIKE,
    FUNDED_DEBT_KEYS,
    NON_OPERATING_ASSET,
    NWC_ASSET_KEYS,
    NWC_LIABILITY_KEYS,
    TERMINAL_METHOD_GORDON,
    label_for,
)

NAN = float("nan")


# --------------------------------------------------------------------------- #
# Primitive helpers
# --------------------------------------------------------------------------- #


def is_missing(value: object) -> bool:
    """True when a value cannot participate in arithmetic."""
    if value is None:
        return True
    try:
        return math.isnan(float(value))
    except (TypeError, ValueError):
        return True


def as_float(value: object, default: float = NAN) -> float:
    """Coerce to float, mapping unusable input to ``default`` without rounding."""
    if value is None:
        return default
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(result):
        return default
    return result


def coalesce(value: object, default: float = 0.0) -> float:
    """Coerce to float, substituting ``default`` for a missing figure.

    Used where the absence of a balance sheet line legitimately means zero
    (for example a SaaS target with no inventory).
    """
    return as_float(value, default)


def safe_divide(numerator: object, denominator: object) -> float:
    """Division that yields NaN rather than raising on a zero or missing input."""
    num = as_float(numerator)
    den = as_float(denominator)
    if is_missing(num) or is_missing(den) or den == 0.0:
        return NAN
    return num / den


def sum_keys(facts: Mapping[str, Mapping[str, float]], keys: Iterable[str], period: str) -> float:
    """Sum the named canonical keys for one period, treating gaps as zero."""
    return math.fsum(coalesce(facts.get(key, {}).get(period), 0.0) for key in keys)


def get_fact(facts: Mapping[str, Mapping[str, float]], key: str, period: str) -> float:
    """Fetch one canonical fact, or NaN when unavailable."""
    return as_float(facts.get(key, {}).get(period))


# --------------------------------------------------------------------------- #
# Core domain objects
# --------------------------------------------------------------------------- #


@dataclass
class Engagement:
    """A normalized diligence target, sourced from live market data or a case."""

    source: str
    entity_name: str
    periods: list[str]
    facts: dict[str, dict[str, float]]
    currency: str = "USD"
    units_note: str = "Figures presented in absolute units of currency."
    ticker: str | None = None
    context: str = ""
    case_key: str | None = None
    warnings: list[str] = field(default_factory=list)
    comparative_period: str | None = None
    """An opening balance sheet held outside the presented window.

    Sandbox cases carry the year preceding the diligence period so that trailing
    average-balance metrics are computed consistently in the first presented
    year rather than falling back to an ending balance.
    """

    @property
    def latest_period(self) -> str:
        return self.periods[-1]

    def fact(self, key: str, period: str | None = None) -> float:
        return get_fact(self.facts, key, period or self.latest_period)

    def series(self, key: str) -> list[float]:
        return [get_fact(self.facts, key, period) for period in self.periods]

    def preceding_period(self, period: str) -> str | None:
        """The period supplying opening balances, including the comparative."""
        index = self.periods.index(period)
        if index > 0:
            return self.periods[index - 1]
        return self.comparative_period


@dataclass
class Adjustment:
    """One QoE normalization applied to reported EBITDA."""

    label: str
    category: str
    period_impacts: dict[str, float]
    rationale: str = ""
    status: str = ADJUSTMENT_STATUSES[0]
    authority: str = ""
    adjustment_id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])

    @property
    def is_accepted(self) -> bool:
        return self.status == ADJUSTMENT_STATUSES[0]

    def impact(self, period: str) -> float:
        return coalesce(self.period_impacts.get(period), 0.0)


@dataclass
class ClassifiedItem:
    """A balance sheet line tagged as debt-like or as a non-operating asset."""

    label: str
    amount: float
    classification: str
    rationale: str = ""
    source_key: str | None = None


@dataclass
class RiskFlag:
    """An analyst-raised diligence risk."""

    title: str
    severity: str
    description: str = ""


# --------------------------------------------------------------------------- #
# EBITDA and the QoE bridge
# --------------------------------------------------------------------------- #


def total_depreciation_amortization(engagement: Engagement, period: str) -> float:
    """Aggregate D&A across the three amortization buckets."""
    return sum_keys(
        engagement.facts,
        ("depreciation", "amortization_software", "amortization_intangibles"),
        period,
    )


def reported_ebitda(engagement: Engagement, period: str) -> float:
    """GAAP EBITDA = operating income plus depreciation and amortization."""
    operating_income = engagement.fact("operating_income", period)
    if is_missing(operating_income):
        return NAN
    return operating_income + total_depreciation_amortization(engagement, period)


def accepted_adjustment_total(adjustments: Sequence[Adjustment], period: str) -> float:
    """Net EBITDA impact of accepted adjustments for one period."""
    return math.fsum(adj.impact(period) for adj in adjustments if adj.is_accepted)


def adjusted_ebitda(
    engagement: Engagement, adjustments: Sequence[Adjustment], period: str
) -> float:
    """Adjusted EBITDA for one period."""
    base = reported_ebitda(engagement, period)
    if is_missing(base):
        return NAN
    return base + accepted_adjustment_total(adjustments, period)


def build_qoe_bridge(
    engagement: Engagement, adjustments: Sequence[Adjustment]
) -> pd.DataFrame:
    """Working-paper bridge from GAAP net income down to Adjusted EBITDA.

    Rows walk Net Income -> +Tax -> +Interest -> +D&A = EBITDA, then each accepted
    adjustment, then Adjusted EBITDA. Columns are diligence periods.
    """
    periods = engagement.periods
    rows: list[tuple[str, list[float]]] = []

    rows.append(("Net Income (GAAP)", [engagement.fact("net_income", p) for p in periods]))
    rows.append(
        ("Income Tax Expense", [engagement.fact("income_tax_expense", p) for p in periods])
    )
    rows.append(("Interest Expense", [engagement.fact("interest_expense", p) for p in periods]))
    rows.append(
        (
            "Other (Income) / Expense, net",
            [-as_float(engagement.fact("other_income_expense", p)) for p in periods],
        )
    )
    rows.append(("Depreciation", [engagement.fact("depreciation", p) for p in periods]))
    rows.append(
        (
            "Amortization — Capitalized Software",
            [engagement.fact("amortization_software", p) for p in periods],
        )
    )
    rows.append(
        (
            "Amortization — Acquired Intangibles",
            [engagement.fact("amortization_intangibles", p) for p in periods],
        )
    )
    rows.append(("EBITDA (as reported)", [reported_ebitda(engagement, p) for p in periods]))

    for adjustment in adjustments:
        if not adjustment.is_accepted:
            continue
        rows.append(
            (f"{adjustment.label}", [adjustment.impact(p) for p in periods])
        )

    rows.append(
        (
            "Total QoE Adjustments",
            [accepted_adjustment_total(adjustments, p) for p in periods],
        )
    )
    rows.append(
        (
            "Adjusted EBITDA",
            [adjusted_ebitda(engagement, adjustments, p) for p in periods],
        )
    )

    frame = pd.DataFrame(
        [values for _, values in rows], index=[label for label, _ in rows], columns=periods
    )
    frame.index.name = "Quality of Earnings Bridge"
    return frame


def margin_summary(
    engagement: Engagement, adjustments: Sequence[Adjustment]
) -> pd.DataFrame:
    """Revenue, reported and adjusted EBITDA with the corresponding margins."""
    periods = engagement.periods
    revenue = [engagement.fact("revenue", p) for p in periods]
    reported = [reported_ebitda(engagement, p) for p in periods]
    adjusted = [adjusted_ebitda(engagement, adjustments, p) for p in periods]

    data = {
        "Revenue": revenue,
        "EBITDA (as reported)": reported,
        "EBITDA Margin %": [safe_divide(e, r) * 100.0 for e, r in zip(reported, revenue)],
        "Adjusted EBITDA": adjusted,
        "Adjusted EBITDA Margin %": [
            safe_divide(a, r) * 100.0 for a, r in zip(adjusted, revenue)
        ],
        "Quality of Earnings Delta": [
            NAN if is_missing(a) or is_missing(e) else a - e
            for a, e in zip(adjusted, reported)
        ],
    }
    frame = pd.DataFrame(data, index=periods).transpose()
    frame.index.name = "Earnings Quality Summary"
    return frame


# --------------------------------------------------------------------------- #
# Net working capital
# --------------------------------------------------------------------------- #


def net_working_capital(engagement: Engagement, period: str) -> float:
    """(Current assets excluding cash) less (current liabilities excluding debt)."""
    assets = sum_keys(engagement.facts, NWC_ASSET_KEYS, period)
    liabilities = sum_keys(engagement.facts, NWC_LIABILITY_KEYS, period)
    return assets - liabilities


def build_nwc_schedule(engagement: Engagement) -> pd.DataFrame:
    """Operational net working capital schedule with component detail."""
    periods = engagement.periods
    rows: list[tuple[str, list[float]]] = []

    for key in NWC_ASSET_KEYS:
        rows.append((label_for(key), [coalesce(engagement.fact(key, p)) for p in periods]))
    rows.append(
        (
            "Operating Current Assets (ex-Cash)",
            [sum_keys(engagement.facts, NWC_ASSET_KEYS, p) for p in periods],
        )
    )

    for key in NWC_LIABILITY_KEYS:
        rows.append((label_for(key), [coalesce(engagement.fact(key, p)) for p in periods]))
    rows.append(
        (
            "Operating Current Liabilities (ex-Debt)",
            [sum_keys(engagement.facts, NWC_LIABILITY_KEYS, p) for p in periods],
        )
    )

    nwc_values = [net_working_capital(engagement, p) for p in periods]
    rows.append(("Net Working Capital", nwc_values))

    revenue = [engagement.fact("revenue", p) for p in periods]
    rows.append(
        (
            "NWC as % of Revenue",
            [safe_divide(n, r) * 100.0 for n, r in zip(nwc_values, revenue)],
        )
    )

    deltas: list[float] = [NAN]
    for index in range(1, len(nwc_values)):
        deltas.append(nwc_values[index] - nwc_values[index - 1])
    rows.append(("Period-over-Period Change in NWC", deltas))

    frame = pd.DataFrame(
        [values for _, values in rows], index=[label for label, _ in rows], columns=periods
    )
    frame.index.name = "Net Working Capital Schedule"
    return frame


def build_efficiency_metrics(
    engagement: Engagement, use_average_balances: bool = True
) -> pd.DataFrame:
    """Trailing DSO, DIO, DPO and the resulting cash conversion cycle.

    With ``use_average_balances`` the numerator is the two-period average balance,
    the convention used in TAS working papers. The first period has no prior
    balance and therefore falls back to the ending balance.
    """
    periods = engagement.periods
    dso: list[float] = []
    dio: list[float] = []
    dpo: list[float] = []

    for period in periods:
        prior = engagement.preceding_period(period)
        revenue = engagement.fact("revenue", period)
        cogs = engagement.fact("cost_of_revenue", period)

        def balance(key: str) -> float:
            """Trailing average balance, falling back to the ending balance.

            The opening balance must actually be present to be averaged. Treating
            a missing prior balance as zero would halve the average and make the
            first period look dramatically more efficient than it is.
            """
            ending = coalesce(engagement.fact(key, period))
            if not use_average_balances or prior is None:
                return ending
            beginning = engagement.fact(key, prior)
            if is_missing(beginning):
                return ending
            return (ending + beginning) / 2.0

        dso.append(safe_divide(balance("accounts_receivable"), revenue) * DAYS_IN_YEAR)
        dio.append(safe_divide(balance("inventory"), cogs) * DAYS_IN_YEAR)
        dpo.append(safe_divide(balance("accounts_payable"), cogs) * DAYS_IN_YEAR)

    cash_cycle = [
        NAN if is_missing(s) else s + coalesce(i, 0.0) - coalesce(p, 0.0)
        for s, i, p in zip(dso, dio, dpo)
    ]

    frame = pd.DataFrame(
        {
            "Days Sales Outstanding (DSO)": dso,
            "Days Inventory Outstanding (DIO)": dio,
            "Days Payable Outstanding (DPO)": dpo,
            "Cash Conversion Cycle": cash_cycle,
        },
        index=periods,
    ).transpose()
    frame.index.name = "Trailing Efficiency Metrics (days)"
    return frame


def normalized_nwc_peg(engagement: Engagement, lookback_periods: int) -> float:
    """Average NWC over the trailing window — the working capital peg."""
    periods = engagement.periods[-max(1, lookback_periods) :]
    values = [net_working_capital(engagement, p) for p in periods]
    usable = [v for v in values if not is_missing(v)]
    if not usable:
        return NAN
    return math.fsum(usable) / float(len(usable))


# --------------------------------------------------------------------------- #
# Classification of debt-like items and non-operating assets
# --------------------------------------------------------------------------- #


def total_funded_debt(engagement: Engagement, period: str) -> float:
    """Short-term borrowings, current maturities and long-term debt."""
    return sum_keys(engagement.facts, FUNDED_DEBT_KEYS, period)


def classification_totals(items: Sequence[ClassifiedItem]) -> tuple[float, float]:
    """Return (debt-like total, non-operating asset total)."""
    debt_like = math.fsum(
        coalesce(item.amount) for item in items if item.classification == DEBT_LIKE
    )
    non_operating = math.fsum(
        coalesce(item.amount) for item in items if item.classification == NON_OPERATING_ASSET
    )
    return debt_like, non_operating


# --------------------------------------------------------------------------- #
# Discounted cash flow
# --------------------------------------------------------------------------- #


@dataclass
class DCFAssumptions:
    horizon_years: int
    revenue_cagr: float
    target_ebitda_margin: float
    capex_pct_revenue: float
    da_pct_revenue: float
    nwc_pct_revenue: float
    wacc: float
    tax_rate: float
    terminal_method: str
    terminal_multiple: float
    terminal_growth: float
    mid_year_convention: bool
    ramp_margin: bool


@dataclass
class DCFResult:
    schedule: pd.DataFrame
    pv_of_forecast: float
    terminal_value: float
    pv_of_terminal_value: float
    enterprise_value: float
    terminal_value_share: float
    implied_entry_multiple: float
    base_revenue: float
    base_adjusted_ebitda: float
    base_margin: float
    note: str = ""


def run_dcf(
    base_revenue: float, base_adjusted_ebitda: float, assumptions: DCFAssumptions
) -> DCFResult:
    """Unlevered free cash flow DCF anchored on the active Adjusted EBITDA."""
    base_revenue = as_float(base_revenue)
    base_adjusted_ebitda = as_float(base_adjusted_ebitda)

    empty = pd.DataFrame()
    if is_missing(base_revenue) or base_revenue <= 0.0 or is_missing(base_adjusted_ebitda):
        return DCFResult(
            schedule=empty,
            pv_of_forecast=NAN,
            terminal_value=NAN,
            pv_of_terminal_value=NAN,
            enterprise_value=NAN,
            terminal_value_share=NAN,
            implied_entry_multiple=NAN,
            base_revenue=base_revenue,
            base_adjusted_ebitda=base_adjusted_ebitda,
            base_margin=NAN,
            note="Baseline revenue or Adjusted EBITDA is unavailable; the DCF cannot be built.",
        )

    base_margin = base_adjusted_ebitda / base_revenue
    horizon = max(1, int(assumptions.horizon_years))

    years: list[int] = []
    revenues: list[float] = []
    margins: list[float] = []
    ebitdas: list[float] = []
    das: list[float] = []
    ebits: list[float] = []
    nopats: list[float] = []
    capexes: list[float] = []
    nwc_balances: list[float] = []
    nwc_changes: list[float] = []
    ufcfs: list[float] = []
    factors: list[float] = []
    pvs: list[float] = []

    prior_nwc = base_revenue * assumptions.nwc_pct_revenue

    for year in range(1, horizon + 1):
        revenue = base_revenue * ((1.0 + assumptions.revenue_cagr) ** year)
        if assumptions.ramp_margin:
            progress = float(year) / float(horizon)
            margin = base_margin + (assumptions.target_ebitda_margin - base_margin) * progress
        else:
            margin = assumptions.target_ebitda_margin

        ebitda = revenue * margin
        depreciation_amortization = revenue * assumptions.da_pct_revenue
        ebit = ebitda - depreciation_amortization
        nopat = ebit * (1.0 - assumptions.tax_rate)
        capex = revenue * assumptions.capex_pct_revenue
        nwc_balance = revenue * assumptions.nwc_pct_revenue
        nwc_change = nwc_balance - prior_nwc
        prior_nwc = nwc_balance

        ufcf = nopat + depreciation_amortization - capex - nwc_change
        exponent = (float(year) - 0.5) if assumptions.mid_year_convention else float(year)
        factor = 1.0 / ((1.0 + assumptions.wacc) ** exponent)
        present_value = ufcf * factor

        years.append(year)
        revenues.append(revenue)
        margins.append(margin * 100.0)
        ebitdas.append(ebitda)
        das.append(depreciation_amortization)
        ebits.append(ebit)
        nopats.append(nopat)
        capexes.append(capex)
        nwc_balances.append(nwc_balance)
        nwc_changes.append(nwc_change)
        ufcfs.append(ufcf)
        factors.append(factor)
        pvs.append(present_value)

    pv_of_forecast = math.fsum(pvs)
    note = ""

    if assumptions.terminal_method == TERMINAL_METHOD_GORDON:
        spread = assumptions.wacc - assumptions.terminal_growth
        if spread <= 0.0:
            terminal_value = NAN
            note = (
                "Terminal growth must remain below WACC for the Gordon Growth model to "
                "converge; the perpetuity term is undefined at the current inputs."
            )
        else:
            terminal_value = ufcfs[-1] * (1.0 + assumptions.terminal_growth) / spread
    else:
        terminal_value = ebitdas[-1] * assumptions.terminal_multiple

    terminal_factor = 1.0 / ((1.0 + assumptions.wacc) ** float(horizon))
    pv_of_terminal_value = (
        NAN if is_missing(terminal_value) else terminal_value * terminal_factor
    )
    enterprise_value = (
        NAN if is_missing(pv_of_terminal_value) else pv_of_forecast + pv_of_terminal_value
    )
    terminal_share = safe_divide(pv_of_terminal_value, enterprise_value)
    implied_entry_multiple = safe_divide(enterprise_value, base_adjusted_ebitda)

    schedule = pd.DataFrame(
        {
            "Revenue": revenues,
            "EBITDA Margin %": margins,
            "EBITDA": ebitdas,
            "Depreciation & Amortization": das,
            "EBIT": ebits,
            "NOPAT": nopats,
            "Capital Expenditures": capexes,
            "Net Working Capital Balance": nwc_balances,
            "(Increase) / Decrease in NWC": [-value for value in nwc_changes],
            "Unlevered Free Cash Flow": ufcfs,
            "Discount Factor": factors,
            "Present Value of UFCF": pvs,
        },
        index=[f"Year {year}" for year in years],
    ).transpose()
    schedule.index.name = "Projection Schedule"

    return DCFResult(
        schedule=schedule,
        pv_of_forecast=pv_of_forecast,
        terminal_value=terminal_value,
        pv_of_terminal_value=pv_of_terminal_value,
        enterprise_value=enterprise_value,
        terminal_value_share=NAN if is_missing(terminal_share) else terminal_share * 100.0,
        implied_entry_multiple=implied_entry_multiple,
        base_revenue=base_revenue,
        base_adjusted_ebitda=base_adjusted_ebitda,
        base_margin=base_margin * 100.0,
        note=note,
    )


# --------------------------------------------------------------------------- #
# Transaction value bridge
# --------------------------------------------------------------------------- #


@dataclass
class ValueBridge:
    table: pd.DataFrame
    enterprise_value: float
    equity_value: float
    steps: list[tuple[str, float]]


def build_value_bridge(
    enterprise_value: float,
    cash: float,
    funded_debt: float,
    debt_like_total: float,
    non_operating_total: float,
    nwc_actual: float,
    nwc_peg: float,
    include_nwc_true_up: bool,
) -> ValueBridge:
    """Walk enterprise value to implied equity value on a cash-free / debt-free basis."""
    steps: list[tuple[str, float]] = [("Enterprise Value", as_float(enterprise_value))]
    steps.append(("Plus: Cash & Cash Equivalents", coalesce(cash)))
    steps.append(("Less: Total Funded Debt", -coalesce(funded_debt)))
    steps.append(("Less: Debt-Like Items", -coalesce(debt_like_total)))
    steps.append(("Plus: Non-Operating Assets", coalesce(non_operating_total)))

    if include_nwc_true_up:
        surplus = coalesce(nwc_actual) - coalesce(nwc_peg)
        steps.append(("Working Capital Surplus / (Deficit) vs. Peg", surplus))

    equity_value = math.fsum(amount for _, amount in steps)
    steps.append(("Implied Equity Value", equity_value))

    table = pd.DataFrame(
        {"Amount": [amount for _, amount in steps]},
        index=[label for label, _ in steps],
    )
    table.index.name = "Transaction Value Bridge"

    return ValueBridge(
        table=table,
        enterprise_value=as_float(enterprise_value),
        equity_value=equity_value,
        steps=steps,
    )


# --------------------------------------------------------------------------- #
# Analytical diagnostics used by the review panel
# --------------------------------------------------------------------------- #


def growth_rates(values: Sequence[float]) -> list[float]:
    """Period-over-period growth, NaN for the first period."""
    result: list[float] = [NAN]
    for index in range(1, len(values)):
        prior = as_float(values[index - 1])
        current = as_float(values[index])
        if is_missing(prior) or is_missing(current) or prior == 0.0:
            result.append(NAN)
        else:
            result.append((current - prior) / abs(prior))
    return result


def compute_diagnostics(
    engagement: Engagement, adjustments: Sequence[Adjustment]
) -> dict[str, object]:
    """Structured analytical facts consumed by both AI and heuristic reviewers."""
    periods = engagement.periods
    latest = engagement.latest_period

    revenue = [engagement.fact("revenue", p) for p in periods]
    reported = [reported_ebitda(engagement, p) for p in periods]
    adjusted = [adjusted_ebitda(engagement, adjustments, p) for p in periods]
    receivables = [coalesce(engagement.fact("accounts_receivable", p)) for p in periods]
    operating_cash = [engagement.fact("cf_operating", p) for p in periods]

    efficiency = build_efficiency_metrics(engagement)
    nwc_values = [net_working_capital(engagement, p) for p in periods]

    accrual_gap: list[float] = []
    for net_income_value, cfo_value in zip(
        [engagement.fact("net_income", p) for p in periods], operating_cash
    ):
        if is_missing(net_income_value) or is_missing(cfo_value):
            accrual_gap.append(NAN)
        else:
            accrual_gap.append(net_income_value - cfo_value)

    return {
        "entity": engagement.entity_name,
        "source": engagement.source,
        "periods": periods,
        "latest_period": latest,
        "currency": engagement.currency,
        "revenue": revenue,
        "revenue_growth": growth_rates(revenue),
        "reported_ebitda": reported,
        "adjusted_ebitda": adjusted,
        "reported_margin": [safe_divide(e, r) for e, r in zip(reported, revenue)],
        "adjusted_margin": [safe_divide(a, r) for a, r in zip(adjusted, revenue)],
        "receivables": receivables,
        "receivables_growth": growth_rates(receivables),
        "dso": efficiency.loc["Days Sales Outstanding (DSO)"].tolist(),
        "dio": efficiency.loc["Days Inventory Outstanding (DIO)"].tolist(),
        "dpo": efficiency.loc["Days Payable Outstanding (DPO)"].tolist(),
        "cash_conversion_cycle": efficiency.loc["Cash Conversion Cycle"].tolist(),
        "net_working_capital": nwc_values,
        "operating_cash_flow": operating_cash,
        "accrual_gap": accrual_gap,
        "funded_debt_latest": total_funded_debt(engagement, latest),
        "cash_latest": coalesce(engagement.fact("cash_and_equivalents", latest)),
        "deferred_revenue_latest": sum_keys(
            engagement.facts,
            ("deferred_revenue_current", "deferred_revenue_non_current"),
            latest,
        ),
        "capitalized_software_latest": coalesce(
            engagement.fact("capitalized_software_net", latest)
        ),
        "inventory_latest": coalesce(engagement.fact("inventory", latest)),
    }
