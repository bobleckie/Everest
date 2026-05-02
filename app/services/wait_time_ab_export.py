"""Excel export for Wait-Time A/B Test runs.

Produces a polished, executive-ready .xlsx with:

  Sheet 1 — Summary
    * Title block with proposal + run name + timestamp
    * KPI cards (OLD total, NEW total, Δ, breaches)
    * Verbatim rule descriptions (OLD vs NEW)
    * Warnings / methodology notes

  Sheet 2 — Monthly Rollup
    * Month-by-month OLD / NEW / Δ totals
    * Bar chart comparing OLD vs NEW per month

  Sheet 3 — Per-Station
    * Sortable table of every station with OLD, NEW, Δ
    * Conditional formatting (red bars for the worst deltas)
    * Bar chart of top-10 stations by Δ

  Sheet 4 — Daily Detail
    * Every breaching day with both LD totals
    * Frozen header row, station/date columns frozen
    * Conditional formatting on the Δ column

  Sheet 5 — Monthly LDs (proxy)
    * Per-station monthly average + LD components
    * Shows which stations crossed each threshold

All currency formatted with $#,##0.00. All numbers right-aligned. No raw
JSON dumps — every cell is human-readable.
"""
from __future__ import annotations

import io
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.formatting.rule import ColorScaleRule, DataBarRule
from openpyxl.styles import (
    Alignment, Border, Font, PatternFill, Side,
)
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo


# ── Brand palette (matches the app's UI tokens) ────────────────────
PARSONS_NAVY = "081931"
PARSONS_BLUE = "00AEE6"
PARSONS_GREEN = "50BF34"
SOFT_GRAY = "F5F7FA"
MID_GRAY = "E0E5EB"
LIGHT_BLUE = "E1F5FE"
WARN_AMBER = "FFF3C4"
RED_BG = "FFE4E0"
GREEN_BG = "E8F5E9"


def _font(**kw) -> Font:
    return Font(name="Calibri", **kw)


def _cell(ws, row: int, col: int, value, *,
           fill: Optional[str] = None, font_color: Optional[str] = None,
           bold: bool = False, italic: bool = False,
           number_format: Optional[str] = None,
           horizontal: str = "left", vertical: str = "center",
           wrap: bool = False, font_size: int = 11,
           border: Optional[Border] = None):
    """Write a styled cell. Tiny helper to keep callers tidy."""
    c = ws.cell(row=row, column=col, value=value)
    if fill:
        c.fill = PatternFill("solid", fgColor=fill)
    c.font = _font(
        size=font_size, bold=bold, italic=italic,
        color=font_color or "1A1A1A",
    )
    c.alignment = Alignment(
        horizontal=horizontal, vertical=vertical, wrap_text=wrap,
    )
    if number_format:
        c.number_format = number_format
    if border:
        c.border = border
    return c


def _thin() -> Border:
    s = Side(style="thin", color=MID_GRAY)
    return Border(left=s, right=s, top=s, bottom=s)


def _money_fmt() -> str:
    return '_-$* #,##0.00_-;[Red]-$* #,##0.00_-;_-$* "-"??_-;_-@_-'


# ─────────────────────────────────────────────────────────────────────
# Sheet builders
# ─────────────────────────────────────────────────────────────────────


def _write_summary(ws, run: Dict[str, Any], breakdown: Dict[str, Any],
                   old_rule: Dict[str, Any], new_rule: Dict[str, Any]) -> None:
    """Sheet 1 — Title + KPI cards + rule descriptions + warnings."""
    ws.title = "Summary"
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 4
    for col in "BCDEFGH":
        ws.column_dimensions[col].width = 18

    # ── Title bar ──
    ws.row_dimensions[2].height = 38
    ws.merge_cells("B2:H2")
    _cell(ws, 2, 2,
          f"Wait-Time LD A/B Test — {run.get('name', '(unnamed)')}",
          fill=PARSONS_NAVY, font_color="FFFFFF", bold=True, font_size=18,
          horizontal="left", vertical="center")

    ws.merge_cells("B3:H3")
    _cell(ws, 3, 2,
          f"Generated {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')} · "
          f"Run id {run.get('id')} · Import id {run.get('import_id')}",
          font_color="606060", italic=True, font_size=10,
          horizontal="left", vertical="center")

    # ── KPI cards ──
    totals = breakdown.get("totals", {})
    old_total = totals.get("old_total_usd", run.get("old_total_usd") or 0.0)
    new_total = totals.get("new_total_usd", run.get("new_total_usd") or 0.0)
    delta = totals.get("delta_usd", run.get("delta_usd") or 0.0)

    cards = [
        ("OLD total", old_total, PARSONS_NAVY, "FFFFFF"),
        ("NEW total", new_total, PARSONS_BLUE, "FFFFFF"),
        ("Δ (NEW − OLD)", delta,
         "C62828" if delta > 0 else PARSONS_GREEN if delta < 0 else "606060",
         "FFFFFF"),
    ]
    for i, (label, value, fill, fc) in enumerate(cards):
        col = 2 + i * 2
        # Card label
        ws.merge_cells(start_row=5, start_column=col, end_row=5, end_column=col + 1)
        _cell(ws, 5, col, label, fill=fill, font_color=fc, bold=True,
              font_size=11, horizontal="center", vertical="center")
        # Card value
        ws.merge_cells(start_row=6, start_column=col, end_row=7, end_column=col + 1)
        _cell(ws, 6, col, value, fill="FFFFFF",
              font_color=fill, bold=True, font_size=22,
              horizontal="center", vertical="center",
              number_format=_money_fmt(), border=_thin())

    # Stations / days counts
    _cell(ws, 9, 2,
          f"{totals.get('stations', 0)} stations · "
          f"{totals.get('days', 0)} station-days · "
          f"{totals.get('breaches_old', 0)} OLD breaches · "
          f"{totals.get('breaches_new', 0)} NEW breaches",
          font_color="606060", italic=True, font_size=10)

    # ── Rule blocks (OLD vs NEW side by side) ──
    row = 12
    ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=4)
    ws.merge_cells(start_row=row, start_column=5, end_row=row, end_column=7)
    _cell(ws, row, 2, "OLD rule", fill=PARSONS_NAVY, font_color="FFFFFF",
          bold=True, font_size=12, horizontal="center")
    _cell(ws, row, 5, "NEW rule", fill=PARSONS_BLUE, font_color="FFFFFF",
          bold=True, font_size=12, horizontal="center")

    def _rule_block(start_col: int, rule: Dict[str, Any]):
        r = row + 1
        ws.merge_cells(start_row=r, start_column=start_col, end_row=r, end_column=start_col + 2)
        _cell(ws, r, start_col, rule.get("name") or "(unnamed)",
              bold=True, font_size=11, horizontal="left",
              fill=SOFT_GRAY, border=_thin())
        r += 1
        rows = [
            ("Mode", rule.get("mode")),
            ("Daily threshold", _fmt_minutes(rule.get("threshold_minutes"))),
            ("Daily $ per unit", rule.get("dollars_per_unit")),
            ("Monthly grace days", rule.get("monthly_grace_days") or 0),
            ("Monthly enabled", "Yes" if rule.get("monthly_enabled") else "No"),
            ("Monthly threshold", _fmt_minutes(rule.get("monthly_threshold_minutes"))),
            ("Monthly $", rule.get("monthly_dollars")),
            ("Sched hrs/day", rule.get("monthly_scheduled_hours_per_day")),
            ("Sched ops days", rule.get("monthly_scheduled_operating_days_override") or "auto"),
        ]
        for label, value in rows:
            _cell(ws, r, start_col, label, font_color="606060",
                  font_size=10, border=_thin())
            ws.merge_cells(start_row=r, start_column=start_col + 1,
                           end_row=r, end_column=start_col + 2)
            is_money = label.endswith("$")
            _cell(ws, r, start_col + 1, value if value is not None else "—",
                  font_size=10,
                  number_format=_money_fmt() if is_money else None,
                  border=_thin(),
                  horizontal="right" if is_money or isinstance(value, (int, float)) else "left")
            r += 1

        if rule.get("description"):
            ws.merge_cells(start_row=r, start_column=start_col,
                           end_row=r + 2, end_column=start_col + 2)
            _cell(ws, r, start_col, rule.get("description"),
                  font_size=9, font_color="404040", italic=True,
                  wrap=True, vertical="top", horizontal="left",
                  fill=SOFT_GRAY, border=_thin())

    _rule_block(2, old_rule)
    _rule_block(5, new_rule)

    # ── Warnings / methodology notes ──
    warnings = run.get("warnings") if isinstance(run.get("warnings"), list) else None
    if warnings:
        wr = 26
        ws.merge_cells(start_row=wr, start_column=2, end_row=wr, end_column=8)
        _cell(ws, wr, 2, "Methodology notes", bold=True, font_size=11,
              fill=WARN_AMBER, horizontal="left")
        for i, w in enumerate(warnings, start=1):
            ws.merge_cells(start_row=wr + i, start_column=2,
                           end_row=wr + i, end_column=8)
            _cell(ws, wr + i, 2, "• " + w, font_size=10,
                  font_color="6D5800", wrap=True, vertical="top",
                  horizontal="left")
            ws.row_dimensions[wr + i].height = 36


def _fmt_minutes(v) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.1f} min"
    except (ValueError, TypeError):
        return str(v)


def _write_monthly(ws, breakdown: Dict[str, Any]) -> None:
    """Sheet 2 — Monthly rollup with bar chart."""
    ws.title = "Monthly"
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 14
    for col in "BCDEF":
        ws.column_dimensions[col].width = 18

    _cell(ws, 1, 1, "Monthly LD Rollup", bold=True, font_size=14,
          horizontal="left")
    _cell(ws, 2, 1,
          "Total LDs incurred per calendar month under each rule. Δ = NEW − OLD; "
          "positive = new rule is more expensive.",
          font_color="606060", italic=True, font_size=10)
    ws.merge_cells("A2:F2")

    headers = ["Month", "OLD total", "NEW total", "Δ (NEW − OLD)",
               "Breaches OLD", "Breaches NEW"]
    for c, h in enumerate(headers, start=1):
        _cell(ws, 4, c, h, fill=PARSONS_NAVY, font_color="FFFFFF",
              bold=True, horizontal="center", border=_thin())

    rows = breakdown.get("monthly", [])
    for i, m in enumerate(rows, start=5):
        _cell(ws, i, 1, m.get("month"), border=_thin(),
              horizontal="center")
        _cell(ws, i, 2, m.get("old_total_usd"), border=_thin(),
              number_format=_money_fmt(), horizontal="right")
        _cell(ws, i, 3, m.get("new_total_usd"), border=_thin(),
              number_format=_money_fmt(), horizontal="right")
        delta = m.get("delta_usd") or 0
        _cell(ws, i, 4, delta, border=_thin(),
              number_format=_money_fmt(),
              horizontal="right", bold=True,
              font_color="C62828" if delta > 0 else "2E7D32" if delta < 0 else "1A1A1A")
        _cell(ws, i, 5, m.get("breaches_old"), border=_thin(),
              horizontal="right")
        _cell(ws, i, 6, m.get("breaches_new"), border=_thin(),
              horizontal="right")

    last_row = 4 + len(rows)
    if rows:
        # Conditional color scale on the delta column
        ws.conditional_formatting.add(
            f"D5:D{last_row}",
            ColorScaleRule(
                start_type="min", start_color=GREEN_BG,
                mid_type="num", mid_value=0, mid_color="FFFFFF",
                end_type="max", end_color=RED_BG,
            ),
        )

        # Bar chart: OLD vs NEW per month
        chart = BarChart()
        chart.type = "col"
        chart.style = 12
        chart.grouping = "clustered"
        chart.title = "OLD vs NEW LDs by month"
        chart.y_axis.title = "USD"
        chart.x_axis.title = "Month"
        chart.height = 9
        chart.width = 16

        data = Reference(ws, min_col=2, min_row=4, max_col=3, max_row=last_row)
        cats = Reference(ws, min_col=1, min_row=5, max_row=last_row)
        chart.add_data(data, titles_from_data=True)
        chart.set_categories(cats)
        ws.add_chart(chart, "A" + str(last_row + 3))


def _write_per_station(ws, breakdown: Dict[str, Any]) -> None:
    """Sheet 3 — Per-station table with data bars on the delta column."""
    ws.title = "Per-Station"
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 14
    ws.column_dimensions["B"].width = 28
    for col in "CDEFGH":
        ws.column_dimensions[col].width = 16

    _cell(ws, 1, 1, "Per-Station LD Totals", bold=True, font_size=14,
          horizontal="left")
    ws.merge_cells("A2:H2")
    _cell(ws, 2, 1,
          "Sorted by Δ (worst delta first). Data bars in the Δ column visualize "
          "which stations contribute the most to the new-rule cost increase.",
          font_color="606060", italic=True, font_size=10)

    headers = ["Station ID", "Station name", "Days", "OLD total",
               "NEW total", "Δ (NEW − OLD)", "OLD breaches", "NEW breaches"]
    for c, h in enumerate(headers, start=1):
        _cell(ws, 4, c, h, fill=PARSONS_NAVY, font_color="FFFFFF",
              bold=True, horizontal="center", border=_thin())

    rows = breakdown.get("per_station", [])
    for i, s in enumerate(rows, start=5):
        _cell(ws, i, 1, s.get("station_id"), border=_thin(),
              font_size=10, horizontal="left", bold=True)
        _cell(ws, i, 2, s.get("station_name") or "", border=_thin(),
              font_size=10, horizontal="left")
        _cell(ws, i, 3, s.get("days"), border=_thin(),
              horizontal="center")
        _cell(ws, i, 4, s.get("old_total"), border=_thin(),
              number_format=_money_fmt(), horizontal="right")
        _cell(ws, i, 5, s.get("new_total"), border=_thin(),
              number_format=_money_fmt(), horizontal="right")
        delta = s.get("delta_total") or 0
        _cell(ws, i, 6, delta, border=_thin(),
              number_format=_money_fmt(), horizontal="right", bold=True,
              font_color="C62828" if delta > 0 else "2E7D32" if delta < 0 else "1A1A1A")
        _cell(ws, i, 7, s.get("old_breaches"), border=_thin(),
              horizontal="right")
        _cell(ws, i, 8, s.get("new_breaches"), border=_thin(),
              horizontal="right")

    last_row = 4 + len(rows)
    if rows:
        # Data bars on the Δ column
        ws.conditional_formatting.add(
            f"F5:F{last_row}",
            DataBarRule(
                start_type="min", end_type="max",
                color="C62828", showValue=True,
            ),
        )
        # Color scale on OLD and NEW columns
        for col in ("D", "E"):
            ws.conditional_formatting.add(
                f"{col}5:{col}{last_row}",
                ColorScaleRule(
                    start_type="min", start_color="FFFFFF",
                    end_type="max", end_color=LIGHT_BLUE,
                ),
            )

    ws.freeze_panes = "C5"


def _write_daily(ws, breakdown: Dict[str, Any]) -> None:
    """Sheet 4 — Daily detail. Every breaching day."""
    ws.title = "Daily"
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 14
    ws.column_dimensions["B"].width = 28
    ws.column_dimensions["C"].width = 12
    for col in "DEF":
        ws.column_dimensions[col].width = 14
    ws.column_dimensions["G"].width = 32
    ws.column_dimensions["H"].width = 32

    _cell(ws, 1, 1, "Daily Breakdown", bold=True, font_size=14,
          horizontal="left")
    ws.merge_cells("A2:H2")
    _cell(ws, 2, 1,
          "Every station-day where either rule produced a daily LD. "
          "Sorted by station then date.",
          font_color="606060", italic=True, font_size=10)

    headers = ["Station ID", "Station name", "Date",
               "OLD daily LD", "NEW daily LD", "Δ",
               "OLD breach hours", "NEW breach hours"]
    for c, h in enumerate(headers, start=1):
        _cell(ws, 4, c, h, fill=PARSONS_NAVY, font_color="FFFFFF",
              bold=True, horizontal="center", border=_thin())

    rows = breakdown.get("daily", [])
    # Only include rows with at least one nonzero penalty to keep the
    # sheet small and useful for review.
    rows = [r for r in rows
            if (r.get("old_penalty_usd") or 0) > 0
            or (r.get("new_penalty_usd") or 0) > 0]

    for i, d in enumerate(rows, start=5):
        _cell(ws, i, 1, d.get("station_id"), border=_thin(),
              font_size=10, bold=True)
        _cell(ws, i, 2, d.get("station_name") or "", border=_thin(),
              font_size=10)
        _cell(ws, i, 3, d.get("test_date"), border=_thin(),
              horizontal="center", font_size=10)
        _cell(ws, i, 4, d.get("old_penalty_usd") or 0, border=_thin(),
              number_format=_money_fmt(), horizontal="right",
              font_size=10)
        _cell(ws, i, 5, d.get("new_penalty_usd") or 0, border=_thin(),
              number_format=_money_fmt(), horizontal="right",
              font_size=10)
        delta = d.get("delta_usd") or 0
        _cell(ws, i, 6, delta, border=_thin(),
              number_format=_money_fmt(), horizontal="right",
              bold=True, font_size=10,
              font_color="C62828" if delta > 0 else "2E7D32" if delta < 0 else "1A1A1A")
        _cell(ws, i, 7,
              ", ".join(d.get("old_breach_hours") or []) or "—",
              border=_thin(), font_size=10, horizontal="left")
        _cell(ws, i, 8,
              ", ".join(d.get("new_breach_hours") or []) or "—",
              border=_thin(), font_size=10, horizontal="left")

    last_row = 4 + len(rows)
    if rows:
        # Color scale on Δ column
        ws.conditional_formatting.add(
            f"F5:F{last_row}",
            ColorScaleRule(
                start_type="min", start_color=GREEN_BG,
                mid_type="num", mid_value=0, mid_color="FFFFFF",
                end_type="max", end_color=RED_BG,
            ),
        )

    ws.freeze_panes = "D5"


def _write_monthly_proxy(ws, breakdown: Dict[str, Any]) -> None:
    """Sheet 5 — Monthly LD breakdown (the per-station-month figures)."""
    ws.title = "Monthly LDs"
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 14
    ws.column_dimensions["B"].width = 12
    for col in "CDEFGHIJ":
        ws.column_dimensions[col].width = 16

    _cell(ws, 1, 1, "Monthly LD Breakdown", bold=True, font_size=14)
    ws.merge_cells("A2:J2")
    _cell(ws, 2, 1,
          "Per-station-per-month average wait times and the resulting LDs. "
          "Avg = Σ(open-hour waits) / (scheduled_hours × scheduled_operating_days). "
          "Default: 9 hrs/day, weekdays+1.",
          font_color="606060", italic=True, font_size=10, wrap=True)
    ws.row_dimensions[2].height = 32

    headers = ["Station", "Month", "Avg (OLD rule)", "Avg (NEW rule)",
               "Sched ops days", "Open-hour count", "Sum minutes",
               "OLD LD", "NEW LD"]
    for c, h in enumerate(headers, start=1):
        _cell(ws, 4, c, h, fill=PARSONS_NAVY, font_color="FFFFFF",
              bold=True, horizontal="center", border=_thin())

    rows = breakdown.get("monthly_proxy", [])
    for i, m in enumerate(rows, start=5):
        _cell(ws, i, 1, m.get("station_id"), border=_thin(),
              bold=True, font_size=10)
        _cell(ws, i, 2, m.get("month"), border=_thin(),
              horizontal="center", font_size=10)
        avg_old = m.get("avg_old_minutes") or m.get("avg_minutes") or 0
        avg_new = m.get("avg_new_minutes") or m.get("avg_minutes") or 0
        _cell(ws, i, 3, avg_old, border=_thin(),
              number_format='0.00" min"', horizontal="right",
              bold=avg_old > 15,
              font_color="C62828" if avg_old > 15 else "1A1A1A")
        _cell(ws, i, 4, avg_new, border=_thin(),
              number_format='0.00" min"', horizontal="right",
              bold=avg_new > 15,
              font_color="C62828" if avg_new > 15 else "1A1A1A")
        _cell(ws, i, 5, m.get("scheduled_operating_days"),
              border=_thin(), horizontal="center")
        _cell(ws, i, 6, m.get("open_hour_count"),
              border=_thin(), horizontal="center")
        _cell(ws, i, 7, m.get("sum_minutes"),
              border=_thin(), horizontal="right",
              number_format='#,##0.00')
        _cell(ws, i, 8, m.get("old_charge_usd") or 0, border=_thin(),
              number_format=_money_fmt(), horizontal="right",
              bold=(m.get("old_charge_usd") or 0) > 0)
        _cell(ws, i, 9, m.get("new_charge_usd") or 0, border=_thin(),
              number_format=_money_fmt(), horizontal="right",
              bold=(m.get("new_charge_usd") or 0) > 0)

    last_row = 4 + len(rows)
    if rows:
        # Color scale on the Avg (OLD rule) column to make threshold-crossers obvious
        ws.conditional_formatting.add(
            f"C5:C{last_row}",
            ColorScaleRule(
                start_type="num", start_value=10, start_color="E8F5E9",
                mid_type="num", mid_value=15, mid_color="FFF8E1",
                end_type="num", end_value=25, end_color="FFCDD2",
            ),
        )
        ws.conditional_formatting.add(
            f"D5:D{last_row}",
            ColorScaleRule(
                start_type="num", start_value=10, start_color="E8F5E9",
                mid_type="num", mid_value=15, mid_color="FFF8E1",
                end_type="num", end_value=25, end_color="FFCDD2",
            ),
        )

    ws.freeze_panes = "C5"


# ─────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────


def export_run_to_xlsx(run: Dict[str, Any], old_rule: Dict[str, Any],
                        new_rule: Dict[str, Any]) -> bytes:
    """Build a polished .xlsx workbook for one A/B-test run.

    Args:
        run:       the dict returned by GET /runs/{id} (must include
                   `breakdown` with totals/monthly/per_station/daily/
                   monthly_proxy).
        old_rule:  full rule dict from rule_to_dict(old_rule_orm)
        new_rule:  full rule dict from rule_to_dict(new_rule_orm)

    Returns:
        bytes of the xlsx workbook ready to stream to the client.
    """
    breakdown = run.get("breakdown") or {}

    wb = Workbook()
    # First sheet
    ws = wb.active
    _write_summary(ws, run, breakdown, old_rule, new_rule)

    # Other sheets
    _write_monthly(wb.create_sheet(), breakdown)
    _write_per_station(wb.create_sheet(), breakdown)
    _write_daily(wb.create_sheet(), breakdown)
    _write_monthly_proxy(wb.create_sheet(), breakdown)

    # Tab colors
    wb["Summary"].sheet_properties.tabColor = PARSONS_NAVY
    wb["Monthly"].sheet_properties.tabColor = PARSONS_BLUE
    wb["Per-Station"].sheet_properties.tabColor = PARSONS_BLUE
    wb["Daily"].sheet_properties.tabColor = PARSONS_GREEN
    wb["Monthly LDs"].sheet_properties.tabColor = "FFA000"

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()
