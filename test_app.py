"""Headless UI tests driving the real Streamlit render path.

Uses ``streamlit.testing.v1.AppTest`` to run ``app.py`` without a browser,
exercise the mode switch, walk every case, and assert that the tabs, editors,
charts and the review panel all render without raising.

Run with ``python test_app.py``.
"""

from __future__ import annotations

import sys

from streamlit.testing.v1 import AppTest

from case_studies import CASE_LIBRARY, case_options
from config import MODE_LIVE, MODE_SANDBOX

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


def start() -> AppTest:
    app = AppTest.from_file("app.py", default_timeout=TIMEOUT)
    app.run()
    return app


def main() -> int:
    section("1. Landing page (Live Deal Mode, no target loaded)")
    app = start()
    check(assert_no_exception(app, "landing"), "landing page renders without exception")
    check(
        any("FDD / QoE Diligence Workspace" in title.value for title in app.title),
        "page title renders",
    )
    check(len(app.sidebar.radio) >= 1, "workspace mode selector renders in the sidebar")
    check(
        any("Available case studies" in heading.value for heading in app.subheader),
        "landing page lists the case library",
    )

    section("2. Sandbox mode — every case")
    for case_key, case in CASE_LIBRARY.items():
        app = start()
        app.sidebar.radio[0].set_value(MODE_SANDBOX).run()
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
        check(len(app.tabs) >= 7, f"{case['name']}: seven top-level tabs render")
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
    app.sidebar.radio[0].set_value(MODE_SANDBOX).run()
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
    app.sidebar.radio[0].set_value(MODE_SANDBOX).run()
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
    app.sidebar.radio[0].set_value(MODE_SANDBOX).run()
    clear = [button for button in app.button if "Clear working papers" in button.label]
    check(len(clear) == 1, "the Clear working papers button renders")
    clear[0].click().run()
    check(assert_no_exception(app, "clear"), "clearing the workspace runs without exception")

    section("6. Live mode guard rails")
    app = start()
    app.sidebar.radio[0].set_value(MODE_LIVE).run()
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
