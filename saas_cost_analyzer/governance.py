"""Strict SaaS governance contract and calculations."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

CONTRACT_VERSION = "saas-cost-analyzer/2.0"
CADENCE_MONTHS = {
    "monthly": Decimal("1"),
    "quarterly": Decimal("3"),
    "annual": Decimal("12"),
}


class GovernanceError(ValueError):
    pass


def _text(value: Any, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise GovernanceError(f"{field} is required")
    return result


def _date(value: Any, field: str) -> date:
    try:
        return date.fromisoformat(_text(value, field))
    except ValueError as exc:
        raise GovernanceError(f"{field} must be ISO YYYY-MM-DD") from exc


def _decimal(value: Any, field: str) -> Decimal:
    if isinstance(value, bool):
        raise GovernanceError(f"{field} must be numeric")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise GovernanceError(f"{field} must be numeric") from exc
    if not result.is_finite() or result < 0:
        raise GovernanceError(f"{field} must be finite and non-negative")
    return result


def _integer(value: Any, field: str) -> int:
    result = _decimal(value, field)
    if result != result.to_integral_value():
        raise GovernanceError(f"{field} must be an integer")
    return int(result)


def _keys(value: dict[str, Any], allowed: set[str], field: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise GovernanceError(f"{field} has unknown fields: {', '.join(unknown)}")


@dataclass(frozen=True)
class Bundle:
    raw: dict[str, Any]
    content_hash: str


def load_bundle(path: Path) -> Bundle:
    try:
        raw_bytes = path.read_bytes()
        value = json.loads(raw_bytes)
    except FileNotFoundError as exc:
        raise GovernanceError(f"bundle not found: {path}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GovernanceError(f"unable to read bundle: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != CONTRACT_VERSION:
        raise GovernanceError(f"bundle must use schema_version {CONTRACT_VERSION}")
    _keys(
        value,
        {
            "schema_version",
            "mode",
            "as_of",
            "currency",
            "inactivity_days",
            "activity_freshness_days",
            "applications",
        },
        "bundle",
    )
    mode = _text(value.get("mode"), "mode").lower()
    if mode not in {"illustrative", "real"}:
        raise GovernanceError("mode must be illustrative or real")
    value["mode"] = mode
    as_of = _date(value.get("as_of"), "as_of")
    currency = _text(value.get("currency"), "currency").upper()
    if len(currency) != 3 or not currency.isalpha():
        raise GovernanceError("currency must be a three-letter ISO code")
    value["currency"] = currency
    threshold = _integer(value.get("inactivity_days"), "inactivity_days")
    freshness = _integer(
        value.get("activity_freshness_days"), "activity_freshness_days"
    )
    if threshold < 1 or freshness < 0:
        raise GovernanceError(
            "inactivity_days must be positive and activity_freshness_days non-negative"
        )
    apps = value.get("applications")
    if not isinstance(apps, list) or not apps:
        raise GovernanceError("applications must be a non-empty array")
    seen_apps: set[str] = set()
    for ai, app in enumerate(apps):
        p = f"applications[{ai}]"
        if not isinstance(app, dict):
            raise GovernanceError(f"{p} must be an object")
        _keys(
            app,
            {
                "id",
                "name",
                "owner",
                "assignment_roster_complete",
                "activity_observed_through",
                "contract",
                "invoices",
                "assignments",
            },
            p,
        )
        app_id = _text(app.get("id"), f"{p}.id")
        if app_id in seen_apps:
            raise GovernanceError(f"duplicate application id: {app_id}")
        seen_apps.add(app_id)
        _text(app.get("name"), f"{p}.name")
        _text(app.get("owner"), f"{p}.owner")
        contract = app.get("contract")
        if not isinstance(contract, dict):
            raise GovernanceError(f"{p}.contract must be an object")
        _keys(
            contract,
            {
                "purchased_seats",
                "billing_cadence",
                "unit_price",
                "commitment",
                "renewal_date",
                "notice_days",
                "evidence_status",
            },
            f"{p}.contract",
        )
        seats = _integer(
            contract.get("purchased_seats"), f"{p}.contract.purchased_seats"
        )
        cadence = _text(
            contract.get("billing_cadence"), f"{p}.contract.billing_cadence"
        ).lower()
        if cadence not in CADENCE_MONTHS:
            raise GovernanceError(
                f"{p}.contract.billing_cadence must be monthly, quarterly, or annual"
            )
        contract["billing_cadence"] = cadence
        unit_price = _decimal(contract.get("unit_price"), f"{p}.contract.unit_price")
        commitment = _decimal(contract.get("commitment"), f"{p}.contract.commitment")
        if commitment != Decimal(seats) * unit_price:
            raise GovernanceError(
                f"{p}.contract.commitment must equal purchased_seats * unit_price"
            )
        renewal = _date(contract.get("renewal_date"), f"{p}.contract.renewal_date")
        if renewal < as_of:
            raise GovernanceError(f"{p}.contract.renewal_date is before as_of")
        _integer(contract.get("notice_days"), f"{p}.contract.notice_days")
        if contract.get("evidence_status") not in {
            "verified_document",
            "user_supplied",
        }:
            raise GovernanceError(f"{p}.contract.evidence_status is unsupported")
        invoices = app.get("invoices")
        if not isinstance(invoices, list):
            raise GovernanceError(f"{p}.invoices must be an array")
        seen_invoices: set[str] = set()
        invoice_periods: list[tuple[date, date, str]] = []
        for ii, invoice in enumerate(invoices):
            ip = f"{p}.invoices[{ii}]"
            if not isinstance(invoice, dict):
                raise GovernanceError(f"{ip} must be an object")
            _keys(invoice, {"id", "period_start", "period_end", "amount"}, ip)
            iid = _text(invoice.get("id"), f"{ip}.id")
            if iid in seen_invoices:
                raise GovernanceError(f"duplicate invoice id in {app_id}: {iid}")
            seen_invoices.add(iid)
            start = _date(invoice.get("period_start"), f"{ip}.period_start")
            end = _date(invoice.get("period_end"), f"{ip}.period_end")
            if end <= start:
                raise GovernanceError(f"{ip}.period_end must be after period_start")
            invoice_periods.append((start, end, iid))
            _decimal(invoice.get("amount"), f"{ip}.amount")
        invoice_periods.sort()
        for previous, current in zip(invoice_periods, invoice_periods[1:]):
            if current[0] < previous[1]:
                raise GovernanceError(
                    f"invoice periods overlap in {app_id}: {previous[2]} and {current[2]}"
                )
        assignments = app.get("assignments")
        if not isinstance(assignments, list):
            raise GovernanceError(f"{p}.assignments must be an array")
        if not isinstance(app.get("assignment_roster_complete"), bool):
            raise GovernanceError(f"{p}.assignment_roster_complete must be boolean")
        observed = _date(
            app.get("activity_observed_through"), f"{p}.activity_observed_through"
        )
        if observed > as_of:
            raise GovernanceError(
                f"{p}.activity_observed_through cannot be after as_of"
            )
        seen_users: set[str] = set()
        for ui, assignment in enumerate(assignments):
            up = f"{p}.assignments[{ui}]"
            if not isinstance(assignment, dict):
                raise GovernanceError(f"{up} must be an object")
            _keys(assignment, {"user_id", "last_activity_at"}, up)
            uid = _text(assignment.get("user_id"), f"{up}.user_id")
            if uid in seen_users:
                raise GovernanceError(f"duplicate assigned user in {app_id}: {uid}")
            seen_users.add(uid)
            if assignment.get("last_activity_at") is not None:
                last = _date(assignment["last_activity_at"], f"{up}.last_activity_at")
                if last > observed:
                    raise GovernanceError(
                        f"{up}.last_activity_at exceeds activity_observed_through"
                    )
        if len(assignments) > seats:
            raise GovernanceError(f"{app_id} assignments exceed purchased seats")
    return Bundle(value, hashlib.sha256(raw_bytes).hexdigest())


def analyze_bundle(bundle: Bundle) -> dict[str, Any]:
    value = bundle.raw
    as_of = _date(value["as_of"], "as_of")
    inactive_cutoff = as_of - timedelta(days=int(value["inactivity_days"]))
    fresh_cutoff = as_of - timedelta(days=int(value["activity_freshness_days"]))
    results = []
    for app in value["applications"]:
        contract = app["contract"]
        assignments = app["assignments"]
        observed = _date(app["activity_observed_through"], "activity_observed_through")
        activity_fresh = observed >= fresh_cutoff
        roster_complete = app["assignment_roster_complete"]
        active = inactive = unknown = 0
        for assignment in assignments:
            last_raw = assignment.get("last_activity_at")
            if not activity_fresh or last_raw is None:
                unknown += 1
            elif _date(last_raw, "last_activity_at") < inactive_cutoff:
                inactive += 1
            else:
                active += 1
        purchased = int(contract["purchased_seats"])
        unassigned = purchased - len(assignments) if roster_complete else None
        evidence_complete = activity_fresh and roster_complete
        reviewable = inactive + (unassigned or 0) if evidence_complete else None
        cadence = contract["billing_cadence"]
        cadence_months = CADENCE_MONTHS[cadence]
        unit_price = _decimal(contract["unit_price"], "unit_price")
        commitment = _decimal(contract["commitment"], "commitment")
        unit_price_monthly = unit_price / cadence_months
        monthly_commitment = commitment / cadence_months
        annualized_commitment = monthly_commitment * Decimal("12")
        monthly_capacity = (
            Decimal(reviewable) * unit_price_monthly if reviewable is not None else None
        )
        invoice_total = sum(
            (_decimal(row["amount"], "invoice amount") for row in app["invoices"]),
            Decimal("0"),
        )
        renewal = _date(contract["renewal_date"], "renewal_date")
        notice_deadline = renewal - timedelta(days=int(contract["notice_days"]))
        results.append(
            {
                "id": app["id"],
                "name": app["name"],
                "owner": app["owner"],
                "purchased_seats": purchased,
                "assigned_seats": len(assignments),
                "unassigned_seats": unassigned,
                "active_seats": active,
                "inactive_seats": inactive,
                "unknown_activity_seats": unknown,
                "activity_evidence_fresh": activity_fresh,
                "assignment_roster_complete": roster_complete,
                "utilization_percentage": (
                    float(Decimal(active) / Decimal(purchased) * Decimal("100"))
                    if purchased > 0
                    and activity_fresh
                    and roster_complete
                    and unknown == 0
                    else None
                ),
                "invoice_total": float(invoice_total),
                "invoice_count": len(app["invoices"]),
                "billing_cadence": cadence,
                "cadence_months": int(cadence_months),
                "unit_price": float(unit_price),
                "unit_price_monthly": float(unit_price_monthly),
                "commitment": float(commitment),
                "monthly_commitment": float(monthly_commitment),
                "annualized_commitment": float(annualized_commitment),
                "renewal_exposure": float(annualized_commitment),
                "renewal_date": renewal.isoformat(),
                "notice_deadline": notice_deadline.isoformat(),
                "days_to_notice_deadline": (notice_deadline - as_of).days,
                "reviewable_seats": reviewable,
                "estimated_monthly_reduction_capacity_at_renewal": (
                    float(monthly_capacity) if monthly_capacity is not None else None
                ),
                "estimated_annual_reduction_capacity_at_renewal": (
                    float(monthly_capacity * 12)
                    if monthly_capacity is not None
                    else None
                ),
                "reconciliation": {
                    "assigned_seats": len(assignments),
                    "classified_assigned_seats": active + inactive + unknown,
                    "assigned_difference": len(assignments)
                    - (active + inactive + unknown),
                    "purchased_seats": purchased,
                    "assigned_plus_unassigned_seats": (
                        len(assignments) + unassigned
                        if unassigned is not None
                        else None
                    ),
                    "normalized_monthly_commitment": float(monthly_commitment),
                    "normalized_annual_commitment": float(annualized_commitment),
                    "status": "passed",
                },
            }
        )
    return {
        "schema_version": CONTRACT_VERSION,
        "mode": value["mode"],
        "as_of": value["as_of"],
        "currency": value["currency"],
        "inactivity_days": value["inactivity_days"],
        "activity_freshness_days": value["activity_freshness_days"],
        "applications": results,
    }
