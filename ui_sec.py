"""Module 2 — SEC Narrative Risk & M&A Red Flag Scanner (UI).

Fetches the target's recent filings, scans the narrative sections for M&A red
flags, diffs them against the prior year, and lets the analyst accept flags with
a dollar haircut that is pushed into the Module 1 QoE ledger.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

import ai_reviewer
import components as ui
import edgar_client as edgar
import glossary
import risk_engine as risk
import text_analysis as nlp
import workspace as ws
from config import (
    EDGAR_CACHE_TTL_SECONDS,
    EDGAR_DEFAULT_ANNUAL_FILINGS,
    EDGAR_DEFAULT_QUARTERLY_FILINGS,
    RISK_SEVERITIES,
)
from export import ReportPayload, ReportSection, safe_filename
from finance_logic import is_missing


@st.cache_data(ttl=EDGAR_CACHE_TTL_SECONDS, show_spinner=False)
def load_filings(ticker: str, annual: int, quarterly: int):
    """Cached EDGAR retrieval. Never re-pings SEC for a bundle already held."""
    return edgar.build_bundle(ticker, annual_count=annual, quarterly_count=quarterly)


@st.cache_data(show_spinner=False)
def scan_document_sections(sections: dict[str, str], fiscal_year: str, form: str):
    """Cached NLP pass over one filing's sections."""
    hits: list[nlp.RiskHit] = []
    for section_key, body in sections.items():
        hits.extend(nlp.scan_text(body, section_key, fiscal_year, form))
    return hits


def _readability_frame(current, prior, current_label: str, prior_label: str) -> pd.DataFrame:
    rows = []
    current_map, prior_map = current.as_dict(), prior.as_dict() if prior else {}
    for metric, value in current_map.items():
        prior_value = prior_map.get(metric, float("nan"))
        change = (
            float("nan")
            if is_missing(prior_value) or is_missing(value)
            else value - prior_value
        )
        rows.append(
            {
                "Metric": metric,
                prior_label: prior_value,
                current_label: value,
                "Change": change,
            }
        )
    return pd.DataFrame(rows).set_index("Metric")


def render(engagement, engagement_id: str, settings: dict, seed_key_fn, widget_key_fn) -> None:
    """Render Module 2."""
    full_precision = settings["full_precision"]
    ticker = (engagement.ticker or "").strip().upper()

    st.subheader("SEC Narrative Risk & M&A Red Flag Scanner")
    st.markdown(
        "Pulls the target's 10-K and 10-Q filings straight from SEC EDGAR, extracts Item 1A, "
        "Item 7 and the contingencies footnote, and scans the narrative for the language that "
        "matters in diligence. Every flag traces to the sentence that triggered it — the scanner "
        "tells you where to read, it does not tell you what to conclude."
    )

    if not ticker:
        st.info(
            "Module 2 reads filings from SEC EDGAR, which indexes US registrants by ticker. "
            "Switch to **Live Deal Mode** in the sidebar and load a ticker to use this module — "
            "the sandbox cases are fictional targets with no EDGAR presence.",
            icon="ℹ️",
        )
        return

    controls_left, controls_mid, controls_right = st.columns([1, 1, 2])
    with controls_left:
        annual = st.slider(
            "Annual filings (10-K)", 2, 5, EDGAR_DEFAULT_ANNUAL_FILINGS,
            key=ws.key(ws.SEC, engagement_id, "annual"),
            help=glossary.combine(
                "10-K", "Two years minimum — the year-over-year diff is the point of this module."
            ),
        )
    with controls_mid:
        quarterly = st.slider(
            "Quarterly filings (10-Q)", 0, 4, EDGAR_DEFAULT_QUARTERLY_FILINGS,
            key=ws.key(ws.SEC, engagement_id, "quarterly"),
            help=glossary.help_for("10-Q"),
        )
    with controls_right:
        st.caption(
            f"Requests declare `{edgar.user_agent()}` as required by SEC, run at most "
            f"{edgar.EDGAR_MAX_CONCURRENCY} concurrently, and are cached for "
            f"{EDGAR_CACHE_TTL_SECONDS // 3600} hours."
        )

    if not st.button("Fetch filings from EDGAR", type="primary", **ui.sizing(element="button")):
        if not ws.consume(engagement_id, "sec_fetched"):
            st.caption("No filings retrieved yet for this engagement.")
            return

    try:
        with st.spinner(f"Retrieving filings for {ticker} from SEC EDGAR…"):
            bundle = load_filings(ticker, annual, quarterly)
        ws.publish(engagement_id, "sec_fetched", True)
    except edgar.EdgarError as exc:
        st.error(str(exc), icon="🚫")
        return
    except Exception as exc:  # noqa: BLE001 - EDGAR fails in many ways
        st.error(
            f"EDGAR retrieval failed unexpectedly ({type(exc).__name__}: {exc}).", icon="🚫"
        )
        return

    for warning in bundle.warnings:
        st.warning(warning, icon="⚠️")

    annual_docs = bundle.annual()
    if not annual_docs:
        st.error(
            "No annual filing could be parsed, so there is nothing to scan. EDGAR throttles "
            "datacenter IP ranges, which affects cloud deployments more than local runs.",
            icon="🚫",
        )
        return

    st.success(
        f"**{bundle.company_name}** · CIK {bundle.cik} · "
        f"{len([d for d in bundle.documents if d.ok])} of {len(bundle.documents)} filings parsed",
        icon="✅",
    )

    current = annual_docs[0]
    prior = annual_docs[1] if len(annual_docs) > 1 else None

    with st.expander("Filings retrieved", expanded=False):
        index = pd.DataFrame(
            [
                {
                    "Form": doc.ref.form,
                    "Fiscal Year": doc.ref.fiscal_year,
                    "Filed": doc.ref.filing_date,
                    "Sections Extracted": len(doc.sections),
                    "Characters": float(len(doc.raw_text)),
                    "Status": "Parsed" if doc.ok else doc.fetch_error[:60],
                }
                for doc in bundle.documents
            ]
        )
        st.dataframe(index, hide_index=True, **ui.sizing())
        for doc in bundle.documents:
            for warning in doc.warnings:
                st.caption(f"{doc.ref.form} {doc.ref.fiscal_year}: {warning}")

    scan_tab, delta_tab, matrix_tab, memo_tab = st.tabs(
        ["Risk Scan", "Year-over-Year Narrative Delta", "Diligence Impact Matrix", "Director Memo"]
    )

    current_hits = scan_document_sections(current.sections, current.ref.fiscal_year, current.ref.form)
    prior_hits = (
        scan_document_sections(prior.sections, prior.ref.fiscal_year, prior.ref.form)
        if prior
        else []
    )
    scored = risk.score_risks(current_hits, prior_hits, current.ref.fiscal_year)

    # ---------------------------------------------------------------- scan --
    with scan_tab:
        critical = [item for item in scored if item.severity == "Critical"]
        high = [item for item in scored if item.severity == "High"]
        new_items = [item for item in scored if item.is_new]

        ui.metric_row(
            [
                ("Flags Raised", f"{len(scored):,}", current.ref.fiscal_year),
                ("Critical", f"{len(critical):,}", "Threshold issues"),
                ("High", f"{len(high):,}", None),
                (
                    "New This Year",
                    f"{len(new_items):,}",
                    f"vs. {prior.ref.fiscal_year}" if prior else "no prior filing",
                ),
            ]
        )

        if scored:
            totals = pd.DataFrame(
                [
                    {
                        "Risk Category": category,
                        "Mentions": float(total),
                        "Flags": float(
                            len([item for item in scored if item.category == category])
                        ),
                    }
                    for category, total in nlp.aggregate_by_category(current_hits).items()
                    if total
                ]
            ).sort_values("Mentions", ascending=False)
            figure = ui.category_bar_chart(totals)
            if figure is not None:
                st.plotly_chart(figure, **ui.chart_sizing())

        ui.explainer("risk_scan")

        st.divider()
        st.subheader("Flagged Risks")
        for item in scored:
            marker = {"Critical": "🔴", "High": "🟠", "Medium": "🟡", "Low": "🟢"}.get(
                item.severity, "⚪"
            )
            movement = (
                "**NEW this year**"
                if item.is_new
                else f"{item.prior_occurrences} → {item.occurrences} mentions"
            )
            with st.expander(
                f"{marker} {item.label} — {item.category} ({movement})", expanded=False
            ):
                ui.metric_row(
                    [
                        ("Impact Score", f"{item.score:,.4f}", "severity × frequency × YoY"),
                        ("Mentions", f"{item.occurrences:,}", current.ref.fiscal_year),
                        (
                            "Prior Year",
                            f"{item.prior_occurrences:,}",
                            prior.ref.fiscal_year if prior else "n/a",
                        ),
                    ]
                )
                ui.markdown(f"**Why it matters.** {item.rationale}")
                if item.evidence:
                    st.markdown("**Evidence from the filing:**")
                    for sentence in item.evidence:
                        st.markdown(f"> {sentence}")

        st.divider()
        st.subheader("Disclosure Quality")
        current_read = nlp.readability(current.section(edgar.SECTION_RISK_FACTORS))
        prior_read = (
            nlp.readability(prior.section(edgar.SECTION_RISK_FACTORS)) if prior else None
        )
        if prior_read:
            ui.render_frame(
                _readability_frame(
                    current_read, prior_read, current.ref.fiscal_year, prior.ref.fiscal_year
                ),
                full_precision,
            )
            st.caption(
                "Risk factor sections that lengthen materially, or become harder to read without "
                "a change in business complexity, are a documented signal of deteriorating "
                "disclosure quality."
            )
        else:
            st.info("A prior-year filing is required to trend disclosure metrics.", icon="ℹ️")

    # --------------------------------------------------------------- delta --
    with delta_tab:
        if prior is None:
            st.info(
                "Only one annual filing was retrieved, so there is nothing to diff. Raise the "
                "annual filing count above.",
                icon="ℹ️",
            )
            deltas = []
        else:
            deltas = []
            for section_key in (
                edgar.SECTION_RISK_FACTORS,
                edgar.SECTION_MDA,
                edgar.SECTION_CONTINGENCIES,
            ):
                current_body, prior_body = current.section(section_key), prior.section(section_key)
                if current_body and prior_body:
                    deltas.append(
                        risk.compare_sections(
                            current_body,
                            prior_body,
                            section_key,
                            current.ref.fiscal_year,
                            prior.ref.fiscal_year,
                        )
                    )

            if not deltas:
                st.warning(
                    "No section could be compared — the same item must extract from both filings.",
                    icon="⚠️",
                )
            else:
                st.subheader("Narrative Delta Matrix")
                ui.render_frame(
                    risk.delta_summary_frame(deltas).set_index("Section").transpose(),
                    full_precision,
                    percent_rows=("Change %", "Similarity %"),
                    define_rows=True,
                )

                st.divider()
                choice = st.selectbox(
                    "Section to compare",
                    deltas,
                    format_func=lambda delta: edgar.SECTION_LABELS.get(
                        delta.section_key, delta.section_key
                    ),
                    key=ws.key(ws.SEC, engagement_id, "delta_section"),
                )
                st.caption(
                    "Language added in the current filing is shown in red; language removed since "
                    "the prior year in green. What a filer newly added is the finding."
                )
                st.components.v1.html(
                    risk.render_delta_html(choice), height=640, scrolling=True
                )
                ui.explainer("narrative_delta")

    # -------------------------------------------------------------- matrix --
    with matrix_tab:
        st.subheader("Diligence Impact Matrix")
        st.markdown(
            "Accept the flags you judge to be real and assign a dollar EBITDA haircut to each. "
            "Enter the haircut as a **positive** number — it is applied as a negative adjustment. "
            "Nothing reaches the QoE ledger without both an explicit accept and a figure you typed."
        )

        seed_matrix = ws.module_state(
            ws.SEC, engagement_id, "matrix", lambda: risk.risks_to_matrix(scored)
        )
        if len(seed_matrix) != len(scored):
            seed_matrix = risk.risks_to_matrix(scored)
            ws.set_module_state(ws.SEC, engagement_id, "matrix", seed_matrix)

        edited = st.data_editor(
            seed_matrix,
            hide_index=True,
            **ui.sizing(element="data_editor"),
            column_config={
                risk.COL_ACCEPT: st.column_config.CheckboxColumn(risk.COL_ACCEPT, width="small"),
                risk.COL_CATEGORY: st.column_config.TextColumn(
                    risk.COL_CATEGORY, width="medium", disabled=True
                ),
                risk.COL_RISK: st.column_config.TextColumn(
                    risk.COL_RISK, width="large", disabled=True
                ),
                risk.COL_SEVERITY: st.column_config.SelectboxColumn(
                    risk.COL_SEVERITY,
                    options=list(RISK_SEVERITIES),
                    width="small",
                    help=glossary.help_for("Severity"),
                ),
                risk.COL_OCCURRENCES: st.column_config.NumberColumn(
                    risk.COL_OCCURRENCES, format="%.0f", disabled=True, width="small"
                ),
                risk.COL_PRIOR: st.column_config.NumberColumn(
                    risk.COL_PRIOR, format="%.0f", disabled=True, width="small"
                ),
                risk.COL_NEW: st.column_config.CheckboxColumn(
                    risk.COL_NEW, disabled=True, width="small"
                ),
                risk.COL_SCORE: st.column_config.NumberColumn(
                    risk.COL_SCORE,
                    format="%.4f",
                    disabled=True,
                    width="small",
                    help=glossary.help_for("Impact Score"),
                ),
                risk.COL_HAIRCUT: st.column_config.NumberColumn(
                    risk.COL_HAIRCUT,
                    format=ui.number_format_for(full_precision),
                    help=glossary.help_for("EBITDA Haircut"),
                ),
                risk.COL_NOTE: st.column_config.TextColumn(risk.COL_NOTE, width="large"),
            },
            key=ws.key(ws.SEC, engagement_id, "matrix_editor"),
        )

        accepted_count, total_haircut, total_score = risk.matrix_totals(edited)
        ui.metric_row(
            [
                ("Flags Accepted", f"{accepted_count:,}", f"of {len(edited):,} raised"),
                ("Total EBITDA Haircut", ui.format_value(total_haircut, full_precision), None),
                ("Accepted Impact Score", f"{total_score:,.4f}", None),
            ]
        )

        target_period = st.selectbox(
            "Apply the haircuts to which diligence period?",
            engagement.periods,
            index=len(engagement.periods) - 1,
            key=ws.key(ws.SEC, engagement_id, "target_period"),
        )

        push_left, push_right = st.columns([1, 3])
        with push_left:
            push = st.button(
                "Push to QoE Adjustments", type="primary", **ui.sizing(element="button")
            )
        with push_right:
            st.caption(
                "Appends one adjustment row per accepted flag to the Module 1 ledger, categorised "
                "and marked as sourced from the SEC filing. Your existing adjustments are preserved."
            )

        if push:
            rows = risk.matrix_to_ledger_rows(
                edited, engagement.periods, target_period, current.ref.fiscal_year
            )
            if rows.empty:
                st.warning(
                    "No flag is both accepted and carries a non-zero haircut, so there is nothing "
                    "to push.",
                    icon="⚠️",
                )
            else:
                count = ws.push_to_qoe_ledger(
                    engagement_id,
                    seed_key_fn(engagement_id, "adjustments"),
                    widget_key_fn(engagement_id, "adjustments"),
                    rows,
                    engagement.periods,
                )
                ws.set_module_state(ws.SEC, engagement_id, "matrix", edited)
                st.success(
                    f"{count} adjustment row(s) pushed into the Module 1 QoE ledger against "
                    f"{target_period}. Open **Module 1 → QoE Working Papers** to see them in the "
                    "bridge and waterfall.",
                    icon="✅",
                )
                st.rerun()

        ws.publish(
            engagement_id,
            "sec_matrix_summary",
            {
                "accepted_flags": accepted_count,
                "total_haircut": total_haircut,
                "total_impact_score": total_score,
                "flags_raised": len(edited),
            },
        )

    # ---------------------------------------------------------------- memo --
    with memo_tab:
        _render_memo(
            engagement_id,
            bundle,
            current,
            prior,
            scored,
            deltas if prior else [],
            settings,
            edited,
        )


def _render_memo(engagement_id, bundle, current, prior, scored, deltas, settings, matrix) -> None:
    st.subheader("M&A Narrative Risk & Red Flag Memo")
    st.markdown(
        "A TAS Director reads the extracted filing language and your accepted flags, then writes "
        "the qualitative risk memo that accompanies the QoE report."
    )

    engine_choice = st.radio(
        "Review engine",
        ("Claude Director review", ai_reviewer.HEURISTIC_ENGINE_NAME),
        index=0 if settings["ai_configured"] else 1,
        horizontal=True,
        key=ws.key(ws.SEC, engagement_id, "engine"),
    )
    use_claude = engine_choice == "Claude Director review" and settings["ai_configured"]

    current_read = nlp.readability(current.section(edgar.SECTION_RISK_FACTORS)).as_dict()
    prior_read = (
        nlp.readability(prior.section(edgar.SECTION_RISK_FACTORS)).as_dict() if prior else {}
    )
    accepted_count, total_haircut, total_score = risk.matrix_totals(matrix)
    matrix_summary = {
        "accepted_flags": accepted_count,
        "total_haircut": total_haircut,
        "total_impact_score": total_score,
        "accepted_detail": [
            {
                "risk": str(row[risk.COL_RISK]),
                "category": str(row[risk.COL_CATEGORY]),
                "severity": str(row[risk.COL_SEVERITY]),
                "haircut": float(row[risk.COL_HAIRCUT]),
            }
            for _, row in risk.accepted_rows(matrix).iterrows()
        ],
    }

    memo_key = ws.key(ws.SEC, engagement_id, "memo")
    if st.button("Generate Risk Memo", type="primary", **ui.sizing(element="button")):
        with st.spinner("The Director is reading the filing…"):
            result = None
            if use_claude:
                prompt = ai_reviewer.build_sec_risk_prompt(
                    bundle.company_name,
                    bundle.ticker,
                    current.ref.fiscal_year,
                    prior.ref.fiscal_year if prior else "n/a",
                    scored,
                    deltas,
                    current_read,
                    prior_read,
                    matrix_summary,
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
                result = ai_reviewer.heuristic_sec_memo(
                    bundle.company_name,
                    bundle.ticker,
                    current.ref.fiscal_year,
                    prior.ref.fiscal_year if prior else "n/a",
                    scored,
                    deltas,
                    current_read,
                    prior_read,
                    matrix_summary,
                )
        st.session_state[memo_key] = result

    result = st.session_state.get(memo_key)
    if result is None:
        st.caption("No memo has been generated for this engagement yet.")
        return

    st.divider()
    st.caption(f"Review engine: **{result.engine}**")
    for notice in result.notices:
        st.caption(notice)
    ui.markdown(result.body)

    payload = ReportPayload(
        title="M&A Narrative Risk & Red Flag Memo",
        entity=f"{bundle.company_name} ({bundle.ticker}) — CIK {bundle.cik}",
        subtitle=(
            f"SEC filing narrative review · {current.ref.form} {current.ref.fiscal_year}"
            + (f" versus {prior.ref.fiscal_year}" if prior else "")
        ),
        sections=[
            ReportSection("Director Review", result.body),
            ReportSection(
                "Risk Flags Raised",
                "",
                pd.DataFrame(
                    [
                        {
                            "Category": item.category,
                            "Risk": item.label,
                            "Severity": item.severity,
                            "Mentions": float(item.occurrences),
                            "Prior Year": float(item.prior_occurrences),
                            "Impact Score": item.score,
                        }
                        for item in scored
                    ]
                ),
                include_index=False,
            ),
            ReportSection(
                "Narrative Delta Summary", "", risk.delta_summary_frame(deltas), include_index=False
            ),
        ],
    )
    ui.render_export_buttons(payload, safe_filename(bundle.ticker, "risk_memo"), key_prefix=f"sec::{engagement_id}")
