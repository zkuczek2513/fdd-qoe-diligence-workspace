"""Cross-module state bus and the QoE ledger hand-off.

The three modules share one engagement but render independently: only the active
module executes on a given run, so Module 3 cannot read a widget that lives in
Module 1. Streamlit also garbage-collects widget state for widgets it did not
render, which makes ``st.session_state["editor::…"]`` unusable as a source of
truth across modules.

The fix is a small publish/consume bus. Whenever a module renders, it publishes
its materialised outputs under a ``shared::`` namespace — plain values, not
widget state — and other modules consume from there. Three namespaces are kept
rigorously separate so no module can overwrite another's inputs:

``seed::<engagement>::<name>``     immutable editor seeds (Module 1)
``editor::<engagement>::<name>``   Streamlit-owned widget state
``shared::<engagement>::<name>``   published cross-module outputs
``sec::<engagement>::<name>``      Module 2 private state
``ppa::<engagement>::<name>``      Module 3 private state
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

SHARED = "shared"
SEC = "sec"
PPA = "ppa"


def key(namespace: str, engagement_id: str, name: str) -> str:
    """Build a namespaced session key."""
    return f"{namespace}::{engagement_id}::{name}"


def publish(engagement_id: str, name: str, value: Any) -> None:
    """Record a module output for other modules to consume."""
    st.session_state[key(SHARED, engagement_id, name)] = value


def consume(engagement_id: str, name: str, default: Any = None) -> Any:
    """Read another module's published output."""
    return st.session_state.get(key(SHARED, engagement_id, name), default)


def module_state(namespace: str, engagement_id: str, name: str, factory) -> Any:
    """Get-or-create a module-private value."""
    state_key = key(namespace, engagement_id, name)
    if state_key not in st.session_state:
        st.session_state[state_key] = factory()
    return st.session_state[state_key]


def set_module_state(namespace: str, engagement_id: str, name: str, value: Any) -> None:
    st.session_state[key(namespace, engagement_id, name)] = value


def clear_namespace(namespace: str, engagement_id: str) -> None:
    """Drop every key a module owns for one engagement."""
    prefix = f"{namespace}::{engagement_id}::"
    for state_key in [k for k in st.session_state if k.startswith(prefix)]:
        st.session_state.pop(state_key, None)


# --------------------------------------------------------------------------- #
# QoE ledger hand-off
# --------------------------------------------------------------------------- #


def current_adjustment_frame(
    engagement_id: str, seed_key_name: str, periods
) -> pd.DataFrame:
    """The adjustment ledger as it currently stands.

    Prefers the frame Module 1 published on its last render, which includes the
    analyst's in-editor edits. Falls back to the immutable seed when Module 1 has
    not been visited this session.
    """
    published = consume(engagement_id, "adjustments_frame")
    if isinstance(published, pd.DataFrame):
        return published.copy()
    seeded = st.session_state.get(seed_key_name)
    if isinstance(seeded, pd.DataFrame):
        return seeded.copy()

    columns = ["Adjustment", "Category", "Treatment", *periods, "Rationale / Support"]
    frame = pd.DataFrame(columns=columns)
    for period in periods:
        frame[period] = frame[period].astype(float)
    return frame


def push_to_qoe_ledger(
    engagement_id: str,
    seed_key_name: str,
    widget_key_name: str,
    new_rows: pd.DataFrame,
    periods,
) -> int:
    """Append rows to the Module 1 adjustment ledger.

    The editor's widget state stores *deltas* against the frame it was given —
    added rows, edited cells, deleted rows. Growing the seed underneath a live
    widget would re-apply those deltas against different row positions and
    corrupt the ledger, so the widget key is dropped and the editor re-seeds from
    the combined frame on the next run.

    Returns the number of rows appended.
    """
    if new_rows is None or new_rows.empty:
        return 0

    base = current_adjustment_frame(engagement_id, seed_key_name, periods)

    columns = ["Adjustment", "Category", "Treatment", *periods, "Rationale / Support"]
    for column in columns:
        if column not in base.columns:
            base[column] = 0.0 if column in periods else ""
        if column not in new_rows.columns:
            new_rows[column] = 0.0 if column in periods else ""

    combined = pd.concat(
        [base[columns], new_rows[columns]], ignore_index=True
    ).reset_index(drop=True)
    for period in periods:
        combined[period] = pd.to_numeric(combined[period], errors="coerce").fillna(0.0)

    st.session_state[seed_key_name] = combined
    st.session_state.pop(widget_key_name, None)
    publish(engagement_id, "adjustments_frame", combined)
    return len(new_rows)
