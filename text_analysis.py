"""Rule-based narrative risk extraction for M&A diligence.

A deliberately transparent scanner: every flag traces to a named regular
expression and the sentence that triggered it, so a finding can be defended in a
report rather than attributed to an opaque score. That is the same standard a
TAS working paper is held to — an adjustment is only as good as the evidence
behind it.

All metrics are counts and ratios carried at full precision; nothing here is
rounded (see the numerical integrity policy in :mod:`config`).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Iterable, Sequence

# --------------------------------------------------------------------------- #
# Risk taxonomy
# --------------------------------------------------------------------------- #

CATEGORY_REVENUE = "Revenue Recognition (ASC 606)"
CATEGORY_LEGAL = "Legal & Regulatory Contingencies"
CATEGORY_WORKING_CAPITAL = "Working Capital & Liquidity"
CATEGORY_GOING_CONCERN = "Going Concern & Solvency"
CATEGORY_CONTROLS = "Internal Control & Reporting Quality"
CATEGORY_CONCENTRATION = "Customer & Supplier Concentration"
CATEGORY_IMPAIRMENT = "Impairment & Asset Recoverability"
CATEGORY_DEBT = "Debt, Covenants & Capital Structure"
CATEGORY_TAX = "Tax Exposure"

RISK_CATEGORIES = (
    CATEGORY_REVENUE,
    CATEGORY_LEGAL,
    CATEGORY_WORKING_CAPITAL,
    CATEGORY_GOING_CONCERN,
    CATEGORY_CONTROLS,
    CATEGORY_CONCENTRATION,
    CATEGORY_IMPAIRMENT,
    CATEGORY_DEBT,
    CATEGORY_TAX,
)

# Base severity by category, expressed on the app's four-point scale. A pattern
# may override it where the specific language is more or less alarming than the
# category average.
_BASE_SEVERITY = {
    CATEGORY_REVENUE: "High",
    CATEGORY_LEGAL: "Medium",
    CATEGORY_WORKING_CAPITAL: "Medium",
    CATEGORY_GOING_CONCERN: "Critical",
    CATEGORY_CONTROLS: "High",
    CATEGORY_CONCENTRATION: "Medium",
    CATEGORY_IMPAIRMENT: "Medium",
    CATEGORY_DEBT: "Medium",
    CATEGORY_TAX: "Low",
}


@dataclass(frozen=True)
class RiskPattern:
    """One named detector."""

    category: str
    label: str
    pattern: str
    severity: str = ""
    rationale: str = ""

    @property
    def effective_severity(self) -> str:
        return self.severity or _BASE_SEVERITY.get(self.category, "Medium")


PATTERNS: tuple[RiskPattern, ...] = (
    # --- Revenue recognition ------------------------------------------------
    RiskPattern(
        CATEGORY_REVENUE,
        "Revenue recognition judgment and estimates",
        r"\b(revenue recognition|recognize[sd]?\s+revenue|ASC\s*606|"
        r"topic\s*606|performance obligation)\b",
        rationale=(
            "Language acknowledging judgment in revenue recognition. Test cut-off, "
            "multi-element arrangements and the timing of control transfer."
        ),
    ),
    RiskPattern(
        CATEGORY_REVENUE,
        "Variable consideration, rebates and returns",
        r"\b(variable consideration|right of return|sales returns?|"
        r"rebates?|price concessions?|chargebacks?)\b",
        rationale=(
            "Variable consideration is constrained under ASC 606-10-32-11. Understated "
            "reserves inflate revenue and EBITDA directly."
        ),
    ),
    RiskPattern(
        CATEGORY_REVENUE,
        "Percentage-of-completion / over-time recognition",
        r"\b(percentage[- ]of[- ]completion|over time|input method|"
        r"cost[- ]to[- ]cost|contract asset|unbilled receivable)\b",
        severity="High",
        rationale=(
            "Over-time recognition depends on management's estimate of progress. "
            "Unbilled receivables growing faster than billings is a classic red flag."
        ),
    ),
    RiskPattern(
        CATEGORY_REVENUE,
        "Deferred revenue and unearned balances",
        r"\b(deferred revenue|unearned revenue|contract liabilit(y|ies)|"
        r"remaining performance obligation)\b",
        rationale=(
            "Deferred revenue is a cash-collected obligation the buyer inherits. Size the "
            "cost to fulfil and consider debt-like treatment in the value bridge."
        ),
    ),
    RiskPattern(
        CATEGORY_REVENUE,
        "Channel, distributor or sell-in arrangements",
        r"\b(sell[- ]in|channel stuffing|distributor inventor(y|ies)|"
        r"bill[- ]and[- ]hold)\b",
        severity="Critical",
        rationale=(
            "Bill-and-hold and channel loading are the textbook mechanisms for pulling "
            "revenue forward. Confirm shipping terms and distributor sell-through."
        ),
    ),
    # --- Legal and regulatory ----------------------------------------------
    RiskPattern(
        CATEGORY_LEGAL,
        "Litigation and legal proceedings",
        r"\b(litigation|lawsuits?|legal proceedings?|class action|"
        r"claims? against (us|the company))\b",
        rationale=(
            "Quantify the reasonably possible loss range and test it against the recorded "
            "accrual under ASC 450-20."
        ),
    ),
    RiskPattern(
        CATEGORY_LEGAL,
        "Government investigation or enforcement",
        r"\b(subpoena|investigation by|SEC investigation|Department of Justice|"
        r"grand jury|enforcement action|consent decree|deferred prosecution)\b",
        severity="Critical",
        rationale=(
            "An open enforcement matter is a structural issue, not a pricing one. Expect "
            "a specific indemnity and possibly an escrow."
        ),
    ),
    RiskPattern(
        CATEGORY_LEGAL,
        "Regulatory compliance exposure",
        r"\b(regulatory (action|scrutiny|proceedings?)|non[- ]?compliance|"
        r"violation of|penalt(y|ies)|fines?)\b",
        rationale="Assess whether remediation cost is recurring and belongs in the run-rate.",
    ),
    RiskPattern(
        CATEGORY_LEGAL,
        "Environmental remediation liability",
        r"\b(environmental (remediation|liabilit|matters)|superfund|"
        r"contaminat(ion|ed)|clean[- ]?up (costs?|obligations?))\b",
        severity="High",
        rationale=(
            "Remediation obligations are frequently unrecorded or understated and are "
            "debt-like in the value bridge."
        ),
    ),
    # --- Working capital and liquidity -------------------------------------
    RiskPattern(
        CATEGORY_WORKING_CAPITAL,
        "Receivable collectability and credit losses",
        r"\b(allowance for (doubtful|credit losses)|uncollectible|"
        r"days sales outstanding|aging of (accounts )?receivable|ASC\s*326)\b",
        rationale=(
            "Compare the allowance to the aging and to realised write-off history. A flat "
            "allowance against a deteriorating aging is a quantifiable QoE adjustment."
        ),
    ),
    RiskPattern(
        CATEGORY_WORKING_CAPITAL,
        "Inventory obsolescence and valuation",
        r"\b(inventory (obsolescence|reserves?|write[- ]?downs?|valuation)|"
        r"excess and obsolete|slow[- ]moving|lower of cost)\b",
        severity="High",
        rationale=(
            "Test the reserve methodology against aged stock and realised recovery rates "
            "under ASC 330-10-35."
        ),
    ),
    RiskPattern(
        CATEGORY_WORKING_CAPITAL,
        "Liquidity and working capital strain",
        r"\b(liquidity (risk|constraints?|concerns?)|working capital (deficit|needs?)|"
        r"insufficient (cash|liquidity)|cash flow (constraints?|difficulties))\b",
        severity="High",
        rationale="Reconcile to the cash flow statement and the revolver availability schedule.",
    ),
    RiskPattern(
        CATEGORY_WORKING_CAPITAL,
        "Supplier financing and receivable factoring",
        r"\b(supply chain financing|reverse factoring|factoring (of|our) receivables?|"
        r"securitization of receivables?)\b",
        severity="High",
        rationale=(
            "These programmes flatter reported working capital and operating cash flow. "
            "Normalise DSO and DPO for the facility before setting the peg."
        ),
    ),
    # --- Going concern ------------------------------------------------------
    RiskPattern(
        CATEGORY_GOING_CONCERN,
        "Going concern / substantial doubt",
        r"\b(going concern|substantial doubt|ability to continue as a going concern)\b",
        severity="Critical",
        rationale=(
            "Substantial doubt disclosed under ASC 205-40 is a threshold issue that "
            "precedes any pricing discussion."
        ),
    ),
    RiskPattern(
        CATEGORY_GOING_CONCERN,
        "Restructuring, impairment of operations or wind-down",
        r"\b(restructuring (plan|charges?|program)|wind[- ]?down|"
        r"discontinued operations|exit activities)\b",
        rationale="Separate genuinely non-recurring exit costs from recurring cost of doing business.",
    ),
    # --- Internal control ---------------------------------------------------
    RiskPattern(
        CATEGORY_CONTROLS,
        "Material weakness in internal control",
        r"\b(material weakness(es)?|significant deficienc(y|ies)|"
        r"not effective|ineffective (internal )?control)\b",
        severity="Critical",
        rationale=(
            "A material weakness undermines reliance on every number in the QoE. Expand "
            "substantive testing and consider an audit-readiness cost adjustment."
        ),
    ),
    RiskPattern(
        CATEGORY_CONTROLS,
        "Restatement or revision of prior financials",
        r"\b(restat(e|ed|ement)|revision of (previously issued|prior)|"
        r"out[- ]of[- ]period adjustment|error in (our )?financial statements)\b",
        severity="Critical",
        rationale="A restatement resets the diligence period. Re-perform on corrected figures.",
    ),
    RiskPattern(
        CATEGORY_CONTROLS,
        "Critical accounting estimates and judgments",
        r"\b(critical accounting (estimates?|policies)|significant (judgment|estimates?)|"
        r"actual results (could|may) differ)\b",
        severity="Low",
        rationale="Standard boilerplate; useful mainly to locate where management sees estimation risk.",
    ),
    # --- Concentration ------------------------------------------------------
    RiskPattern(
        CATEGORY_CONCENTRATION,
        "Customer concentration",
        r"\b(customer concentration|(single|one) customer accounted for|"
        r"largest customers?|significant portion of (our )?(revenue|sales))\b",
        rationale=(
            "Quantify the top-five share and check contract tenure. Concentration drives "
            "both the multiple and the structure."
        ),
    ),
    RiskPattern(
        CATEGORY_CONCENTRATION,
        "Supplier or single-source dependency",
        r"\b(single (source|supplier)|sole source|supplier concentration|"
        r"depend(ent|ence) on (a limited number of )?suppliers?)\b",
        rationale="Assess substitutability and the margin impact of a forced resourcing event.",
    ),
    # --- Impairment ---------------------------------------------------------
    RiskPattern(
        CATEGORY_IMPAIRMENT,
        "Goodwill and intangible impairment",
        r"\b(goodwill impairment|impairment (charge|loss|of (goodwill|intangible))|"
        r"reporting unit.{0,40}fair value|ASC\s*350)\b",
        severity="High",
        rationale=(
            "An impairment signals the acquired growth thesis did not materialise. Review "
            "the prior deal model against realised results."
        ),
    ),
    RiskPattern(
        CATEGORY_IMPAIRMENT,
        "Long-lived asset recoverability",
        r"\b(long[- ]lived assets?|recoverab(le|ility)|asset group|"
        r"triggering event|ASC\s*360)\b",
        rationale="Check whether sustaining capex has been deferred, understating the true cost base.",
    ),
    # --- Debt ---------------------------------------------------------------
    RiskPattern(
        CATEGORY_DEBT,
        "Covenant compliance and waivers",
        r"\b(covenant (compliance|violation|breach)|financial covenants?|"
        r"waiver|forbearance|default under|cross[- ]default)\b",
        severity="High",
        rationale=(
            "A waiver is date-specific and does not reset forward covenants. Confirm the "
            "facility survives a change of control."
        ),
    ),
    RiskPattern(
        CATEGORY_DEBT,
        "Refinancing and maturity risk",
        r"\b(refinanc(e|ing)|maturit(y|ies) of (our )?(debt|indebtedness)|"
        r"repayment obligations?|substantial indebtedness)\b",
        rationale="Map the maturity wall against the projected cash generation in the DCF.",
    ),
    RiskPattern(
        CATEGORY_DEBT,
        "Off-balance-sheet and lease obligations",
        r"\b(off[- ]balance[- ]sheet|operating lease (obligations?|commitments?)|"
        r"finance lease|guarantees? of indebtedness|variable interest entit(y|ies))\b",
        rationale="Common source of unrecorded debt-like items for the value bridge.",
    ),
    # --- Tax ----------------------------------------------------------------
    RiskPattern(
        CATEGORY_TAX,
        "Uncertain tax positions",
        r"\b(uncertain tax positions?|unrecognized tax benefits?|"
        r"tax examinations?|transfer pricing|ASC\s*740)\b",
        rationale="Quantify the exposure and confirm indemnity coverage for pre-closing periods.",
    ),
    RiskPattern(
        CATEGORY_TAX,
        "Valuation allowance on deferred tax assets",
        r"\b(valuation allowance|deferred tax assets?.{0,60}realiz)\b",
        rationale="A valuation allowance signals doubt about future taxable income.",
    ),
)

# Hedging and uncertainty language. Density of this vocabulary is a recognised
# proxy for disclosure opacity in the accounting literature.
HEDGING_TERMS = (
    "may", "might", "could", "possibly", "potentially", "approximately",
    "substantially", "generally", "typically", "believe", "expect", "anticipate",
    "estimate", "intend", "plan", "seek", "uncertain", "uncertainty", "risk",
    "adverse", "adversely", "materially", "no assurance", "cannot predict",
)


# --------------------------------------------------------------------------- #
# Scanning
# --------------------------------------------------------------------------- #

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z(])")
_WORD = re.compile(r"[A-Za-z][A-Za-z'\-]*")
_WHITESPACE_RUN = re.compile(r"\s+")


def normalize_whitespace(text: str) -> str:
    """Collapse every whitespace run to a single space.

    Detector patterns contain literal spaces, so a phrase broken across a line
    ("allowance for credit\nlosses") would silently fail to match. Filing text
    arrives wrapped in unpredictable ways depending on the source markup, so
    normalising once here makes every pattern independent of layout.
    """
    return _WHITESPACE_RUN.sub(" ", text or "").strip()


def sentences(text: str) -> list[str]:
    """Split narrative text into sentences."""
    if not text:
        return []
    return [part.strip() for part in _SENTENCE_SPLIT.split(text) if part.strip()]


def words(text: str) -> list[str]:
    return _WORD.findall(text or "")


@dataclass
class RiskHit:
    """One detector firing against one section of one filing."""

    category: str
    label: str
    severity: str
    occurrences: int
    rationale: str
    evidence: list[str] = field(default_factory=list)
    section_key: str = ""
    fiscal_year: str = ""
    form: str = ""

    @property
    def evidence_text(self) -> str:
        return "\n\n".join(self.evidence)


def scan_text(
    text: str,
    section_key: str = "",
    fiscal_year: str = "",
    form: str = "",
    max_evidence: int = 3,
    patterns: Sequence[RiskPattern] = PATTERNS,
) -> list[RiskHit]:
    """Run every detector over one block of filing text.

    Evidence is captured as the sentence containing each match, truncated to a
    readable length, so the analyst can judge the hit rather than trust it.
    """
    if not text:
        return []

    text = normalize_whitespace(text)
    sentence_list = sentences(text)
    hits: list[RiskHit] = []

    for rule in patterns:
        try:
            regex = re.compile(rule.pattern, re.IGNORECASE)
        except re.error:  # pragma: no cover - guards against a malformed pattern
            continue

        occurrences = len(regex.findall(text))
        if not occurrences:
            continue

        evidence: list[str] = []
        for sentence in sentence_list:
            if len(evidence) >= max_evidence:
                break
            if regex.search(sentence):
                snippet = sentence if len(sentence) <= 400 else sentence[:400] + "…"
                evidence.append(snippet)

        hits.append(
            RiskHit(
                category=rule.category,
                label=rule.label,
                severity=rule.effective_severity,
                occurrences=occurrences,
                rationale=rule.rationale,
                evidence=evidence,
                section_key=section_key,
                fiscal_year=fiscal_year,
                form=form,
            )
        )

    hits.sort(key=lambda hit: (-hit.occurrences, hit.category, hit.label))
    return hits


# --------------------------------------------------------------------------- #
# Readability and disclosure metrics
# --------------------------------------------------------------------------- #


def _count_syllables(word: str) -> int:
    """Approximate syllable count, used for the readability index."""
    token = word.lower().strip("'-")
    if not token:
        return 0
    vowel_groups = re.findall(r"[aeiouy]+", token)
    count = len(vowel_groups)
    if token.endswith("e") and count > 1 and not token.endswith(("le", "ee")):
        count -= 1
    return max(1, count)


@dataclass
class ReadabilityMetrics:
    """Disclosure-quality metrics for one section."""

    word_count: int
    sentence_count: int
    average_sentence_length: float
    complex_word_ratio: float
    hedging_density_per_thousand: float
    flesch_kincaid_grade: float

    def as_dict(self) -> dict[str, float]:
        return {
            "Word count": float(self.word_count),
            "Sentence count": float(self.sentence_count),
            "Average sentence length (words)": self.average_sentence_length,
            "Complex word ratio %": self.complex_word_ratio,
            "Hedging terms per 1,000 words": self.hedging_density_per_thousand,
            "Flesch-Kincaid grade level": self.flesch_kincaid_grade,
        }


NAN = float("nan")


def readability(text: str) -> ReadabilityMetrics:
    """Compute disclosure metrics at full precision.

    The Flesch-Kincaid grade level is included because filings that become
    harder to read year over year, without a change in business complexity, are
    a documented signal of deteriorating disclosure quality.
    """
    text = normalize_whitespace(text)
    word_list = words(text)
    sentence_list = sentences(text)
    word_count = len(word_list)
    sentence_count = len(sentence_list)

    if word_count == 0 or sentence_count == 0:
        return ReadabilityMetrics(word_count, sentence_count, NAN, NAN, NAN, NAN)

    syllables = [_count_syllables(word) for word in word_list]
    total_syllables = math.fsum(float(count) for count in syllables)
    complex_words = sum(1 for count in syllables if count >= 3)

    average_sentence_length = float(word_count) / float(sentence_count)
    complex_word_ratio = float(complex_words) / float(word_count) * 100.0
    syllables_per_word = total_syllables / float(word_count)

    lowered = (text or "").lower()
    hedging_hits = 0
    for term in HEDGING_TERMS:
        hedging_hits += len(re.findall(r"\b" + re.escape(term) + r"\b", lowered))
    hedging_density = float(hedging_hits) / float(word_count) * 1000.0

    grade = 0.39 * average_sentence_length + 11.8 * syllables_per_word - 15.59

    return ReadabilityMetrics(
        word_count=word_count,
        sentence_count=sentence_count,
        average_sentence_length=average_sentence_length,
        complex_word_ratio=complex_word_ratio,
        hedging_density_per_thousand=hedging_density,
        flesch_kincaid_grade=grade,
    )


def aggregate_by_category(hits: Iterable[RiskHit]) -> dict[str, int]:
    """Total occurrences per risk category."""
    totals: dict[str, int] = {category: 0 for category in RISK_CATEGORIES}
    for hit in hits:
        totals[hit.category] = totals.get(hit.category, 0) + hit.occurrences
    return totals
