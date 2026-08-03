"""Reusable Streamlit components and Plotly renderers.

This module owns the entire presentation layer. It is the only place where
numeric precision is reduced, and it does so exclusively through printf display
masks handed to Streamlit and Plotly — the underlying values passed in are never
mutated. See the numerical integrity policy in :mod:`config`.
"""

from __future__ import annotations

import inspect
import re
import textwrap
from functools import lru_cache
from typing import Iterable, Sequence

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from config import (
    ADJUSTMENT_CATEGORIES,
    ADJUSTMENT_STATUSES,
    CHART_FONT_FAMILY,
    CHART_HEIGHT,
    CLASSIFICATION_OPTIONS,
    OPERATING_NEUTRAL,
    PALETTE,
    RISK_SEVERITIES,
    SUBTOTAL_KEYS,
    label_for,
    number_format,
    percent_format,
)
from finance_logic import (
    Adjustment,
    ClassifiedItem,
    Engagement,
    RiskFlag,
    as_float,
    coalesce,
    is_missing,
)

# --------------------------------------------------------------------------- #
# Display helpers
# --------------------------------------------------------------------------- #


_UNESCAPED_DOLLAR = re.compile(r"(?<!\\)\$")


def markdown(text: str, **kwargs) -> None:
    """Render Markdown with dollar signs treated as currency, not as maths.

    Streamlit's Markdown renderer interprets a matched pair of ``$`` as LaTeX
    delimiters. Deal narratives and review memos are dense with currency amounts,
    so an unescaped "$96.0 million ... $16.0 million" silently renders the text
    between them as an equation. Escaping every unescaped ``$`` keeps currency
    literal; no content in this application uses LaTeX.
    """
    st.markdown(_UNESCAPED_DOLLAR.sub(r"\\$", text or ""), **kwargs)


def format_value(value: object, full_precision: bool = False, decimals: int | None = None) -> str:
    """Render a number for inline text. Display only — never mutates the input."""
    numeric = as_float(value)
    if is_missing(numeric):
        return "n/a"
    if decimals is None:
        decimals = 10 if full_precision else 2
    return f"{numeric:,.{decimals}f}"


def format_multiple(value: object) -> str:
    numeric = as_float(value)
    return "n/a" if is_missing(numeric) else f"{numeric:,.2f}x"


def format_percent(value: object, full_precision: bool = False) -> str:
    numeric = as_float(value)
    if is_missing(numeric):
        return "n/a"
    decimals = 10 if full_precision else 2
    return f"{numeric:,.{decimals}f}%"


@lru_cache(maxsize=None)
def _width_kwargs(element: str) -> tuple[tuple[str, object], ...]:
    """Container-width keyword appropriate to the installed Streamlit.

    Streamlit 1.4x replaced ``use_container_width=True`` with ``width="stretch"``
    and emits a deprecation notice for every legacy call — around twenty per
    rerun in this application, which floods a deployment's logs. Older releases
    type ``width`` as pixels only and reject the token. The keyword is therefore
    selected from the installed signature rather than assumed, so one codebase
    runs clean on both.
    """
    func = getattr(st, element, None)
    legacy: tuple[tuple[str, object], ...] = (("use_container_width", True),)
    if func is None:
        return legacy
    try:
        annotation = str(inspect.signature(func).parameters["width"].annotation)
    except (KeyError, TypeError, ValueError):
        return legacy
    # Pre-1.43 signatures read ``int | None``; the token-aware releases use a
    # ``Width`` alias covering int | "stretch" | "content".
    if "int | None" in annotation or annotation in {"int", "<class 'int'>"}:
        return legacy
    return (("width", "stretch"),)


def sizing(height: int | None = None, element: str = "dataframe") -> dict[str, object]:
    """Sizing keyword arguments for Streamlit's data widgets.

    Streamlit 1.4x also tightened height validation: ``height`` must be a
    positive integer, ``"stretch"`` or ``"content"``, and ``None`` now raises
    ``StreamlitInvalidHeightError``. Omitting the argument means "size to
    content" on every release, so it is only included when a height was asked
    for.
    """
    kwargs: dict[str, object] = dict(_width_kwargs(element))
    if height is not None:
        kwargs["height"] = height
    return kwargs


def chart_sizing() -> dict[str, object]:
    """Sizing keyword arguments for ``st.plotly_chart``."""
    return dict(_width_kwargs("plotly_chart"))


def numeric_columns(
    columns: Iterable[str], full_precision: bool, percent_columns: Sequence[str] = ()
) -> dict[str, object]:
    """Build a Streamlit ``column_config`` map of display masks."""
    percent_set = set(percent_columns)
    config: dict[str, object] = {}
    for column in columns:
        if column in percent_set:
            config[column] = st.column_config.NumberColumn(
                column, format=percent_format(full_precision)
            )
        else:
            config[column] = st.column_config.NumberColumn(
                column, format=number_format(full_precision)
            )
    return config


def render_frame(
    frame: pd.DataFrame,
    full_precision: bool,
    percent_rows: Sequence[str] = (),
    height: int | None = None,
) -> None:
    """Render a period-indexed working paper.

    Rows whose label matches ``percent_rows`` are masked as percentages. Because
    Streamlit column formats apply per column rather than per row, percentage
    rows are formatted into strings here; the source frame is left untouched.
    """
    if frame.empty:
        st.info("No data is available for this schedule.")
        return

    display = frame.copy()
    percent_set = {row for row in percent_rows}
    decimals = 10 if full_precision else 2

    if percent_set & set(display.index):
        rendered = display.astype(object)
        for row_label in display.index:
            is_percent = row_label in percent_set
            for column in display.columns:
                value = as_float(display.loc[row_label, column])
                if is_missing(value):
                    rendered.loc[row_label, column] = "n/a"
                elif is_percent:
                    rendered.loc[row_label, column] = f"{value:,.{decimals}f}%"
                else:
                    rendered.loc[row_label, column] = f"{value:,.{decimals}f}"
        st.dataframe(rendered, **sizing(height))
        return

    st.dataframe(
        display,
        **sizing(height),
        column_config=numeric_columns(display.columns, full_precision),
    )


def render_statement(
    engagement: Engagement,
    layout: Sequence[tuple[str, Sequence[str]]],
    full_precision: bool,
    open_first: bool = True,
) -> None:
    """Render a financial statement as collapsible line-item blocks."""
    for index, (block_title, keys) in enumerate(layout):
        rows: list[str] = []
        values: list[list[float]] = []
        for key in keys:
            series = [engagement.fact(key, period) for period in engagement.periods]
            if all(is_missing(value) for value in series):
                continue
            marker = "  ▸ " if key in SUBTOTAL_KEYS else ""
            rows.append(f"{marker}{label_for(key)}")
            values.append(series)

        if not rows:
            continue

        with st.expander(block_title, expanded=(open_first and index == 0)):
            frame = pd.DataFrame(values, index=rows, columns=engagement.periods)
            frame.index.name = block_title
            st.dataframe(
                frame,
                **sizing(),
                column_config=numeric_columns(frame.columns, full_precision),
            )


def metric_row(entries: Sequence[tuple[str, str, str | None]]) -> None:
    """Render a row of headline metrics as (label, value, caption) triples."""
    if not entries:
        return
    columns = st.columns(len(entries))
    for column, (label, value, caption) in zip(columns, entries):
        with column:
            st.metric(label, value)
            if caption:
                st.caption(caption)


# --------------------------------------------------------------------------- #
# Chart theming
# --------------------------------------------------------------------------- #


def _chart_ink() -> tuple[str, str]:
    """Text and gridline colours for the viewer's active Streamlit theme.

    Charts are drawn on a transparent background so they sit on the app's own
    surface; the ink has to follow the theme or the labels disappear.
    """
    try:
        base = str(st.get_option("theme.base") or "light").lower()
    except Exception:  # noqa: BLE001 - option lookup differs across versions
        base = "light"
    if base == "dark":
        return "#E4E7EB", "#3E4C59"
    return "#1F2933", PALETTE["grid"]


def _apply_theme(figure: go.Figure, title: str, height: int = CHART_HEIGHT) -> go.Figure:
    ink, grid = _chart_ink()
    figure.update_layout(
        title={"text": title, "font": {"size": 17, "family": CHART_FONT_FAMILY, "color": ink}},
        height=height,
        margin={"l": 70, "r": 40, "t": 70, "b": 90},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"family": CHART_FONT_FAMILY, "size": 13, "color": ink},
        hoverlabel={"font": {"family": CHART_FONT_FAMILY, "size": 12}},
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.0, "x": 0.0},
    )
    figure.update_xaxes(showgrid=False, linecolor=grid, ticks="outside", tickcolor=grid)
    figure.update_yaxes(
        showgrid=True,
        gridcolor=grid,
        zeroline=True,
        zerolinecolor=PALETTE["neutral"],
        zerolinewidth=1,
    )
    return figure


def _wrap(label: str, width: int = 22) -> str:
    return "<br>".join(textwrap.wrap(label, width=width)) or label


# --------------------------------------------------------------------------- #
# Quality of Earnings waterfall
# --------------------------------------------------------------------------- #


def qoe_waterfall(
    engagement: Engagement,
    adjustments: Sequence[Adjustment],
    period: str,
    reported: float,
    adjusted: float,
) -> go.Figure | None:
    """Bridge reported EBITDA to Adjusted EBITDA for one period."""
    if is_missing(reported) or is_missing(adjusted):
        return None

    accepted = [adj for adj in adjustments if adj.is_accepted and adj.impact(period) != 0.0]

    labels = ["EBITDA<br>(as reported)"]
    measures = ["absolute"]
    values = [reported]
    hover = [f"EBITDA (as reported)<br>{reported:,.2f}"]

    for adjustment in accepted:
        impact = adjustment.impact(period)
        labels.append(_wrap(adjustment.label))
        measures.append("relative")
        values.append(impact)
        hover.append(f"{adjustment.label}<br>{impact:,.2f}<br><i>{adjustment.category}</i>")

    labels.append("Adjusted<br>EBITDA")
    measures.append("total")
    values.append(adjusted)
    hover.append(f"Adjusted EBITDA<br>{adjusted:,.2f}")

    figure = go.Figure(
        go.Waterfall(
            orientation="v",
            measure=measures,
            x=labels,
            y=values,
            text=[f"{value:,.0f}" for value in values],
            textposition="outside",
            customdata=hover,
            hovertemplate="%{customdata}<extra></extra>",
            connector={"line": {"color": PALETTE["neutral"], "width": 1, "dash": "dot"}},
            increasing={"marker": {"color": PALETTE["positive"]}},
            decreasing={"marker": {"color": PALETTE["negative"]}},
            totals={"marker": {"color": PALETTE["total"]}},
        )
    )
    figure.update_yaxes(title_text=f"{engagement.currency}")
    return _apply_theme(
        figure,
        f"Quality of Earnings Bridge — {period}: Reported EBITDA to Adjusted EBITDA",
        height=CHART_HEIGHT + 60,
    )


def value_bridge_chart(steps: Sequence[tuple[str, float]], currency: str) -> go.Figure | None:
    """Waterfall from enterprise value to implied equity value."""
    if not steps:
        return None

    labels: list[str] = []
    measures: list[str] = []
    values: list[float] = []
    for index, (label, amount) in enumerate(steps):
        labels.append(_wrap(label))
        values.append(as_float(amount))
        if index == 0:
            measures.append("absolute")
        elif index == len(steps) - 1:
            measures.append("total")
        else:
            measures.append("relative")

    figure = go.Figure(
        go.Waterfall(
            orientation="v",
            measure=measures,
            x=labels,
            y=values,
            text=[f"{value:,.0f}" for value in values],
            textposition="outside",
            hovertemplate="%{x}<br>%{y:,.2f}<extra></extra>",
            connector={"line": {"color": PALETTE["neutral"], "width": 1, "dash": "dot"}},
            increasing={"marker": {"color": PALETTE["positive"]}},
            decreasing={"marker": {"color": PALETTE["negative"]}},
            totals={"marker": {"color": PALETTE["total"]}},
        )
    )
    figure.update_yaxes(title_text=currency)
    return _apply_theme(
        figure,
        "Transaction Value Bridge — Enterprise Value to Implied Equity Value",
        height=CHART_HEIGHT + 60,
    )


# --------------------------------------------------------------------------- #
# Trend charts
# --------------------------------------------------------------------------- #


def efficiency_trend_chart(metrics: pd.DataFrame) -> go.Figure | None:
    """DSO, DIO and DPO trend with the cash conversion cycle."""
    if metrics.empty:
        return None

    styles = {
        "Days Sales Outstanding (DSO)": (PALETTE["total"], "solid"),
        "Days Inventory Outstanding (DIO)": (PALETTE["accent"], "solid"),
        "Days Payable Outstanding (DPO)": (PALETTE["positive"], "solid"),
        "Cash Conversion Cycle": (PALETTE["negative"], "dash"),
    }

    figure = go.Figure()
    plotted = False
    for row_label, (color, dash) in styles.items():
        if row_label not in metrics.index:
            continue
        series = [as_float(value) for value in metrics.loc[row_label].tolist()]
        if all(is_missing(value) for value in series):
            continue
        plotted = True
        figure.add_trace(
            go.Scatter(
                x=list(metrics.columns),
                y=series,
                name=row_label,
                mode="lines+markers",
                line={"color": color, "width": 2.5, "dash": dash},
                marker={"size": 9, "color": color},
                hovertemplate="%{fullData.name}<br>%{x}: %{y:,.2f} days<extra></extra>",
            )
        )

    if not plotted:
        return None

    figure.update_yaxes(title_text="Days")
    return _apply_theme(figure, "Trailing Working Capital Efficiency (average balances)")


def nwc_trend_chart(
    periods: Sequence[str], nwc_values: Sequence[float], nwc_percent: Sequence[float], peg: float
) -> go.Figure | None:
    """Net working capital balance against the proposed peg."""
    if not periods:
        return None

    figure = go.Figure()
    figure.add_trace(
        go.Bar(
            x=list(periods),
            y=[as_float(value) for value in nwc_values],
            name="Net Working Capital",
            marker={"color": PALETTE["total"]},
            hovertemplate="%{x}<br>NWC: %{y:,.2f}<extra></extra>",
        )
    )
    figure.add_trace(
        go.Scatter(
            x=list(periods),
            y=[as_float(value) for value in nwc_percent],
            name="NWC as % of Revenue",
            mode="lines+markers",
            yaxis="y2",
            line={"color": PALETTE["accent"], "width": 2.5},
            marker={"size": 9},
            hovertemplate="%{x}<br>%{y:,.2f}% of revenue<extra></extra>",
        )
    )
    if not is_missing(peg):
        figure.add_hline(
            y=as_float(peg),
            line={"color": PALETTE["negative"], "width": 2, "dash": "dash"},
            annotation_text=f"Proposed peg: {peg:,.2f}",
            annotation_position="top left",
        )

    figure.update_layout(
        yaxis2={
            "overlaying": "y",
            "side": "right",
            "showgrid": False,
            "title": "% of Revenue",
            "ticksuffix": "%",
        }
    )
    figure.update_yaxes(title_text="Net Working Capital")
    return _apply_theme(figure, "Net Working Capital and the Proposed Peg")


def earnings_quality_chart(
    periods: Sequence[str], reported: Sequence[float], adjusted: Sequence[float]
) -> go.Figure | None:
    """Reported EBITDA against Adjusted EBITDA across the diligence window."""
    if not periods:
        return None

    figure = go.Figure()
    figure.add_trace(
        go.Bar(
            x=list(periods),
            y=[as_float(value) for value in reported],
            name="EBITDA (as reported)",
            marker={"color": PALETTE["neutral"]},
            hovertemplate="%{x}<br>Reported: %{y:,.2f}<extra></extra>",
        )
    )
    figure.add_trace(
        go.Bar(
            x=list(periods),
            y=[as_float(value) for value in adjusted],
            name="Adjusted EBITDA",
            marker={"color": PALETTE["total"]},
            hovertemplate="%{x}<br>Adjusted: %{y:,.2f}<extra></extra>",
        )
    )
    figure.update_layout(barmode="group")
    return _apply_theme(figure, "Reported versus Quality-Adjusted Earnings")


def dcf_chart(schedule: pd.DataFrame, currency: str) -> go.Figure | None:
    """Unlevered free cash flow and its present value across the horizon."""
    if schedule.empty:
        return None

    columns = list(schedule.columns)
    figure = go.Figure()
    figure.add_trace(
        go.Bar(
            x=columns,
            y=[as_float(value) for value in schedule.loc["Unlevered Free Cash Flow"].tolist()],
            name="Unlevered Free Cash Flow",
            marker={"color": PALETTE["neutral"]},
            hovertemplate="%{x}<br>UFCF: %{y:,.2f}<extra></extra>",
        )
    )
    figure.add_trace(
        go.Bar(
            x=columns,
            y=[as_float(value) for value in schedule.loc["Present Value of UFCF"].tolist()],
            name="Present Value of UFCF",
            marker={"color": PALETTE["total"]},
            hovertemplate="%{x}<br>PV: %{y:,.2f}<extra></extra>",
        )
    )
    figure.add_trace(
        go.Scatter(
            x=columns,
            y=[as_float(value) for value in schedule.loc["EBITDA Margin %"].tolist()],
            name="EBITDA Margin %",
            mode="lines+markers",
            yaxis="y2",
            line={"color": PALETTE["accent"], "width": 2.5},
            marker={"size": 9},
            hovertemplate="%{x}<br>Margin: %{y:,.2f}%<extra></extra>",
        )
    )
    figure.update_layout(
        barmode="group",
        yaxis2={
            "overlaying": "y",
            "side": "right",
            "showgrid": False,
            "title": "EBITDA Margin",
            "ticksuffix": "%",
        },
    )
    figure.update_yaxes(title_text=currency)
    return _apply_theme(figure, "Projected Free Cash Flow and Margin Trajectory")


def variance_chart(
    user_value: float, actual_value: float, reported_value: float, management_value: float
) -> go.Figure | None:
    """Compare the analyst's conclusion to the engagement team and management."""
    entries = [
        ("EBITDA<br>(as reported)", reported_value, PALETTE["neutral"]),
        ("Your<br>Adjusted EBITDA", user_value, PALETTE["accent"]),
        ("Engagement Team<br>Adjusted EBITDA", actual_value, PALETTE["total"]),
    ]
    if not is_missing(management_value):
        entries.append(("Management<br>Represented", management_value, PALETTE["negative"]))

    usable = [(label, as_float(value), color) for label, value, color in entries]
    usable = [entry for entry in usable if not is_missing(entry[1])]
    if not usable:
        return None

    figure = go.Figure(
        go.Bar(
            x=[label for label, _, _ in usable],
            y=[value for _, value, _ in usable],
            marker={"color": [color for _, _, color in usable]},
            text=[f"{value:,.0f}" for _, value, _ in usable],
            textposition="outside",
            hovertemplate="%{x}<br>%{y:,.2f}<extra></extra>",
        )
    )
    return _apply_theme(figure, "Your Conclusion Against the Issued Report")


# --------------------------------------------------------------------------- #
# Editors: adjustments, classifications, risks
# --------------------------------------------------------------------------- #

_ADJ_LABEL = "Adjustment"
_ADJ_CATEGORY = "Category"
_ADJ_STATUS = "Treatment"
_ADJ_RATIONALE = "Rationale / Support"


def adjustments_to_frame(adjustments: Sequence[Adjustment], periods: Sequence[str]) -> pd.DataFrame:
    """Serialize adjustments into the shape the data editor expects."""
    records: list[dict[str, object]] = []
    for adjustment in adjustments:
        record: dict[str, object] = {
            _ADJ_LABEL: adjustment.label,
            _ADJ_CATEGORY: adjustment.category,
            _ADJ_STATUS: adjustment.status,
        }
        for period in periods:
            record[period] = float(adjustment.impact(period))
        record[_ADJ_RATIONALE] = adjustment.rationale
        records.append(record)

    columns = [_ADJ_LABEL, _ADJ_CATEGORY, _ADJ_STATUS, *periods, _ADJ_RATIONALE]
    if not records:
        frame = pd.DataFrame(columns=columns)
        for period in periods:
            frame[period] = frame[period].astype(float)
        return frame
    return pd.DataFrame(records, columns=columns)


def frame_to_adjustments(frame: pd.DataFrame, periods: Sequence[str]) -> list[Adjustment]:
    """Deserialize the data editor back into adjustment objects.

    Rows with no label are treated as blank editor rows and skipped. Blank
    period cells are read as zero impact so a partially filled row still books.
    """
    adjustments: list[Adjustment] = []
    for _, row in frame.iterrows():
        label = str(row.get(_ADJ_LABEL) or "").strip()
        if not label:
            continue
        category = str(row.get(_ADJ_CATEGORY) or ADJUSTMENT_CATEGORIES[0])
        status = str(row.get(_ADJ_STATUS) or ADJUSTMENT_STATUSES[0])
        if status not in ADJUSTMENT_STATUSES:
            status = ADJUSTMENT_STATUSES[0]
        impacts = {period: coalesce(row.get(period), 0.0) for period in periods}
        adjustments.append(
            Adjustment(
                label=label,
                category=category,
                period_impacts=impacts,
                rationale=str(row.get(_ADJ_RATIONALE) or ""),
                status=status,
            )
        )
    return adjustments




_CLS_LABEL = "Balance Sheet Item"
_CLS_AMOUNT = "Amount"
_CLS_CLASS = "Classification"
_CLS_RATIONALE = "Diligence Rationale"


def classifications_to_frame(items: Sequence[ClassifiedItem]) -> pd.DataFrame:
    columns = [_CLS_LABEL, _CLS_AMOUNT, _CLS_CLASS, _CLS_RATIONALE]
    if not items:
        frame = pd.DataFrame(columns=columns)
        frame[_CLS_AMOUNT] = frame[_CLS_AMOUNT].astype(float)
        return frame
    return pd.DataFrame(
        [
            {
                _CLS_LABEL: item.label,
                _CLS_AMOUNT: float(coalesce(item.amount)),
                _CLS_CLASS: item.classification,
                _CLS_RATIONALE: item.rationale,
            }
            for item in items
        ],
        columns=columns,
    )


def frame_to_classifications(frame: pd.DataFrame) -> list[ClassifiedItem]:
    items: list[ClassifiedItem] = []
    for _, row in frame.iterrows():
        label = str(row.get(_CLS_LABEL) or "").strip()
        if not label:
            continue
        classification = str(row.get(_CLS_CLASS) or OPERATING_NEUTRAL)
        if classification not in CLASSIFICATION_OPTIONS:
            classification = OPERATING_NEUTRAL
        items.append(
            ClassifiedItem(
                label=label,
                amount=coalesce(row.get(_CLS_AMOUNT), 0.0),
                classification=classification,
                rationale=str(row.get(_CLS_RATIONALE) or ""),
            )
        )
    return items




_RISK_TITLE = "Risk"
_RISK_SEVERITY = "Severity"
_RISK_DESCRIPTION = "Description"


def risks_to_frame(risks: Sequence[RiskFlag]) -> pd.DataFrame:
    columns = [_RISK_TITLE, _RISK_SEVERITY, _RISK_DESCRIPTION]
    if not risks:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(
        [
            {
                _RISK_TITLE: risk.title,
                _RISK_SEVERITY: risk.severity,
                _RISK_DESCRIPTION: risk.description,
            }
            for risk in risks
        ],
        columns=columns,
    )


def frame_to_risks(frame: pd.DataFrame) -> list[RiskFlag]:
    risks: list[RiskFlag] = []
    for _, row in frame.iterrows():
        title = str(row.get(_RISK_TITLE) or "").strip()
        if not title:
            continue
        severity = str(row.get(_RISK_SEVERITY) or RISK_SEVERITIES[0])
        if severity not in RISK_SEVERITIES:
            severity = RISK_SEVERITIES[0]
        risks.append(
            RiskFlag(
                title=title,
                severity=severity,
                description=str(row.get(_RISK_DESCRIPTION) or ""),
            )
        )
    return risks



# --------------------------------------------------------------------------- #
# Balance sheet classification seeding
# --------------------------------------------------------------------------- #

_CANDIDATE_KEYS: tuple[tuple[str, str], ...] = (
    ("deferred_revenue_non_current", "Deferred revenue — non-current"),
    ("other_long_term_liabilities", "Other long-term liabilities"),
    ("other_current_liabilities", "Other current liabilities"),
    ("accrued_liabilities", "Accrued liabilities"),
    ("income_taxes_payable", "Income taxes payable"),
    ("other_non_current_assets", "Other non-current assets"),
    ("capitalized_software_net", "Capitalized software, net"),
    ("goodwill_and_intangibles_net", "Goodwill and intangible assets, net"),
)


def seed_classification_candidates(engagement: Engagement) -> list[ClassifiedItem]:
    """Pre-populate the classification schedule from the closing balance sheet.

    Every candidate starts as operating so nothing is tagged on the analyst's
    behalf — the point of the exercise is that they make the call.
    """
    period = engagement.latest_period
    items: list[ClassifiedItem] = []
    for key, label in _CANDIDATE_KEYS:
        amount = engagement.fact(key, period)
        if is_missing(amount) or amount == 0.0:
            continue
        items.append(
            ClassifiedItem(
                label=f"{label} ({period})",
                amount=float(amount),
                classification=OPERATING_NEUTRAL,
                rationale="",
                source_key=key,
            )
        )
    return items


def severity_badge(severity: str) -> str:
    """Coloured marker for a risk severity."""
    markers = {"Low": "🟢", "Medium": "🟡", "High": "🟠", "Critical": "🔴"}
    return f"{markers.get(severity, '⚪')} **{severity}**"
