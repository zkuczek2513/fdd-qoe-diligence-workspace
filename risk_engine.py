"""Year-over-year narrative delta, diligence impact scoring, and QoE hand-off.

Three jobs:

1. Compare a filing's narrative sections against the prior year's and quantify
   exactly what changed, using :mod:`difflib` at sentence granularity.
2. Score the extracted risk flags into TAS terms — severity and EBITDA impact
   potential — with the score decomposed so it can be defended.
3. Translate accepted flags into rows the existing QoE adjustment ledger
   understands, so a narrative finding becomes a quantified adjustment in the
   Module 1 working papers.

Language that is *new this year* carries far more diligence signal than
boilerplate a filer has repeated for a decade, so the delta feeds the score.
"""

from __future__ import annotations

import difflib
import html
import math
from dataclasses import dataclass, field
from typing import Iterable, Sequence

import pandas as pd

from config import ADJUSTMENT_STATUSES, RISK_SEVERITIES
from text_analysis import (
    CATEGORY_CONCENTRATION,
    CATEGORY_CONTROLS,
    CATEGORY_DEBT,
    CATEGORY_GOING_CONCERN,
    CATEGORY_IMPAIRMENT,
    CATEGORY_LEGAL,
    CATEGORY_REVENUE,
    CATEGORY_TAX,
    CATEGORY_WORKING_CAPITAL,
    RiskHit,
    sentences,
    words,
)

NAN = float("nan")

SEVERITY_WEIGHT = {"Low": 1.0, "Medium": 2.0, "High": 3.0, "Critical": 4.0}

# Which QoE adjustment category a narrative risk maps onto when it is pushed
# into the Module 1 ledger.
RISK_TO_ADJUSTMENT_CATEGORY = {
    CATEGORY_REVENUE: "Cut-Off & Revenue Recognition",
    CATEGORY_LEGAL: "Non-Recurring / One-Time",
    CATEGORY_WORKING_CAPITAL: "Reserve & Allowance Adequacy",
    CATEGORY_GOING_CONCERN: "Other Normalization",
    CATEGORY_CONTROLS: "Accounting & GAAP Correction",
    CATEGORY_CONCENTRATION: "Other Normalization",
    CATEGORY_IMPAIRMENT: "Accounting & GAAP Correction",
    CATEGORY_DEBT: "Other Normalization",
    CATEGORY_TAX: "Other Normalization",
}


# --------------------------------------------------------------------------- #
# Narrative delta
# --------------------------------------------------------------------------- #


@dataclass
class NarrativeDelta:
    """Quantified change between a section and its prior-year counterpart."""

    section_key: str
    current_label: str
    prior_label: str
    current_word_count: int
    prior_word_count: int
    similarity: float
    added_sentences: list[str] = field(default_factory=list)
    removed_sentences: list[str] = field(default_factory=list)

    @property
    def word_count_change(self) -> int:
        return self.current_word_count - self.prior_word_count

    @property
    def word_count_change_percent(self) -> float:
        if not self.prior_word_count:
            return NAN
        return float(self.word_count_change) / float(self.prior_word_count) * 100.0

    @property
    def added_word_count(self) -> int:
        return sum(len(words(sentence)) for sentence in self.added_sentences)

    @property
    def removed_word_count(self) -> int:
        return sum(len(words(sentence)) for sentence in self.removed_sentences)


def compare_sections(
    current_text: str,
    prior_text: str,
    section_key: str = "",
    current_label: str = "current",
    prior_label: str = "prior",
) -> NarrativeDelta:
    """Diff two narrative sections at sentence granularity.

    Sentences rather than words: a word-level diff across two 70,000-character
    risk factor sections is both slow and unreadable, while sentence moves are
    what a reviewer actually wants to see.
    """
    current_sentences = sentences(current_text)
    prior_sentences = sentences(prior_text)

    matcher = difflib.SequenceMatcher(None, prior_sentences, current_sentences, autojunk=False)
    added: list[str] = []
    removed: list[str] = []

    for tag, prior_start, prior_end, current_start, current_end in matcher.get_opcodes():
        if tag in ("replace", "insert"):
            added.extend(current_sentences[current_start:current_end])
        if tag in ("replace", "delete"):
            removed.extend(prior_sentences[prior_start:prior_end])

    return NarrativeDelta(
        section_key=section_key,
        current_label=current_label,
        prior_label=prior_label,
        current_word_count=len(words(current_text)),
        prior_word_count=len(words(prior_text)),
        similarity=matcher.ratio(),
        added_sentences=added,
        removed_sentences=removed,
    )


_DIFF_STYLE = """
<style>
.nd-wrap{font-family:Georgia,'Times New Roman',serif;font-size:0.92rem;line-height:1.55;}
.nd-legend{margin:0 0 0.9rem 0;font-size:0.85rem;color:#5A6B7B;}
.nd-chip{display:inline-block;padding:0.08rem 0.45rem;border-radius:3px;margin-right:0.5rem;}
.nd-added{background:#FBE9E7;color:#8E2A20;border-left:3px solid #B3352C;}
.nd-removed{background:#E6F4EE;color:#125C43;border-left:3px solid #1B7F5E;}
.nd-block{padding:0.5rem 0.75rem;margin:0.4rem 0;border-radius:3px;}
.nd-head{font-weight:bold;margin:1.1rem 0 0.4rem 0;color:#1F3A5F;}
.nd-none{color:#5A6B7B;font-style:italic;}
</style>
"""


def render_delta_html(delta: NarrativeDelta, max_items: int = 40) -> str:
    """Side-by-side comparative diff.

    New language is rendered red and deleted language green, per the review
    convention used in this workspace: what a filer *added* this year is the
    finding, so it gets the alarming colour.
    """
    def block(sentence: str, kind: str) -> str:
        return f'<div class="nd-block nd-{kind}">{html.escape(sentence)}</div>'

    added = delta.added_sentences[:max_items]
    removed = delta.removed_sentences[:max_items]

    parts = [_DIFF_STYLE, '<div class="nd-wrap">']
    parts.append(
        '<div class="nd-legend">'
        '<span class="nd-chip nd-added">Added in ' + html.escape(delta.current_label) + "</span>"
        '<span class="nd-chip nd-removed">Removed since ' + html.escape(delta.prior_label) + "</span>"
        f"Similarity {delta.similarity * 100.0:,.2f}% · "
        f"{delta.added_word_count:,} words added · {delta.removed_word_count:,} removed"
        "</div>"
    )

    parts.append(f'<div class="nd-head">New language in {html.escape(delta.current_label)}</div>')
    if added:
        parts.extend(block(sentence, "added") for sentence in added)
        if len(delta.added_sentences) > max_items:
            parts.append(
                f'<div class="nd-none">… and {len(delta.added_sentences) - max_items:,} '
                "further added passages.</div>"
            )
    else:
        parts.append('<div class="nd-none">No new passages detected.</div>')

    parts.append(f'<div class="nd-head">Language removed since {html.escape(delta.prior_label)}</div>')
    if removed:
        parts.extend(block(sentence, "removed") for sentence in removed)
        if len(delta.removed_sentences) > max_items:
            parts.append(
                f'<div class="nd-none">… and {len(delta.removed_sentences) - max_items:,} '
                "further removed passages.</div>"
            )
    else:
        parts.append('<div class="nd-none">No removed passages detected.</div>')

    parts.append("</div>")
    return "".join(parts)


def delta_summary_frame(deltas: Sequence[NarrativeDelta]) -> pd.DataFrame:
    """Tabular summary of every section delta."""
    if not deltas:
        return pd.DataFrame()
    from edgar_client import SECTION_LABELS

    rows = []
    for delta in deltas:
        rows.append(
            {
                "Section": SECTION_LABELS.get(delta.section_key, delta.section_key),
                "Prior word count": float(delta.prior_word_count),
                "Current word count": float(delta.current_word_count),
                "Change": float(delta.word_count_change),
                "Change %": delta.word_count_change_percent,
                "Similarity %": delta.similarity * 100.0,
                "Passages added": float(len(delta.added_sentences)),
                "Passages removed": float(len(delta.removed_sentences)),
            }
        )
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# Diligence impact scoring
# --------------------------------------------------------------------------- #


@dataclass
class ScoredRisk:
    """A risk flag with its diligence impact score and provenance."""

    category: str
    label: str
    severity: str
    occurrences: int
    prior_occurrences: int
    rationale: str
    evidence: list[str] = field(default_factory=list)
    sections: list[str] = field(default_factory=list)
    fiscal_year: str = ""

    @property
    def is_new(self) -> bool:
        return self.prior_occurrences == 0 and self.occurrences > 0

    @property
    def occurrence_change(self) -> int:
        return self.occurrences - self.prior_occurrences

    @property
    def emphasis_multiplier(self) -> float:
        """Weighting for language the filer newly added or materially expanded.

        New disclosure is the strongest narrative signal in diligence: a filer
        adding a risk that was absent last year has usually experienced
        something. Expanded language is weaker but still meaningful.
        """
        if self.is_new:
            return 2.0
        if self.prior_occurrences and self.occurrences > self.prior_occurrences:
            return 1.5
        return 1.0

    @property
    def score(self) -> float:
        """Severity weight × frequency emphasis × year-over-year emphasis."""
        weight = SEVERITY_WEIGHT.get(self.severity, 2.0)
        frequency = 1.0 + math.log1p(float(self.occurrences))
        return weight * frequency * self.emphasis_multiplier


def score_risks(
    current_hits: Iterable[RiskHit],
    prior_hits: Iterable[RiskHit] = (),
    fiscal_year: str = "",
) -> list[ScoredRisk]:
    """Collapse per-section hits into one scored risk per detector."""
    prior_totals: dict[tuple[str, str], int] = {}
    for hit in prior_hits:
        key = (hit.category, hit.label)
        prior_totals[key] = prior_totals.get(key, 0) + hit.occurrences

    merged: dict[tuple[str, str], ScoredRisk] = {}
    for hit in current_hits:
        key = (hit.category, hit.label)
        existing = merged.get(key)
        if existing is None:
            merged[key] = ScoredRisk(
                category=hit.category,
                label=hit.label,
                severity=hit.severity,
                occurrences=hit.occurrences,
                prior_occurrences=prior_totals.get(key, 0),
                rationale=hit.rationale,
                evidence=list(hit.evidence),
                sections=[hit.section_key] if hit.section_key else [],
                fiscal_year=fiscal_year or hit.fiscal_year,
            )
        else:
            existing.occurrences += hit.occurrences
            for sentence in hit.evidence:
                if len(existing.evidence) < 5 and sentence not in existing.evidence:
                    existing.evidence.append(sentence)
            if hit.section_key and hit.section_key not in existing.sections:
                existing.sections.append(hit.section_key)

    ranked = sorted(merged.values(), key=lambda risk: (-risk.score, risk.category, risk.label))
    return ranked


# --------------------------------------------------------------------------- #
# The reviewable matrix
# --------------------------------------------------------------------------- #

COL_ACCEPT = "Accept"
COL_CATEGORY = "Risk Category"
COL_RISK = "Flagged Risk"
COL_SEVERITY = "Severity"
COL_OCCURRENCES = "Mentions"
COL_PRIOR = "Prior Year"
COL_NEW = "New This Year"
COL_SCORE = "Impact Score"
COL_HAIRCUT = "EBITDA Haircut"
COL_NOTE = "Diligence Note"

MATRIX_COLUMNS = (
    COL_ACCEPT,
    COL_CATEGORY,
    COL_RISK,
    COL_SEVERITY,
    COL_OCCURRENCES,
    COL_PRIOR,
    COL_NEW,
    COL_SCORE,
    COL_HAIRCUT,
    COL_NOTE,
)


def risks_to_matrix(risks: Sequence[ScoredRisk]) -> pd.DataFrame:
    """Build the analyst-reviewable risk matrix.

    Every flag starts *unaccepted* with a zero haircut. The scanner proposes;
    the analyst disposes. Nothing reaches the QoE ledger without an explicit
    decision and a dollar figure the analyst typed.
    """
    if not risks:
        return pd.DataFrame(
            {
                COL_ACCEPT: pd.Series(dtype=bool),
                COL_CATEGORY: pd.Series(dtype=str),
                COL_RISK: pd.Series(dtype=str),
                COL_SEVERITY: pd.Series(dtype=str),
                COL_OCCURRENCES: pd.Series(dtype=float),
                COL_PRIOR: pd.Series(dtype=float),
                COL_NEW: pd.Series(dtype=bool),
                COL_SCORE: pd.Series(dtype=float),
                COL_HAIRCUT: pd.Series(dtype=float),
                COL_NOTE: pd.Series(dtype=str),
            }
        )

    return pd.DataFrame(
        [
            {
                COL_ACCEPT: False,
                COL_CATEGORY: risk.category,
                COL_RISK: risk.label,
                COL_SEVERITY: risk.severity,
                COL_OCCURRENCES: float(risk.occurrences),
                COL_PRIOR: float(risk.prior_occurrences),
                COL_NEW: risk.is_new,
                COL_SCORE: risk.score,
                COL_HAIRCUT: 0.0,
                COL_NOTE: risk.rationale,
            }
            for risk in risks
        ],
        columns=list(MATRIX_COLUMNS),
    )


def accepted_rows(matrix: pd.DataFrame) -> pd.DataFrame:
    """Rows the analyst accepted that carry a non-zero haircut."""
    if matrix is None or matrix.empty:
        return pd.DataFrame(columns=list(MATRIX_COLUMNS))
    accepted = matrix[matrix[COL_ACCEPT].fillna(False).astype(bool)]
    haircuts = pd.to_numeric(accepted[COL_HAIRCUT], errors="coerce").fillna(0.0)
    return accepted[haircuts != 0.0]


def matrix_to_ledger_rows(
    matrix: pd.DataFrame,
    periods: Sequence[str],
    target_period: str,
    fiscal_year: str = "",
) -> pd.DataFrame:
    """Translate accepted risk rows into QoE adjustment ledger rows.

    The output matches the Module 1 adjustment editor schema exactly — label,
    category, treatment, one column per diligence period, rationale — so the
    rows drop straight into the existing ledger without a translation layer.

    A haircut is entered as a positive number and applied as a *negative*
    EBITDA impact, because a narrative risk reduces earnings quality. The
    sign is applied here rather than asked of the analyst.
    """
    rows: list[dict[str, object]] = []
    for _, row in accepted_rows(matrix).iterrows():
        magnitude = abs(float(pd.to_numeric(row[COL_HAIRCUT], errors="coerce") or 0.0))
        if magnitude == 0.0:
            continue

        category = RISK_TO_ADJUSTMENT_CATEGORY.get(
            str(row[COL_CATEGORY]), "Other Normalization"
        )
        source = f" ({fiscal_year} SEC filing)" if fiscal_year else " (SEC filing)"
        note = str(row.get(COL_NOTE) or "").strip()

        record: dict[str, object] = {
            "Adjustment": f"SEC narrative risk — {row[COL_RISK]}{source}",
            "Category": category,
            "Treatment": ADJUSTMENT_STATUSES[0],
            "Rationale / Support": (
                f"Identified by the SEC narrative risk scanner in {row[COL_CATEGORY]} "
                f"(severity {row[COL_SEVERITY]}, {int(float(row[COL_OCCURRENCES]))} mentions). "
                f"{note} Haircut quantified by the analyst; corroborate against the "
                "underlying schedule before it is presented as a supported adjustment."
            ),
        }
        for period in periods:
            record[period] = -magnitude if period == target_period else 0.0
        rows.append(record)

    columns = ["Adjustment", "Category", "Treatment", *periods, "Rationale / Support"]
    if not rows:
        empty = pd.DataFrame(columns=columns)
        for period in periods:
            empty[period] = empty[period].astype(float)
        return empty
    return pd.DataFrame(rows, columns=columns)


def matrix_totals(matrix: pd.DataFrame) -> tuple[int, float, float]:
    """(accepted count, total haircut, total impact score of accepted rows)."""
    accepted = accepted_rows(matrix)
    if accepted.empty:
        return 0, 0.0, 0.0
    haircut = math.fsum(
        abs(float(value))
        for value in pd.to_numeric(accepted[COL_HAIRCUT], errors="coerce").fillna(0.0)
    )
    score = math.fsum(
        float(value)
        for value in pd.to_numeric(accepted[COL_SCORE], errors="coerce").fillna(0.0)
    )
    return len(accepted), haircut, score


def severity_options() -> list[str]:
    return list(RISK_SEVERITIES)
