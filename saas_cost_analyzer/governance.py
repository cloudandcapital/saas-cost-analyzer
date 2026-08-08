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
SCOPE_CONTRACT_VERSION = "saas-cost-analyzer/2.1"
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


@dataclass(frozen=True)
class AnalysisDeclaration:
    raw: dict[str, Any]
    content_hash: str


def load_analysis(path: Path) -> AnalysisDeclaration:
    """Load the run-level CCAC 1.1 accounting and completeness declaration."""
    try:
        raw_bytes = path.read_bytes()
        value = json.loads(raw_bytes)
    except FileNotFoundError as exc:
        raise GovernanceError(f"analysis declaration not found: {path}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GovernanceError(f"unable to read analysis declaration: {exc}") from exc
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != "saas-cost-analysis/1.0"
    ):
        raise GovernanceError(
            "analysis declaration must use schema_version saas-cost-analysis/1.0"
        )
    _keys(
        value,
        {
            "schema_version",
            "mode",
            "accounting_cost_basis",
            "period",
            "completeness",
            "allocation_convention",
            "rounding_convention",
            "component_treatments",
            "eligibility",
            "net_cost_definition",
        },
        "analysis",
    )
    mode = _text(value.get("mode"), "analysis.mode").lower()
    if mode not in {"illustrative", "real"}:
        raise GovernanceError("analysis.mode must be illustrative or real")
    value["mode"] = mode
    if value.get("accounting_cost_basis") != "net_cost":
        raise GovernanceError("analysis.accounting_cost_basis must be net_cost")
    period = value.get("period")
    if not isinstance(period, dict):
        raise GovernanceError("analysis.period must be an object")
    _keys(period, {"start", "end", "timezone"}, "analysis.period")
    start = _date(period.get("start"), "analysis.period.start")
    end = _date(period.get("end"), "analysis.period.end")
    if end <= start:
        raise GovernanceError("analysis.period.end must be after start")
    if period.get("timezone") != "UTC":
        raise GovernanceError("analysis.period.timezone must be UTC")
    completeness = value.get("completeness")
    if not isinstance(completeness, dict):
        raise GovernanceError("analysis.completeness must be an object")
    _keys(
        completeness,
        {"status", "absent_application_charges", "description"},
        "analysis.completeness",
    )
    if completeness.get("status") not in {"complete", "partial"}:
        raise GovernanceError("analysis.completeness.status is unsupported")
    if completeness.get("absent_application_charges") not in {
        "zero_illustrative_charges",
        "unknown",
    }:
        raise GovernanceError(
            "analysis.completeness.absent_application_charges is unsupported"
        )
    _text(completeness.get("description"), "analysis.completeness.description")
    if value.get("allocation_convention") != "calendar_day_overlap_half_open":
        raise GovernanceError("analysis.allocation_convention is unsupported")
    if value.get("rounding_convention") != "currency_minor_unit_half_up":
        raise GovernanceError("analysis.rounding_convention is unsupported")
    treatments = value.get("component_treatments")
    if not isinstance(treatments, dict):
        raise GovernanceError("analysis.component_treatments must be an object")
    _keys(
        treatments,
        {"credits", "taxes", "adjustments", "shared_services"},
        "analysis.component_treatments",
    )
    if any(item != "not_applicable" for item in treatments.values()):
        raise GovernanceError(
            "illustrative component treatments must be declared not_applicable"
        )
    eligibility = value.get("eligibility")
    if not isinstance(eligibility, dict):
        raise GovernanceError("analysis.eligibility must be an object")
    _keys(eligibility, {"total_eligible", "explanation"}, "analysis.eligibility")
    if not isinstance(eligibility.get("total_eligible"), bool):
        raise GovernanceError("analysis.eligibility.total_eligible must be boolean")
    _text(eligibility.get("explanation"), "analysis.eligibility.explanation")
    _text(value.get("net_cost_definition"), "analysis.net_cost_definition")
    if mode == "illustrative":
        if completeness["status"] == "complete":
            if (
                completeness["absent_application_charges"]
                != "zero_illustrative_charges"
            ):
                raise GovernanceError(
                    "complete illustrative analysis must declare zero illustrative absent charges"
                )
            if eligibility["total_eligible"] is not True:
                raise GovernanceError(
                    "complete illustrative analysis must be total eligible"
                )
        elif completeness["status"] == "partial":
            if completeness["absent_application_charges"] != "unknown":
                raise GovernanceError(
                    "partial illustrative analysis must leave absent charges unknown"
                )
            if eligibility["total_eligible"] is not False:
                raise GovernanceError(
                    "partial illustrative analysis cannot be total eligible"
                )
        else:
            raise GovernanceError(
                "illustrative analysis must declare complete or partial coverage"
            )
    else:
        if completeness["status"] != "partial":
            raise GovernanceError("real analysis must declare partial coverage")
        if completeness["absent_application_charges"] != "unknown":
            raise GovernanceError("real analysis must leave absent charges unknown")
        if eligibility["total_eligible"] is not False:
            raise GovernanceError("real analysis cannot be total eligible")
    return AnalysisDeclaration(value, hashlib.sha256(raw_bytes).hexdigest())


def load_bundle(path: Path) -> Bundle:
    try:
        raw_bytes = path.read_bytes()
        value = json.loads(raw_bytes)
    except FileNotFoundError as exc:
        raise GovernanceError(f"bundle not found: {path}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GovernanceError(f"unable to read bundle: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema_version") not in {
        CONTRACT_VERSION,
        SCOPE_CONTRACT_VERSION,
    }:
        raise GovernanceError(
            f"bundle must use schema_version {CONTRACT_VERSION} or {SCOPE_CONTRACT_VERSION}"
        )
    schema_version = value["schema_version"]
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
    seen_charges: dict[str, str] = {}
    for ai, app in enumerate(apps):
        p = f"applications[{ai}]"
        if not isinstance(app, dict):
            raise GovernanceError(f"{p} must be an object")
        app_fields = {
            "id",
            "name",
            "owner",
            "assignment_roster_complete",
            "activity_observed_through",
            "contract",
            "invoices",
            "assignments",
        }
        if schema_version == SCOPE_CONTRACT_VERSION:
            app_fields.update(
                {"vendor", "billing_provider", "billing_channel", "technology_scope"}
            )
        _keys(
            app,
            app_fields,
            p,
        )
        app_id = _text(app.get("id"), f"{p}.id")
        if app_id in seen_apps:
            raise GovernanceError(f"duplicate application id: {app_id}")
        seen_apps.add(app_id)
        _text(app.get("name"), f"{p}.name")
        _text(app.get("owner"), f"{p}.owner")
        if schema_version == SCOPE_CONTRACT_VERSION:
            _text(app.get("vendor"), f"{p}.vendor")
            _text(app.get("billing_provider"), f"{p}.billing_provider")
            channel = _text(app.get("billing_channel"), f"{p}.billing_channel")
            scope = _text(app.get("technology_scope"), f"{p}.technology_scope")
            if channel not in {
                "saas_invoice_or_entitlement",
                "direct_ai_vendor",
                "cloud_provider_billing",
            }:
                raise GovernanceError(f"{p}.billing_channel is unsupported")
            expected_scope = {
                "saas_invoice_or_entitlement": "saas",
                "direct_ai_vendor": "direct_ai",
                "cloud_provider_billing": "cloud",
            }[channel]
            if scope != expected_scope:
                raise GovernanceError(
                    f"{p}.technology_scope contradicts billing_channel"
                )
        contract = app.get("contract")
        if not isinstance(contract, dict):
            raise GovernanceError(f"{p}.contract must be an object")
        contract_fields = {
            "purchased_seats",
            "billing_cadence",
            "unit_price",
            "commitment",
            "renewal_date",
            "notice_days",
            "evidence_status",
        }
        if schema_version == SCOPE_CONTRACT_VERSION:
            contract_fields.update(
                {"charge_id", "service_period_start", "service_period_end"}
            )
        _keys(
            contract,
            contract_fields,
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
        if schema_version == SCOPE_CONTRACT_VERSION:
            charge_id = _text(contract.get("charge_id"), f"{p}.contract.charge_id")
            service_start = _date(
                contract.get("service_period_start"),
                f"{p}.contract.service_period_start",
            )
            service_end = _date(
                contract.get("service_period_end"),
                f"{p}.contract.service_period_end",
            )
            if service_end <= service_start:
                raise GovernanceError(
                    f"{p}.contract.service_period_end must be after service_period_start"
                )
            previous_app = seen_charges.get(charge_id)
            if previous_app is not None:
                raise GovernanceError(
                    f"duplicate charge identity {charge_id} in {previous_app} and {app_id}"
                )
            seen_charges[charge_id] = app_id
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
        supplied_invoice_amount = sum(
            (_decimal(row["amount"], "invoice amount") for row in app["invoices"]),
            Decimal("0"),
        )
        renewal = _date(contract["renewal_date"], "renewal_date")
        notice_deadline = renewal - timedelta(days=int(contract["notice_days"]))
        if renewal == as_of:
            notice_window_status = "renewal_due"
        elif notice_deadline >= as_of:
            notice_window_status = "open"
        else:
            notice_window_status = "closed"
        annual_capacity = (
            monthly_capacity * Decimal("12") if monthly_capacity is not None else None
        )
        eligible_capacity = (
            annual_capacity if notice_window_status == "open" else Decimal("0")
        )
        blocked_capacity = (
            annual_capacity
            if notice_window_status in {"closed", "renewal_due"}
            else Decimal("0")
        )
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
                "supplied_invoice_amount": float(supplied_invoice_amount),
                "invoice_count": len(app["invoices"]),
                "invoice_completeness": "not_asserted",
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
                "notice_window_status": notice_window_status,
                "contract_evidence_status": contract["evidence_status"],
                "reviewable_seats": reviewable,
                "estimated_monthly_reduction_capacity_at_renewal": (
                    float(monthly_capacity) if monthly_capacity is not None else None
                ),
                "estimated_annual_reduction_capacity_at_renewal": (
                    float(annual_capacity) if annual_capacity is not None else None
                ),
                "eligible_annual_reduction_capacity_at_renewal": (
                    float(eligible_capacity) if annual_capacity is not None else None
                ),
                "blocked_annual_reduction_capacity_at_renewal": (
                    float(blocked_capacity) if annual_capacity is not None else None
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
