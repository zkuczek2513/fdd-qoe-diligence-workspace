"""Live market data ingestion.

Fetches up to five fiscal years of income statement, balance sheet and cash flow
data through ``yfinance`` and normalizes the vendor's labels onto the canonical
taxonomy in :mod:`config`. Vendor figures are carried through verbatim — the
mapping layer relabels and aggregates, it never rounds.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from config import (
    BALANCE_SHEET_KEYS,
    CASH_FLOW_KEYS,
    INCOME_STATEMENT_KEYS,
    MAX_HISTORY_YEARS,
    YFINANCE_CACHE_TTL_SECONDS,
)
from finance_logic import Engagement, NAN, as_float, coalesce, is_missing

try:  # pragma: no cover - exercised implicitly at runtime
    import yfinance as yf

    YFINANCE_AVAILABLE = True
    YFINANCE_IMPORT_ERROR = ""
except Exception as exc:  # pragma: no cover
    yf = None  # type: ignore[assignment]
    YFINANCE_AVAILABLE = False
    YFINANCE_IMPORT_ERROR = str(exc)


class DataIngestionError(RuntimeError):
    """Raised when a ticker cannot be resolved into usable statements."""


# --------------------------------------------------------------------------- #
# Label synonyms
#
# yfinance label sets drift between releases and across issuers, so each
# canonical key carries an ordered list of candidates. The first label present
# in the vendor frame wins.
# --------------------------------------------------------------------------- #

INCOME_SYNONYMS: dict[str, tuple[str, ...]] = {
    "revenue": ("Total Revenue", "Operating Revenue", "Revenue"),
    "cost_of_revenue": ("Cost Of Revenue", "Cost of Revenue", "Reconciled Cost Of Revenue"),
    "gross_profit": ("Gross Profit",),
    "selling_and_marketing": (
        "Selling And Marketing Expense",
        "Selling General And Administration",
        "Selling General And Administrative",
    ),
    "research_and_development": ("Research And Development", "Research Development"),
    "general_and_administrative": (
        "General And Administrative Expense",
        "Other Gand A",
    ),
    "other_operating_expense": (
        "Other Operating Expenses",
        "Special Income Charges",
        "Restructuring And Mergern Acquisition",
    ),
    "operating_income": ("Operating Income", "Total Operating Income As Reported", "EBIT"),
    "interest_expense": (
        "Interest Expense",
        "Interest Expense Non Operating",
        "Net Interest Income",
    ),
    "other_income_expense": (
        "Other Non Operating Income Expenses",
        "Other Income Expense",
        "Total Other Finance Cost",
    ),
    "pretax_income": ("Pretax Income", "Income Before Tax"),
    "income_tax_expense": ("Tax Provision", "Income Tax Expense"),
    "net_income": (
        "Net Income",
        "Net Income Common Stockholders",
        "Net Income Continuous Operations",
    ),
    "_ebitda_reported": ("EBITDA", "Normalized EBITDA"),
    "_reconciled_da": ("Reconciled Depreciation",),
    "_da_income_statement": (
        "Depreciation And Amortization In Income Statement",
        "Depreciation Amortization Depletion Income Statement",
    ),
}

BALANCE_SYNONYMS: dict[str, tuple[str, ...]] = {
    "cash_and_equivalents": (
        "Cash And Cash Equivalents",
        "Cash Cash Equivalents And Short Term Investments",
        "Cash Financial",
    ),
    "accounts_receivable": (
        "Accounts Receivable",
        "Receivables",
        "Gross Accounts Receivable",
    ),
    "inventory": ("Inventory", "Finished Goods"),
    "prepaid_expenses": ("Prepaid Assets", "Prepaid Expense"),
    "other_current_assets": ("Other Current Assets", "Hedging Assets Current"),
    "total_current_assets": ("Current Assets", "Total Current Assets"),
    "ppe_net": ("Net PPE", "Net Property Plant And Equipment"),
    "capitalized_software_net": ("Capitalized Software",),
    "goodwill_and_intangibles_net": (
        "Goodwill And Other Intangible Assets",
        "Goodwill",
        "Other Intangible Assets",
    ),
    "other_non_current_assets": (
        "Other Non Current Assets",
        "Non Current Deferred Assets",
    ),
    "total_assets": ("Total Assets",),
    "accounts_payable": ("Accounts Payable", "Payables"),
    "accrued_liabilities": (
        "Current Accrued Expenses",
        "Payables And Accrued Expenses",
        "Accrued Liabilities",
    ),
    "deferred_revenue_current": (
        "Current Deferred Revenue",
        "Current Deferred Liabilities",
    ),
    "income_taxes_payable": ("Income Tax Payable", "Total Tax Payable"),
    "short_term_debt": ("Commercial Paper", "Other Current Borrowings"),
    "current_portion_long_term_debt": (
        "Current Debt",
        "Current Debt And Capital Lease Obligation",
        "Current Capital Lease Obligation",
    ),
    "other_current_liabilities": ("Other Current Liabilities",),
    "total_current_liabilities": ("Current Liabilities", "Total Current Liabilities"),
    "long_term_debt": (
        "Long Term Debt",
        "Long Term Debt And Capital Lease Obligation",
    ),
    "deferred_revenue_non_current": (
        "Non Current Deferred Revenue",
        "Non Current Deferred Liabilities",
    ),
    "other_long_term_liabilities": (
        "Other Non Current Liabilities",
        "Non Current Deferred Taxes Liabilities",
    ),
    "total_liabilities": (
        "Total Liabilities Net Minority Interest",
        "Total Liabilities",
    ),
    "contributed_capital": ("Capital Stock", "Common Stock", "Additional Paid In Capital"),
    "retained_earnings": ("Retained Earnings", "Accumulated Deficit"),
    "total_equity": (
        "Stockholders Equity",
        "Total Equity Gross Minority Interest",
        "Common Stock Equity",
    ),
    "_total_debt": ("Total Debt",),
}

CASH_FLOW_SYNONYMS: dict[str, tuple[str, ...]] = {
    "cf_net_income": (
        "Net Income From Continuing Operations",
        "Net Income",
        "Net Income From Continuing And Discontinued Operation",
    ),
    "cf_depreciation_amortization": (
        "Depreciation And Amortization",
        "Depreciation Amortization Depletion",
        "Depreciation",
    ),
    "cf_stock_based_compensation": ("Stock Based Compensation",),
    "cf_change_accounts_receivable": ("Change In Receivables", "Changes In Account Receivables"),
    "cf_change_inventory": ("Change In Inventory",),
    "cf_change_prepaid_other_current": ("Change In Prepaid Assets", "Change In Other Current Assets"),
    "cf_change_accounts_payable": ("Change In Payable", "Change In Account Payable"),
    "cf_change_accrued_liabilities": ("Change In Accrued Expense",),
    "cf_change_deferred_revenue": ("Change In Other Current Liabilities",),
    "cf_change_other_operating": ("Change In Working Capital", "Other Non Cash Items"),
    "cf_operating": ("Operating Cash Flow", "Cash Flow From Continuing Operating Activities"),
    "cf_capital_expenditure": ("Capital Expenditure", "Purchase Of PPE"),
    "cf_acquisitions_and_intangibles": ("Purchase Of Business", "Net Business Purchase And Sale"),
    "cf_investing": ("Investing Cash Flow", "Cash Flow From Continuing Investing Activities"),
    "cf_net_debt_issuance": ("Net Issuance Payments Of Debt",),
    "cf_equity_issuance": ("Net Common Stock Issuance", "Issuance Of Capital Stock"),
    "cf_distributions": ("Cash Dividends Paid", "Common Stock Dividend Paid"),
    "cf_financing": ("Financing Cash Flow", "Cash Flow From Continuing Financing Activities"),
    "cf_net_change_in_cash": (
        "Changes In Cash",
        "Change In Cash Supplemental As Reported",
    ),
    "cf_beginning_cash": ("Beginning Cash Position",),
    "cf_ending_cash": ("End Cash Position",),
}


@dataclass
class RawStatements:
    """Vendor frames retained verbatim for the raw-data inspector."""

    income_statement: pd.DataFrame
    balance_sheet: pd.DataFrame
    cash_flow: pd.DataFrame


def _normalize_label(label: object) -> str:
    return " ".join(str(label).strip().lower().replace("_", " ").split())


def _lookup(frame: pd.DataFrame, candidates: tuple[str, ...], column: object) -> float:
    """Resolve the first matching vendor label for one period."""
    if frame is None or frame.empty:
        return NAN

    index_map = {_normalize_label(idx): idx for idx in frame.index}
    for candidate in candidates:
        actual = index_map.get(_normalize_label(candidate))
        if actual is None:
            continue
        try:
            value = frame.loc[actual, column]
        except (KeyError, IndexError):
            continue
        if isinstance(value, pd.Series):
            value = value.iloc[0] if not value.empty else NAN
        numeric = as_float(value)
        if not is_missing(numeric):
            return numeric
    return NAN


def _period_label(column: object) -> str:
    """Render a vendor column as a fiscal-year label."""
    timestamp = pd.Timestamp(column) if not isinstance(column, pd.Timestamp) else column
    try:
        return f"FY{timestamp.year}"
    except (AttributeError, ValueError):
        return str(column)


def _ordered_columns(*frames: pd.DataFrame) -> list[object]:
    """Union of vendor period columns, oldest first, capped at the history limit."""
    columns: set[object] = set()
    for frame in frames:
        if frame is not None and not frame.empty:
            columns.update(frame.columns.tolist())

    def sort_key(column: object) -> pd.Timestamp:
        try:
            return pd.Timestamp(column)
        except (TypeError, ValueError):
            return pd.Timestamp.min

    ordered = sorted(columns, key=sort_key)
    return ordered[-MAX_HISTORY_YEARS:]


def _derive_income_facts(
    facts: dict[str, dict[str, float]], income: pd.DataFrame, column: object, period: str
) -> None:
    """Fill statement gaps that yfinance leaves implicit."""
    revenue = facts["revenue"].get(period, NAN)
    cost_of_revenue = facts["cost_of_revenue"].get(period, NAN)
    gross_profit = facts["gross_profit"].get(period, NAN)

    if is_missing(gross_profit) and not (is_missing(revenue) or is_missing(cost_of_revenue)):
        facts["gross_profit"][period] = revenue - cost_of_revenue
    elif is_missing(cost_of_revenue) and not (is_missing(revenue) or is_missing(gross_profit)):
        facts["cost_of_revenue"][period] = revenue - gross_profit

    # yfinance reports interest expense as a positive cost; keep that sign
    # convention so the QoE bridge adds it back.
    interest = facts["interest_expense"].get(period, NAN)
    if not is_missing(interest):
        facts["interest_expense"][period] = abs(interest)

    pretax = facts["pretax_income"].get(period, NAN)
    net_income = facts["net_income"].get(period, NAN)
    tax = facts["income_tax_expense"].get(period, NAN)
    if is_missing(pretax) and not (is_missing(net_income) or is_missing(tax)):
        facts["pretax_income"][period] = net_income + tax
    if is_missing(net_income) and not (is_missing(pretax) or is_missing(tax)):
        facts["net_income"][period] = pretax - tax

    # Depreciation and amortization: prefer the cash flow statement figure, then
    # the reconciled income statement figure. Everything lands in `depreciation`
    # because the vendor does not split software from acquired intangibles.
    da_candidates = (
        facts["cf_depreciation_amortization"].get(period, NAN),
        _lookup(income, INCOME_SYNONYMS["_da_income_statement"], column),
        _lookup(income, INCOME_SYNONYMS["_reconciled_da"], column),
    )
    for candidate in da_candidates:
        if not is_missing(candidate):
            facts["depreciation"][period] = abs(candidate)
            break

    # Backfill operating income from a vendor-reported EBITDA when needed.
    operating_income = facts["operating_income"].get(period, NAN)
    if is_missing(operating_income):
        vendor_ebitda = _lookup(income, INCOME_SYNONYMS["_ebitda_reported"], column)
        depreciation = facts["depreciation"].get(period, NAN)
        if not (is_missing(vendor_ebitda) or is_missing(depreciation)):
            facts["operating_income"][period] = vendor_ebitda - depreciation

    # Avoid double counting: yfinance folds S&M into "Selling General And
    # Administration" for many issuers, so drop G&A when both resolve to the
    # same vendor row.
    sm_label = _lookup(income, INCOME_SYNONYMS["selling_and_marketing"], column)
    ga_label = _lookup(income, INCOME_SYNONYMS["general_and_administrative"], column)
    if not is_missing(sm_label) and not is_missing(ga_label) and sm_label == ga_label:
        facts["general_and_administrative"][period] = NAN


def _derive_balance_facts(
    facts: dict[str, dict[str, float]], balance: pd.DataFrame, column: object, period: str
) -> None:
    """Complete balance sheet subtotals and split total debt when needed."""
    current_assets = facts["total_current_assets"].get(period, NAN)
    if is_missing(current_assets):
        facts["total_current_assets"][period] = math.fsum(
            coalesce(facts[key].get(period))
            for key in (
                "cash_and_equivalents",
                "accounts_receivable",
                "inventory",
                "prepaid_expenses",
                "other_current_assets",
            )
        )

    current_liabilities = facts["total_current_liabilities"].get(period, NAN)
    if is_missing(current_liabilities):
        facts["total_current_liabilities"][period] = math.fsum(
            coalesce(facts[key].get(period))
            for key in (
                "accounts_payable",
                "accrued_liabilities",
                "deferred_revenue_current",
                "income_taxes_payable",
                "short_term_debt",
                "current_portion_long_term_debt",
                "other_current_liabilities",
            )
        )

    total_assets = facts["total_assets"].get(period, NAN)
    total_liabilities = facts["total_liabilities"].get(period, NAN)
    total_equity = facts["total_equity"].get(period, NAN)

    if is_missing(total_liabilities) and not (is_missing(total_assets) or is_missing(total_equity)):
        facts["total_liabilities"][period] = total_assets - total_equity
    if is_missing(total_equity) and not (is_missing(total_assets) or is_missing(total_liabilities)):
        facts["total_equity"][period] = total_assets - total_liabilities

    # When only aggregate debt is reported, place the residual in long-term debt
    # so funded debt totals stay complete.
    total_debt = _lookup(balance, BALANCE_SYNONYMS["_total_debt"], column)
    if not is_missing(total_debt):
        short_term = coalesce(facts["short_term_debt"].get(period))
        current_portion = coalesce(facts["current_portion_long_term_debt"].get(period))
        long_term = facts["long_term_debt"].get(period, NAN)
        residual = total_debt - short_term - current_portion
        if is_missing(long_term) and residual >= 0.0:
            facts["long_term_debt"][period] = residual


def _fetch_frames(ticker_symbol: str) -> tuple[RawStatements, str, str]:
    """Retrieve the three annual statements plus display metadata."""
    if not YFINANCE_AVAILABLE:
        raise DataIngestionError(
            f"The yfinance library is unavailable in this environment: {YFINANCE_IMPORT_ERROR}"
        )

    handle = yf.Ticker(ticker_symbol)

    def annual(primary: str, fallback: str) -> pd.DataFrame:
        for attribute in (primary, fallback):
            try:
                frame = getattr(handle, attribute)
            except Exception:
                continue
            if isinstance(frame, pd.DataFrame) and not frame.empty:
                return frame
        return pd.DataFrame()

    income = annual("income_stmt", "financials")
    balance = annual("balance_sheet", "balancesheet")
    cash_flow = annual("cashflow", "cash_flow")

    if income.empty and balance.empty and cash_flow.empty:
        raise DataIngestionError(
            f"No annual financial statements were returned for '{ticker_symbol}'. "
            "Confirm the ticker symbol and that the data provider is reachable."
        )

    entity_name = ticker_symbol.upper()
    currency = "USD"
    try:
        info = handle.get_info()
        if isinstance(info, dict):
            entity_name = info.get("longName") or info.get("shortName") or entity_name
            currency = info.get("financialCurrency") or info.get("currency") or currency
    except Exception:
        pass

    return RawStatements(income, balance, cash_flow), str(entity_name), str(currency)


def build_live_engagement(ticker_symbol: str) -> tuple[Engagement, RawStatements]:
    """Fetch and normalize a live ticker into an :class:`Engagement`."""
    symbol = (ticker_symbol or "").strip().upper()
    if not symbol:
        raise DataIngestionError("Enter a ticker symbol to load a live target.")

    raw, entity_name, currency = _fetch_frames(symbol)
    columns = _ordered_columns(raw.income_statement, raw.balance_sheet, raw.cash_flow)
    if not columns:
        raise DataIngestionError(f"No fiscal periods were returned for '{symbol}'.")

    # Seed every canonical key, not just the ones with a vendor synonym: the
    # derivation helpers write to keys such as `depreciation` that are computed
    # rather than looked up, and downstream code expects the full taxonomy.
    all_keys = (
        set(INCOME_STATEMENT_KEYS)
        | set(BALANCE_SHEET_KEYS)
        | set(CASH_FLOW_KEYS)
        | set(INCOME_SYNONYMS)
        | set(BALANCE_SYNONYMS)
        | set(CASH_FLOW_SYNONYMS)
    )
    facts: dict[str, dict[str, float]] = {key: {} for key in all_keys}

    periods: list[str] = []
    for column in columns:
        period = _period_label(column)
        if period in periods:
            period = f"{period} ({len(periods)})"
        periods.append(period)

        for key, candidates in INCOME_SYNONYMS.items():
            facts[key][period] = _lookup(raw.income_statement, candidates, column)
        for key, candidates in BALANCE_SYNONYMS.items():
            facts[key][period] = _lookup(raw.balance_sheet, candidates, column)
        for key, candidates in CASH_FLOW_SYNONYMS.items():
            facts[key][period] = _lookup(raw.cash_flow, candidates, column)

        # yfinance does not split amortization by asset class and reports no
        # separate capitalized-software line, so these stay explicitly unknown
        # rather than being silently defaulted to zero.
        for key in (
            "amortization_software",
            "amortization_intangibles",
            "cf_capitalized_software",
            "cf_change_other_non_current_assets",
        ):
            facts[key][period] = NAN

        _derive_income_facts(facts, raw.income_statement, column, period)
        _derive_balance_facts(facts, raw.balance_sheet, column, period)

        # yfinance reports capital expenditure as a negative outflow; the
        # presentation layer expects the same sign convention throughout.
        capex = facts["cf_capital_expenditure"].get(period, NAN)
        if not is_missing(capex):
            facts["cf_capital_expenditure"][period] = -abs(capex)

    for private_key in ("_ebitda_reported", "_reconciled_da", "_da_income_statement", "_total_debt"):
        facts.pop(private_key, None)

    # Providers commonly return one more balance sheet year than income
    # statement year. Rather than presenting an empty leading column, demote it
    # to the comparative period so trailing average-balance metrics can use it.
    comparative_period: str | None = None
    if len(periods) > 1:
        oldest = periods[0]
        has_earnings = any(
            not is_missing(facts[key].get(oldest))
            for key in ("revenue", "operating_income", "net_income")
        )
        if not has_earnings:
            comparative_period = oldest
            periods = periods[1:]

    warnings: list[str] = []
    if len(periods) < MAX_HISTORY_YEARS:
        warnings.append(
            f"The provider returned {len(periods)} annual income statement periods for "
            f"{symbol}; {MAX_HISTORY_YEARS} were requested. Public issuers commonly expose "
            "four years of annual detail through this feed."
        )
    if comparative_period is not None:
        warnings.append(
            f"{comparative_period} returned a balance sheet but no income statement. It is "
            "held as the opening comparative so trailing average-balance metrics are "
            "computed consistently in the first presented year, and is not shown as a "
            "diligence period."
        )
    if all(is_missing(facts["operating_income"].get(period)) for period in periods):
        warnings.append(
            "Operating income could not be resolved from the vendor labels, so reported "
            "EBITDA is unavailable. Review the raw statement inspector."
        )
    if all(is_missing(facts["inventory"].get(period)) for period in periods):
        warnings.append(
            "No inventory balance was reported — Days Inventory Outstanding will be "
            "excluded, which is expected for asset-light and service businesses."
        )

    engagement = Engagement(
        source="live",
        entity_name=entity_name,
        ticker=symbol,
        currency=currency,
        periods=periods,
        facts=facts,
        units_note=(
            "Figures are presented as reported by the data provider, in absolute units "
            f"of {currency}."
        ),
        context=(
            f"**Live Deal Mode — {entity_name} ({symbol}).** Reported public filings have been "
            "normalized onto the standard diligence taxonomy. Treat this as an open-ended "
            "practice engagement: build your own adjustment thesis, classify the balance "
            "sheet, and defend the resulting Adjusted EBITDA to the review panel."
        ),
        warnings=warnings,
        comparative_period=comparative_period,
    )
    return engagement, raw


def cache_ttl_seconds() -> int:
    """Cache lifetime for the Streamlit data cache decorator."""
    return YFINANCE_CACHE_TTL_SECONDS
