"""
Analysis engine: grouping, unused-license detection, and cost forecasting.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List

from ..providers.detector import FocusRecord


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------

def group_by_product(records: List[FocusRecord]) -> List[Dict[str, Any]]:
    """Aggregate cost and usage per SaaS product (ServiceName)."""
    totals: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
        "cost": 0.0, "usage_amount": 0.0, "providers": set(),
    })
    for r in records:
        key = r.ServiceName
        totals[key]["cost"] += _float(r.BilledCost)
        totals[key]["usage_amount"] += _float(r.usage_amount)
        totals[key]["providers"].add(r.provider)

    rows = []
    for key, data in totals.items():
        rows.append({
            "key": key,
            "cost": round(data["cost"], 4),
            "usage_amount": round(data["usage_amount"], 2),
            "provider": ",".join(sorted(data["providers"])),
        })
    rows.sort(key=lambda r: r["cost"], reverse=True)
    return rows


def group_by_user(records: List[FocusRecord]) -> List[Dict[str, Any]]:
    """Aggregate cost per ResourceId (seat/license/user)."""
    totals: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
        "cost": 0.0, "usage_amount": 0.0, "providers": set(),
    })
    for r in records:
        key = r.ResourceId or "(unknown)"
        totals[key]["cost"] += _float(r.BilledCost)
        totals[key]["usage_amount"] += _float(r.usage_amount)
        totals[key]["providers"].add(r.provider)

    rows = []
    for key, data in totals.items():
        rows.append({
            "key": key,
            "cost": round(data["cost"], 4),
            "usage_amount": round(data["usage_amount"], 2),
            "provider": ",".join(sorted(data["providers"])),
        })
    rows.sort(key=lambda r: r["cost"], reverse=True)
    return rows


def group_by_month(records: List[FocusRecord]) -> List[Dict[str, Any]]:
    """Aggregate cost per calendar month (YYYY-MM) from ChargePeriodStart."""
    totals: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
        "cost": 0.0, "usage_amount": 0.0, "providers": set(),
    })
    for r in records:
        month = _month_key(r.ChargePeriodStart)
        totals[month]["cost"] += _float(r.BilledCost)
        totals[month]["usage_amount"] += _float(r.usage_amount)
        totals[month]["providers"].add(r.provider)

    rows = []
    for key, data in totals.items():
        rows.append({
            "key": key,
            "cost": round(data["cost"], 4),
            "usage_amount": round(data["usage_amount"], 2),
            "provider": ",".join(sorted(data["providers"])),
        })
    rows.sort(key=lambda r: r["key"])
    return rows


# ---------------------------------------------------------------------------
# Unused detection
# ---------------------------------------------------------------------------

def flag_unused(records: List[FocusRecord]) -> List[Dict[str, Any]]:
    """
    Return rows where usage_amount is 0 or empty (unused licenses/seats).
    Each row is a dict with the full FOCUS fields plus 'unused': True.
    """
    unused = []
    for r in records:
        ua = r.usage_amount.strip() if r.usage_amount else ""
        if ua == "" or _float(ua) == 0.0:
            d = r.as_dict()
            d["unused"] = True
            unused.append(d)
    return unused


# ---------------------------------------------------------------------------
# Forecasting
# ---------------------------------------------------------------------------

def forecast_next_month(records: List[FocusRecord]) -> Dict[str, Any]:
    """
    Project next-month cost using simple linear trend across monthly data.

    Returns a dict with:
      - monthly_data: list of {month, cost}
      - projected_cost: float
      - method: 'linear_trend' | 'single_period_repeat'
    """
    monthly = group_by_month(records)
    monthly_sorted = sorted(monthly, key=lambda r: r["key"])

    if not monthly_sorted:
        return {"monthly_data": [], "projected_cost": 0.0, "method": "no_data"}

    if len(monthly_sorted) == 1:
        return {
            "monthly_data": monthly_sorted,
            "projected_cost": round(monthly_sorted[0]["cost"], 4),
            "method": "single_period_repeat",
        }

    # Linear regression: x = period index, y = cost
    n = len(monthly_sorted)
    xs = list(range(n))
    ys = [r["cost"] for r in monthly_sorted]
    x_mean = sum(xs) / n
    y_mean = sum(ys) / n
    num = sum((xs[i] - x_mean) * (ys[i] - y_mean) for i in range(n))
    den = sum((xs[i] - x_mean) ** 2 for i in range(n))
    slope = num / den if den != 0 else 0.0
    intercept = y_mean - slope * x_mean
    projected = intercept + slope * n  # next period = index n
    projected = max(0.0, projected)

    return {
        "monthly_data": monthly_sorted,
        "projected_cost": round(projected, 4),
        "method": "linear_trend",
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _float(value: str) -> float:
    """Safely parse a string to float; return 0.0 on failure."""
    try:
        return float(str(value).strip().replace(",", ""))
    except (ValueError, TypeError, AttributeError):
        return 0.0


def _month_key(date_str: str) -> str:
    """Extract YYYY-MM from an ISO date string."""
    s = (date_str or "").strip()
    if len(s) >= 7:
        return s[:7]
    return s or "(unknown)"
