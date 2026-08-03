"""Report export: formal QoE report, narrative risk memo and PPA schedules.

Produces a paginated PDF via ``fpdf2`` plus Markdown and CSV fallbacks. The PDF
uses the built-in core fonts, which are Latin-1 only, so the typographic
characters this application uses throughout — em dashes, curly quotes, arrows —
are transliterated to ASCII on the way in rather than raising a encoding error
mid-render.

Numbers are formatted for presentation here and nowhere else; the values passed
in are never mutated.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import date

import pandas as pd

try:  # pragma: no cover - exercised implicitly at runtime
    from fpdf import FPDF

    FPDF_AVAILABLE = True
    FPDF_IMPORT_ERROR = ""
except Exception as exc:  # pragma: no cover
    FPDF = None  # type: ignore[assignment]
    FPDF_AVAILABLE = False
    FPDF_IMPORT_ERROR = str(exc)

from finance_logic import as_float, is_missing

# --------------------------------------------------------------------------- #
# Text preparation
# --------------------------------------------------------------------------- #

_TRANSLITERATIONS = {
    "—": "-", "–": "-", "‒": "-", "−": "-",
    "‘": "'", "’": "'", "‚": ",", "‛": "'",
    "“": '"', "”": '"', "„": '"',
    "…": "...", "→": "->", "←": "<-", "≥": ">=",
    "≤": "<=", " ": " ", "•": "-", "·": "-",
    "′": "'", "″": '"', "×": "x", "≈": "~",
    "▸": ">", "✓": "y", "✗": "x",
}

_MARKDOWN_NOISE = re.compile(r"^#{1,6}\s*|\*\*|__|`")


def ascii_safe(text: str) -> str:
    """Make text safe for the PDF core fonts without dropping meaning."""
    if not text:
        return ""
    for source, replacement in _TRANSLITERATIONS.items():
        text = text.replace(source, replacement)
    return text.encode("latin-1", errors="replace").decode("latin-1")


def _strip_markdown(line: str) -> str:
    return _MARKDOWN_NOISE.sub("", line).strip()


def format_number(value: object, decimals: int = 2) -> str:
    numeric = as_float(value)
    if is_missing(numeric):
        return "n/a"
    return f"{numeric:,.{decimals}f}"


# --------------------------------------------------------------------------- #
# Report payload
# --------------------------------------------------------------------------- #


@dataclass
class ReportSection:
    """One titled block: prose, a table, or both."""

    title: str
    body: str = ""
    table: pd.DataFrame | None = None
    include_index: bool = True


@dataclass
class ReportPayload:
    """Everything a generated report needs."""

    title: str
    entity: str
    subtitle: str = ""
    sections: list[ReportSection] | None = None

    def blocks(self) -> list[ReportSection]:
        return self.sections or []


# --------------------------------------------------------------------------- #
# PDF
# --------------------------------------------------------------------------- #


class _ReportPDF(FPDF if FPDF_AVAILABLE else object):  # type: ignore[misc]
    """Paginated report with a running header and footer."""

    def __init__(self, title: str, entity: str) -> None:
        super().__init__(orientation="P", unit="mm", format="Letter")
        self.report_title = ascii_safe(title)
        self.report_entity = ascii_safe(entity)
        self.set_auto_page_break(auto=True, margin=18)
        self.set_margins(16, 16, 16)

    def header(self) -> None:  # noqa: D102 - fpdf hook
        if self.page_no() == 1:
            return
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(90, 107, 123)
        self.cell(0, 6, self.report_entity, align="L")
        self.cell(0, 6, self.report_title, align="R", new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(216, 222, 228)
        self.line(16, self.get_y(), self.w - 16, self.get_y())
        self.ln(4)
        self.set_text_color(31, 41, 51)

    def footer(self) -> None:  # noqa: D102 - fpdf hook
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(90, 107, 123)
        self.cell(0, 6, f"Page {self.page_no()} of {{nb}}", align="C")
        self.set_text_color(31, 41, 51)


def _write_table(pdf: "_ReportPDF", frame: pd.DataFrame, include_index: bool) -> None:
    """Render a DataFrame as a fitted table, wrapping onto new pages as needed."""
    if frame is None or frame.empty:
        pdf.set_font("Helvetica", "I", 9)
        pdf.multi_cell(0, 5, "No data.", new_x="LMARGIN", new_y="NEXT")
        return

    display = frame.copy()
    if include_index:
        display = display.reset_index()

    headers = [ascii_safe(str(column)) for column in display.columns]
    rows: list[list[str]] = []
    for _, record in display.iterrows():
        row: list[str] = []
        for value in record:
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                row.append(format_number(value))
            else:
                row.append(ascii_safe(str(value)))
        rows.append(row)

    usable = pdf.w - 32.0
    # First column carries the label and gets the larger share.
    if len(headers) > 1:
        first = usable * 0.34
        others = (usable - first) / float(len(headers) - 1)
        widths = [first] + [others] * (len(headers) - 1)
    else:
        widths = [usable]

    def truncate(text: str, width: float) -> str:
        while text and pdf.get_string_width(text) > width - 2.0:
            text = text[:-1]
        return text

    pdf.set_font("Helvetica", "B", 7.5)
    pdf.set_fill_color(244, 246, 248)
    for header, width in zip(headers, widths):
        pdf.cell(width, 6, truncate(header, width), border=0, fill=True, align="L")
    pdf.ln(6)
    pdf.set_draw_color(216, 222, 228)
    pdf.line(16, pdf.get_y(), pdf.w - 16, pdf.get_y())
    pdf.ln(1)

    pdf.set_font("Helvetica", "", 7.5)
    for index, row in enumerate(rows):
        if pdf.get_y() > pdf.h - 28:
            pdf.add_page()
            pdf.set_font("Helvetica", "B", 7.5)
            for header, width in zip(headers, widths):
                pdf.cell(width, 6, truncate(header, width), border=0, fill=True, align="L")
            pdf.ln(6)
            pdf.set_font("Helvetica", "", 7.5)
        if index % 2 == 1:
            pdf.set_fill_color(250, 251, 252)
            pdf.cell(sum(widths), 5, "", fill=True)
            pdf.ln(0)
            pdf.set_x(16)
        for cell_index, (value, width) in enumerate(zip(row, widths)):
            align = "L" if cell_index == 0 else "R"
            pdf.cell(width, 5, truncate(value, width), align=align)
        pdf.ln(5)
    pdf.ln(3)


def build_pdf(payload: ReportPayload) -> bytes:
    """Render the payload to PDF bytes."""
    if not FPDF_AVAILABLE:
        raise RuntimeError(f"fpdf2 is unavailable: {FPDF_IMPORT_ERROR}")

    pdf = _ReportPDF(payload.title, payload.entity)
    pdf.alias_nb_pages()
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 19)
    pdf.set_text_color(31, 58, 95)
    pdf.multi_cell(0, 9, ascii_safe(payload.title), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)

    pdf.set_font("Helvetica", "", 12)
    pdf.set_text_color(31, 41, 51)
    pdf.multi_cell(0, 6, ascii_safe(payload.entity), new_x="LMARGIN", new_y="NEXT")

    if payload.subtitle:
        pdf.set_font("Helvetica", "I", 9.5)
        pdf.set_text_color(90, 107, 123)
        pdf.multi_cell(0, 5, ascii_safe(payload.subtitle), new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "I", 8.5)
    pdf.set_text_color(90, 107, 123)
    pdf.multi_cell(
        0, 5,
        ascii_safe(f"Generated {date.today().isoformat()} - private and confidential"),
        new_x="LMARGIN", new_y="NEXT",
    )
    pdf.set_text_color(31, 41, 51)
    pdf.ln(3)
    pdf.set_draw_color(31, 58, 95)
    pdf.set_line_width(0.5)
    pdf.line(16, pdf.get_y(), pdf.w - 16, pdf.get_y())
    pdf.set_line_width(0.2)
    pdf.ln(5)

    for block in payload.blocks():
        if pdf.get_y() > pdf.h - 50:
            pdf.add_page()

        pdf.set_font("Helvetica", "B", 12)
        pdf.set_text_color(31, 58, 95)
        pdf.multi_cell(0, 7, ascii_safe(block.title), new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(31, 41, 51)
        pdf.ln(1)

        if block.body:
            pdf.set_font("Helvetica", "", 9.5)
            for line in block.body.splitlines():
                stripped = _strip_markdown(line)
                if not stripped:
                    pdf.ln(2)
                    continue
                if line.strip().startswith("#"):
                    pdf.set_font("Helvetica", "B", 10.5)
                    pdf.multi_cell(0, 5.5, ascii_safe(stripped), new_x="LMARGIN", new_y="NEXT")
                    pdf.set_font("Helvetica", "", 9.5)
                elif line.strip().startswith(("-", "*")):
                    pdf.multi_cell(
                        0, 5, ascii_safe("  - " + stripped.lstrip("-* ")),
                        new_x="LMARGIN", new_y="NEXT",
                    )
                else:
                    pdf.multi_cell(0, 5, ascii_safe(stripped), new_x="LMARGIN", new_y="NEXT")
            pdf.ln(2)

        if block.table is not None:
            _write_table(pdf, block.table, block.include_index)

    output = pdf.output()
    return bytes(output)


# --------------------------------------------------------------------------- #
# Markdown and workbook fallbacks
# --------------------------------------------------------------------------- #


def markdown_table(frame: pd.DataFrame, include_index: bool = True) -> str:
    """Render a DataFrame as a GitHub-flavoured Markdown table.

    Written by hand rather than via ``DataFrame.to_markdown``, which requires the
    optional ``tabulate`` package. pandas 2.x bundles it and pandas 3.x does not,
    so relying on it makes the export silently version-dependent — and the
    failure surfaces as an exception on the report tab rather than a missing
    table.
    """
    if frame is None or frame.empty:
        return "_No data._"

    display = frame.reset_index() if include_index else frame
    headers = [str(column) for column in display.columns]

    def cell(value: object) -> str:
        if isinstance(value, bool):
            return "yes" if value else "no"
        if isinstance(value, (int, float)):
            return format_number(value)
        text = str(value)
        # Escape pipes so a value cannot break out of its column.
        return text.replace("|", "\\|").replace("\n", " ")

    lines = [
        "| " + " | ".join(header.replace("|", "\\|") for header in headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for _, record in display.iterrows():
        lines.append("| " + " | ".join(cell(value) for value in record) + " |")
    return "\n".join(lines)


def build_markdown(payload: ReportPayload) -> str:
    """Markdown rendering of the same payload."""
    lines = [f"# {payload.title}", "", f"**{payload.entity}**", ""]
    if payload.subtitle:
        lines += [payload.subtitle, ""]
    lines += [f"*Generated {date.today().isoformat()} — private and confidential*", "", "---", ""]

    for block in payload.blocks():
        lines += [f"## {block.title}", ""]
        if block.body:
            lines += [block.body, ""]
        if block.table is not None and not block.table.empty:
            lines += [markdown_table(block.table, block.include_index), ""]
    return "\n".join(lines)


def build_csv_bundle(payload: ReportPayload) -> str:
    """Every table in the payload concatenated as labelled CSV blocks."""
    buffer = io.StringIO()
    for block in payload.blocks():
        if block.table is None or block.table.empty:
            continue
        buffer.write(f"# {block.title}\n")
        frame = block.table.reset_index() if block.include_index else block.table
        frame.to_csv(buffer, index=False)
        buffer.write("\n")
    return buffer.getvalue()


def safe_filename(*parts: str) -> str:
    """Filesystem-safe slug for a download."""
    joined = "_".join(str(part) for part in parts if part)
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", joined).strip("_")
    return slug or "report"
