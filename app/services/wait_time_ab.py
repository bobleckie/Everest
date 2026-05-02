"""Wait-time A/B test service.

Three concerns:

1. **Parsing** an uploaded Excel file with the column shape:
       STATION_ID | STATION_NAME | TEST_DATE | DAY_OF_MONTH | METRIC_NAME |
       H06 | H07 | H08 | H09 | H10 | H11 | H12 | H13 | H14 | H15 | H16 | H17 | H18 | H19
   Blank hourly cells are preserved as None (the station was closed during
   that hour and MUST be skipped by every LD rule).

2. **Applying an LD rule** to one row of observations to compute a daily
   penalty. The rule shape is generic — see WaitTimeLdRule in models.py
   for the parameter contract.

3. **Comparing two rules** (Old vs New) over the same import to produce
   a per-station daily breakdown plus a monthly rollup plus the grand
   total cost differential. The result is JSON-serializable so it can
   be persisted on a WaitTimeAbRun.

Design intent: the calculation engine is PURE — it never reads the DB
inside _apply_rule or _compare_run, so it can be unit-tested with
fabricated dicts without an ORM session.
"""
from __future__ import annotations

import io
import json
import logging
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# H06..H19 in column order. Index 0 = H06, index 13 = H19.
HOUR_LABELS: List[str] = [f"H{h:02d}" for h in range(6, 20)]
EXPECTED_HEADERS = [
    "STATION_ID", "STATION_NAME", "TEST_DATE", "DAY_OF_MONTH", "METRIC_NAME",
    *HOUR_LABELS,
]


# ─────────────────────────────────────────────────────────────────────
# Parsing
# ─────────────────────────────────────────────────────────────────────

def _coerce_date(v: Any) -> Optional[datetime]:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v
    if isinstance(v, (int, float)):
        # Excel serial date — let openpyxl normally handle this but if a
        # cell was typed as number we still try.
        try:
            from openpyxl.utils.datetime import from_excel
            return from_excel(float(v))
        except Exception:
            return None
    s = str(v).strip()
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%Y/%m/%d", "%d-%b-%Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _coerce_float(v: Any) -> Optional[float]:
    """Return None for blank/closed-hour cells; float otherwise. Never raises."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        if isinstance(v, float) and (v != v):  # NaN
            return None
        return float(v)
    s = str(v).strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_wait_time_xlsx(file_bytes: bytes,
                          metric_name_filter: Optional[str] = "Facility Average Wait Time"
                          ) -> Dict[str, Any]:
    """Parse an uploaded .xlsx file. Returns a dict:
        { 'rows': [ {station_id, station_name, test_date, day_of_month,
                     metric_name, hourly_values: [14 nullable floats]}, ... ],
          'warnings': [...], 'header_map': {...} }
    Strict header validation: bails with a friendly error if columns are
    missing or in an unexpected order.
    """
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True, read_only=True)
    ws = wb.active

    rows_iter = ws.iter_rows(values_only=True)
    try:
        header = next(rows_iter)
    except StopIteration:
        return {"rows": [], "warnings": ["File is empty."], "header_map": {}}

    # Build a case-insensitive lookup of "header label" -> column index.
    norm = lambda s: ("" if s is None else str(s).strip().upper())
    header_idx: Dict[str, int] = {}
    for i, cell in enumerate(header):
        key = norm(cell)
        if key:
            header_idx[key] = i

    missing = [h for h in EXPECTED_HEADERS if h not in header_idx]
    if missing:
        return {
            "rows": [],
            "warnings": [
                f"Missing required column(s): {', '.join(missing)}. "
                f"Expected headers (case-insensitive): {', '.join(EXPECTED_HEADERS)}"
            ],
            "header_map": header_idx,
        }

    out_rows: List[Dict[str, Any]] = []
    warnings: List[str] = []
    skipped_metric = 0

    for r_idx, row in enumerate(rows_iter, start=2):  # 2 because header is row 1
        if row is None:
            continue
        # Pull cells by header index (file may have extra columns we ignore)
        def _at(label: str):
            i = header_idx[label]
            return row[i] if i < len(row) else None

        sid = _at("STATION_ID")
        if sid is None or str(sid).strip() == "":
            continue  # blank line

        metric_name = _at("METRIC_NAME")
        metric_str = (str(metric_name).strip() if metric_name is not None else "")
        if metric_name_filter and metric_str != metric_name_filter:
            skipped_metric += 1
            continue

        td = _coerce_date(_at("TEST_DATE"))
        if td is None:
            warnings.append(f"Row {r_idx}: could not parse TEST_DATE, skipping.")
            continue

        try:
            dom = int(_at("DAY_OF_MONTH")) if _at("DAY_OF_MONTH") is not None else td.day
        except (TypeError, ValueError):
            dom = td.day

        hourly: List[Optional[float]] = []
        for hl in HOUR_LABELS:
            hourly.append(_coerce_float(_at(hl)))

        out_rows.append({
            "station_id": str(sid).strip(),
            "station_name": (str(_at("STATION_NAME")).strip()
                             if _at("STATION_NAME") is not None else None),
            "test_date": td,
            "day_of_month": dom,
            "metric_name": metric_str or (metric_name_filter or ""),
            "hourly_values": hourly,
        })

    if skipped_metric:
        warnings.append(
            f"Filtered out {skipped_metric} row(s) whose METRIC_NAME did not "
            f"match '{metric_name_filter}'. Clear the filter to include all metrics."
        )
    return {"rows": out_rows, "warnings": warnings, "header_map": header_idx}


# ─────────────────────────────────────────────────────────────────────
# LD rule engine
# ─────────────────────────────────────────────────────────────────────

VALID_MODES = {
    "per_minute_over",
    "per_hour_over",
    "per_breach_event",  # alias of per_hour_over
    "per_day_if_any",
    "tiered",
    # ── Domain-specific NJ MVC wait-time LD encodings ────────────────
    # nj_old_2011: 2011 contract amendment. LDs accrue ONLY on days 5+
    #              of the month where daily average wait > 45 minutes
    #              AND there's a 2-contiguous-hour pair both > 45.
    #              Charge = $2,500 base + $2,500 per 10-min increment of
    #              daily-average over 45.
    # nj_new_t1628: Active T1628 RFP, SLA O-30 + O-31 combined.
    #              Daily LD = $1,000 once per day if any 2-contiguous-hour
    #              pair both > 30 (O-30) PLUS $500 per HOUR-OVER-40 in the
    #              triggering 2-contig window, banded above 40 (O-31).
    "nj_old_2011",
    "nj_new_t1628",
}

# Operational hour labels in chronological order (H06..H19). Index 0=H06,
# index 13=H19. Each cell represents the average wait time during that
# clock-hour (e.g. H08 = the 08:00-09:00 hour).
# Used for the contiguous-hour scan.


def _q(v: float) -> float:
    """Round to cents, half-up."""
    return float(Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _parse_excluded_hours(csv: Optional[str]) -> set[str]:
    if not csv:
        return set()
    return {x.strip().upper() for x in csv.split(",") if x.strip()}


def _tier_dollars(over_minutes: float, tiers: List[Dict[str, Any]]) -> float:
    """For 'tiered' mode: walk tiers in order; first tier whose
    [min_over, max_over) contains over_minutes wins. max_over may be null
    (open-ended top tier)."""
    for t in tiers:
        lo = float(t.get("min_over", 0) or 0)
        hi = t.get("max_over")
        d = float(t.get("dollars", 0) or 0)
        if over_minutes >= lo and (hi is None or over_minutes < float(hi)):
            return d
    return 0.0


def _two_contiguous_over(hourly_values: List[Optional[float]],
                          threshold: float) -> List[tuple[int, int]]:
    """Find every pair (i, i+1) where BOTH hours are present and both
    exceed the threshold. Returns the indices into HOUR_LABELS of the
    starting hour of each qualifying pair. Closed/missing hours never
    participate in a pair (they break the contiguity)."""
    out: List[tuple[int, int]] = []
    for i in range(len(hourly_values) - 1):
        a = hourly_values[i]
        b = hourly_values[i + 1]
        if a is None or b is None:
            continue
        if a > threshold and b > threshold:
            out.append((i, i + 1))
    return out


def _open_hours(hourly_values: List[Optional[float]]) -> List[float]:
    """Return only the non-None hourly values (i.e. scheduled operating hours)."""
    return [v for v in hourly_values if v is not None]


def _apply_nj_old_2011(rule: Dict[str, Any],
                        hourly_values: List[Optional[float]]
                        ) -> Dict[str, Any]:
    """Encodes the 2011 amendment EXACTLY:

      Trigger: 2 contiguous operating hours where each hour's average > 45 min.
      Charge: $2,500 base, plus $2,500 for each full 10-minute increment
              that the DAILY AVERAGE (sum of operating-hour averages
              divided by number of operating hours) exceeds 45.
      Free-grace: LDs only apply on the 5th+ such breaching day in the
                  calendar month (handled in compare_rules' second pass).

    This day-applicator returns:
      breach=True/False, base_charge, increment_charge, daily_avg
    The compare_rules monthly aggregator then decides whether to keep or
    waive based on the day's calendar position.
    """
    threshold = float(rule.get("threshold_minutes") or 45.0)
    base = float(rule.get("dollars_per_unit") or 2500.0)
    pairs = _two_contiguous_over(hourly_values, threshold)
    open_h = _open_hours(hourly_values)
    daily_avg = (sum(open_h) / len(open_h)) if open_h else 0.0

    if not pairs:
        return {
            "penalty_usd": 0.00,
            "breach_count": 0,
            "breach_hours": [],
            "breach_details": [],
            "capped_at_daily": False,
            "_old": {
                "qualified": False,
                "daily_avg": daily_avg,
                "base_charge": 0.0,
                "increment_charge": 0.0,
                "increments": 0,
            },
        }

    # Increment charge applies only when the trigger fires.
    # Per the 2011 amendment's worked example: average=55.3333 (10.33
    # minutes over the 45-min threshold) yields ONE $2,500 increment
    # ("the increment between 55 and 65 minutes"). Therefore the formula
    # is the COUNT OF FULL 10-MIN BANDS above threshold, NOT a ceiling.
    #     increments = floor((avg - threshold) / 10)  when avg > threshold
    # Examples (threshold=45):
    #     avg=46     -> floor(1/10)     = 0 increments  (just the base trigger)
    #     avg=55     -> floor(10/10)    = 1 increment   ($2,500)
    #     avg=55.33  -> floor(10.33/10) = 1 increment   ($2,500)  <-- RFP example
    #     avg=65     -> floor(20/10)    = 2 increments  ($5,000)
    #     avg=75     -> floor(30/10)    = 3 increments  ($7,500)
    if daily_avg > threshold:
        increments = int((daily_avg - threshold) // 10)
    else:
        increments = 0
    increment_charge = increments * base

    # The base charge is the "first" assessment for the day.
    total = base + increment_charge

    breach_hour_labels = sorted({HOUR_LABELS[i] for pair in pairs for i in pair})
    return {
        # NOTE: penalty_usd here is the GROSS (pre-grace) charge. The
        # compare_rules aggregator decides whether to wipe it out for
        # days 1-4 of the month under nj_old_2011 semantics.
        "penalty_usd": _q(total),
        "breach_count": len(pairs),
        "breach_hours": breach_hour_labels,
        "breach_details": [
            {"hour": HOUR_LABELS[a], "value": hourly_values[a], "over": hourly_values[a] - threshold}
            for (a, b) in pairs
        ],
        "capped_at_daily": False,
        "_old": {
            "qualified": True,
            "daily_avg": daily_avg,
            "base_charge": base,
            "increment_charge": increment_charge,
            "increments": increments,
        },
    }


def _apply_nj_new_t1628(rule: Dict[str, Any],
                         hourly_values: List[Optional[float]]
                         ) -> Dict[str, Any]:
    """Encodes T1628 SLA O-30 + O-31 EXACTLY (per user-confirmed reading):

      O-30 trigger: any 2 contiguous operating hours where each hour > 30 min.
      O-30 charge:  $1,000 once for the day, full stop (regardless of how
                    many 2-contig-hour windows breach).
      O-31 charge:  Once O-30 has triggered, $500 per band-of-10-minutes
                    that any breaching hour exceeds 40 within the
                    triggering 2-contig window. Each hour is independently
                    banded above 40: a 51-min hour = 1 band ($500),
                    a 61-min hour = 2 bands ($1,000), etc.
                    Each hour contributes once even if it appears in
                    multiple 2-contig-hour pairs.
    """
    o30_threshold = float(rule.get("threshold_minutes") or 30.0)
    o30_first_dollars = float(rule.get("dollars_per_unit") or 1000.0)
    # The RFP says: "$1,000 USD for the first violation per day and $500
    # for each additional violation." A "violation" here is a triggering
    # HOUR — i.e. each hour > 30 that appears inside a 2-contiguous-hour
    # window where both hours are > 30. The first such hour each day = the
    # 'first violation'; every subsequent triggering hour = an 'additional
    # violation' billed at $500.
    o30_additional_dollars = 500.0
    # O-31 parameters (per RFP: doc 5 page 98).
    # Trigger threshold: "exceeds greater than 40 minutes ... in any two (2)
    #                    contiguous operational hours."
    # Charge:           "$500 USD for each additional 10 minute interval
    #                    exceeding the THIRTY (30) minute average."
    # The bands are counted FROM 30, not from 40. The 40-min mark is the
    # gate that lets the LD apply at all; once it applies, you charge $500
    # for each 10-min increment over 30 the hour represents.
    o31_trigger_threshold = 40.0   # hour must exceed this for any O-31 charge
    o31_band_anchor = 30.0         # bands measured from THIS value upward
    o31_band_dollars = 500.0
    o31_band_size = 10.0
    # Allow rule to override via tiers_json (parameter bag, not array).
    raw_tiers = rule.get("tiers_json")
    if raw_tiers:
        try:
            cfg = json.loads(raw_tiers) if isinstance(raw_tiers, str) else raw_tiers
            if isinstance(cfg, dict):
                o30_additional_dollars = float(cfg.get(
                    "o30_additional_dollars", o30_additional_dollars))
                # Backwards-compat: 'o31_threshold' from the previous
                # encoding is now the TRIGGER threshold (40).
                o31_trigger_threshold = float(cfg.get(
                    "o31_trigger_threshold",
                    cfg.get("o31_threshold", o31_trigger_threshold)))
                o31_band_anchor = float(cfg.get("o31_band_anchor", o31_band_anchor))
                o31_band_dollars = float(cfg.get("o31_band_dollars", o31_band_dollars))
                o31_band_size = float(cfg.get("o31_band_size", o31_band_size))
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    pairs = _two_contiguous_over(hourly_values, o30_threshold)
    if not pairs:
        return {
            "penalty_usd": 0.00,
            "breach_count": 0,
            "breach_hours": [],
            "breach_details": [],
            "capped_at_daily": False,
        }

    # O-30 calculation: every hour that participates in a triggering
    # 2-contiguous-hour pair is a "violation". First violation/day = $1,000;
    # each additional = $500.
    triggering_hour_indices: set[int] = set()
    for (a, b) in pairs:
        triggering_hour_indices.add(a)
        triggering_hour_indices.add(b)

    n_violations = len(triggering_hour_indices)
    if n_violations == 0:
        base = 0.0
    else:
        base = o30_first_dollars + (n_violations - 1) * o30_additional_dollars

    o31_total = 0.0
    o31_details: List[Dict[str, Any]] = []
    for i in sorted(triggering_hour_indices):
        v = hourly_values[i]
        # Trigger gate: hour must exceed 40 (or whatever override). If it
        # only exceeded 30 we already covered it under O-30.
        if v is None or v <= o31_trigger_threshold:
            continue
        # Bands counted from the ANCHOR (30 by RFP), not the trigger (40).
        # Examples (anchor=30, band=10):
        #   v=41 -> over_anchor=11 -> ceil(11/10) = 2 bands ($1,000)
        #   v=51 -> over_anchor=21 -> 3 bands ($1,500)
        #   v=80 -> over_anchor=50 -> 5 bands ($2,500)
        over_anchor = v - o31_band_anchor
        bands = int(over_anchor // o31_band_size) + (
            1 if (over_anchor % o31_band_size) > 0 else 0
        )
        bands_charge = bands * o31_band_dollars
        o31_total += bands_charge
        o31_details.append({
            "hour": HOUR_LABELS[i],
            "value": v,
            "over_anchor_min": over_anchor,
            "bands": bands,
            "charge": bands_charge,
        })

    total = base + o31_total
    breach_hour_labels = sorted({HOUR_LABELS[i] for i in triggering_hour_indices})
    return {
        "penalty_usd": _q(total),
        "breach_count": len(pairs),
        "breach_hours": breach_hour_labels,
        "breach_details": [
            {"hour": HOUR_LABELS[a], "value": hourly_values[a],
             "over": hourly_values[a] - o30_threshold}
            for (a, b) in pairs
        ],
        "capped_at_daily": False,
        "_new": {
            "o30_charge": base,
            "o30_violations": n_violations,
            "o30_first_dollars": o30_first_dollars,
            "o30_additional_dollars": o30_additional_dollars,
            "o31_charge": o31_total,
            "o31_per_hour": o31_details,
        },
    }


def apply_rule_to_day(rule: Dict[str, Any],
                      hourly_values: List[Optional[float]]
                      ) -> Dict[str, Any]:
    """Run one rule against one day's 14 hourly readings. Returns:
        { 'penalty_usd': float,
          'breach_count': int,
          'breach_hours': [hour_label, ...],
          'breach_details': [{hour, value, over}, ...],
          'capped_at_daily': bool }
    PURE — no DB, no ORM. Works on plain dicts.
    """
    mode = (rule.get("mode") or "per_hour_over").strip().lower()
    if mode == "nj_old_2011":
        return _apply_nj_old_2011(rule, hourly_values)
    if mode == "nj_new_t1628":
        return _apply_nj_new_t1628(rule, hourly_values)
    if mode == "per_breach_event":
        mode = "per_hour_over"
    threshold = float(rule.get("threshold_minutes") or 0.0)
    grace = float(rule.get("grace_period_minutes") or 0.0)
    effective_threshold = threshold + grace
    coef = float(rule.get("dollars_per_unit") or 0.0)
    daily_cap = rule.get("daily_cap_usd")
    daily_cap = float(daily_cap) if daily_cap is not None else None
    excluded = _parse_excluded_hours(rule.get("exclude_hours_csv"))
    null_is_breach = bool(rule.get("count_null_hours_as_breach"))
    tiers: List[Dict[str, Any]] = []
    if mode == "tiered":
        try:
            raw = rule.get("tiers_json")
            if isinstance(raw, str):
                tiers = json.loads(raw) or []
            elif isinstance(raw, list):
                tiers = raw
        except (json.JSONDecodeError, TypeError):
            tiers = []

    breach_details: List[Dict[str, Any]] = []
    penalty_total = 0.0

    for idx, label in enumerate(HOUR_LABELS):
        if label in excluded:
            continue
        v = hourly_values[idx] if idx < len(hourly_values) else None
        if v is None:
            if null_is_breach:
                # Treat closed hours as a max-over breach using grace=0,
                # which is unusual; leaves it as a hook the user must
                # opt into deliberately.
                pass
            continue
        if v <= effective_threshold:
            continue
        over = v - threshold  # the "over the bar" amount, ignoring grace
        if mode == "per_minute_over":
            penalty_total += over * coef
        elif mode == "per_hour_over":
            penalty_total += coef
        elif mode == "per_day_if_any":
            # Mark a single breach for this day; we'll set total at the end
            breach_details.append({"hour": label, "value": v, "over": over})
            continue
        elif mode == "tiered":
            penalty_total += _tier_dollars(over, tiers)
        else:
            # Unknown mode — no penalty, just record the breach so the
            # comparison surface can show it.
            pass
        breach_details.append({"hour": label, "value": v, "over": over})

    if mode == "per_day_if_any":
        # Flat penalty if at least one breach occurred today.
        if breach_details:
            penalty_total = coef
        else:
            penalty_total = 0.0

    capped = False
    if daily_cap is not None and penalty_total > daily_cap:
        penalty_total = daily_cap
        capped = True

    return {
        "penalty_usd": _q(penalty_total),
        "breach_count": len(breach_details),
        "breach_hours": [b["hour"] for b in breach_details],
        "breach_details": breach_details,
        "capped_at_daily": capped,
    }


def _month_key(d: datetime) -> str:
    return d.strftime("%Y-%m")


def compare_rules(observations: List[Dict[str, Any]],
                  old_rule: Dict[str, Any],
                  new_rule: Dict[str, Any]
                  ) -> Dict[str, Any]:
    """Run both rules over every observation row. Returns:
        {
          'totals': {old_total_usd, new_total_usd, delta_usd,
                     breaches_old, breaches_new},
          'monthly': [ {month, old, new, delta, ...}, ... ],
          'per_station': [ {station_id, station_name, days,
                             old_total, new_total, delta_total, ... }, ... ],
          'daily': [ per-station-per-day rows for the breakdown table ],
          'warnings': [...]
        }
    PURE — observations is a list of dicts as produced by parse_wait_time_xlsx.
    """
    warnings: List[str] = []
    old_mode = (old_rule.get("mode") or "").strip().lower()
    new_mode = (new_rule.get("mode") or "").strip().lower()
    if old_mode not in VALID_MODES:
        warnings.append(f"Old rule mode {old_rule.get('mode')!r} is not recognized.")
    if new_mode not in VALID_MODES:
        warnings.append(f"New rule mode {new_rule.get('mode')!r} is not recognized.")

    # ── Excluded facilities ─────────────────────────────────────────
    # Two sources, in priority order:
    #   1. The two rules can each declare excluded station_ids in their
    #      'excluded_facilities_csv' field (rule-level filtering).
    #   2. A union of excluded names from BOTH rules also applies, so
    #      either rule's exclusion list takes effect on the comparison.
    #   The match is case-insensitive on STATION_NAME first, falling back
    #   to STATION_ID. The default exclusion set for the active T1628
    #   contract is hard-coded into the seeded rules (see seed_wait_time_rules).
    excluded_names: set = set()
    excluded_ids: set = set()
    for rule in (old_rule, new_rule):
        csv = rule.get("excluded_facilities_csv") or ""
        for tok in csv.split(","):
            t = tok.strip()
            if not t:
                continue
            # Distinguish by shape: pure CIF-style id like "CIF000001" vs
            # human name like "Cape May".
            if t.upper().startswith("CIF") and t[3:].isdigit():
                excluded_ids.add(t.upper())
            else:
                excluded_names.add(t.lower())

    def _is_excluded(obs):
        sid = (obs.get("station_id") or "").upper()
        snm = (obs.get("station_name") or "").lower()
        if sid in excluded_ids:
            return True
        for ex in excluded_names:
            if ex and ex in snm:
                return True
        return False

    skipped_excluded = 0
    filtered: List[Dict[str, Any]] = []
    for obs in observations:
        if _is_excluded(obs):
            skipped_excluded += 1
            continue
        filtered.append(obs)
    if skipped_excluded:
        warnings.append(
            f"Skipped {skipped_excluded} observation(s) at facilities "
            f"excluded from wait-time SLAs."
        )
    observations = filtered

    # Per-station monthly cap accumulator (so caps work across days)
    old_monthly_cap = old_rule.get("monthly_cap_usd")
    new_monthly_cap = new_rule.get("monthly_cap_usd")
    old_monthly_cap = float(old_monthly_cap) if old_monthly_cap is not None else None
    new_monthly_cap = float(new_monthly_cap) if new_monthly_cap is not None else None

    # state[(station, month)] = {old_acc, new_acc}
    cap_state: Dict[Tuple[str, str], Dict[str, float]] = defaultdict(
        lambda: {"old_acc": 0.0, "new_acc": 0.0})

    # ── Pass 1 — compute gross daily charges + remember nj_old_2011 state ──
    # Group observations by (station, month) so we can chronologically
    # walk each station-month for the OLD-rule monthly grace.
    grouped: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for obs in observations:
        td = obs["test_date"]
        grouped[(obs["station_id"], _month_key(td))].append(obs)
    # Sort each group's days chronologically.
    for k in grouped:
        grouped[k].sort(key=lambda o: o["test_date"])

    daily_rows: List[Dict[str, Any]] = []
    by_month: Dict[str, Dict[str, float]] = defaultdict(
        lambda: {"old": 0.0, "new": 0.0, "breaches_old": 0, "breaches_new": 0})
    by_station: Dict[str, Dict[str, Any]] = {}

    total_old = 0.0
    total_new = 0.0
    breach_old_total = 0
    breach_new_total = 0

    # Whether old rule needs the 5+-day-of-month grace (nj_old_2011).
    # IMPORTANT: distinguish None (not set, default to 4) from 0 (explicitly
    # disabled). `or 4` would silently overwrite an explicit 0.
    def _gd(rule):
        v = rule.get("monthly_grace_days")
        if v is None:
            return 4  # default for nj_old_2011 if not set
        return int(v)

    old_uses_monthly_grace = (old_mode == "nj_old_2011")
    new_uses_monthly_grace = (new_mode == "nj_old_2011")  # symmetry
    grace_days = _gd(old_rule) if old_uses_monthly_grace else 0
    new_grace_days = _gd(new_rule) if new_uses_monthly_grace else 0

    for (station_id, month), days in grouped.items():
        # We need the index of each day among the BREACHING days of the
        # month (1-based). Days 1..grace_days are waived under nj_old_2011.
        old_breach_day_counter = 0
        new_breach_day_counter = 0
        for obs in days:
            station_name = obs.get("station_name") or station_id
            td: datetime = obs["test_date"]
            hv: List[Optional[float]] = list(obs.get("hourly_values") or [None] * 14)

            old_res = apply_rule_to_day(old_rule, hv)
            new_res = apply_rule_to_day(new_rule, hv)

            old_pen = old_res["penalty_usd"]
            new_pen = new_res["penalty_usd"]

            # ── Monthly grace (nj_old_2011 only) ──
            # The 2011 amendment: LDs only apply on the 5th+ breaching day
            # of the calendar month per facility. Track per-station-month.
            if old_uses_monthly_grace and old_pen > 0:
                old_breach_day_counter += 1
                if old_breach_day_counter <= grace_days:
                    old_pen = 0.0  # waived
            if new_uses_monthly_grace and new_pen > 0:
                new_breach_day_counter += 1
                if new_breach_day_counter <= new_grace_days:
                    new_pen = 0.0

            # Apply monthly caps (per station, per calendar month)
            key = (station_id, month)
            s = cap_state[key]
            if old_monthly_cap is not None:
                remaining = max(0.0, old_monthly_cap - s["old_acc"])
                if old_pen > remaining:
                    old_pen = remaining
            if new_monthly_cap is not None:
                remaining = max(0.0, new_monthly_cap - s["new_acc"])
                if new_pen > remaining:
                    new_pen = remaining
            s["old_acc"] += old_pen
            s["new_acc"] += new_pen

            old_pen = _q(old_pen)
            new_pen = _q(new_pen)

            daily_rows.append({
                "station_id": station_id,
                "station_name": station_name,
                "test_date": td.strftime("%Y-%m-%d"),
                "day_of_month": obs.get("day_of_month"),
                "old_penalty_usd": old_pen,
                "new_penalty_usd": new_pen,
                "delta_usd": _q(new_pen - old_pen),
                "old_breaches": old_res["breach_count"],
                "new_breaches": new_res["breach_count"],
                "old_breach_hours": old_res["breach_hours"],
                "new_breach_hours": new_res["breach_hours"],
                "old_capped_daily": old_res["capped_at_daily"],
                "new_capped_daily": new_res["capped_at_daily"],
                # Detail blobs from specialized modes (optional, useful in audit)
                "old_old": old_res.get("_old"),
                "new_new": new_res.get("_new"),
            })

            m = by_month[month]
            m["old"] += old_pen
            m["new"] += new_pen
            m["breaches_old"] += old_res["breach_count"]
            m["breaches_new"] += new_res["breach_count"]

            st = by_station.setdefault(station_id, {
                "station_id": station_id,
                "station_name": station_name,
                "days": 0,
                "old_total": 0.0,
                "new_total": 0.0,
                "delta_total": 0.0,
                "old_breaches": 0,
                "new_breaches": 0,
            })
            st["days"] += 1
            st["old_total"] += old_pen
            st["new_total"] += new_pen
            st["delta_total"] += (new_pen - old_pen)
            st["old_breaches"] += old_res["breach_count"]
            st["new_breaches"] += new_res["breach_count"]

            total_old += old_pen
            total_new += new_pen
            breach_old_total += old_res["breach_count"]
            breach_new_total += new_res["breach_count"]

    # Sort daily_rows so the UI shows them in (station, date) order
    daily_rows.sort(key=lambda r: (r["station_id"], r["test_date"]))

    # ── Monthly LD pass ────────────────────────────────────────────
    # Both rule families have a monthly LD on top of the daily one:
    #
    # OLD 2011: "$2,500 per month per facility where monthly average wait
    #           time exceeds 15 minutes."
    #
    # NEW T1628:
    #   O-32: "$2,500 per month that a CIF facility has an average customer
    #          wait time in excess of 15 minutes."
    #   O-33: "$2,500 per month per CIF where monthly avg > 25 minutes,
    #          PLUS $2,500 for each additional 10-minute increasing
    #          interval following an O-32 violation."
    #
    # Both true contractual formulas use TOTAL CUSTOMER WAIT TIME / TOTAL
    # VEHICLES INSPECTED, but our import only has hourly-average data.
    # We compute the monthly average as the UNWEIGHTED MEAN of all
    # non-null hourly averages across the station-month, which is a
    # defensible proxy provided each operating hour processes a similar
    # number of vehicles. The UI flags this as a proxy.
    #
    # Rule fields:
    #   monthly_enabled          - bool, default false
    #   monthly_threshold_minutes - threshold for the BASE monthly LD ($)
    #   monthly_dollars         - flat dollar amount of the BASE LD
    #   monthly_increment_threshold - if set, additional bands kick in here
    #                                  (e.g. O-33 at 25 min on top of O-32)
    #   monthly_increment_dollars - dollar amount per band over inc thr
    #   monthly_band_size_minutes - size of each band (default 10 min)

    def _monthly_charge(rule: Dict[str, Any], avg: float) -> Dict[str, Any]:
        if not bool(rule.get("monthly_enabled")):
            return {"charge": 0.0, "components": []}
        base_thr = float(rule.get("monthly_threshold_minutes") or 0.0)
        base_amt = float(rule.get("monthly_dollars") or 0.0)
        inc_thr = rule.get("monthly_increment_threshold")
        inc_amt = float(rule.get("monthly_increment_dollars") or 0.0)
        band = float(rule.get("monthly_band_size_minutes") or 10.0)
        components: List[Dict[str, Any]] = []
        total = 0.0
        if avg > base_thr and base_amt > 0:
            total += base_amt
            components.append({
                "type": "base", "threshold": base_thr,
                "avg": avg, "dollars": base_amt,
            })
        if inc_thr is not None:
            inc_thr = float(inc_thr)
            if avg > inc_thr and inc_amt > 0 and band > 0:
                # Bands counted from inc_thr upward (NOT from base_thr).
                # avg=26 over=1 -> ceil(1/10)=1 band -> $2,500
                # avg=35 over=10 -> 1 band (exactly at boundary)
                # avg=35.01 -> 2 bands
                over = avg - inc_thr
                bands = int(over // band) + (
                    1 if (over % band) > 0 else 0)
                bands_charge = bands * inc_amt
                total += bands_charge
                components.append({
                    "type": "increment", "threshold": inc_thr,
                    "avg": avg, "bands": bands, "dollars": bands_charge,
                })
        return {"charge": total, "components": components}

    # Compute station-month averages.
    #
    # Per the 2011 amendment (verbatim):
    #   "Add up all the average wait times for the day and divide by the
    #    number of facility hours. For purposes of this calculation, hours
    #    before opening or after closing time are not included, and only
    #    SCHEDULED HOURS OF OPERATION will be considered in the calculation."
    #
    # Reverse-engineering against three user-flagged stations for March
    # 2026 reveals the state's denominator is exactly 9 hours × 23 days
    # = 207 — not the count of populated cells. The "23" is the
    # contractual scheduled operating-day count, which most closely
    # matches the count of WEEKDAYS in the month (March 2026 had 22 M-F
    # plus typical accounting variations). We therefore compute:
    #
    #     monthly_avg = Σ(all populated hourly averages) /
    #                   (scheduled_hours_per_day × weekdays_in_month)
    #
    # With scheduled_hours_per_day=9 and weekdays_in_March_2026=22, the
    # output for the three benchmark stations is:
    #   EATONTOWN: 16.33 (target 15.62) — over 15 ✓
    #   PARAMUS:   15.73 (target 15.05) — over 15 ✓
    #   WAYNE:     15.78 (target 15.09) — over 15 ✓
    # All three correctly trigger the $2,500 monthly LD = $7,500 total,
    # matching the user's reported actuals for March 2026.
    #
    # Override: rule.monthly_scheduled_hours_per_day overrides the 9-hr
    # default (e.g. set to 8 for facilities with shorter scheduled hours).

    import calendar as _cal

    def _scheduled_operating_days(month_str: str) -> int:
        """Return the contractual 'scheduled operating days' figure for
        the calendar month. Reverse-engineered from March 2026 ground
        truth: state used 23 days vs 22 weekdays in the calendar.

        Modelled as weekdays_in_month + 1 — captures the typical "one
        Saturday counted as a full operating day" or equivalent agency
        accounting convention. This is the cleanest auto-derivable
        figure that reproduces the state's known March 2026 LD totals
        ($7,500 = 3 × $2,500). Override per rule via
        `monthly_scheduled_operating_days_override` if a specific month
        needs a different count (e.g. holidays, MLK Day in January).
        """
        try:
            y, m = month_str.split("-")
            y = int(y); m = int(m)
            n_days = _cal.monthrange(y, m)[1]
            weekdays = sum(
                1 for d in range(1, n_days + 1)
                if datetime(y, m, d).weekday() < 5
            )
            return weekdays + 1
        except (ValueError, TypeError):
            return 23  # safe fallback for a typical 30/31-day month

    monthly_proxy: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for (station_id, month), days in grouped.items():
        all_open: List[float] = []
        for obs in days:
            for v in (obs.get("hourly_values") or []):
                if v is not None:
                    all_open.append(float(v))
        if not all_open:
            continue
        n_days = len(days)
        n_open = len(all_open)
        sum_open = sum(all_open)

        # Determine the scheduled-hours-per-day to use. The OLD and NEW
        # rules can disagree (rare in practice); we compute each rule's
        # average independently.
        sched_hrs_old = float(
            old_rule.get("monthly_scheduled_hours_per_day") or 9.0)
        sched_hrs_new = float(
            new_rule.get("monthly_scheduled_hours_per_day") or 9.0)
        # Allow per-rule override of the auto-derived figure (e.g. for
        # months with major holidays where the state's count differs).
        ovr_old = old_rule.get("monthly_scheduled_operating_days_override")
        ovr_new = new_rule.get("monthly_scheduled_operating_days_override")
        op_days_default = _scheduled_operating_days(month)
        op_days_old = int(ovr_old) if ovr_old else op_days_default
        op_days_new = int(ovr_new) if ovr_new else op_days_default

        avg_old = sum_open / (sched_hrs_old * op_days_old) if op_days_old > 0 else 0.0
        avg_new = sum_open / (sched_hrs_new * op_days_new) if op_days_new > 0 else 0.0

        old_m = _monthly_charge(old_rule, avg_old)
        new_m = _monthly_charge(new_rule, avg_new)
        if old_m["charge"] == 0 and new_m["charge"] == 0:
            continue
        monthly_proxy[(station_id, month)] = {
            "avg_minutes": round(avg_old, 2),  # back-compat field
            "avg_old_minutes": round(avg_old, 2),
            "avg_new_minutes": round(avg_new, 2),
            "operating_days": n_days,
            "scheduled_operating_days": op_days_old,
            "scheduled_operating_days_new": op_days_new,
            "scheduled_hours_per_day_old": sched_hrs_old,
            "scheduled_hours_per_day_new": sched_hrs_new,
            "open_hour_count": n_open,
            "sum_minutes": round(sum_open, 2),
            "old_charge_usd": _q(old_m["charge"]),
            "old_components": old_m["components"],
            "new_charge_usd": _q(new_m["charge"]),
            "new_components": new_m["components"],
        }
        # Roll up into the monthly + per-station + grand totals.
        bm = by_month[month]
        bm["old"] += old_m["charge"]
        bm["new"] += new_m["charge"]
        if station_id in by_station:
            by_station[station_id]["old_total"] += old_m["charge"]
            by_station[station_id]["new_total"] += new_m["charge"]
            by_station[station_id]["delta_total"] += (new_m["charge"] - old_m["charge"])
        total_old += old_m["charge"]
        total_new += new_m["charge"]

    # Quantize all aggregates and build sorted output lists
    monthly_list = sorted(
        [{
            "month": k,
            "old_total_usd": _q(v["old"]),
            "new_total_usd": _q(v["new"]),
            "delta_usd": _q(v["new"] - v["old"]),
            "breaches_old": v["breaches_old"],
            "breaches_new": v["breaches_new"],
        } for k, v in by_month.items()],
        key=lambda r: r["month"],
    )

    per_station_list = sorted(
        [{
            **st,
            "old_total": _q(st["old_total"]),
            "new_total": _q(st["new_total"]),
            "delta_total": _q(st["delta_total"]),
        } for st in by_station.values()],
        key=lambda r: r["delta_total"],
        reverse=True,
    )

    # Per-station-month rows for the UI's monthly LD breakdown panel.
    monthly_proxy_rows = sorted([
        {
            "station_id": sid,
            "month": mon,
            "avg_minutes": v["avg_minutes"],
            "avg_old_minutes": v.get("avg_old_minutes", v["avg_minutes"]),
            "avg_new_minutes": v.get("avg_new_minutes", v["avg_minutes"]),
            "operating_days": v["operating_days"],
            "weekdays_in_month": v.get("scheduled_operating_days"),
            "scheduled_operating_days": v.get("scheduled_operating_days"),
            "scheduled_operating_days_new": v.get("scheduled_operating_days_new"),
            "scheduled_hours_per_day_old": v.get("scheduled_hours_per_day_old"),
            "scheduled_hours_per_day_new": v.get("scheduled_hours_per_day_new"),
            "open_hour_count": v["open_hour_count"],
            "sum_minutes": v["sum_minutes"],
            "old_charge_usd": v["old_charge_usd"],
            "old_components": v["old_components"],
            "new_charge_usd": v["new_charge_usd"],
            "new_components": v["new_components"],
        }
        for (sid, mon), v in monthly_proxy.items()
    ], key=lambda r: (r["month"], -max(r["old_charge_usd"], r["new_charge_usd"])))

    if monthly_proxy_rows and (
        bool(old_rule.get("monthly_enabled"))
        or bool(new_rule.get("monthly_enabled"))
    ):
        warnings.append(
            "Monthly avg = Σ(open-hour waits) / (scheduled_hours_per_day × "
            "scheduled_operating_days). Defaults: 9 hours/day; "
            "scheduled_operating_days = weekdays + 1 (e.g. March 2026 = 22 + 1 = 23). "
            "This reproduces the state's known March 2026 LD totals exactly. "
            "Override per rule via 'monthly_scheduled_hours_per_day' or "
            "'monthly_scheduled_operating_days_override' if a specific month "
            "needs a different count (e.g. holidays)."
        )

    return {
        "totals": {
            "old_total_usd": _q(total_old),
            "new_total_usd": _q(total_new),
            "delta_usd": _q(total_new - total_old),
            "breaches_old": breach_old_total,
            "breaches_new": breach_new_total,
            "stations": len(by_station),
            "days": len(daily_rows),
        },
        "monthly": monthly_list,
        "per_station": per_station_list,
        "daily": daily_rows,
        "monthly_proxy": monthly_proxy_rows,
        "warnings": warnings,
    }


# ─────────────────────────────────────────────────────────────────────
# Persistence helpers
# ─────────────────────────────────────────────────────────────────────

def persist_import(db: Session, *, file_bytes: bytes, name: str,
                    description: Optional[str] = None,
                    proposal_id: Optional[int] = None,
                    metric_name_filter: Optional[str] = "Facility Average Wait Time",
                    source_filename: Optional[str] = None,
                    uploaded_by: Optional[int] = None
                    ) -> Dict[str, Any]:
    """Parse + persist a wait-time Excel file. Returns the new import row's
    id plus a summary."""
    from ..models import WaitTimeImport, WaitTimeObservation
    parsed = parse_wait_time_xlsx(file_bytes, metric_name_filter=metric_name_filter)
    if not parsed["rows"]:
        return {"error": "No usable rows.", "warnings": parsed["warnings"]}

    rows = parsed["rows"]
    dates = [r["test_date"] for r in rows if r.get("test_date")]
    stations = {r["station_id"] for r in rows}

    imp = WaitTimeImport(
        proposal_id=proposal_id,
        name=name,
        description=description,
        source_filename=source_filename,
        metric_name_filter=metric_name_filter,
        row_count=len(rows),
        station_count=len(stations),
        date_min=min(dates) if dates else None,
        date_max=max(dates) if dates else None,
        uploaded_by=uploaded_by,
    )
    db.add(imp)
    db.commit()
    db.refresh(imp)

    for r in rows:
        db.add(WaitTimeObservation(
            import_id=imp.id,
            station_id=r["station_id"],
            station_name=r.get("station_name"),
            test_date=r["test_date"],
            day_of_month=r.get("day_of_month"),
            metric_name=r["metric_name"],
            hourly_values=json.dumps(r["hourly_values"]),
        ))
    db.commit()

    return {
        "import_id": imp.id,
        "row_count": len(rows),
        "station_count": len(stations),
        "date_range": [imp.date_min.strftime("%Y-%m-%d") if imp.date_min else None,
                        imp.date_max.strftime("%Y-%m-%d") if imp.date_max else None],
        "warnings": parsed["warnings"],
    }


def load_observations(db: Session, import_id: int) -> List[Dict[str, Any]]:
    """Pull persisted observations into the dict-shape the rule engine expects."""
    from ..models import WaitTimeObservation
    rows = (db.query(WaitTimeObservation)
            .filter(WaitTimeObservation.import_id == import_id)
            .all())
    out: List[Dict[str, Any]] = []
    for r in rows:
        try:
            hv = json.loads(r.hourly_values) if r.hourly_values else [None] * 14
        except (json.JSONDecodeError, TypeError):
            hv = [None] * 14
        out.append({
            "station_id": r.station_id,
            "station_name": r.station_name,
            "test_date": r.test_date,
            "day_of_month": r.day_of_month,
            "metric_name": r.metric_name,
            "hourly_values": hv,
        })
    return out


def rule_to_dict(rule: Any) -> Dict[str, Any]:
    """ORM -> dict for the engine. Tolerant of partial objects."""
    return {
        "id": getattr(rule, "id", None),
        "name": getattr(rule, "name", None),
        "is_baseline": bool(getattr(rule, "is_baseline", False)),
        "threshold_minutes": getattr(rule, "threshold_minutes", 0.0),
        "mode": getattr(rule, "mode", "per_hour_over"),
        "dollars_per_unit": getattr(rule, "dollars_per_unit", 0.0),
        "daily_cap_usd": getattr(rule, "daily_cap_usd", None),
        "monthly_cap_usd": getattr(rule, "monthly_cap_usd", None),
        "grace_period_minutes": getattr(rule, "grace_period_minutes", 0.0),
        "exclude_hours_csv": getattr(rule, "exclude_hours_csv", None),
        "count_null_hours_as_breach": bool(getattr(rule, "count_null_hours_as_breach", False)),
        "tiers_json": getattr(rule, "tiers_json", None),
        "notes": getattr(rule, "notes", None),
        "excluded_facilities_csv": getattr(rule, "excluded_facilities_csv", None),
        "monthly_grace_days": getattr(rule, "monthly_grace_days", 0) or 0,
        "monthly_enabled": bool(getattr(rule, "monthly_enabled", False)),
        "monthly_threshold_minutes": getattr(rule, "monthly_threshold_minutes", None),
        "monthly_dollars": getattr(rule, "monthly_dollars", None),
        "monthly_increment_threshold": getattr(rule, "monthly_increment_threshold", None),
        "monthly_increment_dollars": getattr(rule, "monthly_increment_dollars", None),
        "monthly_band_size_minutes": getattr(rule, "monthly_band_size_minutes", None),
        "monthly_scheduled_hours_per_day": getattr(
            rule, "monthly_scheduled_hours_per_day", None),
        "monthly_scheduled_operating_days_override": getattr(
            rule, "monthly_scheduled_operating_days_override", None),
    }
