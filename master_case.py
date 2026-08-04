"""The Master Case — a completed diligence file, loaded in one click.

The three sandbox cases hand the analyst a blank set of working papers and hide
the answer key until they ask for it. That is the right way to *practise*, and
the wrong way to *learn what the finished thing looks like*. This module fills
every working paper on the ``helios_master`` engagement at once — the QoE
adjustment ledger, the debt-like classification schedule, the risk register, the
SEC narrative risk matrix and the ASC 805 purchase price allocation — so a first
time visitor sees a worked file rather than an empty grid.

**Nothing here is a new number.** The adjustment ledger, the classifications and
the risk register are materialised from ``PROJECT_HELIOS_MASTER``'s own answer
key, so the loaded file and the "Compare to Actual Deal" answer key are the same
data by construction and cannot drift apart. Only the Module 2 scan output and
the Module 3 fair values — which have no home in the case schema — are declared
here.

**No value is rounded.** Every figure below is a literal ``float`` written at
full precision and stored unmodified; the risk impact scores are computed by
``risk_engine.ScoredRisk.score`` rather than transcribed. Display masks are
applied at render time by Streamlit and never touch what is in session state.

The loader writes across four of the five state namespaces described in
``workspace``:

``seed::helios_master::{adjustments,classifications,risks}``   Module 1
``sec::helios_master::matrix``                                 Module 2
``ppa::helios_master::{tangible,intangibles}``                 Module 3

Widget keys are *dropped* rather than written. ``st.data_editor`` stores deltas
against the frame it was handed, so writing a new seed underneath a live editor
would re-apply the analyst's added- and deleted-row deltas against shifted row
positions. Dropping the key makes the editor re-seed cleanly on the next run —
the same discipline ``workspace.push_to_qoe_ledger`` follows.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

import components as ui
import ppa_engine as ppa
import risk_engine as risk
import workspace as ws
from case_studies import (
    answer_key_adjustments,
    answer_key_classifications,
    answer_key_risks,
)
from config import MODE_SANDBOX

CASE_KEY = "helios_master"
ENGAGEMENT_ID = f"case:{CASE_KEY}"

BUTTON_LABEL = "🚀 Load Example Master Case (Project Helios)"
RESET_LABEL = "Clear Data / Start Fresh"
SUCCESS_MESSAGE = "✅ Master Case Loaded: You are now viewing a completed diligence file."

#: Set by the sidebar button, consumed by ``main`` once the engagement resolves.
PENDING_FLAG = "master_case::pending"

#: Set by the loader, consumed once to raise the success toast after the rerun.
LOADED_FLAG = "master_case::loaded"


# --------------------------------------------------------------------------- #
# Module 2 — the narrative risk scan the scanner would have produced
# --------------------------------------------------------------------------- #

#: ``(category, label, severity, occurrences, prior occurrences, rationale)``.
#:
#: Occurrence counts are what a regex detector returns over the registrant's
#: Item 1A and Item 7, and drive the impact score through the frequency and
#: year-over-year emphasis terms. A prior count of zero marks language that is
#: new this year and carries the 2.0x emphasis multiplier.
SEC_RISKS: tuple[tuple[str, str, str, int, int, str], ...] = (
    (
        "Revenue Recognition",
        "ASC 606 Revenue concentration",
        "High",
        14,
        6,
        (
            "Item 1A names the five largest dental service organization customers as 34% of "
            "annual recurring revenue and discloses that those relationships contract on "
            "multi-year prepaid terms. The ASC 606 measurement of a small number of "
            "arrangements therefore drives a disproportionate share of reported revenue. "
            "Mentions more than doubled year over year."
        ),
    ),
    (
        "Impairment & Capitalization",
        "Internal-use software capitalization policy",
        "Medium",
        9,
        7,
        (
            "Item 7 expands the critical accounting estimate covering internal-use software. "
            "Capitalized software grew 178% across the diligence period against 84% growth in "
            "engineering headcount. This is the disclosure that supports the FY2024 GAAP "
            "correction booked in the QoE ledger."
        ),
    ),
    (
        "Debt & Covenants",
        "Covenant headroom on the senior credit facility",
        "Medium",
        5,
        0,
        (
            "New this year. Item 1A adds a risk factor describing two scheduled step-downs in "
            "the maximum net leverage covenant before the contemplated close. Funded debt of "
            "$29.50 million against Adjusted EBITDA of $16.30 million is 1.81x gross, so "
            "headroom is adequate today."
        ),
    ),
)

SEC_FISCAL_YEAR = "FY2024"


def sec_matrix() -> pd.DataFrame:
    """The Module 2 risk matrix as the scanner would hand it over.

    Every row arrives *unaccepted* with a zero haircut, which is the engine's
    standing contract: the scanner proposes and the analyst disposes. Nothing
    reaches the QoE ledger without an explicit acceptance and a dollar figure the
    analyst typed — so loading the master case cannot silently move Adjusted
    EBITDA away from the $16,300,000.0 the ledger supports.

    Impact scores are computed by ``ScoredRisk.score``, not transcribed, so they
    carry full precision and stay correct if the weighting ever changes.
    """
    scored = [
        risk.ScoredRisk(
            category=category,
            label=label,
            severity=severity,
            occurrences=occurrences,
            prior_occurrences=prior,
            rationale=rationale,
            evidence=[],
            sections=["item_1a", "item_7"],
            fiscal_year=SEC_FISCAL_YEAR,
        )
        for category, label, severity, occurrences, prior, rationale in SEC_RISKS
    ]
    return risk.risks_to_matrix(sorted(scored, key=lambda item: -item.score))


# --------------------------------------------------------------------------- #
# Module 3 — the ASC 805 fair values from the valuation specialist's work
# --------------------------------------------------------------------------- #

#: ``{acquired asset: fair value step-up / (down)}``, applied over the schedule
#: seeded from the target's closing balance sheet.
#:
#: The capitalized software step-*down* is the same finding as the QoE ledger's
#: "Aggressive capitalized software" adjustment seen from the balance sheet
#: side: costs that fail ASC 350-40 are not an asset, so the carrying value is
#: written to fair value on Day 1.
TANGIBLE_STEP_UPS: dict[str, float] = {
    "Property, Plant & Equipment, net": 1_200_000.0,
    "Capitalized Software, net": -3_400_000.0,
    "Other Non-Current Assets": 0.0,
}

#: ``{identifiable intangible asset: fair value}``. Lives and valuation methods
#: come from ``ppa_engine.DEFAULT_INTANGIBLES`` and are left as seeded. Classes
#: the specialist did not value stay at zero.
INTANGIBLE_FAIR_VALUES: dict[str, float] = {
    "Customer relationships": 42_600_000.0,
    "Developed technology": 28_400_000.0,
}


def tangible_frame(engagement, period: str | None = None) -> pd.DataFrame:
    """Tangible schedule seeded from the balance sheet, with the step-ups applied."""
    frame = ppa.seed_tangible_frame(engagement, period)
    if frame.empty:
        return frame
    steps = frame[ppa.COL_ASSET].map(lambda label: TANGIBLE_STEP_UPS.get(str(label), 0.0))
    frame[ppa.COL_STEP] = steps.astype(float)
    frame[ppa.COL_FAIR] = frame[ppa.COL_BOOK].astype(float) + frame[ppa.COL_STEP]
    return frame


def intangible_frame() -> pd.DataFrame:
    """Identifiable intangible matrix with the specialist's fair values applied."""
    frame = ppa.seed_intangible_frame()
    values = frame[ppa.COL_INTANGIBLE].map(
        lambda label: INTANGIBLE_FAIR_VALUES.get(str(label), 0.0)
    )
    frame[ppa.COL_INT_FAIR] = values.astype(float)
    return frame


# --------------------------------------------------------------------------- #
# Load and reset
# --------------------------------------------------------------------------- #


def is_master_case(engagement_id: str) -> bool:
    return engagement_id == ENGAGEMENT_ID


def case_option() -> tuple[str, str]:
    """The ``(key, label)`` pair the sandbox case selectbox holds for this case."""
    from case_studies import case_options

    return next(option for option in case_options() if option[0] == CASE_KEY)


def request_load() -> None:
    """Sidebar hand-off, run as a button ``on_click`` callback.

    Two constraints shape this. The sidebar renders before ``main`` resolves the
    engagement, so the button cannot write the working papers itself — it does
    not yet know the engagement id, and on a live-mode or wrong-case run it
    would write to the wrong namespace. And the mode and case selectors are
    rendered *above* the button, so by the time the button's branch executes
    Streamlit has already instantiated them and refuses further writes to their
    keys with ``StreamlitAPIException``.

    A callback resolves both. Streamlit runs ``on_click`` before it re-executes
    the script, at a point where no widget has been instantiated yet, so the
    selectors can be steered freely. The flag is picked up by ``main`` on the
    run that follows, once the engagement exists.
    """
    st.session_state["workspace_mode"] = MODE_SANDBOX
    st.session_state["case_selector"] = case_option()
    st.session_state[PENDING_FLAG] = True


def request_clear() -> None:
    """Reset hand-off, run as a button ``on_click`` callback.

    Deleting a widget key is permitted where overwriting it is not, and a
    callback avoids the question entirely by running before instantiation.
    """
    clear(ENGAGEMENT_ID)


def load(engagement, engagement_id: str) -> None:
    """Populate every working paper for the master case engagement.

    Existing analyst work is *overwritten* — this is the "load the finished
    file" action, and a half-loaded file would be worse than either state. The
    reset button below is the way back.
    """
    seed = lambda name: ws.key("seed", engagement_id, name)  # noqa: E731
    editor = lambda name: ws.key("editor", engagement_id, name)  # noqa: E731

    # -- Module 1: the adjustment ledger, classifications and risk register --
    st.session_state[seed("adjustments")] = ui.adjustments_to_frame(
        answer_key_adjustments(CASE_KEY), engagement.periods
    )
    st.session_state[seed("classifications")] = ui.classifications_to_frame(
        answer_key_classifications(CASE_KEY)
    )
    st.session_state[seed("risks")] = ui.risks_to_frame(answer_key_risks(CASE_KEY))

    # -- Module 2: the narrative risk matrix --
    ws.set_module_state(ws.SEC, engagement_id, "matrix", sec_matrix())

    # -- Module 3: the ASC 805 fair values --
    ws.set_module_state(ws.PPA, engagement_id, "tangible", tangible_frame(engagement))
    ws.set_module_state(ws.PPA, engagement_id, "intangibles", intangible_frame())

    # The editors own their deltas; drop the widget keys so each re-seeds from
    # the frames written above rather than replaying edits against new rows.
    for name in ("adjustments", "classifications", "risks"):
        st.session_state.pop(editor(name), None)
    for name in ("tangible_editor", "intangible_editor", "matrix_editor"):
        st.session_state.pop(ws.key(ws.PPA, engagement_id, name), None)
        st.session_state.pop(ws.key(ws.SEC, engagement_id, name), None)

    # A stale comparison or review panel would describe the previous file.
    st.session_state.pop(f"comparison_run::{engagement_id}", None)
    st.session_state.pop(f"review::{engagement_id}", None)
    ws.clear_namespace(ws.SHARED, engagement_id)

    st.session_state[LOADED_FLAG] = True


def clear(engagement_id: str) -> None:
    """Drop exactly the keys the loader wrote, and nothing else.

    Every removal is a ``pop`` with a default or a prefix sweep, so clearing a
    workspace that was never loaded is a no-op rather than a ``KeyError``. The
    engagement itself is untouched — the case reloads from ``CASE_LIBRARY`` on
    the next run and ``initialise_seeds`` rebuilds empty working papers.
    """
    for name in ("adjustments", "classifications", "risks"):
        st.session_state.pop(ws.key("seed", engagement_id, name), None)
        st.session_state.pop(ws.key("editor", engagement_id, name), None)

    ws.clear_namespace(ws.SEC, engagement_id)
    ws.clear_namespace(ws.PPA, engagement_id)
    ws.clear_namespace(ws.SHARED, engagement_id)

    st.session_state.pop(f"comparison_run::{engagement_id}", None)
    st.session_state.pop(f"review::{engagement_id}", None)
    st.session_state.pop(PENDING_FLAG, None)
    st.session_state.pop(LOADED_FLAG, None)


def consume_pending() -> bool:
    """True once, on the run in which the sidebar button was pressed."""
    return bool(st.session_state.pop(PENDING_FLAG, False))


def consume_loaded() -> bool:
    """True once, on the run that should raise the success message."""
    return bool(st.session_state.pop(LOADED_FLAG, False))
