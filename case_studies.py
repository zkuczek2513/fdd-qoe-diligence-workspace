"""Sandbox case library and the work comparison engine.

Each case hardcodes the primitive line items of a realistic transaction. The
builder derives every subtotal, derives cash as the balancing plug from the
accounting identity, and derives the indirect cash flow statement from the
period-over-period balance sheet movements. Because cash is the plug and
retained earnings rolls forward on net income less distributions, the balance
sheet balances and the cash flow statement foots by construction rather than by
hand-tuning — see ``validate_case`` for the assertions that prove it.

Each case also carries a hidden answer key: the adjustments the engagement team
actually booked, the items they classified as debt-like or non-operating, the
risks they flagged, and the narrative summary of the issued FDD report.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import pandas as pd

from config import ADJUSTMENT_STATUSES, DEBT_LIKE, NON_OPERATING_ASSET
from finance_logic import (
    NAN,
    Adjustment,
    ClassifiedItem,
    Engagement,
    RiskFlag,
    adjusted_ebitda,
    is_missing,
    reported_ebitda,
    safe_divide,
)

ACCEPTED = ADJUSTMENT_STATUSES[0]
REJECTED = ADJUSTMENT_STATUSES[1]

# Balance sheet primitives that the builder consumes. Cash and retained earnings
# are deliberately absent: both are derived.
_BALANCE_INPUT_KEYS = (
    "accounts_receivable",
    "inventory",
    "prepaid_expenses",
    "other_current_assets",
    "ppe_net",
    "capitalized_software_net",
    "goodwill_and_intangibles_net",
    "other_non_current_assets",
    "accounts_payable",
    "accrued_liabilities",
    "deferred_revenue_current",
    "income_taxes_payable",
    "short_term_debt",
    "current_portion_long_term_debt",
    "other_current_liabilities",
    "long_term_debt",
    "deferred_revenue_non_current",
    "other_long_term_liabilities",
    "contributed_capital",
)

_NON_CASH_ASSET_KEYS = (
    "accounts_receivable",
    "inventory",
    "prepaid_expenses",
    "other_current_assets",
    "ppe_net",
    "capitalized_software_net",
    "goodwill_and_intangibles_net",
    "other_non_current_assets",
)

_LIABILITY_KEYS = (
    "accounts_payable",
    "accrued_liabilities",
    "deferred_revenue_current",
    "income_taxes_payable",
    "short_term_debt",
    "current_portion_long_term_debt",
    "other_current_liabilities",
    "long_term_debt",
    "deferred_revenue_non_current",
    "other_long_term_liabilities",
)

_DEBT_KEYS = ("short_term_debt", "current_portion_long_term_debt", "long_term_debt")


# --------------------------------------------------------------------------- #
# Case definitions
# --------------------------------------------------------------------------- #

PROJECT_HELIOS: dict[str, Any] = {
    "key": "helios",
    "name": "Project Helios",
    "target": "Helios Practice Systems, Inc.",
    "sector": "Vertical SaaS — Dental Practice Management Software",
    "deal_type": "Buy-side financial due diligence — growth equity recapitalization",
    "currency": "USD",
    "seed_period": "FY2021",
    "periods": ["FY2022", "FY2023", "FY2024"],
    "context": """
**Sponsor.** Meridian Growth Partners (MGP), a $2.4bn software-focused growth equity fund.

**Transaction.** MGP has signed a letter of intent to acquire a 68% controlling interest in
Helios Practice Systems for an enterprise value of **$62.0 million**, struck at **12.5x**
management's represented FY2024 Adjusted EBITDA of **$4.96 million**. The transaction is
structured cash-free and debt-free with a net working capital peg. This is the company's first
institutional capital; the founder-CEO will roll 32% and remain in seat for 24 months.

**Target.** Helios sells a cloud practice-management and revenue-cycle platform to
multi-location dental service organizations. 1,240 practice locations are live on the platform.
Revenue is 88% subscription with the balance in implementation and training services. The
company has never been audited — FY2022 and FY2023 were reviewed by a regional firm, and
FY2024 is management-prepared only.

**Scope.** MGP has engaged you to perform a Quality of Earnings analysis over the FY2022–FY2024
period, assess net working capital and propose a peg, and identify debt-like items and
non-operating assets for the value bridge. Particular attention has been requested on revenue
recognition, given the company's shift to multi-year prepaid enterprise agreements in FY2024,
and on the capitalized software balance, which has grown 194% over the diligence period.
""",
    "income_statement": {
        "revenue": {"FY2022": 33_300_000.0, "FY2023": 42_350_000.0, "FY2024": 51_200_000.0},
        "cost_of_revenue": {"FY2022": 8_325_000.0, "FY2023": 10_164_000.0, "FY2024": 12_288_000.0},
        "selling_and_marketing": {
            "FY2022": 9_990_000.0,
            "FY2023": 12_705_000.0,
            "FY2024": 15_872_000.0,
        },
        "research_and_development": {
            "FY2022": 6_660_000.0,
            "FY2023": 8_047_000.0,
            "FY2024": 9_728_000.0,
        },
        "general_and_administrative": {
            "FY2022": 5_328_000.0,
            "FY2023": 6_352_500.0,
            "FY2024": 8_704_000.0,
        },
        "other_operating_expense": {"FY2022": 0.0, "FY2023": 0.0, "FY2024": 0.0},
        "depreciation": {"FY2022": 620_000.0, "FY2023": 690_000.0, "FY2024": 780_000.0},
        "amortization_software": {
            "FY2022": 712_000.0,
            "FY2023": 1_004_000.0,
            "FY2024": 1_268_000.0,
        },
        "amortization_intangibles": {"FY2022": 0.0, "FY2023": 0.0, "FY2024": 180_000.0},
        "interest_expense": {"FY2022": 520_000.0, "FY2023": 680_000.0, "FY2024": 1_150_000.0},
        "other_income_expense": {"FY2022": -250_000.0, "FY2023": 400_000.0, "FY2024": -180_000.0},
        "income_tax_expense": {"FY2022": 0.0, "FY2023": 0.0, "FY2024": 320_000.0},
    },
    "balance_sheet": {
        "accounts_receivable": {
            "FY2021": 3_200_000.0,
            "FY2022": 4_100_000.0,
            "FY2023": 5_900_000.0,
            "FY2024": 8_650_000.0,
        },
        "inventory": {"FY2021": 0.0, "FY2022": 0.0, "FY2023": 0.0, "FY2024": 0.0},
        "prepaid_expenses": {
            "FY2021": 720_000.0,
            "FY2022": 900_000.0,
            "FY2023": 1_150_000.0,
            "FY2024": 1_480_000.0,
        },
        "other_current_assets": {
            "FY2021": 500_000.0,
            "FY2022": 620_000.0,
            "FY2023": 780_000.0,
            "FY2024": 960_000.0,
        },
        "ppe_net": {
            "FY2021": 1_600_000.0,
            "FY2022": 1_850_000.0,
            "FY2023": 2_100_000.0,
            "FY2024": 2_450_000.0,
        },
        "capitalized_software_net": {
            "FY2021": 2_100_000.0,
            "FY2022": 3_200_000.0,
            "FY2023": 6_200_000.0,
            "FY2024": 9_400_000.0,
        },
        "goodwill_and_intangibles_net": {
            "FY2021": 2_400_000.0,
            "FY2022": 2_400_000.0,
            "FY2023": 2_400_000.0,
            "FY2024": 5_900_000.0,
        },
        "other_non_current_assets": {
            "FY2021": 420_000.0,
            "FY2022": 480_000.0,
            "FY2023": 520_000.0,
            "FY2024": 610_000.0,
        },
        "accounts_payable": {
            "FY2021": 1_050_000.0,
            "FY2022": 1_250_000.0,
            "FY2023": 1_480_000.0,
            "FY2024": 1_720_000.0,
        },
        "accrued_liabilities": {
            "FY2021": 1_750_000.0,
            "FY2022": 2_100_000.0,
            "FY2023": 2_650_000.0,
            "FY2024": 3_180_000.0,
        },
        "deferred_revenue_current": {
            "FY2021": 7_600_000.0,
            "FY2022": 9_800_000.0,
            "FY2023": 12_400_000.0,
            "FY2024": 14_900_000.0,
        },
        "income_taxes_payable": {
            "FY2021": 0.0,
            "FY2022": 0.0,
            "FY2023": 0.0,
            "FY2024": 180_000.0,
        },
        "short_term_debt": {
            "FY2021": 0.0,
            "FY2022": 0.0,
            "FY2023": 0.0,
            "FY2024": 3_400_000.0,
        },
        "current_portion_long_term_debt": {
            "FY2021": 1_000_000.0,
            "FY2022": 1_000_000.0,
            "FY2023": 1_000_000.0,
            "FY2024": 1_500_000.0,
        },
        "other_current_liabilities": {
            "FY2021": 380_000.0,
            "FY2022": 430_000.0,
            "FY2023": 510_000.0,
            "FY2024": 640_000.0,
        },
        "long_term_debt": {
            "FY2021": 4_000_000.0,
            "FY2022": 5_500_000.0,
            "FY2023": 7_000_000.0,
            "FY2024": 9_000_000.0,
        },
        "deferred_revenue_non_current": {
            "FY2021": 1_500_000.0,
            "FY2022": 1_900_000.0,
            "FY2023": 2_300_000.0,
            "FY2024": 2_650_000.0,
        },
        "other_long_term_liabilities": {
            "FY2021": 560_000.0,
            "FY2022": 640_000.0,
            "FY2023": 700_000.0,
            "FY2024": 820_000.0,
        },
        "contributed_capital": {
            "FY2021": 6_000_000.0,
            "FY2022": 6_260_000.0,
            "FY2023": 6_610_000.0,
            "FY2024": 7_040_000.0,
        },
    },
    "seed_cash": 2_800_000.0,
    "stock_based_compensation": {"FY2022": 260_000.0, "FY2023": 350_000.0, "FY2024": 720_000.0},
    "distributions": {"FY2022": 0.0, "FY2023": 0.0, "FY2024": 0.0},
    "management_adjusted_ebitda": {"FY2024": 4_963_000.0},
    "answer_key": [
        {
            "label": "Revenue cut-off — multi-year prepaid contracts recognized on invoice",
            "category": "Cut-Off & Revenue Recognition",
            "status": ACCEPTED,
            "authority": "ASC 606-10-25-27 (transfer of control over time)",
            "impacts": {"FY2022": 0.0, "FY2023": -620_000.0, "FY2024": -1_850_000.0},
            "rationale": (
                "Fourteen enterprise agreements signed in FY2024 carry 24- to 36-month prepaid "
                "terms. The company recognized the full invoice on execution rather than "
                "ratably over the service period. Because the platform is a stand-ready "
                "obligation satisfied over time, the unearned portion must be deferred. The "
                "restatement removes $1.85m of FY2024 revenue and a corresponding $1.85m of "
                "EBITDA, and increases the deferred revenue balance the buyer inherits."
            ),
        },
        {
            "label": "Over-capitalized internal-use software reclassified to research and development",
            "category": "Accounting & GAAP Correction",
            "status": ACCEPTED,
            "authority": "ASC 350-40-25 (application development stage criteria)",
            "impacts": {"FY2022": -1_100_000.0, "FY2023": -1_600_000.0, "FY2024": -2_300_000.0},
            "rationale": (
                "Sprint-level review of the engineering time-tracking export shows that "
                "post-implementation maintenance, bug remediation and customer-specific "
                "configuration were capitalized alongside genuine application development. "
                "Only costs incurred during the application development stage qualify. "
                "Reclassifying the ineligible portion to operating expense reduces EBITDA and "
                "is the single largest driver of the gap to management's number."
            ),
        },
        {
            "label": "Excess owner and related-party compensation normalized to market",
            "category": "Owner / Management Compensation",
            "status": ACCEPTED,
            "authority": "Normalization to third-party market compensation study",
            "impacts": {"FY2022": 980_000.0, "FY2023": 1_050_000.0, "FY2024": 1_150_000.0},
            "rationale": (
                "The founder-CEO drew $1.9m in FY2024 against a market benchmark of $0.95m for "
                "a company of this scale, and the founder's spouse was carried on payroll at "
                "$0.2m in a non-working capacity. The add-back normalizes both to the "
                "post-close employment agreement."
            ),
        },
        {
            "label": "Non-recurring transaction, audit-readiness and sell-side preparation costs",
            "category": "Non-Recurring / One-Time",
            "status": ACCEPTED,
            "authority": "Non-recurring by nature; will not burden the go-forward entity",
            "impacts": {"FY2022": 0.0, "FY2023": 260_000.0, "FY2024": 840_000.0},
            "rationale": (
                "Investment banking retainer, sell-side QoE fees, first-time audit readiness "
                "work and legal diligence support. These are transaction costs and do not "
                "recur post-close."
            ),
        },
        {
            "label": "One-time patent litigation settlement and related legal fees",
            "category": "Non-Recurring / One-Time",
            "status": ACCEPTED,
            "authority": "Settled and released; no ongoing exposure",
            "impacts": {"FY2022": 0.0, "FY2023": 0.0, "FY2024": 1_200_000.0},
            "rationale": (
                "Settlement of a non-practicing-entity claim over the appointment-reminder "
                "module, plus outside counsel fees. A full release was executed in September "
                "2024 and no further exposure exists."
            ),
        },
        {
            "label": "Allowance for credit losses shortfall on aged receivables",
            "category": "Reserve & Allowance Adequacy",
            "status": ACCEPTED,
            "authority": "ASC 326-20 (current expected credit losses)",
            "impacts": {"FY2022": 0.0, "FY2023": -180_000.0, "FY2024": -480_000.0},
            "rationale": (
                "The >120-day receivable bucket grew from $0.3m to $1.9m while the allowance "
                "was held flat at $0.35m. Applying the company's own historical loss rates to "
                "the current aging produces a required allowance of $0.83m; the shortfall is "
                "charged against the period in which the receivables aged."
            ),
        },
        {
            "label": "Stock-based compensation add-back",
            "category": "Other Normalization",
            "status": ACCEPTED,
            "authority": "Non-cash charge; presented consistently with sponsor convention",
            "impacts": {"FY2022": 260_000.0, "FY2023": 350_000.0, "FY2024": 720_000.0},
            "rationale": (
                "Non-cash equity compensation is added back consistent with the sponsor's "
                "reporting convention. Note this is a presentation choice, not a GAAP "
                "correction — the dilution is real and belongs in the equity story rather "
                "than the earnings story."
            ),
        },
        {
            "label": "Run-rate pricing uplift on the announced 2025 list price increase",
            "category": "Run-Rate / Pro Forma",
            "status": REJECTED,
            "authority": "Rejected — no executed contracts or realized evidence",
            "impacts": {"FY2022": 0.0, "FY2023": 0.0, "FY2024": 2_400_000.0},
            "rationale": (
                "Management proposed annualizing a 7% list price increase announced in "
                "November 2024. No amended contracts were executed as of the diligence cut-off, "
                "the increase applies only on renewal, and the company has no realized history "
                "of pushing price at this magnitude without churn. Rejected in full and "
                "reframed as an upside case rather than an earnings adjustment."
            ),
        },
    ],
    "debt_like_key": [
        {
            "label": "Non-current deferred revenue — cash collected against unperformed obligations",
            "amount": 2_650_000.0,
            "classification": DEBT_LIKE,
            "rationale": (
                "The buyer inherits an obligation to deliver 12+ months of service for which "
                "the seller has already collected cash. Treated as debt-like at the estimated "
                "cost to fulfil rather than face value in the final report."
            ),
        },
        {
            "label": "Deferred purchase price and earnout on the Q4 tuck-in acquisition",
            "amount": 1_450_000.0,
            "classification": DEBT_LIKE,
            "rationale": (
                "Fixed deferred consideration payable in two instalments through 2026, plus "
                "the probability-weighted earnout. A committed cash obligation of the seller "
                "that survives closing."
            ),
        },
        {
            "label": "Accrued paid-time-off and unfunded bonus liability",
            "amount": 640_000.0,
            "classification": DEBT_LIKE,
            "rationale": (
                "Accrued but unpaid PTO and the FY2024 discretionary bonus pool, neither of "
                "which is funded. Payable in cash by the buyer in the first post-close quarter."
            ),
        },
        {
            "label": "Uninsured litigation reserve shortfall identified in diligence",
            "amount": 350_000.0,
            "classification": DEBT_LIKE,
            "rationale": (
                "A former reseller's breach-of-contract claim is not covered by the company's "
                "policy and carries no recorded reserve. Counsel's estimated exposure is "
                "treated as debt-like."
            ),
        },
        {
            "label": "Cash surrender value of founder key-person life insurance",
            "amount": 310_000.0,
            "classification": NON_OPERATING_ASSET,
            "rationale": (
                "Held within other non-current assets. The policy is not required to operate "
                "the platform and is realizable in cash; added back in the bridge."
            ),
        },
        {
            "label": "Refundable state research and development tax credit receivable",
            "amount": 240_000.0,
            "classification": NON_OPERATING_ASSET,
            "rationale": (
                "A filed and refundable state credit unrelated to operations. Collectible "
                "within 12 months and added to the bridge as a non-operating asset."
            ),
        },
    ],
    "risk_key": [
        {
            "title": "Revenue recognition policy fails ASC 606 for multi-year prepaid arrangements",
            "severity": "Critical",
            "description": (
                "The FY2024 shift to prepaid enterprise agreements was not accompanied by a "
                "policy update. This is a restatement-level issue that will surface in the "
                "first post-close audit."
            ),
        },
        {
            "title": "Quality-adjusted earnings declined year over year despite 21% revenue growth",
            "severity": "Critical",
            "description": (
                "Adjusted EBITDA falls from $4.342m in FY2023 to $3.888m in FY2024, driven by "
                "accelerating over-capitalization and cut-off errors. The reported growth story "
                "does not survive normalization."
            ),
        },
        {
            "title": "Internal-use software capitalization policy is not supportable",
            "severity": "High",
            "description": (
                "Capitalized software grew 194% while the engineering headcount grew 61%. The "
                "capitalization rate is now 34% of gross R&D spend against a 12-18% peer range."
            ),
        },
        {
            "title": "Days sales outstanding deteriorated by 11.8 days across the diligence period",
            "severity": "High",
            "description": (
                "DSO extended from 40.0 to 51.9 days on average balances. Collection "
                "performance is deteriorating faster than revenue is growing."
            ),
        },
        {
            "title": "No audited financial statements exist for any diligence period",
            "severity": "High",
            "description": (
                "FY2022 and FY2023 were reviewed only; FY2024 is management-prepared. There is "
                "no independent attestation over any period in the QoE."
            ),
        },
        {
            "title": "Customer concentration in the top five dental service organizations",
            "severity": "Medium",
            "description": (
                "The five largest DSO customers represent 38% of ARR, and the largest single "
                "relationship is on a contract that expires within the earnout period."
            ),
        },
        {
            "title": "Founder-led control environment with no segregation of duties over billing",
            "severity": "Medium",
            "description": (
                "The same individual originates contracts, raises invoices and approves credit "
                "memos. This is the control weakness that permitted the cut-off errors."
            ),
        },
    ],
    "fdd_report_summary": """
### Project Helios — Report of Factual Findings, Executive Summary

**Conclusion on earnings quality.** Management represented FY2024 Adjusted EBITDA of
**$4.963 million**. Our procedures support **$3.888 million**, a shortfall of **$1.075 million
(21.7%)**. At the LOI's 12.5x multiple, the finding implies a headline enterprise value
reduction of approximately **$13.4 million**, from $62.0 million to $48.6 million, before any
value bridge effects.

**The gap is not a rounding difference; it is directional.** Two GAAP corrections drive it. The
company recognized $1.85 million of multi-year prepaid contract revenue on invoice rather than
over the service period, and capitalized $2.30 million of software costs that fail the ASC
350-40 application development criteria. Neither is a matter of judgment at the margin — both
are policy failures that will be corrected in the first audited period regardless of the
transaction.

**Earnings quality is deteriorating, not improving.** On a normalized basis Adjusted EBITDA
declines from $4.342 million in FY2023 to $3.888 million in FY2024 while reported revenue grows
20.9%. The entire reported margin expansion is attributable to the two accounting errors above.
We do not believe the FY2024 trajectory supports a growth multiple.

**Adjustments rejected.** We declined management's proposed $2.40 million run-rate pricing
uplift in full. No contracts had been amended at the diligence cut-off, the increase applies
only on renewal, and the company has no realized precedent for price action at this magnitude.
We have presented it separately as a sensitivity.

**Working capital.** Net working capital is structurally negative — a normal and favourable
feature of a prepaid subscription model — but the trend is unfavourable. DSO extended 11.8 days
over the period while deferred revenue growth decelerated. We recommend a peg set on the
trailing twelve-month average rather than the year-end balance, which flatters the seller.

**Value bridge.** We identified **$5.09 million** of debt-like items, of which the non-current
deferred revenue balance ($2.65 million) and the tuck-in deferred purchase price ($1.45 million)
are the largest, against **$0.55 million** of non-operating assets.

**Recommendation.** Reprice, and restructure. We recommend (i) resetting the multiple to
reflect flat-to-declining normalized earnings, (ii) a specific indemnity for the revenue
recognition exposure surviving through the first audited period, and (iii) shifting a meaningful
portion of consideration into an earnout tied to *audited* Adjusted EBITDA rather than a
management-prepared figure.
""",
}


PROJECT_ANVIL: dict[str, Any] = {
    "key": "anvil",
    "name": "Project Anvil",
    "target": "Anvil Precision Components, LLC",
    "sector": "Industrial Manufacturing — Precision-Machined Components",
    "deal_type": "Buy-side financial due diligence — control buyout of a family-owned business",
    "currency": "USD",
    "seed_period": "FY2021",
    "periods": ["FY2022", "FY2023", "FY2024"],
    "context": """
**Sponsor.** Kettle Creek Industrial Partners, a lower-middle-market buyout firm specializing
in engineered components.

**Transaction.** Kettle Creek has an exclusivity agreement to acquire 100% of Anvil Precision
Components at an enterprise value of **$96.0 million**, struck at **6.0x** management's
represented FY2024 Adjusted EBITDA of **$16.0 million**. The business is second-generation
family-owned and taxed as an S-corporation; three of the five manufacturing facilities are
leased from an entity owned by the selling family.

**Target.** Anvil machines close-tolerance components for off-highway equipment, agricultural
machinery and hydraulic systems across five plants in Illinois, Wisconsin and Indiana. FY2024
saw a sharp industry-wide destocking cycle: reported revenue fell 4.6% while inventory rose
12.9%.

**Scope.** Kettle Creek has asked for a Quality of Earnings analysis over FY2022–FY2024, a net
working capital analysis with a proposed peg, and a debt-like item schedule. The firm has
specifically flagged three areas: the adequacy of the inventory obsolescence reserve given the
build, the related-party lease arrangements, and the sustainability of the FY2023 peak. The
company's lender has issued a covenant waiver for the FY2024 measurement date, the terms of
which have been made available in the data room.
""",
    "income_statement": {
        "revenue": {"FY2022": 118_400_000.0, "FY2023": 137_900_000.0, "FY2024": 131_600_000.0},
        "cost_of_revenue": {
            "FY2022": 91_168_000.0,
            "FY2023": 104_804_000.0,
            "FY2024": 102_648_000.0,
        },
        "selling_and_marketing": {
            "FY2022": 4_736_000.0,
            "FY2023": 5_240_000.0,
            "FY2024": 5_264_000.0,
        },
        "research_and_development": {"FY2022": 0.0, "FY2023": 0.0, "FY2024": 0.0},
        "general_and_administrative": {
            "FY2022": 12_432_000.0,
            "FY2023": 13_790_000.0,
            "FY2024": 14_476_000.0,
        },
        "other_operating_expense": {"FY2022": 0.0, "FY2023": 0.0, "FY2024": 1_850_000.0},
        "depreciation": {
            "FY2022": 5_920_000.0,
            "FY2023": 6_205_500.0,
            "FY2024": 6_580_000.0,
        },
        "amortization_software": {"FY2022": 0.0, "FY2023": 0.0, "FY2024": 0.0},
        "amortization_intangibles": {
            "FY2022": 900_000.0,
            "FY2023": 900_000.0,
            "FY2024": 900_000.0,
        },
        "interest_expense": {
            "FY2022": 2_160_000.0,
            "FY2023": 2_830_000.0,
            "FY2024": 4_480_000.0,
        },
        "other_income_expense": {
            "FY2022": 1_400_000.0,
            "FY2023": 250_000.0,
            "FY2024": -420_000.0,
        },
        "income_tax_expense": {"FY2022": 0.0, "FY2023": 0.0, "FY2024": 0.0},
    },
    "balance_sheet": {
        "accounts_receivable": {
            "FY2021": 16_800_000.0,
            "FY2022": 19_400_000.0,
            "FY2023": 23_600_000.0,
            "FY2024": 24_900_000.0,
        },
        "inventory": {
            "FY2021": 22_500_000.0,
            "FY2022": 27_800_000.0,
            "FY2023": 34_200_000.0,
            "FY2024": 38_600_000.0,
        },
        "prepaid_expenses": {
            "FY2021": 1_200_000.0,
            "FY2022": 1_380_000.0,
            "FY2023": 1_560_000.0,
            "FY2024": 1_640_000.0,
        },
        "other_current_assets": {
            "FY2021": 890_000.0,
            "FY2022": 940_000.0,
            "FY2023": 1_050_000.0,
            "FY2024": 1_180_000.0,
        },
        "ppe_net": {
            "FY2021": 41_000_000.0,
            "FY2022": 43_500_000.0,
            "FY2023": 46_800_000.0,
            "FY2024": 48_200_000.0,
        },
        "capitalized_software_net": {
            "FY2021": 0.0,
            "FY2022": 0.0,
            "FY2023": 0.0,
            "FY2024": 0.0,
        },
        "goodwill_and_intangibles_net": {
            "FY2021": 14_600_000.0,
            "FY2022": 13_700_000.0,
            "FY2023": 12_800_000.0,
            "FY2024": 11_900_000.0,
        },
        "other_non_current_assets": {
            "FY2021": 2_100_000.0,
            "FY2022": 2_250_000.0,
            "FY2023": 2_400_000.0,
            "FY2024": 2_580_000.0,
        },
        "accounts_payable": {
            "FY2021": 12_400_000.0,
            "FY2022": 14_100_000.0,
            "FY2023": 16_900_000.0,
            "FY2024": 15_200_000.0,
        },
        "accrued_liabilities": {
            "FY2021": 5_600_000.0,
            "FY2022": 6_200_000.0,
            "FY2023": 7_100_000.0,
            "FY2024": 6_800_000.0,
        },
        "deferred_revenue_current": {
            "FY2021": 0.0,
            "FY2022": 0.0,
            "FY2023": 0.0,
            "FY2024": 0.0,
        },
        "income_taxes_payable": {"FY2021": 0.0, "FY2022": 0.0, "FY2023": 0.0, "FY2024": 0.0},
        "short_term_debt": {
            "FY2021": 8_000_000.0,
            "FY2022": 15_000_000.0,
            "FY2023": 25_200_000.0,
            "FY2024": 41_000_000.0,
        },
        "current_portion_long_term_debt": {
            "FY2021": 3_000_000.0,
            "FY2022": 3_000_000.0,
            "FY2023": 3_000_000.0,
            "FY2024": 3_000_000.0,
        },
        "other_current_liabilities": {
            "FY2021": 1_450_000.0,
            "FY2022": 1_580_000.0,
            "FY2023": 1_720_000.0,
            "FY2024": 1_890_000.0,
        },
        "long_term_debt": {
            "FY2021": 22_000_000.0,
            "FY2022": 21_000_000.0,
            "FY2023": 20_000_000.0,
            "FY2024": 19_000_000.0,
        },
        "deferred_revenue_non_current": {
            "FY2021": 0.0,
            "FY2022": 0.0,
            "FY2023": 0.0,
            "FY2024": 0.0,
        },
        "other_long_term_liabilities": {
            "FY2021": 3_200_000.0,
            "FY2022": 3_450_000.0,
            "FY2023": 3_700_000.0,
            "FY2024": 4_100_000.0,
        },
        "contributed_capital": {
            "FY2021": 5_000_000.0,
            "FY2022": 5_000_000.0,
            "FY2023": 5_000_000.0,
            "FY2024": 5_000_000.0,
        },
    },
    "seed_cash": 3_500_000.0,
    "stock_based_compensation": {"FY2022": 0.0, "FY2023": 0.0, "FY2024": 0.0},
    "distributions": {"FY2022": 2_800_000.0, "FY2023": 4_200_000.0, "FY2024": 1_500_000.0},
    "management_adjusted_ebitda": {"FY2024": 16_000_000.0},
    "answer_key": [
        {
            "label": "Inventory obsolescence reserve shortfall on aged and slow-moving stock",
            "category": "Reserve & Allowance Adequacy",
            "status": ACCEPTED,
            "authority": "ASC 330-10-35 (lower of cost or net realizable value)",
            "impacts": {"FY2022": -450_000.0, "FY2023": -1_300_000.0, "FY2024": -3_850_000.0},
            "rationale": (
                "The reserve methodology has not been revised since 2019 and applies a flat 2% "
                "of gross inventory. Aging the perpetual file shows $9.4m of stock with no "
                "movement in 24 months, largely tooling and work-in-process for two "
                "discontinued OEM programs. Applying the company's own realized recovery rates "
                "on prior disposals produces a required reserve of $4.62m against $0.77m "
                "recorded. The shortfall is allocated to the periods in which the stock aged."
            ),
        },
        {
            "label": "Physical-to-perpetual inventory cut-off adjustment at year end",
            "category": "Cut-Off & Revenue Recognition",
            "status": ACCEPTED,
            "authority": "ASC 330; cut-off testing at the December count",
            "impacts": {"FY2022": 0.0, "FY2023": -620_000.0, "FY2024": -1_150_000.0},
            "rationale": (
                "Goods received in the final week of the year were recorded in inventory but "
                "the corresponding payable was not booked until January. Testing of the "
                "receiving log against the December count identified $1.15m of FY2024 "
                "unrecorded cost of sales."
            ),
        },
        {
            "label": "Related-party facility leases normalized to market rent",
            "category": "Related-Party Normalization",
            "status": ACCEPTED,
            "authority": "Third-party appraisal of market rent across three leased plants",
            "impacts": {"FY2022": 1_680_000.0, "FY2023": 1_680_000.0, "FY2024": 1_680_000.0},
            "rationale": (
                "Three of five plants are leased from an entity owned by the selling family at "
                "rents materially above an independent appraisal of market. Normalizing to "
                "appraised market rent increases EBITDA. Note the sign: the seller has been "
                "over-charging the operating company, so correcting it is favourable to the "
                "buyer — this is the adjustment analysts most often get backwards."
            ),
        },
        {
            "label": "Excess owner and family compensation normalized to market",
            "category": "Owner / Management Compensation",
            "status": ACCEPTED,
            "authority": "Normalization to third-party compensation study",
            "impacts": {"FY2022": 2_240_000.0, "FY2023": 2_410_000.0, "FY2024": 2_560_000.0},
            "rationale": (
                "Four family members are on payroll, two of whom have no operating role. "
                "Combined compensation of $4.1m in FY2024 normalizes to $1.54m against the "
                "post-close management structure."
            ),
        },
        {
            "label": "One-time plant consolidation and severance charges",
            "category": "Non-Recurring / One-Time",
            "status": ACCEPTED,
            "authority": "Completed restructuring; no residual obligation",
            "impacts": {"FY2022": 0.0, "FY2023": 0.0, "FY2024": 1_850_000.0},
            "rationale": (
                "Closure of the Rockford cell and consolidation into Janesville, comprising "
                "severance for 41 employees, equipment relocation and lease termination. The "
                "programme completed in November 2024."
            ),
        },
        {
            "label": "Warranty accrual shortfall relative to trailing claims experience",
            "category": "Reserve & Allowance Adequacy",
            "status": ACCEPTED,
            "authority": "ASC 460-10 (guarantees and product warranties)",
            "impacts": {"FY2022": 0.0, "FY2023": -380_000.0, "FY2024": -720_000.0},
            "rationale": (
                "The warranty accrual is set at 0.2% of revenue while trailing 36-month claims "
                "have run at 0.75%. Two hydraulic manifold programs entered field service in "
                "FY2023 and account for the divergence."
            ),
        },
        {
            "label": "Non-recurring sell-side transaction and quality-of-earnings preparation costs",
            "category": "Non-Recurring / One-Time",
            "status": ACCEPTED,
            "authority": "Transaction costs; do not burden the go-forward entity",
            "impacts": {"FY2022": 0.0, "FY2023": 180_000.0, "FY2024": 940_000.0},
            "rationale": (
                "Sell-side advisory retainer, vendor QoE fees and legal costs of preparing the "
                "data room."
            ),
        },
        {
            "label": "Standalone infrastructure costs not currently borne by the business",
            "category": "Carve-Out / Standalone Cost",
            "status": ACCEPTED,
            "authority": "Cost to replicate services provided by the family office",
            "impacts": {"FY2022": 0.0, "FY2023": 0.0, "FY2024": -1_100_000.0},
            "rationale": (
                "The family office currently provides treasury, insurance procurement, IT "
                "administration and payroll at no charge. Post-close the business must hire a "
                "CFO and controller, purchase standalone D&O and property coverage, and "
                "replace an unsupported ERP. This is a negative adjustment analysts routinely "
                "omit because nothing in the reported financials points to it."
            ),
        },
        {
            "label": "Run-rate savings from the announced 2025 procurement initiative",
            "category": "Run-Rate / Pro Forma",
            "status": REJECTED,
            "authority": "Rejected — unsupported and not yet contracted",
            "impacts": {"FY2022": 0.0, "FY2023": 0.0, "FY2024": 2_900_000.0},
            "rationale": (
                "Management proposed annualizing projected savings from a steel resourcing "
                "programme. No supply agreements had been executed at the diligence cut-off "
                "and the quoted pricing was indicative only. Rejected in full."
            ),
        },
        {
            "label": "Scrap and rework variances presented by management as non-recurring",
            "category": "Non-Recurring / One-Time",
            "status": REJECTED,
            "authority": "Rejected — recurring in nature",
            "impacts": {"FY2022": 0.0, "FY2023": 0.0, "FY2024": 1_350_000.0},
            "rationale": (
                "Management characterized FY2024 scrap and rework as an anomaly. Variance "
                "analysis shows scrap has run between 2.8% and 3.4% of material cost in every "
                "period tested. This is a cost of doing business, not a one-time event."
            ),
        },
    ],
    "debt_like_key": [
        {
            "label": "Estimated multiemployer pension withdrawal liability",
            "amount": 4_600_000.0,
            "classification": DEBT_LIKE,
            "rationale": (
                "Two plants participate in a multiemployer plan certified in critical status. "
                "The actuary's estimated withdrawal liability crystallizes on a change of "
                "control and is unrecorded on the balance sheet."
            ),
        },
        {
            "label": "Deferred maintenance capital expenditure backlog",
            "amount": 3_200_000.0,
            "classification": DEBT_LIKE,
            "rationale": (
                "Two years of deferred press rebuilds and CNC spindle replacements documented "
                "in the maintenance log. The spend is required to sustain current capacity and "
                "is therefore an inherited obligation, not discretionary growth capex."
            ),
        },
        {
            "label": "Finance lease obligations on production equipment",
            "amount": 2_300_000.0,
            "classification": DEBT_LIKE,
            "rationale": (
                "Recorded within other long-term liabilities. Economically indistinguishable "
                "from funded debt and excluded from management's debt schedule."
            ),
        },
        {
            "label": "Accrued paid-time-off and deferred employer payroll taxes",
            "amount": 1_890_000.0,
            "classification": DEBT_LIKE,
            "rationale": (
                "Unfunded employee obligations payable in cash by the buyer, including the "
                "remaining deferred employer social security balance."
            ),
        },
        {
            "label": "Environmental remediation reserve at the legacy Rockford site",
            "amount": 1_750_000.0,
            "classification": DEBT_LIKE,
            "rationale": (
                "A Phase II assessment identified chlorinated solvent contamination. The "
                "consultant's remediation estimate is unreserved on the balance sheet."
            ),
        },
        {
            "label": "Company-owned aircraft used substantially for owner personal travel",
            "amount": 2_400_000.0,
            "classification": NON_OPERATING_ASSET,
            "rationale": (
                "Carried within property, plant and equipment. Flight logs show 78% personal "
                "use. Not required to operate the business and excluded from the transaction "
                "perimeter or added back at appraised value."
            ),
        },
        {
            "label": "Idle land parcel adjacent to the Rockford facility",
            "amount": 1_850_000.0,
            "classification": NON_OPERATING_ASSET,
            "rationale": (
                "Eleven acres of undeveloped land held since 2008 with no operational use. "
                "Appraised value added back in the bridge."
            ),
        },
        {
            "label": "Note receivable from a shareholder-affiliated entity",
            "amount": 780_000.0,
            "classification": NON_OPERATING_ASSET,
            "rationale": (
                "Advance to the family real estate entity within other non-current assets. To "
                "be settled at closing."
            ),
        },
    ],
    "risk_key": [
        {
            "title": "Inventory reserve methodology unchanged since 2019 despite a 39% inventory build",
            "severity": "Critical",
            "description": (
                "Inventory grew from $27.8m to $38.6m while the reserve stayed at a flat 2% of "
                "gross. This is the single largest quantified finding in the engagement."
            ),
        },
        {
            "title": "Revolver availability under the borrowing base is projected to exhaust in Q2 2025",
            "severity": "Critical",
            "description": (
                "Drawings rose from $8.0m to $41.0m across the period, funding a working "
                "capital sink rather than growth. Eligible collateral will not support the "
                "current trajectory."
            ),
        },
        {
            "title": "Fixed charge coverage covenant breached at the FY2024 measurement date",
            "severity": "Critical",
            "description": (
                "A waiver was obtained but is specific to the measurement date and does not "
                "reset forward covenants. The capital structure requires refinancing at close."
            ),
        },
        {
            "title": "Days inventory outstanding extended from 101 to 129 days",
            "severity": "High",
            "description": (
                "On average balances against cost of sales. The extension coincides with the "
                "destocking cycle and the two discontinued OEM programs."
            ),
        },
        {
            "title": "Customer concentration — the top three OEMs represent 61% of revenue",
            "severity": "High",
            "description": (
                "No long-term supply agreements are in place; all three purchase on rolling "
                "blanket orders terminable on 90 days' notice."
            ),
        },
        {
            "title": "Three manufacturing facilities leased from an owner-affiliated entity",
            "severity": "High",
            "description": (
                "Rents are materially above appraised market and the leases have no renewal "
                "option beyond 2027. Both the rent level and the tenure are transaction issues."
            ),
        },
        {
            "title": "No formal physical inventory count performed at two of five locations",
            "severity": "High",
            "description": (
                "Janesville and Fort Wayne rely on cycle counting with no annual wall-to-wall "
                "count. Given the reserve finding, the recorded quantity itself is unverified."
            ),
        },
        {
            "title": "Deferred maintenance on a press fleet averaging 14 years of age",
            "severity": "Medium",
            "description": (
                "Sustaining capital expenditure has run below depreciation in each period "
                "tested, understating the true cash cost of maintaining capacity."
            ),
        },
    ],
    "fdd_report_summary": """
### Project Anvil — Report of Factual Findings, Executive Summary

**Conclusion on earnings quality.** Management represented FY2024 Adjusted EBITDA of
**$16.000 million**. Our procedures support **$7.572 million** — a shortfall of **$8.428
million, or 52.7%**. At the 6.0x multiple contemplated in the exclusivity agreement, the finding
implies an enterprise value of approximately **$45.4 million** against the $96.0 million
headline.

**Management's number is a FY2023 number.** The central issue is not any single adjustment but
the choice of period. FY2023 was a cyclical peak: normalized Adjusted EBITDA of $16.036 million
on revenue of $137.9 million. FY2024 normalized to $7.572 million on revenue of $131.6 million.
Management's represented figure is, to the dollar, the FY2023 normalized result. The diligence
question is which period represents the go-forward business, and we do not believe FY2023 does.

**The inventory build is the earnings story.** Inventory rose 12.9% in a year when revenue fell
4.6%. Days inventory outstanding extended from 101 to 129 days. A reserve methodology frozen
since 2019 has absorbed none of this: $9.4 million of stock has not moved in 24 months, largely
tooling for two discontinued OEM programs, against a recorded reserve of $0.77 million. The
$3.85 million charge we have booked is, in our view, conservative.

**Two adjustments run in the buyer's favour and are frequently missed.** The related-party
leases are *above* market, not below — normalizing to appraised rent adds $1.68 million to
EBITDA. Conversely, the family office provides treasury, insurance, IT and payroll services at
no charge; replicating them post-close costs $1.10 million per year and must be deducted.
Analysts who assume every related-party item is an add-back will book the first with the wrong
sign and omit the second entirely.

**Adjustments rejected.** We declined $2.90 million of unsupported procurement savings (no
executed supply agreements) and $1.35 million of scrap and rework recharacterized as
non-recurring (scrap has run 2.8%–3.4% of material cost in every period tested).

**Value bridge.** We identified **$13.74 million** of debt-like items, of which the unrecorded
multiemployer pension withdrawal liability ($4.60 million) and the deferred maintenance capital
backlog ($3.20 million) are the most consequential. Against these sit **$5.03 million** of
non-operating assets, principally the aircraft and the idle Rockford land.

**Capital structure.** Net funded debt of $60.72 million against normalized FY2024 Adjusted
EBITDA of $7.572 million is **8.0x**. The fixed charge coverage covenant was breached at the
FY2024 measurement date. The existing facility cannot be assumed; the transaction requires a
full refinancing.

**Recommendation.** Do not proceed on the current terms. If the sponsor wishes to continue, we
recommend repricing to normalized FY2024 earnings, a specific indemnity for the pension
withdrawal and environmental exposures, and a working capital peg set on a trailing average
that captures the inventory build rather than the year-end balance.
""",
}


PROJECT_CASCADE: dict[str, Any] = {
    "key": "cascade",
    "name": "Project Cascade",
    "target": "Cascade Vision Partners, LLC",
    "sector": "Healthcare Services — Multi-Site Optometry and Ophthalmology MSO",
    "deal_type": "Buy-side financial due diligence — secondary buyout of a platform roll-up",
    "currency": "USD",
    "seed_period": "FY2021",
    "periods": ["FY2022", "FY2023", "FY2024"],
    "context": """
**Sponsor.** Longmeadow Health Capital, evaluating a secondary buyout from the incumbent
sponsor, Ridgeline Partners.

**Transaction.** Longmeadow has been invited into a limited process to acquire Cascade Vision
Partners at an enterprise value of **$117.0 million**, struck at **12.0x** management's
represented FY2024 "Pro Forma Adjusted EBITDA" of **$9.75 million**. Ridgeline has held the
platform for four years and completed 23 add-on acquisitions, eleven of them in FY2024 alone.

**Target.** Cascade is a management services organization supporting 34 optometry and
ophthalmology locations across the Pacific Northwest and Mountain West. Revenue comprises
professional service fees, optical goods and a captive finishing lab. The clinical entities are
held in friendly professional corporations under management services agreements.

**Scope.** Longmeadow has asked for a Quality of Earnings analysis over FY2022–FY2024 with
particular scrutiny of the pro forma acquisition adjustments, an assessment of accounts
receivable and the payer denial reserve, and a debt-like item schedule. Management's bridge
relies heavily on run-rate and synergy adjustments; Longmeadow's investment committee has asked
you to state explicitly which of those adjustments you support and which you do not.
""",
    "income_statement": {
        "revenue": {"FY2022": 62_400_000.0, "FY2023": 78_900_000.0, "FY2024": 96_300_000.0},
        "cost_of_revenue": {
            "FY2022": 24_960_000.0,
            "FY2023": 31_560_000.0,
            "FY2024": 39_483_000.0,
        },
        "selling_and_marketing": {
            "FY2022": 3_744_000.0,
            "FY2023": 4_734_000.0,
            "FY2024": 6_741_000.0,
        },
        "research_and_development": {"FY2022": 0.0, "FY2023": 0.0, "FY2024": 0.0},
        "general_and_administrative": {
            "FY2022": 26_208_000.0,
            "FY2023": 33_141_000.0,
            "FY2024": 41_409_000.0,
        },
        "other_operating_expense": {"FY2022": 0.0, "FY2023": 1_100_000.0, "FY2024": 2_950_000.0},
        "depreciation": {
            "FY2022": 2_184_000.0,
            "FY2023": 2_762_000.0,
            "FY2024": 3_370_000.0,
        },
        "amortization_software": {"FY2022": 0.0, "FY2023": 0.0, "FY2024": 0.0},
        "amortization_intangibles": {
            "FY2022": 1_560_000.0,
            "FY2023": 2_340_000.0,
            "FY2024": 3_120_000.0,
        },
        "interest_expense": {
            "FY2022": 2_460_000.0,
            "FY2023": 4_460_000.0,
            "FY2024": 7_040_000.0,
        },
        "other_income_expense": {
            "FY2022": -150_000.0,
            "FY2023": -320_000.0,
            "FY2024": -480_000.0,
        },
        "income_tax_expense": {"FY2022": 0.0, "FY2023": 0.0, "FY2024": 0.0},
    },
    "balance_sheet": {
        "accounts_receivable": {
            "FY2021": 6_900_000.0,
            "FY2022": 8_600_000.0,
            "FY2023": 11_900_000.0,
            "FY2024": 16_400_000.0,
        },
        "inventory": {
            "FY2021": 3_100_000.0,
            "FY2022": 3_900_000.0,
            "FY2023": 4_800_000.0,
            "FY2024": 6_100_000.0,
        },
        "prepaid_expenses": {
            "FY2021": 900_000.0,
            "FY2022": 1_100_000.0,
            "FY2023": 1_400_000.0,
            "FY2024": 1_750_000.0,
        },
        "other_current_assets": {
            "FY2021": 450_000.0,
            "FY2022": 560_000.0,
            "FY2023": 720_000.0,
            "FY2024": 910_000.0,
        },
        "ppe_net": {
            "FY2021": 12_400_000.0,
            "FY2022": 15_800_000.0,
            "FY2023": 20_100_000.0,
            "FY2024": 24_600_000.0,
        },
        "capitalized_software_net": {
            "FY2021": 0.0,
            "FY2022": 0.0,
            "FY2023": 0.0,
            "FY2024": 0.0,
        },
        "goodwill_and_intangibles_net": {
            "FY2021": 48_000_000.0,
            "FY2022": 71_500_000.0,
            "FY2023": 96_800_000.0,
            "FY2024": 118_200_000.0,
        },
        "other_non_current_assets": {
            "FY2021": 1_800_000.0,
            "FY2022": 2_400_000.0,
            "FY2023": 3_100_000.0,
            "FY2024": 3_900_000.0,
        },
        "accounts_payable": {
            "FY2021": 4_200_000.0,
            "FY2022": 5_300_000.0,
            "FY2023": 6_800_000.0,
            "FY2024": 8_100_000.0,
        },
        "accrued_liabilities": {
            "FY2021": 5_100_000.0,
            "FY2022": 6_400_000.0,
            "FY2023": 8_200_000.0,
            "FY2024": 10_300_000.0,
        },
        "deferred_revenue_current": {
            "FY2021": 1_200_000.0,
            "FY2022": 1_500_000.0,
            "FY2023": 1_900_000.0,
            "FY2024": 2_300_000.0,
        },
        "income_taxes_payable": {"FY2021": 0.0, "FY2022": 0.0, "FY2023": 0.0, "FY2024": 0.0},
        "short_term_debt": {
            "FY2021": 0.0,
            "FY2022": 2_000_000.0,
            "FY2023": 5_000_000.0,
            "FY2024": 9_500_000.0,
        },
        "current_portion_long_term_debt": {
            "FY2021": 2_500_000.0,
            "FY2022": 3_500_000.0,
            "FY2023": 4_500_000.0,
            "FY2024": 5_500_000.0,
        },
        "other_current_liabilities": {
            "FY2021": 2_100_000.0,
            "FY2022": 2_700_000.0,
            "FY2023": 3_400_000.0,
            "FY2024": 4_200_000.0,
        },
        "long_term_debt": {
            "FY2021": 30_000_000.0,
            "FY2022": 44_000_000.0,
            "FY2023": 60_000_000.0,
            "FY2024": 72_000_000.0,
        },
        "deferred_revenue_non_current": {
            "FY2021": 0.0,
            "FY2022": 0.0,
            "FY2023": 0.0,
            "FY2024": 0.0,
        },
        "other_long_term_liabilities": {
            "FY2021": 6_400_000.0,
            "FY2022": 9_800_000.0,
            "FY2023": 13_600_000.0,
            "FY2024": 17_200_000.0,
        },
        "contributed_capital": {
            "FY2021": 32_000_000.0,
            "FY2022": 42_000_000.0,
            "FY2023": 49_000_000.0,
            "FY2024": 62_000_000.0,
        },
    },
    "seed_cash": 4_200_000.0,
    "stock_based_compensation": {"FY2022": 0.0, "FY2023": 0.0, "FY2024": 0.0},
    "distributions": {"FY2022": 0.0, "FY2023": 0.0, "FY2024": 0.0},
    "management_adjusted_ebitda": {"FY2024": 9_750_000.0},
    "answer_key": [
        {
            "label": "Pro forma full-period ownership of acquisitions closed during the year",
            "category": "Run-Rate / Pro Forma",
            "status": ACCEPTED,
            "authority": "Supported by seller-audited stub-period financials for each target",
            "impacts": {"FY2022": 1_200_000.0, "FY2023": 2_600_000.0, "FY2024": 3_850_000.0},
            "rationale": (
                "Eleven practices closed during FY2024 at various dates. The adjustment "
                "annualizes their pre-acquisition results for the portion of the year prior to "
                "ownership. We accepted this in full because each target's stub-period "
                "financials were independently prepared and we were able to tie them to bank "
                "statements — pro forma acquisition adjustments are supportable when the "
                "underlying periods are evidenced, and unsupportable when they are projections."
            ),
        },
        {
            "label": "Non-recurring acquisition, integration and de novo opening costs",
            "category": "Non-Recurring / One-Time",
            "status": ACCEPTED,
            "authority": "Transaction and integration costs; do not recur in steady state",
            "impacts": {"FY2022": 0.0, "FY2023": 1_100_000.0, "FY2024": 2_950_000.0},
            "rationale": (
                "Legal and diligence fees on closed transactions, EMR conversion costs and "
                "pre-opening expenses for two de novo clinics. Recurring only for as long as "
                "the roll-up strategy continues, which the buyer must decide separately."
            ),
        },
        {
            "label": "Sponsor management fee — terminates on change of control",
            "category": "Related-Party Normalization",
            "status": ACCEPTED,
            "authority": "Management services agreement terminates at close",
            "impacts": {"FY2022": 600_000.0, "FY2023": 750_000.0, "FY2024": 900_000.0},
            "rationale": (
                "Ridgeline charges an annual monitoring fee under the existing MSA. The "
                "agreement terminates on a change of control, so the cost does not transfer."
            ),
        },
        {
            "label": "Physician and optometrist compensation normalized to post-close contracted rates",
            "category": "Owner / Management Compensation",
            "status": ACCEPTED,
            "authority": "Executed post-close employment agreements",
            "impacts": {"FY2022": -1_450_000.0, "FY2023": -2_100_000.0, "FY2024": -3_300_000.0},
            "rationale": (
                "Selling providers accepted below-market compensation during their earnout "
                "periods in exchange for transaction consideration. Their executed post-close "
                "agreements step compensation up materially. This is a *negative* adjustment "
                "and the one most often missed — the reported cost base understates what the "
                "buyer will actually pay to retain the clinicians who generate the revenue."
            ),
        },
        {
            "label": "Allowance for payer denials and contractual adjustments — shortfall on aged receivables",
            "category": "Reserve & Allowance Adequacy",
            "status": ACCEPTED,
            "authority": "ASC 326-20; ASC 606 variable consideration constraint",
            "impacts": {"FY2022": 0.0, "FY2023": -880_000.0, "FY2024": -2_640_000.0},
            "rationale": (
                "Gross accounts receivable grew 91% over two years while the allowance was held "
                "broadly flat. The >120-day bucket expanded from 9% to 27% of gross AR, "
                "concentrated in two payers that changed prior-authorization rules in FY2024. "
                "Applying realized collection rates by aging bucket and payer produces a "
                "required allowance of $4.55m against $1.91m recorded."
            ),
        },
        {
            "label": "Deferred maintenance and equipment refresh reclassified from capital to operating expense",
            "category": "Accounting & GAAP Correction",
            "status": ACCEPTED,
            "authority": "ASC 360-10-25 (capitalization criteria)",
            "impacts": {"FY2022": 0.0, "FY2023": -420_000.0, "FY2024": -980_000.0},
            "rationale": (
                "Routine repairs to diagnostic equipment, replacement lenses for existing "
                "instruments and clinic repainting were capitalized into leasehold "
                "improvements. None extends the useful life or capacity of the underlying "
                "asset. Reclassification reduces EBITDA and increases the true sustaining "
                "capital requirement."
            ),
        },
        {
            "label": "Standalone MSO infrastructure required post-close",
            "category": "Carve-Out / Standalone Cost",
            "status": ACCEPTED,
            "authority": "Cost to replicate services currently provided by the incumbent sponsor",
            "impacts": {"FY2022": 0.0, "FY2023": 0.0, "FY2024": -1_250_000.0},
            "rationale": (
                "Revenue cycle management, compliance monitoring and IT infrastructure are "
                "currently delivered through Ridgeline's shared platform under the monitoring "
                "fee. The buyer must stand these up independently, at a cost materially above "
                "the fee being added back."
            ),
        },
        {
            "label": "Litigation settlement with a former practice seller",
            "category": "Non-Recurring / One-Time",
            "status": ACCEPTED,
            "authority": "Settled and released in FY2024",
            "impacts": {"FY2022": 0.0, "FY2023": 0.0, "FY2024": 640_000.0},
            "rationale": (
                "Settlement of an earnout dispute arising from a 2022 acquisition, together "
                "with defence costs. Fully released."
            ),
        },
        {
            "label": "Unsupported cost synergies on the FY2024 acquisition cohort",
            "category": "Run-Rate / Pro Forma",
            "status": REJECTED,
            "authority": "Rejected — projected, not realized",
            "impacts": {"FY2022": 0.0, "FY2023": 0.0, "FY2024": 2_750_000.0},
            "rationale": (
                "Management claimed procurement, lab-insourcing and back-office synergies "
                "across the eleven FY2024 acquisitions. None had been realized at the diligence "
                "cut-off, no supplier contracts had been renegotiated, and the prior cohort's "
                "realized synergies ran at 31% of the equivalent claim. Rejected in full."
            ),
        },
        {
            "label": "Full-year run-rate for a de novo clinic opened in December 2024",
            "category": "Run-Rate / Pro Forma",
            "status": REJECTED,
            "authority": "Rejected — a projection, not an historical result",
            "impacts": {"FY2022": 0.0, "FY2023": 0.0, "FY2024": 1_150_000.0},
            "rationale": (
                "Unlike an acquisition of an operating practice, a de novo has no pre-opening "
                "trading history to annualize. The clinic had one month of operations and was "
                "loss-making. The claimed figure is a business plan, not an adjustment."
            ),
        },
    ],
    "debt_like_key": [
        {
            "label": "Contingent consideration and deferred purchase price — non-current",
            "amount": 17_200_000.0,
            "classification": DEBT_LIKE,
            "rationale": (
                "Earnouts and deferred consideration on closed acquisitions, recorded within "
                "other long-term liabilities. A committed obligation of the seller that "
                "survives closing and is the largest single item in the bridge."
            ),
        },
        {
            "label": "Contingent consideration and deferred purchase price — current portion",
            "amount": 4_200_000.0,
            "classification": DEBT_LIKE,
            "rationale": (
                "The portion of acquisition consideration payable within twelve months, "
                "recorded within other current liabilities."
            ),
        },
        {
            "label": "Finance lease obligations on clinic and diagnostic equipment",
            "amount": 3_400_000.0,
            "classification": DEBT_LIKE,
            "rationale": (
                "Equipment financing across 34 locations, economically equivalent to funded "
                "debt and omitted from management's debt schedule."
            ),
        },
        {
            "label": "Accrued physician deferred compensation",
            "amount": 2_150_000.0,
            "classification": DEBT_LIKE,
            "rationale": (
                "Unfunded deferred compensation owed to selling providers under their "
                "transition agreements, payable in cash."
            ),
        },
        {
            "label": "Accrued but unremitted payer overpayment recoupments",
            "amount": 1_900_000.0,
            "classification": DEBT_LIKE,
            "rationale": (
                "Identified overpayments subject to recoupment by two commercial payers. Cash "
                "will be withheld from future remittances post-close."
            ),
        },
        {
            "label": "Real estate holding entity owning three clinic buildings",
            "amount": 6_800_000.0,
            "classification": NON_OPERATING_ASSET,
            "rationale": (
                "A consolidated affiliate holding owned real estate. Excluded from the "
                "operating perimeter and added back at appraised value; the buyer should "
                "expect a corresponding market rent charge in the go-forward P&L."
            ),
        },
        {
            "label": "Escrow receivable on the 2023 seller indemnity claim",
            "amount": 950_000.0,
            "classification": NON_OPERATING_ASSET,
            "rationale": (
                "An indemnity claim against a prior seller, held in escrow and expected to "
                "release within nine months. Non-operating and collectible."
            ),
        },
    ],
    "risk_key": [
        {
            "title": "Accounts receivable aging deteriorated sharply with no reserve response",
            "severity": "Critical",
            "description": (
                "The >120-day bucket grew from 9% to 27% of gross accounts receivable while the "
                "allowance was held broadly flat. Two payers changed prior-authorization rules in "
                "FY2024 and the company has not re-underwritten its collection assumptions."
            ),
        },
        {
            "title": "Management's bridge is majority-composed of unsupported adjustments",
            "severity": "Critical",
            "description": (
                "Of the $4.06m gap between our conclusion and management's, $3.90m sits in two "
                "adjustments we rejected in full — projected synergies and a de novo business "
                "plan. Neither has any realized history."
            ),
        },
        {
            "title": "Net leverage of approximately 14x normalized Adjusted EBITDA",
            "severity": "Critical",
            "description": (
                "Funded debt of $87.0m against normalized FY2024 Adjusted EBITDA of $5.89m. "
                "The capital structure is not serviceable on normalized earnings and the "
                "existing facility cannot be assumed."
            ),
        },
        {
            "title": "Corporate practice of medicine structure undocumented in nine locations",
            "severity": "High",
            "description": (
                "Friendly-PC arrangements and management services agreements could not be "
                "located for 9 of 34 locations. This is a regulatory and consolidation issue, "
                "not merely a documentation gap."
            ),
        },
        {
            "title": "Payer concentration — two vision plans represent 54% of net revenue",
            "severity": "High",
            "description": (
                "Both contracts are on evergreen terms terminable on 120 days' notice, and one "
                "is the payer driving the denial-rate increase."
            ),
        },
        {
            "title": "Eleven acquisitions closed in FY2024 with no post-close integration accounting",
            "severity": "High",
            "description": (
                "Purchase accounting is provisional across the FY2024 cohort, opening balance "
                "sheets have not been finalized, and no acquired entity has been through a "
                "post-close close process."
            ),
        },
        {
            "title": "Physician retention risk on seven expiring employment agreements",
            "severity": "High",
            "description": (
                "Seven selling providers have agreements expiring within twelve months of "
                "close. Their compensation steps up on renewal, which is the basis of our "
                "negative compensation adjustment."
            ),
        },
        {
            "title": "No quality of earnings was performed on any add-on acquisition",
            "severity": "Medium",
            "description": (
                "Twenty-three acquisitions were completed on seller-prepared financials alone. "
                "The platform's consolidated results inherit whatever errors those carried."
            ),
        },
    ],
    "fdd_report_summary": """
### Project Cascade — Report of Factual Findings, Executive Summary

**Conclusion on earnings quality.** Management represented FY2024 "Pro Forma Adjusted EBITDA"
of **$9.750 million**. Our procedures support **$5.887 million**, a shortfall of **$3.863
million (39.6%)**. At the 12.0x multiple contemplated, the finding implies an enterprise value
of approximately **$70.6 million** against the $117.0 million headline.

**The disagreement is about evidence, not judgment.** We accepted management's largest pro forma
adjustment — $3.85 million for the partial-period ownership of eleven practices acquired during
FY2024 — in full, because each target's stub-period financials were independently prepared and
we tied them to bank statements. We rejected $2.75 million of claimed cost synergies and $1.15
million of de novo run-rate for the opposite reason: neither had occurred. The distinction is
the entire discipline. A pro forma adjustment that annualizes an evidenced historical result is
supportable; one that annualizes a plan is a forecast wearing an adjustment's clothing.

**Two negative adjustments carry the rest of the gap.** Selling physicians accepted below-market
compensation during their earnout periods; their executed post-close agreements step
compensation up by $3.30 million annually. Separately, the payer denial reserve is short by
$2.64 million against realized collection rates by aging bucket. Both reduce earnings, and both
are invisible in the reported financial statements — an analyst working only from the trial
balance will not find either.

**Receivables are the leading indicator.** Gross accounts receivable grew 91% over two years
against 54% revenue growth. Days sales outstanding extended from 45 to 54 days. The >120-day
bucket tripled as a share of gross AR. The company treated this as a timing issue; the aging by
payer shows it is a collectability issue concentrated in two commercial plans that tightened
prior authorization in FY2024.

**The monitoring fee is not a clean add-back.** Management added back the $0.90 million sponsor
monitoring fee. That is correct as far as it goes — the agreement terminates at close — but the
services it pays for do not disappear. Revenue cycle management, compliance and IT run on the
incumbent sponsor's shared platform. Standing them up independently costs $1.25 million. The
net effect of the pair is negative, which management's bridge does not show.

**Value bridge.** We identified **$28.85 million** of debt-like items, dominated by $21.40
million of contingent and deferred acquisition consideration, against **$7.75 million** of
non-operating assets. The deferred consideration alone represents 24% of the headline enterprise
value and is not reflected in management's net debt schedule.

**Capital structure.** Funded debt of $87.00 million against normalized Adjusted EBITDA of $5.89
million is approximately **14x**. The platform has consumed $30.0 million of sponsor equity over
three years while normalized earnings have declined from $9.415 million to $5.887 million. The
roll-up has been buying revenue, not earnings.

**Recommendation.** We are unable to support the transaction at the contemplated value. If
Longmeadow proceeds, we recommend repricing to normalized rather than pro forma earnings,
requiring the seller to satisfy all deferred and contingent consideration at close, a specific
indemnity for the corporate-practice-of-medicine documentation gaps, and a working capital peg
constructed on collected cash rather than billed receivables.
""",
}


PROJECT_HELIOS_MASTER: dict[str, Any] = {
    "key": "helios_master",
    "name": "Project Helios — Master Case",
    "target": "Helios Practice Systems Holdings, Inc.",
    "sector": "Vertical SaaS — worked example",
    "deal_type": "Buy-side financial due diligence — sponsor-to-sponsor secondary buyout",
    "currency": "USD",
    "seed_period": "FY2021",
    "periods": ["FY2022", "FY2023", "FY2024"],
    "context": """
**This is the worked master case.** Unlike the three blind cases, this engagement ships with its
working papers already completed — the adjustment ledger, the SEC narrative risk matrix and the
ASC 805 purchase price allocation are all populated. Load it to see what a finished diligence
file looks like end to end before attempting a case cold.

**Sponsor.** Meridian Growth Partners, exiting to Ridgeline Software Capital.

**Transaction.** Ridgeline has signed a letter of intent to acquire 100% of Helios Practice
Systems Holdings at an enterprise value of **$179.30 million**, struck at **11.0x** the
engagement team's FY2024 Adjusted EBITDA of **$16.30 million**. The transaction is structured as
a stock purchase, cash-free and debt-free, with a net working capital peg. No section 338(h)(10)
election is contemplated, so the target's tax basis carries over and every fair value step-up
creates a deferred tax liability.

**Target.** Helios sells a cloud practice-management and revenue-cycle platform to multi-location
dental service organizations, now scaled to 3,900 live practice locations. Revenue is 91%
subscription. FY2022–FY2024 are audited.

**Scope.** Quality of Earnings over FY2022–FY2024, net working capital and the peg, debt-like
items and non-operating assets for the value bridge, a narrative risk scan of the registrant's
filings, and a full ASC 805 allocation of the consideration to the opening balance sheet.
""",
    "income_statement": {
        "revenue": {"FY2022": 61_500_000.0, "FY2023": 78_000_000.0, "FY2024": 96_000_000.0},
        "cost_of_revenue": {
            "FY2022": 15_375_000.0,
            "FY2023": 18_720_000.0,
            "FY2024": 23_040_000.0,
        },
        "selling_and_marketing": {
            "FY2022": 17_835_000.0,
            "FY2023": 22_230_000.0,
            "FY2024": 26_880_000.0,
        },
        "research_and_development": {
            "FY2022": 11_685_000.0,
            "FY2023": 14_430_000.0,
            "FY2024": 17_280_000.0,
        },
        "general_and_administrative": {
            "FY2022": 8_610_000.0,
            "FY2023": 10_920_000.0,
            "FY2024": 13_300_000.0,
        },
        "other_operating_expense": {"FY2022": 0.0, "FY2023": 0.0, "FY2024": 0.0},
        "depreciation": {"FY2022": 1_150_000.0, "FY2023": 1_340_000.0, "FY2024": 1_580_000.0},
        "amortization_software": {
            "FY2022": 1_900_000.0,
            "FY2023": 2_600_000.0,
            "FY2024": 3_400_000.0,
        },
        "amortization_intangibles": {"FY2022": 0.0, "FY2023": 0.0, "FY2024": 420_000.0},
        "interest_expense": {"FY2022": 900_000.0, "FY2023": 1_180_000.0, "FY2024": 1_720_000.0},
        "other_income_expense": {"FY2022": -180_000.0, "FY2023": 260_000.0, "FY2024": -320_000.0},
        "income_tax_expense": {"FY2022": 320_000.0, "FY2023": 640_000.0, "FY2024": 980_000.0},
    },
    "balance_sheet": {
        "accounts_receivable": {
            "FY2021": 8_400_000.0,
            "FY2022": 10_250_000.0,
            "FY2023": 13_600_000.0,
            "FY2024": 17_100_000.0,
        },
        "inventory": {"FY2021": 0.0, "FY2022": 0.0, "FY2023": 0.0, "FY2024": 0.0},
        "prepaid_expenses": {
            "FY2021": 1_400_000.0,
            "FY2022": 1_750_000.0,
            "FY2023": 2_200_000.0,
            "FY2024": 2_750_000.0,
        },
        "other_current_assets": {
            "FY2021": 900_000.0,
            "FY2022": 1_120_000.0,
            "FY2023": 1_400_000.0,
            "FY2024": 1_760_000.0,
        },
        "ppe_net": {
            "FY2021": 3_200_000.0,
            "FY2022": 3_900_000.0,
            "FY2023": 4_800_000.0,
            "FY2024": 5_800_000.0,
        },
        "capitalized_software_net": {
            "FY2021": 6_800_000.0,
            "FY2022": 9_600_000.0,
            "FY2023": 13_900_000.0,
            "FY2024": 18_900_000.0,
        },
        "goodwill_and_intangibles_net": {
            "FY2021": 4_200_000.0,
            "FY2022": 4_200_000.0,
            "FY2023": 4_200_000.0,
            "FY2024": 9_700_000.0,
        },
        "other_non_current_assets": {
            "FY2021": 850_000.0,
            "FY2022": 980_000.0,
            "FY2023": 1_180_000.0,
            "FY2024": 1_450_000.0,
        },
        "accounts_payable": {
            "FY2021": 2_300_000.0,
            "FY2022": 2_750_000.0,
            "FY2023": 3_300_000.0,
            "FY2024": 4_000_000.0,
        },
        "accrued_liabilities": {
            "FY2021": 3_900_000.0,
            "FY2022": 4_700_000.0,
            "FY2023": 5_900_000.0,
            "FY2024": 7_200_000.0,
        },
        "deferred_revenue_current": {
            "FY2021": 16_800_000.0,
            "FY2022": 20_400_000.0,
            "FY2023": 25_600_000.0,
            "FY2024": 31_400_000.0,
        },
        "income_taxes_payable": {
            "FY2021": 0.0,
            "FY2022": 120_000.0,
            "FY2023": 260_000.0,
            "FY2024": 480_000.0,
        },
        "short_term_debt": {
            "FY2021": 0.0,
            "FY2022": 0.0,
            "FY2023": 0.0,
            "FY2024": 2_500_000.0,
        },
        "current_portion_long_term_debt": {
            "FY2021": 2_000_000.0,
            "FY2022": 2_000_000.0,
            "FY2023": 2_500_000.0,
            "FY2024": 3_000_000.0,
        },
        "other_current_liabilities": {
            "FY2021": 800_000.0,
            "FY2022": 940_000.0,
            "FY2023": 1_150_000.0,
            "FY2024": 1_400_000.0,
        },
        "long_term_debt": {
            "FY2021": 12_000_000.0,
            "FY2022": 14_000_000.0,
            "FY2023": 18_000_000.0,
            "FY2024": 24_000_000.0,
        },
        "deferred_revenue_non_current": {
            "FY2021": 3_200_000.0,
            "FY2022": 4_000_000.0,
            "FY2023": 4_900_000.0,
            "FY2024": 5_900_000.0,
        },
        "other_long_term_liabilities": {
            "FY2021": 1_200_000.0,
            "FY2022": 1_400_000.0,
            "FY2023": 1_600_000.0,
            "FY2024": 1_900_000.0,
        },
        "contributed_capital": {
            "FY2021": 14_000_000.0,
            "FY2022": 14_400_000.0,
            "FY2023": 14_950_000.0,
            "FY2024": 15_600_000.0,
        },
    },
    "seed_cash": 6_500_000.0,
    "stock_based_compensation": {
        "FY2022": 640_000.0,
        "FY2023": 880_000.0,
        "FY2024": 1_340_000.0,
    },
    "distributions": {"FY2022": 0.0, "FY2023": 0.0, "FY2024": 0.0},
    "management_adjusted_ebitda": {"FY2024": 16_300_000.0},
    "answer_key": [
        {
            "label": "One-time legal settlement",
            "category": "Non-Recurring / One-Time",
            "status": ACCEPTED,
            "authority": "Non-recurring by nature; settled and released with no ongoing exposure",
            "impacts": {"FY2022": 0.0, "FY2023": 0.0, "FY2024": 1_200_000.0},
            "rationale": (
                "Settlement of a non-practicing-entity patent claim over the appointment-reminder "
                "module, together with outside counsel fees. A full release was executed in "
                "September 2024 and no further exposure exists, so the charge is added back to "
                "FY2024 earnings."
            ),
        },
        {
            "label": "Aggressive capitalized software",
            "category": "Accounting & GAAP Correction",
            "status": ACCEPTED,
            "authority": "ASC 350-40-25 (application development stage criteria)",
            "impacts": {"FY2022": 0.0, "FY2023": 0.0, "FY2024": -400_000.0},
            "rationale": (
                "Sprint-level review of the engineering time-tracking export shows that "
                "post-implementation maintenance and customer-specific configuration were "
                "capitalized alongside genuine application development. Only costs incurred "
                "during the application development stage qualify. Reclassifying the ineligible "
                "portion to operating expense reduces FY2024 EBITDA."
            ),
        },
    ],
    "debt_like_key": [
        {
            "label": "Non-current deferred revenue — cash collected against unperformed obligations",
            "amount": 5_900_000.0,
            "classification": DEBT_LIKE,
            "rationale": (
                "The buyer inherits an obligation to deliver 12+ months of service for which the "
                "seller has already collected the cash. Treated as debt-like in the value bridge."
            ),
        },
        {
            "label": "Accrued paid-time-off and unfunded bonus liability",
            "amount": 1_900_000.0,
            "classification": DEBT_LIKE,
            "rationale": (
                "Accrued but unpaid PTO and the FY2024 discretionary bonus pool, neither of which "
                "is funded. Payable in cash by the buyer in the first post-close quarter."
            ),
        },
        {
            "label": "Cash surrender value of founder key-person life insurance",
            "amount": 620_000.0,
            "classification": NON_OPERATING_ASSET,
            "rationale": (
                "Held within other non-current assets. Not required to operate the platform and "
                "realizable in cash; added back in the bridge."
            ),
        },
    ],
    "risk_key": [
        {
            "title": "ASC 606 revenue concentration",
            "severity": "High",
            "description": (
                "The five largest dental service organization customers represent 34% of ARR and "
                "contract on multi-year prepaid terms. The registrant's own filings identify the "
                "concentration as a risk factor; the ASC 606 measurement of those arrangements "
                "therefore drives a disproportionate share of reported revenue."
            ),
        },
        {
            "title": "Internal-use software capitalization policy",
            "severity": "Medium",
            "description": (
                "Capitalized software grew 178% across the diligence period against 84% headcount "
                "growth in engineering. The capitalization rate remains above the peer range and "
                "supports the FY2024 GAAP correction booked in the ledger."
            ),
        },
        {
            "title": "Covenant headroom on the senior credit facility",
            "severity": "Medium",
            "description": (
                "Funded debt of $29.50 million against Adjusted EBITDA of $16.30 million is 1.81x "
                "gross. Headroom is adequate today, but the facility steps down twice before the "
                "contemplated close."
            ),
        },
    ],
    "fdd_report_summary": """
### Project Helios (Master Case) — Report of Factual Findings, Executive Summary

**Conclusion on earnings quality.** Reported FY2024 EBITDA of **$15.500 million** normalizes to
Adjusted EBITDA of **$16.300 million**, an uplift of **$0.800 million (5.2%)**. Management's
represented figure agrees to our conclusion. This is a clean file, and it is presented as the
worked example precisely because it shows what agreement looks like.

**The bridge.** Two adjustments carry it. We added back **$1.200 million** of one-time patent
litigation settlement and related counsel fees, released in full in September 2024. We deducted
**$0.400 million** of internal-use software costs that fail the ASC 350-40 application
development stage criteria and belong in operating expense. Both are supported by documents in
the data room rather than by management representation.

**Working capital.** Net working capital is structurally negative, the normal and favourable
signature of a prepaid subscription model. We recommend a peg set on the trailing twelve-month
average rather than the year-end balance.

**Value bridge.** We identified **$7.800 million** of debt-like items, of which the non-current
deferred revenue balance ($5.900 million) is the largest, against **$0.620 million** of
non-operating assets.

**Purchase price allocation.** At the LOI's $179.30 million enterprise value, the ASC 805
allocation recognizes **$42.600 million** of customer relationships and **$28.400 million** of
developed technology, a net tangible step-*down* of **$2.200 million** driven by writing the
over-capitalized software balance to fair value, and a deferred tax liability of **$17.200
million** at the 25.00% marginal rate. Because this is a stock purchase with no section
338(h)(10) election, the target's tax basis carries over and the deferred tax liability increases
goodwill dollar for dollar.

Book net assets acquired are **negative $4.520 million** on a cash-free debt-free view — the
normal signature of a prepaid subscription model, where deferred revenue exceeds the receivable
and prepaid balances. Fair value of identifiable net assets is therefore **$47.080 million** and
the residual **goodwill is $132.220 million**, or **73.74%** of consideration. A goodwill-heavy
allocation is expected where the acquired value sits in the workforce, the platform's market
position and the recurring revenue base rather than on the seller's balance sheet. Forward
amortization of the recognized intangibles is **$8,317,142.8571428573** per year, a real and
frequently overlooked drag on post-close reported earnings.

**Recommendation.** We support the transaction at the contemplated value, subject to a working
capital peg on the trailing twelve-month average and a specific indemnity for the software
capitalization exposure through the first post-close audited period.
""",
}


CASE_LIBRARY: dict[str, dict[str, Any]] = {
    PROJECT_HELIOS["key"]: PROJECT_HELIOS,
    PROJECT_ANVIL["key"]: PROJECT_ANVIL,
    PROJECT_CASCADE["key"]: PROJECT_CASCADE,
    PROJECT_HELIOS_MASTER["key"]: PROJECT_HELIOS_MASTER,
}


def case_options() -> list[tuple[str, str]]:
    """(key, display label) pairs for the sandbox selector."""
    return [
        (key, f"{case['name']} — {case['sector']}")
        for key, case in CASE_LIBRARY.items()
    ]


# --------------------------------------------------------------------------- #
# Three-statement builder
# --------------------------------------------------------------------------- #


def _blank_facts() -> dict[str, dict[str, float]]:
    from config import BALANCE_SHEET_KEYS, CASH_FLOW_KEYS, INCOME_STATEMENT_KEYS

    keys = set(INCOME_STATEMENT_KEYS) | set(BALANCE_SHEET_KEYS) | set(CASH_FLOW_KEYS)
    return {key: {} for key in keys}


def build_case_facts(case: Mapping[str, Any]) -> dict[str, dict[str, float]]:
    """Expand a case definition into the full canonical fact table.

    Cash is derived as the balancing plug from ``Assets = Liabilities + Equity``
    while retained earnings rolls forward on net income less distributions. That
    ordering makes the balance sheet balance and the indirect cash flow statement
    foot without any hand-tuned reconciling item.
    """
    facts = _blank_facts()
    seed = case["seed_period"]
    periods: list[str] = list(case["periods"])
    all_periods = [seed] + periods

    balance_inputs = case["balance_sheet"]
    income_inputs = case["income_statement"]

    # --- Balance sheet primitives -----------------------------------------
    for key in _BALANCE_INPUT_KEYS:
        series = balance_inputs.get(key, {})
        for period in all_periods:
            facts[key][period] = float(series.get(period, 0.0))

    # --- Income statement primitives and derived subtotals ----------------
    for key, series in income_inputs.items():
        for period in periods:
            facts[key][period] = float(series.get(period, 0.0))

    for period in periods:
        revenue = facts["revenue"][period]
        cost_of_revenue = facts["cost_of_revenue"][period]
        gross_profit = revenue - cost_of_revenue
        facts["gross_profit"][period] = gross_profit

        operating_expense = math.fsum(
            (
                facts["selling_and_marketing"][period],
                facts["research_and_development"][period],
                facts["general_and_administrative"][period],
                facts["other_operating_expense"][period],
                facts["depreciation"][period],
                facts["amortization_software"][period],
                facts["amortization_intangibles"][period],
            )
        )
        operating_income = gross_profit - operating_expense
        facts["operating_income"][period] = operating_income

        pretax = (
            operating_income
            - facts["interest_expense"][period]
            + facts["other_income_expense"][period]
        )
        facts["pretax_income"][period] = pretax
        facts["net_income"][period] = pretax - facts["income_tax_expense"][period]

    # --- Retained earnings roll-forward and the derived cash plug ---------
    def non_cash_assets(period: str) -> float:
        return math.fsum(facts[key][period] for key in _NON_CASH_ASSET_KEYS)

    def liabilities(period: str) -> float:
        return math.fsum(facts[key][period] for key in _LIABILITY_KEYS)

    seed_cash = float(case["seed_cash"])
    seed_retained = (
        seed_cash
        + non_cash_assets(seed)
        - liabilities(seed)
        - facts["contributed_capital"][seed]
    )
    facts["cash_and_equivalents"][seed] = seed_cash
    facts["retained_earnings"][seed] = seed_retained

    distributions = case.get("distributions", {})
    prior = seed
    for period in periods:
        retained = (
            facts["retained_earnings"][prior]
            + facts["net_income"][period]
            - float(distributions.get(period, 0.0))
        )
        facts["retained_earnings"][period] = retained
        facts["cash_and_equivalents"][period] = (
            liabilities(period)
            + facts["contributed_capital"][period]
            + retained
            - non_cash_assets(period)
        )
        prior = period

    # --- Balance sheet subtotals ------------------------------------------
    for period in all_periods:
        facts["total_current_assets"][period] = math.fsum(
            (
                facts["cash_and_equivalents"][period],
                facts["accounts_receivable"][period],
                facts["inventory"][period],
                facts["prepaid_expenses"][period],
                facts["other_current_assets"][period],
            )
        )
        facts["total_assets"][period] = math.fsum(
            (
                facts["total_current_assets"][period],
                facts["ppe_net"][period],
                facts["capitalized_software_net"][period],
                facts["goodwill_and_intangibles_net"][period],
                facts["other_non_current_assets"][period],
            )
        )
        facts["total_current_liabilities"][period] = math.fsum(
            (
                facts["accounts_payable"][period],
                facts["accrued_liabilities"][period],
                facts["deferred_revenue_current"][period],
                facts["income_taxes_payable"][period],
                facts["short_term_debt"][period],
                facts["current_portion_long_term_debt"][period],
                facts["other_current_liabilities"][period],
            )
        )
        facts["total_liabilities"][period] = math.fsum(
            (
                facts["total_current_liabilities"][period],
                facts["long_term_debt"][period],
                facts["deferred_revenue_non_current"][period],
                facts["other_long_term_liabilities"][period],
            )
        )
        facts["total_equity"][period] = (
            facts["contributed_capital"][period] + facts["retained_earnings"][period]
        )

    # --- Indirect cash flow statement, derived from balance sheet movement -
    sbc_series = case.get("stock_based_compensation", {})
    prior = seed
    for period in periods:

        def delta(key: str) -> float:
            return facts[key][period] - facts[key][prior]

        net_income = facts["net_income"][period]
        depreciation = facts["depreciation"][period]
        amortization_software = facts["amortization_software"][period]
        amortization_intangibles = facts["amortization_intangibles"][period]
        stock_compensation = float(sbc_series.get(period, 0.0))

        change_receivable = -delta("accounts_receivable")
        change_inventory = -delta("inventory")
        change_prepaid = -(delta("prepaid_expenses") + delta("other_current_assets"))
        change_payable = delta("accounts_payable")
        change_accrued = (
            delta("accrued_liabilities")
            + delta("income_taxes_payable")
            + delta("other_current_liabilities")
        )
        change_deferred_revenue = delta("deferred_revenue_current") + delta(
            "deferred_revenue_non_current"
        )
        change_other_operating = delta("other_long_term_liabilities")

        operating = math.fsum(
            (
                net_income,
                depreciation,
                amortization_software,
                amortization_intangibles,
                stock_compensation,
                change_receivable,
                change_inventory,
                change_prepaid,
                change_payable,
                change_accrued,
                change_deferred_revenue,
                change_other_operating,
            )
        )

        capital_expenditure = -(delta("ppe_net") + depreciation)
        capitalized_software = -(delta("capitalized_software_net") + amortization_software)
        acquisitions = -(
            delta("goodwill_and_intangibles_net") + amortization_intangibles
        )
        change_other_assets = -delta("other_non_current_assets")
        investing = math.fsum(
            (capital_expenditure, capitalized_software, acquisitions, change_other_assets)
        )

        net_debt_issuance = math.fsum(delta(key) for key in _DEBT_KEYS)
        equity_issuance = delta("contributed_capital") - stock_compensation
        distribution = -float(distributions.get(period, 0.0))
        financing = math.fsum((net_debt_issuance, equity_issuance, distribution))

        net_change = math.fsum((operating, investing, financing))

        facts["cf_net_income"][period] = net_income
        facts["cf_depreciation_amortization"][period] = math.fsum(
            (depreciation, amortization_software, amortization_intangibles)
        )
        facts["cf_stock_based_compensation"][period] = stock_compensation
        facts["cf_change_accounts_receivable"][period] = change_receivable
        facts["cf_change_inventory"][period] = change_inventory
        facts["cf_change_prepaid_other_current"][period] = change_prepaid
        facts["cf_change_accounts_payable"][period] = change_payable
        facts["cf_change_accrued_liabilities"][period] = change_accrued
        facts["cf_change_deferred_revenue"][period] = change_deferred_revenue
        facts["cf_change_other_operating"][period] = change_other_operating
        facts["cf_operating"][period] = operating
        facts["cf_capital_expenditure"][period] = capital_expenditure
        facts["cf_capitalized_software"][period] = capitalized_software
        facts["cf_acquisitions_and_intangibles"][period] = acquisitions
        facts["cf_change_other_non_current_assets"][period] = change_other_assets
        facts["cf_investing"][period] = investing
        facts["cf_net_debt_issuance"][period] = net_debt_issuance
        facts["cf_equity_issuance"][period] = equity_issuance
        facts["cf_distributions"][period] = distribution
        facts["cf_financing"][period] = financing
        facts["cf_net_change_in_cash"][period] = net_change
        facts["cf_beginning_cash"][period] = facts["cash_and_equivalents"][prior]
        facts["cf_ending_cash"][period] = facts["cash_and_equivalents"][period]

        prior = period

    # The seed period stays in the fact table as the comparative balance sheet.
    # It is excluded from ``Engagement.periods`` so it never appears in a
    # presented statement, but trailing average-balance metrics read from it.
    return facts


def build_case_engagement(case_key: str) -> Engagement:
    """Materialize a sandbox case as an :class:`Engagement`."""
    case = CASE_LIBRARY[case_key]
    facts = build_case_facts(case)
    return Engagement(
        source="sandbox",
        entity_name=case["target"],
        ticker=None,
        currency=case["currency"],
        periods=list(case["periods"]),
        facts=facts,
        units_note=f"Figures presented in absolute units of {case['currency']}.",
        context=case["context"],
        case_key=case_key,
        comparative_period=case["seed_period"],
    )


def answer_key_adjustments(case_key: str) -> list[Adjustment]:
    """The engagement team's booked adjustments for a case."""
    case = CASE_LIBRARY[case_key]
    return [
        Adjustment(
            label=entry["label"],
            category=entry["category"],
            period_impacts=dict(entry["impacts"]),
            rationale=entry["rationale"],
            status=entry["status"],
            authority=entry.get("authority", ""),
        )
        for entry in case["answer_key"]
    ]


def answer_key_classifications(case_key: str) -> list[ClassifiedItem]:
    """The engagement team's debt-like and non-operating classifications."""
    case = CASE_LIBRARY[case_key]
    return [
        ClassifiedItem(
            label=entry["label"],
            amount=float(entry["amount"]),
            classification=entry["classification"],
            rationale=entry["rationale"],
        )
        for entry in case["debt_like_key"]
    ]


def answer_key_risks(case_key: str) -> list[RiskFlag]:
    """The engagement team's risk register for a case."""
    case = CASE_LIBRARY[case_key]
    return [
        RiskFlag(
            title=entry["title"], severity=entry["severity"], description=entry["description"]
        )
        for entry in case["risk_key"]
    ]


# --------------------------------------------------------------------------- #
# Work comparison engine
# --------------------------------------------------------------------------- #

_STOPWORDS = frozenset(
    {
        "a", "an", "and", "at", "by", "for", "from", "in", "into", "of", "on", "or",
        "the", "to", "with", "not", "no", "is", "are", "was", "were", "be", "been",
        "as", "that", "this", "it", "its", "per", "over", "under", "than", "adjustment",
        "adjustments", "expense", "expenses", "cost", "costs", "item", "items",
    }
)


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {word for word in words if word not in _STOPWORDS and len(word) > 2}


def _similarity(left: str, right: str) -> float:
    """Jaccard overlap of significant tokens, used to pair analyst work to the key."""
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    intersection = len(left_tokens & right_tokens)
    union = len(left_tokens | right_tokens)
    return intersection / union if union else 0.0


MATCH_THRESHOLD = 0.18


@dataclass
class ComparisonResult:
    table: pd.DataFrame
    user_adjusted_ebitda: float
    actual_adjusted_ebitda: float
    variance: float
    variance_percent: float
    reported_ebitda: float
    management_ebitda: float
    matched: list[tuple[Adjustment, Adjustment, float]]
    missed: list[Adjustment]
    unsupported: list[Adjustment]
    period: str
    coverage_percent: float


def compare_to_answer_key(
    engagement: Engagement,
    user_adjustments: Sequence[Adjustment],
    case_key: str,
    period: str,
) -> ComparisonResult:
    """Pair the analyst's adjustments against the engagement team's ledger.

    Matching is a greedy best-first pass over token similarity between labels;
    every analyst entry is either paired with an answer-key item, or reported as
    unsupported by the issued report. Unpaired key items are reported as missed.
    """
    case = CASE_LIBRARY[case_key]
    key_adjustments = answer_key_adjustments(case_key)

    scored: list[tuple[float, int, int]] = []
    for user_index, user_adj in enumerate(user_adjustments):
        for key_index, key_adj in enumerate(key_adjustments):
            score = _similarity(user_adj.label, key_adj.label)
            if score >= MATCH_THRESHOLD:
                scored.append((score, user_index, key_index))
    scored.sort(key=lambda entry: entry[0], reverse=True)

    used_user: set[int] = set()
    used_key: set[int] = set()
    matched: list[tuple[Adjustment, Adjustment, float]] = []
    for score, user_index, key_index in scored:
        if user_index in used_user or key_index in used_key:
            continue
        used_user.add(user_index)
        used_key.add(key_index)
        matched.append((user_adjustments[user_index], key_adjustments[key_index], score))

    unsupported = [
        adjustment
        for index, adjustment in enumerate(user_adjustments)
        if index not in used_user
    ]
    missed = [
        adjustment
        for index, adjustment in enumerate(key_adjustments)
        if index not in used_key
    ]

    rows: list[dict[str, Any]] = []
    for user_adj, key_adj, score in matched:
        user_impact = user_adj.impact(period) if user_adj.is_accepted else 0.0
        key_impact = key_adj.impact(period) if key_adj.is_accepted else 0.0
        rows.append(
            {
                "Outcome": "Identified",
                "Your Adjustment": user_adj.label,
                "Senior Associate Adjustment": key_adj.label,
                "Your Impact": user_impact,
                "Actual Impact": key_impact,
                "Variance": user_impact - key_impact,
                "Your Treatment": user_adj.status,
                "Actual Treatment": key_adj.status,
                "Match Confidence %": score * 100.0,
                "Technical Authority": key_adj.authority,
            }
        )

    for key_adj in missed:
        key_impact = key_adj.impact(period) if key_adj.is_accepted else 0.0
        rows.append(
            {
                "Outcome": "Missed",
                "Your Adjustment": "—",
                "Senior Associate Adjustment": key_adj.label,
                "Your Impact": 0.0,
                "Actual Impact": key_impact,
                "Variance": -key_impact,
                "Your Treatment": "—",
                "Actual Treatment": key_adj.status,
                "Match Confidence %": 0.0,
                "Technical Authority": key_adj.authority,
            }
        )

    for user_adj in unsupported:
        user_impact = user_adj.impact(period) if user_adj.is_accepted else 0.0
        rows.append(
            {
                "Outcome": "Not in the issued report",
                "Your Adjustment": user_adj.label,
                "Senior Associate Adjustment": "—",
                "Your Impact": user_impact,
                "Actual Impact": 0.0,
                "Variance": user_impact,
                "Your Treatment": user_adj.status,
                "Actual Treatment": "—",
                "Match Confidence %": 0.0,
                "Technical Authority": "—",
            }
        )

    order = {"Identified": 0, "Missed": 1, "Not in the issued report": 2}
    rows.sort(key=lambda row: (order[row["Outcome"]], -abs(row["Variance"])))
    table = pd.DataFrame(rows)

    user_total = adjusted_ebitda(engagement, user_adjustments, period)
    actual_total = adjusted_ebitda(engagement, key_adjustments, period)
    variance = (
        NAN
        if is_missing(user_total) or is_missing(actual_total)
        else user_total - actual_total
    )
    variance_percent = safe_divide(variance, actual_total)

    accepted_key = [adj for adj in key_adjustments if adj.is_accepted]
    identified_accepted = sum(
        1 for _, key_adj, _ in matched if key_adj.is_accepted
    )
    coverage = safe_divide(identified_accepted, len(accepted_key))

    return ComparisonResult(
        table=table,
        user_adjusted_ebitda=user_total,
        actual_adjusted_ebitda=actual_total,
        variance=variance,
        variance_percent=NAN if is_missing(variance_percent) else variance_percent * 100.0,
        reported_ebitda=reported_ebitda(engagement, period),
        management_ebitda=float(
            case.get("management_adjusted_ebitda", {}).get(period, NAN)
        ),
        matched=matched,
        missed=missed,
        unsupported=unsupported,
        period=period,
        coverage_percent=NAN if is_missing(coverage) else coverage * 100.0,
    )


# --------------------------------------------------------------------------- #
# Integrity validation
# --------------------------------------------------------------------------- #


def validate_case(case_key: str, tolerance: float = 1e-6) -> list[str]:
    """Assert that a case's three statements articulate. Returns failure messages."""
    case = CASE_LIBRARY[case_key]
    facts = build_case_facts(case)
    failures: list[str] = []

    for period in case["periods"]:
        assets = facts["total_assets"][period]
        liabilities_equity = facts["total_liabilities"][period] + facts["total_equity"][period]
        if abs(assets - liabilities_equity) > tolerance:
            failures.append(
                f"{case['name']} {period}: balance sheet out by "
                f"{assets - liabilities_equity:.10f}"
            )

        net_change = facts["cf_net_change_in_cash"][period]
        beginning = facts["cf_beginning_cash"][period]
        ending = facts["cf_ending_cash"][period]
        if abs(beginning + net_change - ending) > tolerance:
            failures.append(
                f"{case['name']} {period}: cash flow does not foot by "
                f"{beginning + net_change - ending:.10f}"
            )

        if facts["cash_and_equivalents"][period] < 0.0:
            failures.append(
                f"{case['name']} {period}: derived cash balance is negative "
                f"({facts['cash_and_equivalents'][period]:.2f})"
            )

    return failures


def validate_all_cases(tolerance: float = 1e-6) -> list[str]:
    """Validate every case in the library."""
    failures: list[str] = []
    for key in CASE_LIBRARY:
        failures.extend(validate_case(key, tolerance))
    return failures
