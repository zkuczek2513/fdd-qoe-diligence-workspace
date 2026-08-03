"""FDD / QoE Diligence Workspace — Streamlit entry point.

Run with::

    streamlit run app.py

State model
-----------
Each editable working paper (the adjustment ledger, the balance sheet
classification schedule and the risk register) is rendered from a *seed* frame
held in ``st.session_state`` and namespaced by engagement. The seed is written
once and never mutated: ``st.data_editor`` owns the analyst's edits and returns
the current truth on every run. Writing the returned frame back over the seed
would re-apply the widget's added- and deleted-row deltas on the following run
and duplicate rows, so the app deliberately does not do that.

Because downstream tabs need the edited values, the three editors are rendered
first into containers reserved inside their target tabs. Streamlit places
content in container-creation order rather than execution order, so the editors
appear in the right place on screen while executing early enough for every other
tab to read from them.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

import ai_reviewer
import components as ui
from api_client import DataIngestionError, build_live_engagement, cache_ttl_seconds
from case_studies import (
    CASE_LIBRARY,
    answer_key_classifications,
    answer_key_risks,
    build_case_engagement,
    case_options,
    compare_to_answer_key,
)
from config import (
    ADJUSTMENT_CATEGORIES,
    ADJUSTMENT_STATUSES,
    APP_TAGLINE,
    APP_TITLE,
    APP_VERSION,
    BALANCE_SHEET_LAYOUT,
    CASH_FLOW_LAYOUT,
    CLASSIFICATION_OPTIONS,
    DCF_DEFAULTS,
    DEBT_LIKE,
    DEFAULT_TICKERS,
    INCOME_STATEMENT_LAYOUT,
    MODE_LIVE,
    MODE_SANDBOX,
    MODES,
    NON_OPERATING_ASSET,
    PRECISION_POLICY,
    RISK_SEVERITIES,
    TERMINAL_METHOD_GORDON,
    TERMINAL_METHOD_MULTIPLE,
    TERMINAL_METHODS,
    number_format,
)
from finance_logic import (
    NAN,
    DCFAssumptions,
    adjusted_ebitda,
    build_efficiency_metrics,
    build_nwc_schedule,
    build_qoe_bridge,
    build_value_bridge,
    classification_totals,
    coalesce,
    compute_diagnostics,
    is_missing,
    margin_summary,
    net_working_capital,
    normalized_nwc_peg,
    reported_ebitda,
    run_dcf,
    safe_divide,
    total_funded_debt,
)

st.set_page_config(
    page_title=APP_TITLE,
    page_icon="📘",
    layout="wide",
    initial_sidebar_state="expanded",
)


# --------------------------------------------------------------------------- #
# Cached data access
# --------------------------------------------------------------------------- #


@st.cache_data(ttl=cache_ttl_seconds(), show_spinner=False)
def load_live(ticker: str):
    """Fetch and normalize a live ticker. Cached to avoid repeat vendor calls."""
    return build_live_engagement(ticker)


@st.cache_data(show_spinner=False)
def load_case(case_key: str):
    return build_case_engagement(case_key)


# --------------------------------------------------------------------------- #
# Working paper seeds
# --------------------------------------------------------------------------- #


def seed_key(engagement_id: str, name: str) -> str:
    return f"seed::{engagement_id}::{name}"


def widget_key(engagement_id: str, name: str) -> str:
    return f"editor::{engagement_id}::{name}"


def initialise_seeds(engagement, engagement_id: str) -> None:
    """Create the immutable seed frames for a newly selected engagement."""
    adjustments = seed_key(engagement_id, "adjustments")
    classifications = seed_key(engagement_id, "classifications")
    risks = seed_key(engagement_id, "risks")

    if adjustments not in st.session_state:
        st.session_state[adjustments] = ui.adjustments_to_frame([], engagement.periods)
    if classifications not in st.session_state:
        st.session_state[classifications] = ui.classifications_to_frame(
            ui.seed_classification_candidates(engagement)
        )
    if risks not in st.session_state:
        st.session_state[risks] = ui.risks_to_frame([])


def clear_working_papers(engagement_id: str) -> None:
    """Discard the analyst's work and the derived outputs for one engagement."""
    for name in ("adjustments", "classifications", "risks"):
        st.session_state.pop(seed_key(engagement_id, name), None)
        st.session_state.pop(widget_key(engagement_id, name), None)
    st.session_state.pop(f"comparison_run::{engagement_id}", None)
    st.session_state.pop(f"review::{engagement_id}", None)


# --------------------------------------------------------------------------- #
# Column configurations
# --------------------------------------------------------------------------- #


def adjustment_columns(periods, full_precision: bool) -> dict:
    config: dict = {
        "Adjustment": st.column_config.TextColumn(
            "Adjustment", width="large", help="Describe the normalization in report language."
        ),
        "Category": st.column_config.SelectboxColumn(
            "Category", options=list(ADJUSTMENT_CATEGORIES), width="medium"
        ),
        "Treatment": st.column_config.SelectboxColumn(
            "Treatment",
            options=list(ADJUSTMENT_STATUSES),
            width="medium",
            help=(
                "A rejected adjustment stays in the working papers but is excluded from "
                "Adjusted EBITDA — that is how a management proposal is documented and declined."
            ),
        ),
        "Rationale / Support": st.column_config.TextColumn(
            "Rationale / Support",
            width="large",
            help="What document evidences this? 'Management represented' is not support.",
        ),
    }
    for period in periods:
        config[period] = st.column_config.NumberColumn(
            period,
            format=number_format(full_precision),
            help=f"EBITDA impact in {period}. Positive adds back, negative deducts.",
        )
    return config


def classification_columns(full_precision: bool) -> dict:
    return {
        "Balance Sheet Item": st.column_config.TextColumn("Balance Sheet Item", width="large"),
        "Amount": st.column_config.NumberColumn(
            "Amount",
            format=number_format(full_precision),
            help="Enter a positive amount; the value bridge applies the sign by classification.",
        ),
        "Classification": st.column_config.SelectboxColumn(
            "Classification", options=list(CLASSIFICATION_OPTIONS), width="medium"
        ),
        "Diligence Rationale": st.column_config.TextColumn("Diligence Rationale", width="large"),
    }


def risk_columns() -> dict:
    return {
        "Risk": st.column_config.TextColumn("Risk", width="large"),
        "Severity": st.column_config.SelectboxColumn(
            "Severity", options=list(RISK_SEVERITIES), width="small"
        ),
        "Description": st.column_config.TextColumn("Description", width="large"),
    }


# --------------------------------------------------------------------------- #
# Sidebar
# --------------------------------------------------------------------------- #


def query_param(name: str) -> str:
    """Read a deep-link query parameter, tolerating older Streamlit builds."""
    try:
        value = st.query_params.get(name, "")
    except Exception:  # noqa: BLE001 - the API differs across Streamlit versions
        return ""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    return str(value or "").strip()


def initial_mode_index() -> int:
    """Default workspace mode, honouring ``?mode=sandbox`` on first load."""
    requested = query_param("mode").lower()
    if requested.startswith("sand") or requested == "sandbox":
        return MODES.index(MODE_SANDBOX)
    return MODES.index(MODE_LIVE)


def initial_case_index(options: list[tuple[str, str]]) -> int:
    """Default sandbox case, honouring ``?case=<key>`` on first load."""
    requested = query_param("case").lower()
    for index, (key, _) in enumerate(options):
        if key == requested:
            return index
    return 0


def render_sidebar() -> dict:
    st.sidebar.title("Engagement Setup")
    st.sidebar.caption(f"{APP_TITLE} · v{APP_VERSION}")

    mode = st.sidebar.radio(
        "Workspace mode",
        MODES,
        index=initial_mode_index(),
        key="workspace_mode",
        help=(
            "Live Deal Mode loads a public issuer for open-ended practice. Sandbox Mode loads a "
            "guided case with a hidden answer key you can grade your work against."
        ),
    )
    settings: dict = {"mode": mode}

    if mode == MODE_LIVE:
        st.sidebar.subheader("Target")
        preset = st.sidebar.selectbox(
            "Suggested tickers", ("Enter my own",) + DEFAULT_TICKERS, index=0
        )
        default_ticker = query_param("ticker").upper() if preset == "Enter my own" else preset
        settings["ticker"] = (
            st.sidebar.text_input("Ticker symbol", value=default_ticker, placeholder="e.g. CAT")
            .strip()
            .upper()
        )
        settings["load"] = st.sidebar.button(
            "Load target", type="primary", use_container_width=True
        )
    else:
        st.sidebar.subheader("Case")
        options = case_options()
        selected = st.sidebar.selectbox(
            "Select a transaction",
            options,
            index=initial_case_index(options),
            key="case_selector",
            format_func=lambda option: option[1],
        )
        settings["case_key"] = selected[0]

    st.sidebar.divider()
    st.sidebar.subheader("Presentation")
    settings["full_precision"] = st.sidebar.toggle(
        "Full raw precision",
        value=False,
        help=(
            "Switches every table to a ten-decimal display mask. This changes the mask only — "
            "no stored value is ever rounded."
        ),
    )
    st.sidebar.caption(PRECISION_POLICY)

    st.sidebar.divider()
    st.sidebar.subheader("Manager Review Panel")
    settings["api_key"] = st.sidebar.text_input(
        "Anthropic API key",
        value="",
        type="password",
        help=(
            "Optional. Leave blank to use ANTHROPIC_API_KEY from the environment, or to run the "
            "local heuristic review engine."
        ),
    )
    configured, status_message = ai_reviewer.review_engine_status(settings["api_key"])
    settings["ai_configured"] = configured
    if configured:
        st.sidebar.success(status_message, icon="✅")
    else:
        st.sidebar.info(status_message, icon="ℹ️")

    settings["model"] = st.sidebar.selectbox(
        "Model", ai_reviewer.MODEL_CHOICES, index=0, disabled=not configured
    )
    settings["effort"] = st.sidebar.select_slider(
        "Reasoning effort",
        options=ai_reviewer.EFFORT_CHOICES,
        value=ai_reviewer.DEFAULT_EFFORT,
        disabled=not configured,
    )
    return settings


# --------------------------------------------------------------------------- #
# Section renderers
# --------------------------------------------------------------------------- #


def render_overview(engagement, adjustments, full_precision: bool, case=None) -> None:
    st.subheader("Deal Context")
    ui.markdown(engagement.context)

    if case is not None:
        management = case.get("management_adjusted_ebitda", {}).get(engagement.latest_period)
        if management is not None:
            st.info(
                f"**Management has represented {engagement.latest_period} Adjusted EBITDA of "
                f"{ui.format_value(management, full_precision)}.** Your task is to test it.",
                icon="📌",
            )

    for warning in engagement.warnings:
        st.warning(warning, icon="⚠️")

    st.divider()
    st.subheader("Headline Metrics")

    latest = engagement.latest_period
    revenue = engagement.fact("revenue", latest)
    reported = reported_ebitda(engagement, latest)
    adjusted = adjusted_ebitda(engagement, adjustments, latest)
    funded_debt = total_funded_debt(engagement, latest)
    cash = coalesce(engagement.fact("cash_and_equivalents", latest))
    net_debt = funded_debt - cash

    ui.metric_row(
        [
            (f"Revenue ({latest})", ui.format_value(revenue, full_precision), engagement.currency),
            (
                "EBITDA (as reported)",
                ui.format_value(reported, full_precision),
                f"Margin {ui.format_percent(safe_divide(reported, revenue) * 100.0)}",
            ),
            (
                "Adjusted EBITDA",
                ui.format_value(adjusted, full_precision),
                f"Margin {ui.format_percent(safe_divide(adjusted, revenue) * 100.0)}",
            ),
            (
                "Net Funded Debt",
                ui.format_value(net_debt, full_precision),
                f"{ui.format_multiple(safe_divide(net_debt, adjusted))} Adjusted EBITDA",
            ),
        ]
    )

    st.divider()
    st.subheader("Earnings Quality Summary")
    ui.render_frame(
        margin_summary(engagement, adjustments),
        full_precision,
        percent_rows=("EBITDA Margin %", "Adjusted EBITDA Margin %"),
    )

    figure = ui.earnings_quality_chart(
        engagement.periods,
        [reported_ebitda(engagement, period) for period in engagement.periods],
        [adjusted_ebitda(engagement, adjustments, period) for period in engagement.periods],
    )
    if figure is not None:
        st.plotly_chart(figure, **ui.chart_sizing())


def render_statements(engagement, full_precision: bool, raw=None) -> None:
    st.subheader("Three-Statement Model")
    st.caption(engagement.units_note + " Expand any block to inspect the line items.")

    income_tab, balance_tab, cash_tab = st.tabs(
        ["Income Statement", "Balance Sheet", "Cash Flow Statement"]
    )
    with income_tab:
        ui.render_statement(engagement, INCOME_STATEMENT_LAYOUT, full_precision)
    with balance_tab:
        ui.render_statement(engagement, BALANCE_SHEET_LAYOUT, full_precision)
        checks = []
        for period in engagement.periods:
            assets = engagement.fact("total_assets", period)
            liabilities_equity = engagement.fact("total_liabilities", period) + engagement.fact(
                "total_equity", period
            )
            checks.append(
                NAN
                if is_missing(assets) or is_missing(liabilities_equity)
                else assets - liabilities_equity
            )
        with st.expander("Balance Sheet Check", expanded=False):
            ui.render_frame(
                pd.DataFrame(
                    [checks],
                    index=["Assets less Liabilities and Equity"],
                    columns=engagement.periods,
                ),
                full_precision,
            )
            st.caption(
                "A non-zero balance means the data provider did not report every line item. "
                "Sandbox cases balance exactly by construction."
            )
    with cash_tab:
        ui.render_statement(engagement, CASH_FLOW_LAYOUT, full_precision)

    if raw is not None:
        st.divider()
        with st.expander("Raw provider statements (as returned by yfinance)", expanded=False):
            for label, frame in (
                ("Income Statement", raw.income_statement),
                ("Balance Sheet", raw.balance_sheet),
                ("Cash Flow", raw.cash_flow),
            ):
                st.markdown(f"**{label}**")
                if frame is None or frame.empty:
                    st.caption("Not returned by the provider.")
                else:
                    st.dataframe(frame, **ui.sizing())


def render_qoe_header() -> None:
    st.subheader("Quality of Earnings Working Papers")
    st.markdown(
        "Build your adjustment schedule below. A **positive** impact adds back to EBITDA and a "
        "**negative** impact deducts from it. Mark an item as rejected to keep it in the working "
        "papers while excluding it from Adjusted EBITDA — that is how management's proposed "
        "adjustments are documented and declined."
    )


def render_qoe_body(engagement, engagement_id: str, adjustments, full_precision: bool) -> None:
    st.divider()
    period = st.selectbox(
        "Bridge period",
        engagement.periods,
        index=len(engagement.periods) - 1,
        key=f"qoe_period::{engagement_id}",
    )

    reported = reported_ebitda(engagement, period)
    adjusted = adjusted_ebitda(engagement, adjustments, period)
    accepted = [adjustment for adjustment in adjustments if adjustment.is_accepted]

    ui.metric_row(
        [
            ("EBITDA (as reported)", ui.format_value(reported, full_precision), period),
            (
                "Net QoE Adjustments",
                ui.format_value(
                    NAN if is_missing(adjusted) or is_missing(reported) else adjusted - reported,
                    full_precision,
                ),
                f"{len(accepted)} accepted, {len(adjustments) - len(accepted)} rejected",
            ),
            ("Adjusted EBITDA", ui.format_value(adjusted, full_precision), period),
        ]
    )

    figure = ui.qoe_waterfall(engagement, adjustments, period, reported, adjusted)
    if figure is not None:
        st.plotly_chart(figure, **ui.chart_sizing())
    else:
        st.info("The waterfall renders once reported EBITDA can be computed for this period.")

    st.divider()
    st.subheader("Bridge: GAAP Net Income to Adjusted EBITDA")
    ui.render_frame(build_qoe_bridge(engagement, adjustments), full_precision)

    rejected = [adjustment for adjustment in adjustments if not adjustment.is_accepted]
    if rejected:
        with st.expander(f"Rejected adjustments ({len(rejected)})", expanded=False):
            for adjustment in rejected:
                ui.markdown(
                    f"**{adjustment.label}** — "
                    f"{ui.format_value(adjustment.impact(period), full_precision)} "
                    f"({adjustment.category})"
                )
                if adjustment.rationale:
                    st.caption(adjustment.rationale)


def render_nwc(engagement, engagement_id: str, full_precision: bool) -> tuple[float, float]:
    st.subheader("Operational Net Working Capital")
    st.caption(
        "Net working capital is current assets excluding cash, less current liabilities "
        "excluding short-term borrowings and current maturities of long-term debt."
    )

    ui.render_frame(
        build_nwc_schedule(engagement), full_precision, percent_rows=("NWC as % of Revenue",)
    )

    lookback = st.slider(
        "Peg lookback (periods averaged)",
        min_value=1,
        max_value=max(1, len(engagement.periods)),
        value=min(3, len(engagement.periods)),
        key=f"peg_lookback::{engagement_id}",
        help=(
            "The peg is the trailing average of net working capital. Setting it on the year-end "
            "balance alone flatters a seller whose position has been managed into the close."
        ),
    )
    peg = normalized_nwc_peg(engagement, lookback)
    latest_nwc = net_working_capital(engagement, engagement.latest_period)
    surplus = NAN if is_missing(latest_nwc) or is_missing(peg) else latest_nwc - peg

    ui.metric_row(
        [
            ("Proposed NWC Peg", ui.format_value(peg, full_precision), f"{lookback}-period average"),
            (
                f"Actual NWC ({engagement.latest_period})",
                ui.format_value(latest_nwc, full_precision),
                None,
            ),
            (
                "Surplus / (Deficit) vs. Peg",
                ui.format_value(surplus, full_precision),
                "Flows into the value bridge",
            ),
        ]
    )

    figure = ui.nwc_trend_chart(
        engagement.periods,
        [net_working_capital(engagement, period) for period in engagement.periods],
        [
            safe_divide(net_working_capital(engagement, period), engagement.fact("revenue", period))
            * 100.0
            for period in engagement.periods
        ],
        peg,
    )
    if figure is not None:
        st.plotly_chart(figure, **ui.chart_sizing())

    st.divider()
    st.subheader("Trailing Efficiency Metrics")
    metrics = build_efficiency_metrics(engagement)
    ui.render_frame(metrics, full_precision)
    efficiency_figure = ui.efficiency_trend_chart(metrics)
    if efficiency_figure is not None:
        st.plotly_chart(efficiency_figure, **ui.chart_sizing())
    else:
        st.info("Efficiency metrics require revenue, cost of sales and the related balances.")

    st.divider()
    return peg, latest_nwc


def render_classification_header() -> None:
    st.subheader("Debt-Like Items and Non-Operating Assets")
    st.markdown(
        "Tag balance sheet items that belong in the transaction value bridge rather than in "
        "operating working capital. **Debt-like items** are obligations the buyer inherits and "
        "settles in cash; they reduce equity value. **Non-operating assets** are not required to "
        "run the business; they increase it. Enter every amount as a positive number — the bridge "
        "applies the sign."
    )


def render_classification_totals(classifications, full_precision: bool) -> None:
    debt_like_total, non_operating_total = classification_totals(classifications)
    ui.metric_row(
        [
            (
                "Total Debt-Like Items",
                ui.format_value(debt_like_total, full_precision),
                f"{len([i for i in classifications if i.classification == DEBT_LIKE])} items",
            ),
            (
                "Total Non-Operating Assets",
                ui.format_value(non_operating_total, full_precision),
                f"{len([i for i in classifications if i.classification == NON_OPERATING_ASSET])} "
                "items",
            ),
        ]
    )
    st.divider()
    st.subheader("Risk Register")
    st.caption(
        "Record the diligence risks that do not carry a quantified EBITDA impact but change how "
        "the deal should be structured."
    )


def render_valuation(
    engagement,
    engagement_id: str,
    adjustments,
    classifications,
    peg: float,
    latest_nwc: float,
    full_precision: bool,
) -> dict:
    st.subheader("Discounted Cash Flow")
    latest = engagement.latest_period
    base_revenue = engagement.fact("revenue", latest)
    base_adjusted = adjusted_ebitda(engagement, adjustments, latest)

    st.caption(
        f"The projection is anchored on {latest} revenue of "
        f"{ui.format_value(base_revenue, full_precision)} and the Adjusted EBITDA of "
        f"{ui.format_value(base_adjusted, full_precision)} produced by your working papers. "
        "Change an adjustment and the valuation moves with it."
    )

    left, middle, right = st.columns(3)
    with left:
        horizon = st.slider(
            "Projection horizon (years)",
            3,
            10,
            DCF_DEFAULTS["horizon_years"],
            key=f"dcf_horizon::{engagement_id}",
        )
        revenue_cagr = (
            st.slider(
                "Revenue CAGR (%)",
                -15.0,
                40.0,
                DCF_DEFAULTS["revenue_cagr"] * 100.0,
                step=0.25,
                key=f"dcf_cagr::{engagement_id}",
            )
            / 100.0
        )
        target_margin = (
            st.slider(
                "Target EBITDA margin (%)",
                0.0,
                60.0,
                DCF_DEFAULTS["target_ebitda_margin"] * 100.0,
                step=0.25,
                key=f"dcf_margin::{engagement_id}",
            )
            / 100.0
        )
        ramp_margin = st.checkbox(
            "Ramp margin linearly from the current level",
            value=DCF_DEFAULTS["ramp_margin"],
            key=f"dcf_ramp::{engagement_id}",
            help=(
                "With the ramp on, the margin interpolates from today's adjusted margin to the "
                "target across the horizon rather than stepping to it in year one."
            ),
        )
    with middle:
        capex_pct = (
            st.slider(
                "Capital expenditure (% of revenue)",
                0.0,
                20.0,
                DCF_DEFAULTS["capex_pct_revenue"] * 100.0,
                step=0.1,
                key=f"dcf_capex::{engagement_id}",
            )
            / 100.0
        )
        da_pct = (
            st.slider(
                "Depreciation & amortization (% of revenue)",
                0.0,
                20.0,
                DCF_DEFAULTS["da_pct_revenue"] * 100.0,
                step=0.1,
                key=f"dcf_da::{engagement_id}",
            )
            / 100.0
        )
        nwc_pct = (
            st.slider(
                "Net working capital (% of revenue)",
                -30.0,
                50.0,
                DCF_DEFAULTS["nwc_pct_revenue"] * 100.0,
                step=0.5,
                key=f"dcf_nwc::{engagement_id}",
            )
            / 100.0
        )
        tax_rate = (
            st.slider(
                "Cash tax rate (%)",
                0.0,
                45.0,
                DCF_DEFAULTS["tax_rate"] * 100.0,
                step=0.5,
                key=f"dcf_tax::{engagement_id}",
            )
            / 100.0
        )
    with right:
        wacc = (
            st.slider(
                "WACC (%)",
                4.0,
                30.0,
                DCF_DEFAULTS["wacc"] * 100.0,
                step=0.1,
                key=f"dcf_wacc::{engagement_id}",
            )
            / 100.0
        )
        terminal_method = st.radio(
            "Terminal value method", TERMINAL_METHODS, key=f"dcf_tv_method::{engagement_id}"
        )
        terminal_multiple = st.slider(
            "Exit EBITDA multiple",
            2.0,
            25.0,
            DCF_DEFAULTS["terminal_multiple"],
            step=0.25,
            key=f"dcf_tv_multiple::{engagement_id}",
            disabled=terminal_method != TERMINAL_METHOD_MULTIPLE,
        )
        terminal_growth = (
            st.slider(
                "Terminal growth (%)",
                0.0,
                6.0,
                DCF_DEFAULTS["terminal_growth"] * 100.0,
                step=0.1,
                key=f"dcf_tv_growth::{engagement_id}",
                disabled=terminal_method != TERMINAL_METHOD_GORDON,
            )
            / 100.0
        )
        mid_year = st.checkbox(
            "Mid-year discounting convention",
            value=DCF_DEFAULTS["mid_year_convention"],
            key=f"dcf_midyear::{engagement_id}",
        )

    result = run_dcf(
        base_revenue,
        base_adjusted,
        DCFAssumptions(
            horizon_years=horizon,
            revenue_cagr=revenue_cagr,
            target_ebitda_margin=target_margin,
            capex_pct_revenue=capex_pct,
            da_pct_revenue=da_pct,
            nwc_pct_revenue=nwc_pct,
            wacc=wacc,
            tax_rate=tax_rate,
            terminal_method=terminal_method,
            terminal_multiple=terminal_multiple,
            terminal_growth=terminal_growth,
            mid_year_convention=mid_year,
            ramp_margin=ramp_margin,
        ),
    )

    if result.note:
        st.warning(result.note, icon="⚠️")

    ui.metric_row(
        [
            (
                "PV of Forecast Cash Flows",
                ui.format_value(result.pv_of_forecast, full_precision),
                None,
            ),
            (
                "PV of Terminal Value",
                ui.format_value(result.pv_of_terminal_value, full_precision),
                f"{ui.format_percent(result.terminal_value_share)} of EV",
            ),
            ("Enterprise Value", ui.format_value(result.enterprise_value, full_precision), None),
            (
                "Implied EV / Adjusted EBITDA",
                ui.format_multiple(result.implied_entry_multiple),
                f"on {latest} Adjusted EBITDA",
            ),
        ]
    )

    if not result.schedule.empty:
        with st.expander("Projection schedule", expanded=False):
            ui.render_frame(result.schedule, full_precision, percent_rows=("EBITDA Margin %",))
        figure = ui.dcf_chart(result.schedule, engagement.currency)
        if figure is not None:
            st.plotly_chart(figure, **ui.chart_sizing())

    st.divider()
    st.subheader("Transaction Value Bridge")
    include_true_up = st.checkbox(
        "Include the working capital true-up against the peg",
        value=True,
        key=f"bridge_nwc::{engagement_id}",
    )

    debt_like_total, non_operating_total = classification_totals(classifications)
    bridge = build_value_bridge(
        enterprise_value=result.enterprise_value,
        cash=engagement.fact("cash_and_equivalents", latest),
        funded_debt=total_funded_debt(engagement, latest),
        debt_like_total=debt_like_total,
        non_operating_total=non_operating_total,
        nwc_actual=latest_nwc,
        nwc_peg=peg,
        include_nwc_true_up=include_true_up,
    )

    bridge_left, bridge_right = st.columns([2, 3])
    with bridge_left:
        ui.render_frame(bridge.table, full_precision)
    with bridge_right:
        figure = ui.value_bridge_chart(bridge.steps, engagement.currency)
        if figure is not None:
            st.plotly_chart(figure, **ui.chart_sizing())

    ui.metric_row(
        [
            ("Enterprise Value", ui.format_value(bridge.enterprise_value, full_precision), None),
            ("Implied Equity Value", ui.format_value(bridge.equity_value, full_precision), None),
        ]
    )

    return {
        "enterprise_value": result.enterprise_value,
        "equity_value": bridge.equity_value,
        "implied_entry_multiple": result.implied_entry_multiple,
        "pv_of_forecast": result.pv_of_forecast,
        "pv_of_terminal_value": result.pv_of_terminal_value,
        "terminal_value_share_percent": result.terminal_value_share,
        "wacc": wacc,
        "revenue_cagr": revenue_cagr,
        "target_ebitda_margin": target_margin,
        "terminal_method": terminal_method,
        "debt_like_total": debt_like_total,
        "non_operating_total": non_operating_total,
        "nwc_peg": peg,
        "nwc_actual": latest_nwc,
    }


def render_comparison(
    engagement, engagement_id: str, adjustments, classifications, risks, case_key, full_precision
):
    case = CASE_LIBRARY[case_key]
    st.subheader("Compare Your Work to the Actual Deal")
    st.markdown(
        "This reveals the engagement team's adjustment ledger and the issued report. Complete "
        "your working papers first — once you have seen the answer key you cannot unsee it."
    )

    period = st.selectbox(
        "Diligence period to compare",
        engagement.periods,
        index=len(engagement.periods) - 1,
        key=f"cmp_period::{engagement_id}",
    )

    run_key = f"comparison_run::{engagement_id}"
    if st.button("Compare to Actual Deal", type="primary", use_container_width=True):
        st.session_state[run_key] = True

    if not st.session_state.get(run_key):
        accepted = len([adjustment for adjustment in adjustments if adjustment.is_accepted])
        st.info(
            f"You currently have {accepted} accepted adjustment"
            f"{'s' if accepted != 1 else ''}, "
            f"{len([i for i in classifications if i.classification == DEBT_LIKE])} debt-like "
            f"item(s) and {len(risks)} risk(s) on file.",
            icon="📝",
        )
        return None

    comparison = compare_to_answer_key(engagement, adjustments, case_key, period)

    ui.metric_row(
        [
            (
                "Your Adjusted EBITDA",
                ui.format_value(comparison.user_adjusted_ebitda, full_precision),
                period,
            ),
            (
                "Engagement Team",
                ui.format_value(comparison.actual_adjusted_ebitda, full_precision),
                "Issued report",
            ),
            (
                "Variance",
                ui.format_value(comparison.variance, full_precision),
                ui.format_percent(comparison.variance_percent),
            ),
            (
                "Adjustment Coverage",
                ui.format_percent(comparison.coverage_percent),
                "of accepted adjustments identified",
            ),
        ]
    )

    figure = ui.variance_chart(
        comparison.user_adjusted_ebitda,
        comparison.actual_adjusted_ebitda,
        comparison.reported_ebitda,
        comparison.management_ebitda,
    )
    if figure is not None:
        st.plotly_chart(figure, **ui.chart_sizing())

    st.divider()
    st.subheader("Your Adjustments Against the Senior Associate's")
    if comparison.table.empty:
        st.info("No adjustments to compare. Build your schedule in the QoE tab first.")
    else:
        st.dataframe(
            comparison.table,
            **ui.sizing(),
            hide_index=True,
            column_config=ui.numeric_columns(
                ("Your Impact", "Actual Impact", "Variance", "Match Confidence %"),
                full_precision,
                percent_columns=("Match Confidence %",),
            ),
        )

    if comparison.missed:
        st.divider()
        st.subheader("What the Engagement Team Found That You Did Not")
        for adjustment in comparison.missed:
            marker = "✅" if adjustment.is_accepted else "🚫"
            with st.expander(
                f"{marker} {adjustment.label} — "
                f"{ui.format_value(adjustment.impact(period), full_precision)}",
                expanded=False,
            ):
                ui.markdown(f"**Category:** {adjustment.category}")
                ui.markdown(f"**Treatment:** {adjustment.status}")
                if adjustment.authority:
                    ui.markdown(f"**Technical authority:** {adjustment.authority}")
                ui.markdown(adjustment.rationale)

    st.divider()
    left, right = st.columns(2)
    with left:
        st.subheader("Engagement Team — Value Bridge Items")
        for item in answer_key_classifications(case_key):
            badge = "🔻" if item.classification == DEBT_LIKE else "🔺"
            with st.expander(
                f"{badge} {item.label} — {ui.format_value(item.amount, full_precision)}",
                expanded=False,
            ):
                st.caption(item.classification)
                ui.markdown(item.rationale)
    with right:
        st.subheader("Engagement Team — Risk Register")
        for risk in answer_key_risks(case_key):
            with st.expander(f"{ui.severity_badge(risk.severity)} {risk.title}", expanded=False):
                ui.markdown(risk.description)

    st.divider()
    st.subheader("Actual FDD Report Summary")
    ui.markdown(case["fdd_report_summary"])

    return comparison


def render_review_panel(
    engagement,
    engagement_id: str,
    adjustments,
    classifications,
    risks,
    valuation,
    settings,
    mode,
    case_key=None,
    comparison=None,
) -> None:
    st.subheader("TAS Manager Review Panel")
    st.markdown(
        "A Senior Associate reviews your working papers before they reach the Partner. In Live "
        "Deal Mode the reviewer challenges your adjustment logic and the red flags visible in the "
        "reported financials. In Sandbox Mode the reviewer works through your variance from the "
        "issued report and coaches you on what you missed."
    )

    diagnostics = compute_diagnostics(engagement, adjustments)
    engine_choice = st.radio(
        "Review engine",
        ("Claude review panel", ai_reviewer.HEURISTIC_ENGINE_NAME),
        index=0 if settings["ai_configured"] else 1,
        horizontal=True,
        key=f"engine::{engagement_id}",
    )
    use_claude = engine_choice == "Claude review panel"
    if use_claude and not settings["ai_configured"]:
        st.warning(
            "No API key is configured, so the Claude panel is unavailable and the local "
            "heuristic engine will run instead.",
            icon="⚠️",
        )
        use_claude = False

    if mode == MODE_SANDBOX and comparison is None:
        st.info(
            "Run **Compare to Actual Deal** first to unlock the coaching review. Until then the "
            "reviewer can only assess your work in isolation.",
            icon="ℹ️",
        )

    review_key = f"review::{engagement_id}"
    if st.button("Request Manager Review", type="primary", use_container_width=True):
        with st.spinner("The reviewer is working through your papers…"):
            result = None
            if use_claude:
                if mode == MODE_SANDBOX and comparison is not None and case_key:
                    prompt = ai_reviewer.build_sandbox_prompt(
                        engagement,
                        diagnostics,
                        adjustments,
                        classifications,
                        risks,
                        comparison,
                        CASE_LIBRARY[case_key],
                        answer_key_classifications(case_key),
                        answer_key_risks(case_key),
                    )
                else:
                    prompt = ai_reviewer.build_live_prompt(
                        engagement, diagnostics, adjustments, classifications, risks, valuation
                    )
                result = ai_reviewer.run_claude_review(
                    prompt,
                    api_key=ai_reviewer.resolve_api_key(settings["api_key"]) or "",
                    model=settings["model"],
                    effort=settings["effort"],
                )
                if result.is_error:
                    st.error(result.body, icon="🚫")
                    result = None
            if result is None:
                result = ai_reviewer.heuristic_review(
                    engagement,
                    diagnostics,
                    adjustments,
                    classifications,
                    risks,
                    valuation,
                    comparison,
                )
        st.session_state[review_key] = result

    result = st.session_state.get(review_key)
    if result is None:
        st.caption("No review has been requested for this engagement yet.")
        return

    st.divider()
    st.caption(f"Review engine: **{result.engine}**")
    for notice in result.notices:
        st.caption(notice)
    ui.markdown(result.body)
    st.download_button(
        "Download the memo (Markdown)",
        data=result.body,
        file_name=f"diligence_review_{engagement_id.replace(':', '_')}.md",
        mime="text/markdown",
    )


def render_landing() -> None:
    st.divider()
    st.subheader("What this workspace does")
    left, right = st.columns(2)
    with left:
        st.markdown(
            "**Live Deal Mode** pulls five years of income statement, balance sheet and cash "
            "flow data for any public issuer, normalizes it onto a standard diligence taxonomy, "
            "and hands you a blank set of working papers. You build the adjustment schedule, "
            "classify the balance sheet, and defend the result."
        )
    with right:
        st.markdown(
            "**TAS Analyst Sandbox Mode** loads a realistic transaction with a hidden answer "
            "key. Work the case, then compare your Adjusted EBITDA to the engagement team's, see "
            "exactly which adjustments you missed and why they were booked, and read the issued "
            "FDD report."
        )
    st.divider()
    st.subheader("Available case studies")
    for case in CASE_LIBRARY.values():
        with st.expander(f"{case['name']} — {case['sector']}", expanded=False):
            ui.markdown(f"**{case['target']}** · {case['deal_type']}")
            ui.markdown(case["context"])


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def main() -> None:
    settings = render_sidebar()
    mode = settings["mode"]

    st.title(APP_TITLE)
    st.caption(APP_TAGLINE)

    raw = None
    case = None
    case_key = None

    if mode == MODE_LIVE:
        ticker = settings.get("ticker", "")
        if settings.get("load") and ticker:
            st.session_state["active_ticker"] = ticker
        active = st.session_state.get("active_ticker")
        if not active:
            st.info(
                "Enter a ticker in the sidebar and select **Load target** to begin an open-ended "
                "practice engagement, or switch to **TAS Analyst Sandbox Mode** for a guided case "
                "with a hidden answer key.",
                icon="👋",
            )
            render_landing()
            return
        try:
            with st.spinner(f"Fetching annual statements for {active}…"):
                engagement, raw = load_live(active)
        except DataIngestionError as exc:
            st.error(str(exc), icon="🚫")
            st.session_state.pop("active_ticker", None)
            return
        except Exception as exc:  # noqa: BLE001 - the vendor can fail in many ways
            st.error(
                f"The data provider request failed ({type(exc).__name__}: {exc}). Check network "
                "connectivity, or switch to Sandbox Mode, which needs no external data.",
                icon="🚫",
            )
            st.session_state.pop("active_ticker", None)
            return
        engagement_id = f"live:{active}"
    else:
        case_key = settings["case_key"]
        case = CASE_LIBRARY[case_key]
        engagement = load_case(case_key)
        engagement_id = f"case:{case_key}"

    initialise_seeds(engagement, engagement_id)

    header_left, header_right = st.columns([4, 1])
    with header_left:
        subtitle = engagement.entity_name
        if engagement.ticker:
            subtitle += f" ({engagement.ticker})"
        if case is not None:
            subtitle = f"{case['name']} — {subtitle}"
        st.markdown(f"### {subtitle}")
        st.caption(
            f"{len(engagement.periods)} diligence periods · {engagement.periods[0]}–"
            f"{engagement.periods[-1]} · {engagement.currency}"
        )
    with header_right:
        if st.button("Clear working papers", use_container_width=True):
            clear_working_papers(engagement_id)
            st.rerun()

    tab_labels = [
        "Deal Overview",
        "Financial Statements",
        "QoE Working Papers",
        "Working Capital & Classification",
        "Valuation & Value Bridge",
    ]
    if mode == MODE_SANDBOX:
        tab_labels.append("Compare to Actual Deal")
    tab_labels.append("Manager Review")
    tabs = st.tabs(tab_labels)

    # Reserve slots so the editors can execute early while still rendering in
    # the correct on-screen position.
    with tabs[2]:
        qoe_header_slot = st.container()
        qoe_editor_slot = st.container()
        qoe_body_slot = st.container()
    with tabs[3]:
        nwc_slot = st.container()
        classification_header_slot = st.container()
        classification_editor_slot = st.container()
        classification_totals_slot = st.container()
        risk_editor_slot = st.container()

    full_precision = settings["full_precision"]

    # --- Editors first: everything downstream reads their return values ----
    with qoe_editor_slot:
        edited_adjustments = st.data_editor(
            st.session_state[seed_key(engagement_id, "adjustments")],
            num_rows="dynamic",
            **ui.sizing(element="data_editor"),
            hide_index=True,
            column_config=adjustment_columns(engagement.periods, full_precision),
            key=widget_key(engagement_id, "adjustments"),
        )
    adjustments = ui.frame_to_adjustments(edited_adjustments, engagement.periods)

    with classification_editor_slot:
        edited_classifications = st.data_editor(
            st.session_state[seed_key(engagement_id, "classifications")],
            num_rows="dynamic",
            **ui.sizing(element="data_editor"),
            hide_index=True,
            column_config=classification_columns(full_precision),
            key=widget_key(engagement_id, "classifications"),
        )
    classifications = ui.frame_to_classifications(edited_classifications)

    with risk_editor_slot:
        edited_risks = st.data_editor(
            st.session_state[seed_key(engagement_id, "risks")],
            num_rows="dynamic",
            **ui.sizing(element="data_editor"),
            hide_index=True,
            column_config=risk_columns(),
            key=widget_key(engagement_id, "risks"),
        )
    risks = ui.frame_to_risks(edited_risks)

    # --- Everything else ----------------------------------------------------
    with qoe_header_slot:
        render_qoe_header()
    with qoe_body_slot:
        render_qoe_body(engagement, engagement_id, adjustments, full_precision)

    with nwc_slot:
        peg, latest_nwc = render_nwc(engagement, engagement_id, full_precision)
    with classification_header_slot:
        render_classification_header()
    with classification_totals_slot:
        render_classification_totals(classifications, full_precision)

    with tabs[0]:
        render_overview(engagement, adjustments, full_precision, case)
    with tabs[1]:
        render_statements(engagement, full_precision, raw)
    with tabs[4]:
        valuation = render_valuation(
            engagement,
            engagement_id,
            adjustments,
            classifications,
            peg,
            latest_nwc,
            full_precision,
        )

    comparison = None
    review_tab_index = 5
    if mode == MODE_SANDBOX:
        with tabs[5]:
            comparison = render_comparison(
                engagement,
                engagement_id,
                adjustments,
                classifications,
                risks,
                case_key,
                full_precision,
            )
        review_tab_index = 6

    with tabs[review_tab_index]:
        render_review_panel(
            engagement,
            engagement_id,
            adjustments,
            classifications,
            risks,
            valuation,
            settings,
            mode,
            case_key,
            comparison,
        )


if __name__ == "__main__":
    main()
