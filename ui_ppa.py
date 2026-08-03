"""Module 3 — ASC 805 Purchase Price Allocation Engine (UI).

Imports the enterprise value and net working capital produced by the Module 1
workspace, takes fair value step-ups and identifiable intangibles from the
analyst, computes the deferred tax consequences of both, and derives goodwill or
a bargain purchase gain along with the Day 1 opening balance sheet.
"""

from __future__ import annotations

import streamlit as st

import components as ui
import ppa_engine as ppa
import workspace as ws
from export import ReportPayload, ReportSection, safe_filename
from finance_logic import (
    is_missing,
    net_working_capital,
    safe_divide,
    total_funded_debt,
)


def _default_consideration(engagement, engagement_id: str) -> tuple[float, str]:
    """Consideration imported from Module 1, with the provenance to display."""
    valuation = ws.consume(engagement_id, "valuation")
    nwc = net_working_capital(engagement, engagement.latest_period)
    imported, _ = ppa.import_from_workspace(valuation, nwc)

    if not is_missing(imported) and imported > 0.0:
        return imported, "enterprise value from the Module 1 DCF and value bridge"

    published = ws.consume(engagement_id, "adjusted_ebitda")
    if published is not None and not is_missing(published) and published > 0.0:
        return published * 9.0, "9.0x the Module 1 Adjusted EBITDA (no DCF run yet)"

    return 0.0, "no Module 1 valuation available — enter the consideration directly"


def render(engagement, engagement_id: str, settings: dict) -> None:
    """Render Module 3."""
    full_precision = settings["full_precision"]
    currency = engagement.currency
    period = engagement.latest_period

    st.subheader("ASC 805 Purchase Price Allocation")
    st.markdown(
        "Allocates the consideration transferred across the acquired balance sheet at fair value "
        "and derives the residual goodwill. The consideration and working capital are imported "
        "from your Module 1 workspace, so a change to a QoE adjustment flows through the DCF, "
        "into the value bridge, and lands here."
    )

    imported_value, provenance = _default_consideration(engagement, engagement_id)
    nwc_actual = net_working_capital(engagement, period)
    book_value = ppa.book_net_assets(engagement, period)

    # ------------------------------------------------------------ inputs --
    st.divider()
    left, middle, right = st.columns(3)
    with left:
        consideration = st.number_input(
            f"Consideration transferred ({currency})",
            value=float(imported_value),
            step=1_000_000.0,
            format="%.2f",
            key=ws.key(ws.PPA, engagement_id, "consideration"),
            help="Imported from Module 1. Override to price a different scenario.",
        )
        st.caption(f"Imported from {provenance}.")
    with middle:
        tax_rate = (
            st.slider(
                "Marginal tax rate (%)",
                0.0,
                45.0,
                ppa.DEFAULT_MARGINAL_TAX_RATE * 100.0,
                step=0.25,
                key=ws.key(ws.PPA, engagement_id, "tax_rate"),
            )
            / 100.0
        )
        st.caption("Applied to the book-versus-tax basis difference created by the step-ups.")
    with right:
        structure = st.radio(
            "Transaction structure",
            ppa.TAX_STRUCTURES,
            key=ws.key(ws.PPA, engagement_id, "structure"),
            help=(
                "Deferred tax arises only where book and tax basis diverge. A taxable asset "
                "acquisition steps up both, so no DTL is recorded."
            ),
        )

    ui.metric_row(
        [
            (
                f"NWC Acquired ({period})",
                ui.format_value(nwc_actual, full_precision),
                "Imported from Module 1",
            ),
            (
                "Book Net Assets Acquired",
                ui.format_value(book_value, full_precision),
                "Cash-free, debt-free",
            ),
            (
                "Funded Debt Retired at Close",
                ui.format_value(total_funded_debt(engagement, period), full_precision),
                "Excluded from the allocation",
            ),
        ]
    )

    # ------------------------------------------------------------ editors --
    st.divider()
    st.subheader("Fair Value Step-Ups — Tangible Assets")
    st.markdown(
        "Enter the step-up (or step-down) the valuation specialist supports for each acquired "
        "tangible asset. Inventory is commonly stepped up to selling price less costs to sell "
        "and a reasonable margin, which produces a one-time margin drag as it turns."
    )

    tangible_seed = ws.module_state(
        ws.PPA, engagement_id, "tangible", lambda: ppa.seed_tangible_frame(engagement, period)
    )
    tangible_edited = st.data_editor(
        tangible_seed,
        num_rows="dynamic",
        hide_index=True,
        **ui.sizing(element="data_editor"),
        column_config={
            ppa.COL_ASSET: st.column_config.TextColumn(ppa.COL_ASSET, width="large"),
            ppa.COL_BOOK: st.column_config.NumberColumn(
                ppa.COL_BOOK, format=ui.number_format_for(full_precision)
            ),
            ppa.COL_STEP: st.column_config.NumberColumn(
                ppa.COL_STEP,
                format=ui.number_format_for(full_precision),
                help="Positive steps the asset up; negative steps it down.",
            ),
            ppa.COL_FAIR: st.column_config.NumberColumn(
                ppa.COL_FAIR, format=ui.number_format_for(full_precision), disabled=True
            ),
        },
        key=ws.key(ws.PPA, engagement_id, "tangible_editor"),
    )

    st.subheader("Identifiable Intangible Assets")
    st.markdown(
        "Assets that meet the separability or contractual-legal criterion in ASC 805-20-25-10 are "
        "recognised apart from goodwill. Fair values start at zero — they come from the valuation "
        "specialist's work, not from this engine. A life of zero denotes an indefinite-lived asset, "
        "which is not amortized."
    )

    intangible_seed = ws.module_state(
        ws.PPA, engagement_id, "intangibles", ppa.seed_intangible_frame
    )
    intangible_edited = st.data_editor(
        intangible_seed,
        num_rows="dynamic",
        hide_index=True,
        **ui.sizing(element="data_editor"),
        column_config={
            ppa.COL_INTANGIBLE: st.column_config.TextColumn(ppa.COL_INTANGIBLE, width="large"),
            ppa.COL_INT_FAIR: st.column_config.NumberColumn(
                ppa.COL_INT_FAIR, format=ui.number_format_for(full_precision)
            ),
            ppa.COL_INT_LIFE: st.column_config.NumberColumn(
                ppa.COL_INT_LIFE, format="%.1f", help="Zero denotes an indefinite-lived asset."
            ),
            ppa.COL_INT_METHOD: st.column_config.TextColumn(ppa.COL_INT_METHOD, width="medium"),
            ppa.COL_INT_AMORT: st.column_config.NumberColumn(
                ppa.COL_INT_AMORT, format=ui.number_format_for(full_precision), disabled=True
            ),
        },
        key=ws.key(ws.PPA, engagement_id, "intangible_editor"),
    )

    tangible_view, intangible_view = ppa.with_derived_columns(tangible_edited, intangible_edited)

    # ----------------------------------------------------------- compute --
    result = ppa.run_ppa(
        engagement,
        tangible_view,
        intangible_view,
        ppa.PPAAssumptions(
            consideration=consideration,
            marginal_tax_rate=tax_rate,
            tax_structure=structure,
        ),
        period,
    )

    st.divider()
    st.subheader("Allocation Result")

    if result.is_bargain_purchase:
        st.warning(
            f"**Bargain purchase gain of {ui.format_value(result.bargain_purchase_gain, full_precision)}.** "
            "Consideration is below the fair value of identifiable net assets. ASC 805-30-25-4 "
            "requires you to reassess whether every asset and liability has been identified and "
            "measured correctly before recognising a gain — this is far more often an allocation "
            "error than a windfall.",
            icon="⚠️",
        )

    ui.metric_row(
        [
            ("Consideration Transferred", ui.format_value(result.consideration, full_precision), None),
            (
                "Identifiable Net Assets",
                ui.format_value(result.fair_value_identifiable_net_assets, full_precision),
                "At fair value, net of DTL",
            ),
            (
                "Bargain Purchase Gain" if result.is_bargain_purchase else "Goodwill",
                ui.format_value(
                    result.bargain_purchase_gain if result.is_bargain_purchase else result.goodwill,
                    full_precision,
                ),
                None
                if result.is_bargain_purchase
                else f"{ui.format_percent(result.goodwill_percent_of_consideration)} of price",
            ),
            (
                "Deferred Tax Liability",
                ui.format_value(result.deferred_tax_liability, full_precision),
                "On book/tax basis difference",
            ),
        ]
    )

    bridge_left, bridge_right = st.columns([2, 3])
    with bridge_left:
        ui.render_frame(result.bridge, full_precision)
    with bridge_right:
        figure = ui.ppa_bridge_chart(result.steps, currency)
        if figure is not None:
            st.plotly_chart(figure, **ui.chart_sizing())

    for note in result.notes:
        st.info(note, icon="📘")

    # ------------------------------------------------- opening balance --
    st.divider()
    st.subheader("Day 1 Opening Balance Sheet")
    st.caption(
        "Historical book value against the Day 1 fair value at which the buyer records the "
        "acquired business. Cash and funded debt are excluded under the cash-free, debt-free "
        "convention; the seller's goodwill is eliminated and replaced by the residual above."
    )
    opening = ppa.opening_balance_sheet(engagement, tangible_view, intangible_view, result, period)
    st.dataframe(
        opening,
        **ui.sizing(),
        column_config={
            "Historical Book Value": st.column_config.NumberColumn(
                "Historical Book Value", format=ui.number_format_for(full_precision)
            ),
            "Day 1 Fair Value": st.column_config.NumberColumn(
                "Day 1 Fair Value", format=ui.number_format_for(full_precision)
            ),
            "Fair Value Adjustment": st.column_config.NumberColumn(
                "Fair Value Adjustment", format=ui.number_format_for(full_precision)
            ),
            "Basis of Measurement": st.column_config.TextColumn(
                "Basis of Measurement", width="large"
            ),
        },
    )

    # -------------------------------------------------- amortization --
    schedule = ppa.amortization_schedule(intangible_view)
    if not schedule.empty:
        st.divider()
        st.subheader("Post-Close Amortization Drag")
        annual = result.annual_intangible_amortization
        published_ebitda = ws.consume(engagement_id, "adjusted_ebitda")
        caption_ebitda = ""
        if published_ebitda is not None and not is_missing(published_ebitda) and published_ebitda:
            share = safe_divide(annual, published_ebitda) * 100.0
            caption_ebitda = (
                f" That is {ui.format_percent(share)} of the Module 1 Adjusted EBITDA, and it hits "
                "reported earnings every year without touching cash."
            )
        st.caption(
            f"Straight-line amortization of the recognised intangibles is "
            f"{ui.format_value(annual, full_precision)} per year.{caption_ebitda}"
        )
        ui.render_frame(schedule, full_precision)

    ws.publish(
        engagement_id,
        "ppa_summary",
        {
            "consideration": result.consideration,
            "goodwill": result.goodwill,
            "bargain_purchase_gain": result.bargain_purchase_gain,
            "deferred_tax_liability": result.deferred_tax_liability,
            "intangibles": result.intangible_fair_value,
            "annual_amortization": result.annual_intangible_amortization,
        },
    )

    # ------------------------------------------------------- export --
    payload = ReportPayload(
        title="ASC 805 Purchase Price Allocation",
        entity=f"{engagement.entity_name}"
        + (f" ({engagement.ticker})" if engagement.ticker else ""),
        subtitle=(
            f"Allocation as of {period} · {structure} · marginal tax rate "
            f"{tax_rate * 100.0:,.2f}%"
        ),
        sections=[
            ReportSection(
                "Allocation of Consideration",
                "Consideration transferred allocated to identifiable net assets at fair value, "
                "with the residual recognised as goodwill under ASC 805-30-30-1.",
                result.bridge,
            ),
            ReportSection(
                "Day 1 Opening Balance Sheet", "", opening
            ),
            ReportSection(
                "Identifiable Intangible Assets", "", intangible_view, include_index=False
            ),
            ReportSection("Tangible Fair Value Step-Ups", "", tangible_view, include_index=False),
            ReportSection("Intangible Amortization Schedule", "", schedule),
            ReportSection("Technical Notes", "\n\n".join(result.notes)),
        ],
    )
    ui.render_export_buttons(
        payload,
        safe_filename(engagement.ticker or engagement.entity_name, "ppa_asc805"),
        key_prefix=f"ppa::{engagement_id}",
    )
