"""Global configuration, taxonomy, and presentation constants.

NUMERICAL INTEGRITY POLICY
--------------------------
No function anywhere in this package rounds, truncates, or otherwise reduces the
precision of a computed value. ``round()``, ``numpy.round``, ``DataFrame.round``
and ``Decimal.quantize`` are deliberately absent from the codebase. Every stored
value is an IEEE-754 double carried at full precision from ingestion through the
QoE bridge, the net working capital schedule, the DCF and the value bridge.

The only place precision is reduced is the *rendering* layer: Streamlit column
formats and Plotly hover templates are printf-style display masks applied by the
front end to a value that remains unmodified in ``st.session_state``. Toggling
"Full raw precision" in the sidebar swaps the mask, never the datum.
"""

from __future__ import annotations

APP_TITLE = "FDD / QoE Diligence Workspace"
APP_TAGLINE = "Financial Due Diligence & Quality of Earnings — Learning Platform and Case Study Workspace"
APP_VERSION = "1.0.1"

PRECISION_POLICY = (
    "All calculations are carried at full IEEE-754 double precision. No value is "
    "rounded at any point in the model. Decimal masks shown in tables and charts "
    "are display-only and never mutate the underlying figures."
)

# --------------------------------------------------------------------------- #
# Modes
# --------------------------------------------------------------------------- #

MODE_LIVE = "Live Deal Mode"
MODE_SANDBOX = "TAS Analyst Sandbox Mode"
MODES = (MODE_LIVE, MODE_SANDBOX)

# --------------------------------------------------------------------------- #
# Data ingestion
# --------------------------------------------------------------------------- #

MAX_HISTORY_YEARS = 5
DAYS_IN_YEAR = 365.0
YFINANCE_CACHE_TTL_SECONDS = 900

DEFAULT_TICKERS = (
    "MSFT",
    "CAT",
    "ADBE",
    "DE",
    "CRM",
    "HON",
    "NOW",
    "PH",
    "WM",
    "ROP",
)

# --------------------------------------------------------------------------- #
# Canonical line-item taxonomy
#
# Every downstream calculation reads from these keys, so the sandbox cases and
# the live yfinance feed converge on one schema.
# --------------------------------------------------------------------------- #

INCOME_STATEMENT_KEYS = (
    "revenue",
    "cost_of_revenue",
    "gross_profit",
    "selling_and_marketing",
    "research_and_development",
    "general_and_administrative",
    "other_operating_expense",
    "depreciation",
    "amortization_software",
    "amortization_intangibles",
    "operating_income",
    "interest_expense",
    "other_income_expense",
    "pretax_income",
    "income_tax_expense",
    "net_income",
)

BALANCE_SHEET_KEYS = (
    "cash_and_equivalents",
    "accounts_receivable",
    "inventory",
    "prepaid_expenses",
    "other_current_assets",
    "total_current_assets",
    "ppe_net",
    "capitalized_software_net",
    "goodwill_and_intangibles_net",
    "other_non_current_assets",
    "total_assets",
    "accounts_payable",
    "accrued_liabilities",
    "deferred_revenue_current",
    "income_taxes_payable",
    "short_term_debt",
    "current_portion_long_term_debt",
    "other_current_liabilities",
    "total_current_liabilities",
    "long_term_debt",
    "deferred_revenue_non_current",
    "other_long_term_liabilities",
    "total_liabilities",
    "contributed_capital",
    "retained_earnings",
    "total_equity",
)

CASH_FLOW_KEYS = (
    "cf_net_income",
    "cf_depreciation_amortization",
    "cf_stock_based_compensation",
    "cf_change_accounts_receivable",
    "cf_change_inventory",
    "cf_change_prepaid_other_current",
    "cf_change_accounts_payable",
    "cf_change_accrued_liabilities",
    "cf_change_deferred_revenue",
    "cf_change_other_operating",
    "cf_operating",
    "cf_capital_expenditure",
    "cf_capitalized_software",
    "cf_acquisitions_and_intangibles",
    "cf_change_other_non_current_assets",
    "cf_investing",
    "cf_net_debt_issuance",
    "cf_equity_issuance",
    "cf_distributions",
    "cf_financing",
    "cf_net_change_in_cash",
    "cf_beginning_cash",
    "cf_ending_cash",
)

# Human-readable labels used for statement presentation.
LINE_ITEM_LABELS = {
    "revenue": "Revenue",
    "cost_of_revenue": "Cost of Revenue",
    "gross_profit": "Gross Profit",
    "selling_and_marketing": "Selling & Marketing",
    "research_and_development": "Research & Development",
    "general_and_administrative": "General & Administrative",
    "other_operating_expense": "Other Operating Expense",
    "depreciation": "Depreciation",
    "amortization_software": "Amortization — Capitalized Software",
    "amortization_intangibles": "Amortization — Acquired Intangibles",
    "operating_income": "Operating Income (EBIT)",
    "interest_expense": "Interest Expense",
    "other_income_expense": "Other Income / (Expense)",
    "pretax_income": "Income Before Taxes",
    "income_tax_expense": "Income Tax Expense",
    "net_income": "Net Income (GAAP)",
    "cash_and_equivalents": "Cash & Cash Equivalents",
    "accounts_receivable": "Accounts Receivable, net",
    "inventory": "Inventory",
    "prepaid_expenses": "Prepaid Expenses",
    "other_current_assets": "Other Current Assets",
    "total_current_assets": "Total Current Assets",
    "ppe_net": "Property, Plant & Equipment, net",
    "capitalized_software_net": "Capitalized Software, net",
    "goodwill_and_intangibles_net": "Goodwill & Intangible Assets, net",
    "other_non_current_assets": "Other Non-Current Assets",
    "total_assets": "Total Assets",
    "accounts_payable": "Accounts Payable",
    "accrued_liabilities": "Accrued Liabilities",
    "deferred_revenue_current": "Deferred Revenue — Current",
    "income_taxes_payable": "Income Taxes Payable",
    "short_term_debt": "Short-Term Borrowings / Revolver",
    "current_portion_long_term_debt": "Current Portion of Long-Term Debt",
    "other_current_liabilities": "Other Current Liabilities",
    "total_current_liabilities": "Total Current Liabilities",
    "long_term_debt": "Long-Term Debt",
    "deferred_revenue_non_current": "Deferred Revenue — Non-Current",
    "other_long_term_liabilities": "Other Long-Term Liabilities",
    "total_liabilities": "Total Liabilities",
    "contributed_capital": "Contributed Capital / APIC",
    "retained_earnings": "Retained Earnings / (Accumulated Deficit)",
    "total_equity": "Total Shareholders' Equity",
    "cf_net_income": "Net Income",
    "cf_depreciation_amortization": "Depreciation & Amortization",
    "cf_stock_based_compensation": "Stock-Based Compensation",
    "cf_change_accounts_receivable": "(Increase) / Decrease in Accounts Receivable",
    "cf_change_inventory": "(Increase) / Decrease in Inventory",
    "cf_change_prepaid_other_current": "(Increase) / Decrease in Prepaid & Other Current Assets",
    "cf_change_accounts_payable": "Increase / (Decrease) in Accounts Payable",
    "cf_change_accrued_liabilities": "Increase / (Decrease) in Accrued Liabilities",
    "cf_change_deferred_revenue": "Increase / (Decrease) in Deferred Revenue",
    "cf_change_other_operating": "Increase / (Decrease) in Other Operating Liabilities",
    "cf_operating": "Net Cash Provided by Operating Activities",
    "cf_capital_expenditure": "Capital Expenditures",
    "cf_capitalized_software": "Capitalized Internal-Use Software",
    "cf_acquisitions_and_intangibles": "Acquisitions & Intangible Additions",
    "cf_change_other_non_current_assets": "(Increase) / Decrease in Other Non-Current Assets",
    "cf_investing": "Net Cash Used in Investing Activities",
    "cf_net_debt_issuance": "Net Borrowings / (Repayments)",
    "cf_equity_issuance": "Proceeds from Equity Issuance",
    "cf_distributions": "Dividends & Shareholder Distributions",
    "cf_financing": "Net Cash Provided by Financing Activities",
    "cf_net_change_in_cash": "Net Change in Cash",
    "cf_beginning_cash": "Cash — Beginning of Period",
    "cf_ending_cash": "Cash — End of Period",
}

# Sub-total rows are rendered in bold in the statement presentation.
SUBTOTAL_KEYS = frozenset(
    {
        "gross_profit",
        "operating_income",
        "pretax_income",
        "net_income",
        "total_current_assets",
        "total_assets",
        "total_current_liabilities",
        "total_liabilities",
        "total_equity",
        "cf_operating",
        "cf_investing",
        "cf_financing",
        "cf_net_change_in_cash",
        "cf_ending_cash",
    }
)

# Ordered presentation blocks for the three-statement layout.
INCOME_STATEMENT_LAYOUT = (
    ("Revenue & Gross Profit", ("revenue", "cost_of_revenue", "gross_profit")),
    (
        "Operating Expenses",
        (
            "selling_and_marketing",
            "research_and_development",
            "general_and_administrative",
            "other_operating_expense",
            "depreciation",
            "amortization_software",
            "amortization_intangibles",
        ),
    ),
    ("Operating Result", ("operating_income",)),
    (
        "Non-Operating & Taxes",
        (
            "interest_expense",
            "other_income_expense",
            "pretax_income",
            "income_tax_expense",
            "net_income",
        ),
    ),
)

BALANCE_SHEET_LAYOUT = (
    (
        "Current Assets",
        (
            "cash_and_equivalents",
            "accounts_receivable",
            "inventory",
            "prepaid_expenses",
            "other_current_assets",
            "total_current_assets",
        ),
    ),
    (
        "Non-Current Assets",
        (
            "ppe_net",
            "capitalized_software_net",
            "goodwill_and_intangibles_net",
            "other_non_current_assets",
            "total_assets",
        ),
    ),
    (
        "Current Liabilities",
        (
            "accounts_payable",
            "accrued_liabilities",
            "deferred_revenue_current",
            "income_taxes_payable",
            "short_term_debt",
            "current_portion_long_term_debt",
            "other_current_liabilities",
            "total_current_liabilities",
        ),
    ),
    (
        "Non-Current Liabilities",
        (
            "long_term_debt",
            "deferred_revenue_non_current",
            "other_long_term_liabilities",
            "total_liabilities",
        ),
    ),
    ("Shareholders' Equity", ("contributed_capital", "retained_earnings", "total_equity")),
)

CASH_FLOW_LAYOUT = (
    (
        "Operating Activities",
        (
            "cf_net_income",
            "cf_depreciation_amortization",
            "cf_stock_based_compensation",
            "cf_change_accounts_receivable",
            "cf_change_inventory",
            "cf_change_prepaid_other_current",
            "cf_change_accounts_payable",
            "cf_change_accrued_liabilities",
            "cf_change_deferred_revenue",
            "cf_change_other_operating",
            "cf_operating",
        ),
    ),
    (
        "Investing Activities",
        (
            "cf_capital_expenditure",
            "cf_capitalized_software",
            "cf_acquisitions_and_intangibles",
            "cf_change_other_non_current_assets",
            "cf_investing",
        ),
    ),
    (
        "Financing Activities",
        (
            "cf_net_debt_issuance",
            "cf_equity_issuance",
            "cf_distributions",
            "cf_financing",
        ),
    ),
    (
        "Cash Reconciliation",
        ("cf_net_change_in_cash", "cf_beginning_cash", "cf_ending_cash"),
    ),
)

# --------------------------------------------------------------------------- #
# Working capital taxonomy
# --------------------------------------------------------------------------- #

NWC_ASSET_KEYS = (
    "accounts_receivable",
    "inventory",
    "prepaid_expenses",
    "other_current_assets",
)

NWC_LIABILITY_KEYS = (
    "accounts_payable",
    "accrued_liabilities",
    "deferred_revenue_current",
    "income_taxes_payable",
    "other_current_liabilities",
)

# Excluded from operational NWC by construction (cash-free / debt-free convention).
NWC_EXCLUDED_KEYS = (
    "cash_and_equivalents",
    "short_term_debt",
    "current_portion_long_term_debt",
)

FUNDED_DEBT_KEYS = (
    "short_term_debt",
    "current_portion_long_term_debt",
    "long_term_debt",
)

# --------------------------------------------------------------------------- #
# QoE adjustment taxonomy
# --------------------------------------------------------------------------- #

ADJUSTMENT_CATEGORIES = (
    "Non-Recurring / One-Time",
    "Accounting & GAAP Correction",
    "Owner / Management Compensation",
    "Related-Party Normalization",
    "Run-Rate / Pro Forma",
    "Cut-Off & Revenue Recognition",
    "Reserve & Allowance Adequacy",
    "Carve-Out / Standalone Cost",
    "Other Normalization",
)

ADJUSTMENT_STATUSES = ("Accepted", "Rejected — Proposed by Management")

RISK_SEVERITIES = ("Low", "Medium", "High", "Critical")

CLASSIFICATION_OPTIONS = (
    "Operating (no adjustment)",
    "Debt-Like Item",
    "Non-Operating Asset",
)

DEBT_LIKE = "Debt-Like Item"
NON_OPERATING_ASSET = "Non-Operating Asset"
OPERATING_NEUTRAL = "Operating (no adjustment)"

# --------------------------------------------------------------------------- #
# DCF defaults
# --------------------------------------------------------------------------- #

DCF_DEFAULTS = {
    "horizon_years": 5,
    "revenue_cagr": 0.08,
    "target_ebitda_margin": 0.18,
    "capex_pct_revenue": 0.035,
    "da_pct_revenue": 0.030,
    "nwc_pct_revenue": 0.10,
    "wacc": 0.115,
    "terminal_multiple": 9.0,
    "terminal_growth": 0.025,
    "tax_rate": 0.25,
    "mid_year_convention": True,
    "ramp_margin": True,
}

TERMINAL_METHOD_MULTIPLE = "Exit EBITDA Multiple"
TERMINAL_METHOD_GORDON = "Gordon Growth (Perpetuity)"
TERMINAL_METHODS = (TERMINAL_METHOD_MULTIPLE, TERMINAL_METHOD_GORDON)

# --------------------------------------------------------------------------- #
# Presentation
# --------------------------------------------------------------------------- #

DISPLAY_DECIMALS_STANDARD = 2
DISPLAY_DECIMALS_FULL = 10

PALETTE = {
    "positive": "#1B7F5E",
    "negative": "#B3352C",
    "total": "#1F3A5F",
    "accent": "#C08A2E",
    "neutral": "#5A6B7B",
    "grid": "#D8DEE4",
    "surface": "rgba(0,0,0,0)",
}

CHART_HEIGHT = 460
CHART_FONT_FAMILY = "Georgia, 'Times New Roman', serif"


def number_format(full_precision: bool) -> str:
    """printf mask for Streamlit ``NumberColumn`` — display only."""
    decimals = DISPLAY_DECIMALS_FULL if full_precision else DISPLAY_DECIMALS_STANDARD
    return f"%.{decimals}f"


def percent_format(full_precision: bool) -> str:
    """printf mask for percentage columns — display only."""
    decimals = DISPLAY_DECIMALS_FULL if full_precision else DISPLAY_DECIMALS_STANDARD
    return f"%.{decimals}f%%"


def label_for(key: str) -> str:
    """Return the presentation label for a canonical key."""
    return LINE_ITEM_LABELS.get(key, key.replace("_", " ").title())
