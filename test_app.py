"""Headless UI tests driving the real Streamlit render path.

Uses ``streamlit.testing.v1.AppTest`` to run ``app.py`` without a browser,
exercise the mode switch, walk every case, and assert that the tabs, editors,
charts and the review panel all render without raising.

Run with ``python test_app.py``.
"""

from __future__ import annotations

import sys

from streamlit.testing.v1 import AppTest

import master_case
import workspace as ws
from case_studies import CASE_LIBRARY, case_options
from config import MODE_LIVE, MODE_SANDBOX, MODULE_PPA, MODULE_QOE, MODULE_SEC

FAILURES: list[str] = []
TIMEOUT = 120


def case_option(case_key: str) -> tuple[str, str]:
    """The (key, label) tuple the sandbox selector is bound to."""
    return next(option for option in case_options() if option[0] == case_key)


def element_counts(app: AppTest) -> dict[str, int]:
    """Count rendered element types by walking the AppTest element tree.

    AppTest exposes no ``data_editor`` accessor and folds editors in with
    ``st.dataframe``; Plotly figures surface as ``UnknownElement``. Walking the
    tree is the only way to assert on either.
    """
    counts: dict[str, int] = {}

    def walk(node) -> None:
        counts[type(node).__name__] = counts.get(type(node).__name__, 0) + 1
        children = getattr(node, "children", None)
        if isinstance(children, dict):
            children = children.values()
        for child in children or []:
            walk(child)

    walk(app.main)
    walk(app.sidebar)
    return counts


def check(condition: bool, message: str) -> None:
    if condition:
        print(f"  PASS  {message}")
    else:
        print(f"  FAIL  {message}")
        FAILURES.append(message)


def section(title: str) -> None:
    print()
    print(title)
    print("-" * len(title))


def assert_no_exception(app: AppTest, context: str) -> bool:
    if app.exception:
        for exception in app.exception:
            print(f"        {context}: {exception.value}")
        FAILURES.append(f"{context} raised an exception")
        return False
    return True


# The sidebar now leads with the module selector, so the workspace-mode radio
# sits at index 1. Named helpers keep the tests readable if that order changes.
MODULE_RADIO = 0
MODE_RADIO = 1


def start() -> AppTest:
    app = AppTest.from_file("app.py", default_timeout=TIMEOUT)
    app.run()
    return app


def set_mode(app: AppTest, mode: str) -> AppTest:
    app.sidebar.radio[MODE_RADIO].set_value(mode).run()
    return app


def set_module(app: AppTest, module: str) -> AppTest:
    app.sidebar.radio[MODULE_RADIO].set_value(module).run()
    return app


def main() -> int:
    section("1. Landing page (Live Deal Mode, no target loaded)")
    app = start()
    check(assert_no_exception(app, "landing"), "landing page renders without exception")
    check(
        any("FDD / QoE Diligence Workspace" in title.value for title in app.title),
        "page title renders",
    )
    check(len(app.sidebar.radio) >= 2, "module and workspace-mode selectors both render")
    check(
        app.sidebar.radio[MODULE_RADIO].value == MODULE_QOE,
        "Module 1 is the default module",
    )
    check(
        any("Available case studies" in heading.value for heading in app.subheader),
        "landing page lists the case library",
    )

    section("2. Sandbox mode — every case")
    for case_key, case in CASE_LIBRARY.items():
        app = start()
        set_mode(app, MODE_SANDBOX)
        if not assert_no_exception(app, f"{case['name']} mode switch"):
            continue

        # The case selector is the first sidebar selectbox in sandbox mode. Its
        # underlying values are (key, label) tuples; AppTest surfaces only the
        # formatted labels, so select from the source of truth instead.
        app.sidebar.selectbox[0].set_value(case_option(case_key)).run()
        if not assert_no_exception(app, f"{case['name']} selection"):
            continue

        print(f"  · {case['name']}")
        counts = element_counts(app)
        check(len(app.tabs) >= 8, f"{case['name']}: all Module 1 tabs render (incl. Reports & Export)")
        check(
            counts.get("Dataframe", 0) >= 15,
            f"{case['name']}: statements, schedules and editors render "
            f"({counts.get('Dataframe', 0)} tables)",
        )
        check(
            counts.get("UnknownElement", 0) >= 6,
            f"{case['name']}: all six Plotly figures render "
            f"({counts.get('UnknownElement', 0)} charts)",
        )
        check(len(app.metric) >= 8, f"{case['name']}: headline metrics render")

        markdown_text = " ".join(block.value for block in app.markdown)
        check(
            case["target"] in markdown_text,
            f"{case['name']}: the target name appears in the deal context",
        )
        check(
            "Management has represented" in " ".join(info.value for info in app.info),
            f"{case['name']}: management's represented figure is surfaced",
        )

        check(
            any("Debt-Like Items" in heading.value for heading in app.subheader),
            f"{case['name']}: the classification schedule renders",
        )

    section("3. Sandbox workflow — comparison and review")
    app = start()
    set_mode(app, MODE_SANDBOX)
    app.sidebar.selectbox[0].set_value(case_option("anvil")).run()
    assert_no_exception(app, "anvil load")

    compare = [button for button in app.button if "Compare to Actual Deal" in button.label]
    check(len(compare) == 1, "the Compare to Actual Deal button renders")
    compare[0].click().run()
    check(assert_no_exception(app, "comparison"), "comparison runs without exception")

    markdown_text = " ".join(block.value for block in app.markdown)
    check(
        "Report of Factual Findings" in markdown_text,
        "the issued FDD report summary is revealed after comparing",
    )
    check(
        any("Engagement Team" in metric.label for metric in app.metric),
        "the variance metrics render",
    )

    review = [button for button in app.button if "Request Manager Review" in button.label]
    check(len(review) == 1, "the Request Manager Review button renders")
    review[0].click().run()
    check(assert_no_exception(app, "review"), "manager review runs without exception")
    markdown_text = " ".join(block.value for block in app.markdown)
    check(
        "Diligence Summary Memo" in markdown_text,
        "the review panel produces a diligence summary memo",
    )

    section("4. Presentation controls")
    app = start()
    set_mode(app, MODE_SANDBOX)
    app.sidebar.toggle[0].set_value(True).run()
    check(assert_no_exception(app, "full precision"), "full raw precision toggle renders")

    slider_labels = [slider.label for slider in app.slider]
    for expected in ("Revenue CAGR (%)", "WACC (%)", "Target EBITDA margin (%)"):
        check(expected in slider_labels, f"DCF control renders: {expected}")

    check(
        any("Exit EBITDA multiple" in label for label in slider_labels),
        "DCF control renders: Exit EBITDA multiple",
    )

    section("5. Clear working papers")
    app = start()
    set_mode(app, MODE_SANDBOX)
    clear = [button for button in app.button if "Clear working papers" in button.label]
    check(len(clear) == 1, "the Clear working papers button renders")
    clear[0].click().run()
    check(assert_no_exception(app, "clear"), "clearing the workspace runs without exception")

    section("6. Live mode guard rails")
    app = start()
    set_mode(app, MODE_LIVE)
    load = [button for button in app.button if "Load target" in button.label]
    check(len(load) == 1, "the Load target button renders in live mode")
    load[0].click().run()
    check(
        assert_no_exception(app, "empty ticker"),
        "selecting Load target with no ticker does not raise",
    )
    check(
        any("Enter a ticker in the sidebar" in info.value for info in app.info),
        "an empty ticker leaves the landing guidance in place",
    )

    section("6b. Learning layer — tooltips, explainers, glossary")
    app = start()
    set_mode(app, MODE_SANDBOX)
    check(assert_no_exception(app, "learning layer"), "sandbox renders with the learning layer")

    counts = element_counts(app)
    check(
        counts.get("Expander", 0) >= 18,
        f"explainers and term keys render as expanders ({counts.get('Expander', 0)})",
    )

    metric_helps = [m for m in app.metric if getattr(m, "help", None)]
    check(
        len(metric_helps) >= 8,
        f"metrics carry glossary tooltips ({len(metric_helps)} of {len(app.metric)})",
    )

    slider_helps = [s for s in app.slider if getattr(s, "help", None)]
    check(
        len(slider_helps) >= 8,
        f"DCF controls carry tooltips ({len(slider_helps)} of {len(app.slider)})",
    )

    markdown_text = " ".join(b.value for b in app.markdown)
    check(
        "How is this calculated" in " ".join(e.label for e in app.expander)
        if hasattr(app, "expander")
        else True,
        "'How is this calculated?' expanders are present",
    )
    check(
        "Debt-Like Item" in markdown_text or "Debt-Like Item" in " ".join(
            c.value for c in app.caption
        ),
        "the classification colour legend renders",
    )
    check(
        any("Glossary" in str(getattr(e, "label", "")) for e in app.sidebar.expander)
        if hasattr(app.sidebar, "expander")
        else True,
        "the sidebar glossary renders",
    )

    section("7. Module navigation and cross-module isolation")
    app = start()
    set_mode(app, MODE_SANDBOX)
    check(assert_no_exception(app, "module 1"), "Module 1 renders in sandbox mode")
    module_one_tabs = len(app.tabs)

    set_module(app, MODULE_PPA)
    check(assert_no_exception(app, "module 3"), "Module 3 (ASC 805 PPA) renders")
    subheaders = " ".join(heading.value for heading in app.subheader)
    check(
        "ASC 805 Purchase Price Allocation" in subheaders,
        "Module 3 shows the allocation header",
    )
    check(
        any("Day 1 Opening Balance Sheet" in heading.value for heading in app.subheader),
        "Module 3 renders the Day 1 opening balance sheet",
    )
    check(
        any("Marginal tax rate (%)" in slider.label for slider in app.slider),
        "Module 3 exposes the marginal tax rate control",
    )
    check(
        any("Goodwill" in metric.label or "Bargain" in metric.label for metric in app.metric),
        "Module 3 surfaces goodwill or a bargain purchase gain",
    )

    set_module(app, MODULE_SEC)
    check(assert_no_exception(app, "module 2"), "Module 2 (SEC scanner) renders")
    info_text = " ".join(item.value for item in app.info)
    check(
        "EDGAR" in info_text or "EDGAR" in " ".join(h.value for h in app.subheader),
        "Module 2 explains its EDGAR dependency in sandbox mode",
    )

    set_module(app, MODULE_QOE)
    check(assert_no_exception(app, "back to module 1"), "returning to Module 1 renders")
    check(
        len(app.tabs) == module_one_tabs,
        "Module 1 tab structure is unchanged after visiting other modules",
    )
    check(
        any("Quality of Earnings Working Papers" in heading.value for heading in app.subheader),
        "Module 1 working papers survive the round trip",
    )

    section("7b. Master Case — one-click load, propagation and reset")
    app = start()
    loaders = [b for b in app.sidebar.button if b.label == master_case.BUTTON_LABEL]
    resets = [b for b in app.sidebar.button if b.label == master_case.RESET_LABEL]
    check(len(loaders) == 1, "the Load Master Case button renders in the sidebar")
    check(len(resets) == 1, "the Clear Data / Start Fresh button renders alongside it")

    loaders[0].click().run()
    check(assert_no_exception(app, "master case load"), "loading the master case does not raise")
    # Streamlit 1.60 lifts a leading emoji out of the message and renders it as
    # the callout's icon, so the body arrives without it; 1.37 leaves it in
    # place. Assert on the text, which is identical on both.
    confirmation = master_case.SUCCESS_MESSAGE.removeprefix("✅").strip()
    check(
        any(confirmation in s.value for s in app.success),
        "the master case load confirmation renders",
    )
    check(
        app.sidebar.radio[MODE_RADIO].value == MODE_SANDBOX,
        "loading from live mode switches the workspace into Sandbox Mode",
    )

    eid = master_case.ENGAGEMENT_ID
    for name in ("adjustments", "classifications", "risks"):
        frame = app.session_state[f"seed::{eid}::{name}"]
        check(not frame.empty, f"the master case populates the {name} working paper")
    check(
        not app.session_state[ws.key(ws.SEC, eid, "matrix")].empty,
        "the master case populates the Module 2 risk matrix",
    )
    check(
        not app.session_state[ws.key(ws.PPA, eid, "intangibles")].empty,
        "the master case populates the Module 3 intangible schedule",
    )

    # The injected ledger must move Adjusted EBITDA by exactly the stated
    # amounts. These are the headline numbers of the whole feature, so they are
    # asserted bit-for-bit rather than to a tolerance.
    metrics = {m.label: m.value for m in app.metric}
    check(
        metrics.get("EBITDA (as reported)") == "15,500,000.00",
        f"master case reported EBITDA renders as 15,500,000.00 "
        f"(got {metrics.get('EBITDA (as reported)')!r})",
    )
    check(
        metrics.get("Adjusted EBITDA") == "16,300,000.00",
        f"master case Adjusted EBITDA renders as 16,300,000.00 "
        f"(got {metrics.get('Adjusted EBITDA')!r})",
    )
    check(
        metrics.get("Net QoE Adjustments") == "800,000.00",
        f"the two injected adjustments net to 800,000.00 "
        f"(got {metrics.get('Net QoE Adjustments')!r})",
    )

    # The semantic tint shipped as a tested helper that no UI ever called, so
    # the colours existed and were never seen. Assert the wiring, not just the
    # Styler: a colour-coded view of the classified items must actually render.
    classified = [
        d.value
        for d in app.dataframe
        if "Classification" in list(getattr(d.value, "columns", []))
    ]
    check(
        len(classified) >= 2,
        f"the classification schedule renders alongside a colour-coded view "
        f"({len(classified)} views)",
    )
    check(
        any(
            set(frame["Classification"]) <= {"Debt-Like Item", "Non-Operating Asset"}
            and len(frame) == 3
            for frame in classified
        ),
        "the colour-coded view lists only the classified value bridge inputs",
    )
    markdown_text = " ".join(b.value for b in app.markdown)
    check(
        "Value bridge inputs" in markdown_text,
        "the colour-coded view is labelled as the value bridge inputs",
    )

    set_module(app, MODULE_SEC)
    check(
        assert_no_exception(app, "master case module 2"),
        "Module 2 renders the pre-loaded matrix without a ticker",
    )
    check(
        any("Pre-Loaded Narrative Risk Matrix" in h.value for h in app.subheader),
        "the pre-loaded risk matrix is surfaced in Module 2",
    )

    set_module(app, MODULE_PPA)
    check(assert_no_exception(app, "master case module 3"), "Module 3 renders the allocation")
    ppa_metrics = {m.label: m.value for m in app.metric}
    check(
        ppa_metrics.get("Identifiable Net Assets") == "47,080,000.00",
        f"the injected fair values produce identifiable net assets of 47,080,000.00 "
        f"(got {ppa_metrics.get('Identifiable Net Assets')!r})",
    )
    check(
        ppa_metrics.get("Deferred Tax Liability") == "17,200,000.00",
        f"the step-ups produce a deferred tax liability of 17,200,000.00 "
        f"(got {ppa_metrics.get('Deferred Tax Liability')!r})",
    )

    set_module(app, MODULE_QOE)
    [b for b in app.sidebar.button if b.label == master_case.RESET_LABEL][0].click().run()
    check(assert_no_exception(app, "master case reset"), "clearing the master case does not raise")
    check(
        app.session_state[f"seed::{eid}::adjustments"].empty,
        "the reset empties the adjustment ledger",
    )
    check(
        ws.key(ws.SEC, eid, "matrix") not in app.session_state,
        "the reset drops the Module 2 risk matrix",
    )
    check(
        ws.key(ws.PPA, eid, "intangibles") not in app.session_state,
        "the reset drops the Module 3 intangible schedule",
    )

    # Pressing reset on an already-clean workspace must be a no-op, not a
    # KeyError. Every removal in ``master_case.clear`` is a pop with a default.
    [b for b in app.sidebar.button if b.label == master_case.RESET_LABEL][0].click().run()
    check(
        assert_no_exception(app, "master case double reset"),
        "clearing an already-clear workspace is safe",
    )

    section("8. Live-mode module availability")
    app = start()
    set_mode(app, MODE_LIVE)
    set_module(app, MODULE_SEC)
    check(
        assert_no_exception(app, "module 2 live, no ticker"),
        "Module 2 handles live mode with no ticker loaded",
    )

    print()
    if FAILURES:
        print(f"{len(FAILURES)} CHECK(S) FAILED")
        for failure in FAILURES:
            print(f"  - {failure}")
        return 1
    print("All UI checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
