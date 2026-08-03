"""ASC 805 purchase price allocation and the Day 1 opening balance sheet.

Walks consideration transferred down to goodwill (or a bargain purchase gain),
recognising fair value step-ups on acquired tangible assets, separately
identifiable intangible assets, and the deferred tax consequences of both.

Two technical points the engine handles explicitly, because they are where
allocations most often go wrong:

* **Deferred tax only arises where book and tax basis diverge.** In a stock
  acquisition the target's tax basis carries over, so every step-up creates a
  deferred tax liability under ASC 740-10. In a taxable asset acquisition — or a
  stock purchase with a section 338(h)(10) or 336(e) election — tax basis steps
  up alongside book basis and no DTL arises. The structure is an input, not an
  assumption.
* **No deferred tax is recorded on goodwill.** ASC 805-740-25-3 exempts the
  initial recognition of non-deductible goodwill, which is why goodwill is the
  residual rather than part of the DTL calculation.

Nothing in this module is rounded (see :mod:`config`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

from finance_logic import Engagement, as_float, coalesce, is_missing, safe_divide

NAN = float("nan")

# --------------------------------------------------------------------------- #
# Structure and defaults
# --------------------------------------------------------------------------- #

STRUCTURE_STOCK = "Stock acquisition — carryover tax basis"
STRUCTURE_ASSET = "Asset acquisition or 338(h)(10) — stepped-up tax basis"
TAX_STRUCTURES = (STRUCTURE_STOCK, STRUCTURE_ASSET)

DEFAULT_MARGINAL_TAX_RATE = 0.25

# Balance sheet lines carried into the opening balance sheet, in presentation
# order, with the canonical key each is sourced from.
TANGIBLE_STEP_UP_KEYS = (
    ("inventory", "Inventory"),
    ("ppe_net", "Property, Plant & Equipment, net"),
    ("capitalized_software_net", "Capitalized Software, net"),
    ("other_non_current_assets", "Other Non-Current Assets"),
)

# The intangible classes a PPA normally identifies, with indicative lives. Fair
# values start at zero: the analyst supplies them from the valuation specialist's
# work, the engine does not invent them.
DEFAULT_INTANGIBLES: tuple[tuple[str, float, str], ...] = (
    ("Customer relationships", 10.0, "Multi-period excess earnings"),
    ("Developed technology", 7.0, "Relief-from-royalty"),
    ("Trade name / trademarks", 15.0, "Relief-from-royalty"),
    ("Non-compete agreements", 3.0, "With-and-without"),
    ("Order backlog", 1.0, "Multi-period excess earnings"),
    ("In-process research & development", 0.0, "Indefinite until completion"),
)

COL_ASSET = "Acquired Asset"
COL_BOOK = "Historical Book Value"
COL_STEP = "Fair Value Step-Up / (Down)"
COL_FAIR = "Day 1 Fair Value"

COL_INTANGIBLE = "Identifiable Intangible Asset"
COL_INT_FAIR = "Fair Value"
COL_INT_LIFE = "Useful Life (years)"
COL_INT_METHOD = "Valuation Method"
COL_INT_AMORT = "Annual Amortization"

TANGIBLE_COLUMNS = (COL_ASSET, COL_BOOK, COL_STEP, COL_FAIR)
INTANGIBLE_COLUMNS = (COL_INTANGIBLE, COL_INT_FAIR, COL_INT_LIFE, COL_INT_METHOD, COL_INT_AMORT)


# --------------------------------------------------------------------------- #
# Seeds
# --------------------------------------------------------------------------- #


def seed_tangible_frame(engagement: Engagement, period: str | None = None) -> pd.DataFrame:
    """Tangible step-up schedule seeded from the target's closing balance sheet."""
    target = period or engagement.latest_period
    records: list[dict[str, object]] = []
    for key, label in TANGIBLE_STEP_UP_KEYS:
        book = coalesce(engagement.fact(key, target))
        if book == 0.0:
            continue
        records.append(
            {COL_ASSET: label, COL_BOOK: book, COL_STEP: 0.0, COL_FAIR: book}
        )
    if not records:
        frame = pd.DataFrame(columns=list(TANGIBLE_COLUMNS))
        for column in (COL_BOOK, COL_STEP, COL_FAIR):
            frame[column] = frame[column].astype(float)
        return frame
    return pd.DataFrame(records, columns=list(TANGIBLE_COLUMNS))


def seed_intangible_frame() -> pd.DataFrame:
    """Identifiable intangible matrix with indicative lives and zero values."""
    return pd.DataFrame(
        [
            {
                COL_INTANGIBLE: name,
                COL_INT_FAIR: 0.0,
                COL_INT_LIFE: life,
                COL_INT_METHOD: method,
                COL_INT_AMORT: 0.0,
            }
            for name, life, method in DEFAULT_INTANGIBLES
        ],
        columns=list(INTANGIBLE_COLUMNS),
    )


# --------------------------------------------------------------------------- #
# Core computation
# --------------------------------------------------------------------------- #


@dataclass
class PPAAssumptions:
    consideration: float
    marginal_tax_rate: float
    tax_structure: str = STRUCTURE_STOCK
    goodwill_tax_deductible: bool = False


@dataclass
class PPAResult:
    consideration: float
    book_net_assets: float
    tangible_step_up: float
    intangible_fair_value: float
    deferred_tax_liability: float
    fair_value_identifiable_net_assets: float
    goodwill: float
    bargain_purchase_gain: float
    annual_intangible_amortization: float
    goodwill_percent_of_consideration: float
    bridge: pd.DataFrame
    steps: list[tuple[str, float]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def is_bargain_purchase(self) -> bool:
        return self.bargain_purchase_gain > 0.0


def _numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    if frame is None or frame.empty or column not in frame.columns:
        return pd.Series(dtype=float)
    return pd.to_numeric(frame[column], errors="coerce").fillna(0.0)


def tangible_step_up_total(tangible: pd.DataFrame) -> float:
    return math.fsum(float(value) for value in _numeric(tangible, COL_STEP))


def intangible_total(intangibles: pd.DataFrame) -> float:
    return math.fsum(float(value) for value in _numeric(intangibles, COL_INT_FAIR))


def annual_amortization(intangibles: pd.DataFrame) -> float:
    """Straight-line amortization of finite-lived intangibles.

    A zero or blank life denotes an indefinite-lived asset (in-process R&D until
    completion, certain trade names), which is not amortized under ASC 350-30-35.
    """
    if intangibles is None or intangibles.empty:
        return 0.0
    fair_values = _numeric(intangibles, COL_INT_FAIR)
    lives = _numeric(intangibles, COL_INT_LIFE)
    charges: list[float] = []
    for fair_value, life in zip(fair_values, lives):
        if float(life) > 0.0 and float(fair_value) != 0.0:
            charges.append(float(fair_value) / float(life))
    return math.fsum(charges)


def with_derived_columns(
    tangible: pd.DataFrame, intangibles: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Recompute the derived columns of both editors."""
    tangible_out = tangible.copy() if tangible is not None else pd.DataFrame()
    if not tangible_out.empty:
        tangible_out[COL_FAIR] = _numeric(tangible_out, COL_BOOK) + _numeric(
            tangible_out, COL_STEP
        )

    intangible_out = intangibles.copy() if intangibles is not None else pd.DataFrame()
    if not intangible_out.empty:
        fair_values = _numeric(intangible_out, COL_INT_FAIR)
        lives = _numeric(intangible_out, COL_INT_LIFE)
        intangible_out[COL_INT_AMORT] = [
            (float(value) / float(life)) if float(life) > 0.0 else 0.0
            for value, life in zip(fair_values, lives)
        ]
    return tangible_out, intangible_out


def book_net_assets(engagement: Engagement, period: str | None = None) -> float:
    """Historical book value of net assets acquired, on a cash-free debt-free view.

    Cash, funded debt and existing goodwill are excluded: cash and debt are
    settled at closing under the transaction's cash-free debt-free convention,
    and the seller's goodwill is never carried forward — it is replaced by the
    goodwill this allocation computes.
    """
    target = period or engagement.latest_period
    assets = math.fsum(
        coalesce(engagement.fact(key, target))
        for key in (
            "accounts_receivable",
            "inventory",
            "prepaid_expenses",
            "other_current_assets",
            "ppe_net",
            "capitalized_software_net",
            "other_non_current_assets",
        )
    )
    liabilities = math.fsum(
        coalesce(engagement.fact(key, target))
        for key in (
            "accounts_payable",
            "accrued_liabilities",
            "deferred_revenue_current",
            "income_taxes_payable",
            "other_current_liabilities",
            "deferred_revenue_non_current",
            "other_long_term_liabilities",
        )
    )
    return assets - liabilities


def run_ppa(
    engagement: Engagement,
    tangible: pd.DataFrame,
    intangibles: pd.DataFrame,
    assumptions: PPAAssumptions,
    period: str | None = None,
) -> PPAResult:
    """Allocate consideration to identifiable net assets and derive goodwill."""
    target = period or engagement.latest_period
    consideration = as_float(assumptions.consideration)
    notes: list[str] = []

    book_value = book_net_assets(engagement, target)
    step_up = tangible_step_up_total(tangible)
    intangible_value = intangible_total(intangibles)
    taxable_basis_difference = step_up + intangible_value

    if assumptions.tax_structure == STRUCTURE_ASSET:
        deferred_tax = 0.0
        notes.append(
            "Structured as a taxable asset acquisition (or a stock purchase with a "
            "338(h)(10)/336(e) election), so tax basis steps up alongside book basis and "
            "no deferred tax liability arises on the step-ups. The buyer also obtains "
            "amortizable goodwill under IRC section 197."
        )
    else:
        deferred_tax = taxable_basis_difference * assumptions.marginal_tax_rate
        notes.append(
            "Structured as a stock acquisition with carryover tax basis, so the fair value "
            "step-ups and recognised intangibles create a deferred tax liability at the "
            "marginal rate under ASC 740-10. The DTL reduces identifiable net assets and "
            "therefore increases goodwill dollar for dollar."
        )

    identifiable = book_value + step_up + intangible_value - deferred_tax
    residual = consideration - identifiable

    goodwill = residual if residual >= 0.0 else 0.0
    bargain_gain = -residual if residual < 0.0 else 0.0

    if bargain_gain > 0.0:
        notes.append(
            "Consideration is below the fair value of identifiable net assets, producing a "
            "bargain purchase gain. ASC 805-30-25-4 requires the acquirer to reassess "
            "whether all assets and liabilities have been identified and measured correctly "
            "before recognising any gain in earnings — a bargain purchase is rare and is "
            "usually an allocation error rather than a windfall."
        )
    if not assumptions.goodwill_tax_deductible and goodwill > 0.0:
        notes.append(
            "No deferred tax is recognised on goodwill itself. ASC 805-740-25-3 exempts the "
            "initial recognition of non-deductible goodwill, which is why goodwill is the "
            "residual rather than an input to the deferred tax calculation."
        )

    amortization = annual_amortization(intangibles)

    steps: list[tuple[str, float]] = [
        ("Consideration transferred", consideration),
        ("Less: book value of net assets acquired", -book_value),
        ("Less: fair value step-up on tangible assets", -step_up),
        ("Less: identifiable intangible assets recognised", -intangible_value),
        ("Plus: deferred tax liability on step-ups", deferred_tax),
    ]
    steps.append(
        ("Bargain purchase gain", -bargain_gain) if bargain_gain > 0.0 else ("Goodwill", goodwill)
    )

    bridge = pd.DataFrame(
        {"Amount": [amount for _, amount in steps]},
        index=[label for label, _ in steps],
    )
    bridge.index.name = "Purchase Price Allocation"

    return PPAResult(
        consideration=consideration,
        book_net_assets=book_value,
        tangible_step_up=step_up,
        intangible_fair_value=intangible_value,
        deferred_tax_liability=deferred_tax,
        fair_value_identifiable_net_assets=identifiable,
        goodwill=goodwill,
        bargain_purchase_gain=bargain_gain,
        annual_intangible_amortization=amortization,
        goodwill_percent_of_consideration=safe_divide(goodwill, consideration) * 100.0,
        bridge=bridge,
        steps=steps,
        notes=notes,
    )


# --------------------------------------------------------------------------- #
# Opening balance sheet
# --------------------------------------------------------------------------- #


def opening_balance_sheet(
    engagement: Engagement,
    tangible: pd.DataFrame,
    intangibles: pd.DataFrame,
    result: PPAResult,
    period: str | None = None,
) -> pd.DataFrame:
    """Historical book value against the Day 1 fair value opening balance sheet."""
    target = period or engagement.latest_period

    step_by_label: dict[str, float] = {}
    if tangible is not None and not tangible.empty:
        steps = _numeric(tangible, COL_STEP)
        for label, value in zip(tangible[COL_ASSET], steps):
            step_by_label[str(label)] = step_by_label.get(str(label), 0.0) + float(value)

    def book(key: str) -> float:
        return coalesce(engagement.fact(key, target))

    rows: list[tuple[str, float, float, str]] = []

    def add(label: str, book_value: float, fair_value: float, note: str = "") -> None:
        rows.append((label, book_value, fair_value, note))

    add("Cash & cash equivalents", book("cash_and_equivalents"), 0.0,
        "Excluded — cash-free / debt-free")
    add("Accounts receivable, net", book("accounts_receivable"), book("accounts_receivable"),
        "Carried at fair value; already net of credit losses")

    for key, label in TANGIBLE_STEP_UP_KEYS:
        book_value = book(key)
        if book_value == 0.0 and step_by_label.get(label, 0.0) == 0.0:
            continue
        step = step_by_label.get(label, 0.0)
        note = "Stepped up to fair value" if step else "No step-up applied"
        add(label, book_value, book_value + step, note)

    add("Prepaid expenses", book("prepaid_expenses"), book("prepaid_expenses"), "")

    if intangibles is not None and not intangibles.empty:
        fair_values = _numeric(intangibles, COL_INT_FAIR)
        lives = _numeric(intangibles, COL_INT_LIFE)
        for label, value, life in zip(intangibles[COL_INTANGIBLE], fair_values, lives):
            if float(value) == 0.0:
                continue
            descriptor = (
                f"Recognised on acquisition; {float(life):,.1f}-year life"
                if float(life) > 0.0
                else "Recognised on acquisition; indefinite-lived"
            )
            add(f"Intangible — {label}", 0.0, float(value), descriptor)

    add(
        "Goodwill",
        book("goodwill_and_intangibles_net"),
        result.goodwill,
        "Seller's goodwill eliminated; residual recognised",
    )

    add("Accounts payable", -book("accounts_payable"), -book("accounts_payable"), "")
    add("Accrued liabilities", -book("accrued_liabilities"), -book("accrued_liabilities"), "")
    deferred_revenue = book("deferred_revenue_current") + book("deferred_revenue_non_current")
    if deferred_revenue:
        add(
            "Deferred revenue",
            -deferred_revenue,
            -deferred_revenue,
            "ASC 606 measurement applies post-ASU 2021-08; a legacy fair value haircut "
            "would reduce this balance",
        )
    other_current = book("other_current_liabilities") + book("income_taxes_payable")
    if other_current:
        add("Other current liabilities", -other_current, -other_current, "")
    other_long = book("other_long_term_liabilities")
    if other_long:
        add("Other long-term liabilities", -other_long, -other_long, "")

    add("Funded debt", -(
        book("short_term_debt")
        + book("current_portion_long_term_debt")
        + book("long_term_debt")
    ), 0.0, "Excluded — cash-free / debt-free")

    if result.deferred_tax_liability:
        add(
            "Deferred tax liability on step-ups",
            0.0,
            -result.deferred_tax_liability,
            "Recognised under ASC 740-10 on carryover tax basis",
        )

    book_total = math.fsum(row[1] for row in rows)
    fair_total = math.fsum(row[2] for row in rows)
    rows.append(("Net assets acquired", book_total, fair_total, ""))

    frame = pd.DataFrame(
        {
            "Historical Book Value": [row[1] for row in rows],
            "Day 1 Fair Value": [row[2] for row in rows],
            "Fair Value Adjustment": [row[2] - row[1] for row in rows],
            "Basis of Measurement": [row[3] for row in rows],
        },
        index=[row[0] for row in rows],
    )
    frame.index.name = "Opening Balance Sheet — ASC 805"
    return frame


def amortization_schedule(intangibles: pd.DataFrame, horizon_years: int = 10) -> pd.DataFrame:
    """Forward amortization of the recognised intangibles, by year.

    The post-close earnings drag from intangible amortization is routinely
    omitted from a buyer's model and then surprises them in year one.
    """
    if intangibles is None or intangibles.empty:
        return pd.DataFrame()

    fair_values = _numeric(intangibles, COL_INT_FAIR)
    lives = _numeric(intangibles, COL_INT_LIFE)
    labels = [str(label) for label in intangibles[COL_INTANGIBLE]]

    horizon = max(1, int(horizon_years))
    years = [f"Year {index}" for index in range(1, horizon + 1)]
    records: dict[str, list[float]] = {}

    for label, value, life in zip(labels, fair_values, lives):
        value = float(value)
        life = float(life)
        if value == 0.0:
            continue
        if life <= 0.0:
            records[f"{label} (indefinite-lived)"] = [0.0] * horizon
            continue
        annual = value / life
        records[label] = [annual if index + 1 <= life else 0.0 for index in range(horizon)]

    if not records:
        return pd.DataFrame()

    frame = pd.DataFrame(records, index=years).transpose()
    frame.loc["Total annual amortization"] = [
        math.fsum(frame[column].tolist()) for column in frame.columns
    ]
    frame.index.name = "Intangible Amortization Schedule"
    return frame


def import_from_workspace(valuation: dict | None, nwc_actual: float) -> tuple[float, float]:
    """Pull the consideration and acquired working capital from Module 1.

    Prefers the enterprise value produced by the DCF and value bridge; falls
    back to implied equity value when enterprise value is unavailable.
    """
    if not valuation:
        return NAN, as_float(nwc_actual)
    enterprise = as_float(valuation.get("enterprise_value"))
    if is_missing(enterprise):
        enterprise = as_float(valuation.get("equity_value"))
    return enterprise, as_float(nwc_actual)
