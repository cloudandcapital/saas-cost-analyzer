"""CCAC producer for SaaS governance results."""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .governance import Bundle, GovernanceError, analyze_bundle, load_bundle

CONTRACT = "ccac/1.0.0"


def _timestamp(value: str | None) -> str:
    try:
        parsed = (
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            if value
            else datetime.now(timezone.utc)
        )
    except ValueError as exc:
        raise GovernanceError("generated_at must be RFC3339") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (
        parsed.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _slug(value: str) -> str:
    clean = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "application"
    return f"{clean[:45].rstrip('-')}-{hashlib.sha256(value.encode()).hexdigest()[:8]}"


def _metric(
    mid: str,
    name: str,
    value: float | int | None,
    unit: str,
    basis: str,
    period: dict[str, str],
    dims: dict[str, Any],
    evidence: str,
    *,
    currency: str | None = None,
    formula: str | None = None,
    unknown_reason: str | None = None,
    additivity: str = "additive",
) -> dict[str, Any]:
    return {
        "id": mid,
        "name": name,
        "value": value,
        "unknown_reason": unknown_reason,
        "unit": unit,
        "currency": currency,
        "basis": basis,
        "additivity": additivity,
        "period": period,
        "dimensions": dims,
        "formula": formula,
        "input_metric_ids": [],
        "evidence_ids": [evidence],
        "quality_status": "partial" if value is None else "valid",
    }


def build_result(
    path: Path, *, mode: str, run_id: str | None = None, generated_at: str | None = None
) -> dict[str, Any]:
    if mode not in {"illustrative", "real"}:
        raise GovernanceError("mode must be illustrative or real")
    bundle: Bundle = load_bundle(path)
    if bundle.raw["mode"] != mode:
        raise GovernanceError(
            f"bundle mode {bundle.raw['mode']!r} cannot be used for {mode!r} analysis"
        )
    analysis = analyze_bundle(bundle)
    try:
        rid = str(uuid.UUID(run_id)) if run_id else str(uuid.uuid4())
    except (ValueError, TypeError) as exc:
        raise GovernanceError("run_id must be a UUID") from exc
    generated = _timestamp(generated_at)
    as_of = analysis["as_of"]
    snapshot_period = {
        "start": as_of,
        "end": (date.fromisoformat(as_of) + timedelta(days=1)).isoformat(),
        "timezone": "UTC",
    }
    invoice_rows = [
        invoice for app in bundle.raw["applications"] for invoice in app["invoices"]
    ]
    overall_start = min([as_of] + [row["period_start"] for row in invoice_rows])
    overall_end = max(
        [snapshot_period["end"]] + [row["period_end"] for row in invoice_rows]
    )
    period = {"start": overall_start, "end": overall_end, "timezone": "UTC"}
    source_id = "source.saas-governance.bundle"
    evidence_id = "evidence.saas-governance.bundle"
    inputs = [
        {
            "id": source_id,
            "source_type": "saas_governance_bundle",
            "source_version": analysis["schema_version"],
            "adapter_version": __version__,
            "content_sha256": bundle.content_hash,
            "access": (
                "illustrative_fixture" if mode == "illustrative" else "local_read_only"
            ),
            "data_classification": (
                "public_illustrative"
                if mode == "illustrative"
                else "customer_confidential"
            ),
            "lossy_mapping": False,
            "mapping_notes": [
                "Contracts, invoices, assignment rosters, and activity observations remain separate evidence classes."
            ],
        }
    ]
    evidence = [
        {
            "id": evidence_id,
            "kind": "other",
            "source_ids": [source_id],
            "description": "Strict SaaS governance bundle containing separately declared evidence ledgers.",
            "locator": "json:applications",
            "observed_at": generated,
            "content_sha256": bundle.content_hash,
        }
    ]
    metrics: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    opportunities: list[dict[str, Any]] = []
    quality_issues: list[dict[str, Any]] = []
    for app in analysis["applications"]:
        raw_app = next(
            item for item in bundle.raw["applications"] if item["id"] == app["id"]
        )
        invoice_period = {
            "start": min(
                (row["period_start"] for row in raw_app["invoices"]), default=as_of
            ),
            "end": max(
                (row["period_end"] for row in raw_app["invoices"]),
                default=snapshot_period["end"],
            ),
            "timezone": "UTC",
        }
        slug = _slug(app["id"])
        prefix = f"metric.saas.{slug}"
        dims = {
            "scope": "saas_application",
            "application_id": app["id"],
            "application": app["name"],
            "owner": app["owner"],
            "billing_cadence": app["billing_cadence"],
        }
        values = [
            ("purchased-seats", "Purchased seats", app["purchased_seats"]),
            ("assigned-seats", "Assigned seats", app["assigned_seats"]),
            ("active-seats", "Active seats", app["active_seats"]),
            ("inactive-seats", "Inactive seats", app["inactive_seats"]),
            (
                "unknown-activity-seats",
                "Seats with unknown activity",
                app["unknown_activity_seats"],
            ),
        ]
        for suffix, name, value in values:
            metrics.append(
                _metric(
                    f"{prefix}.{suffix}",
                    name,
                    value,
                    "seats",
                    (
                        "observed"
                        if suffix in {"purchased-seats", "assigned-seats"}
                        else "calculated"
                    ),
                    snapshot_period,
                    dims,
                    evidence_id,
                    formula=(
                        None
                        if suffix in {"purchased-seats", "assigned-seats"}
                        else "classify assignments using declared inactivity threshold and activity evidence freshness"
                    ),
                )
            )
        if app["unassigned_seats"] is None:
            metrics.append(
                _metric(
                    f"{prefix}.unassigned-seats",
                    "Unassigned seats",
                    None,
                    "seats",
                    "unknown",
                    snapshot_period,
                    dims,
                    evidence_id,
                    unknown_reason="assignment roster is not declared complete",
                )
            )
        else:
            metrics.append(
                _metric(
                    f"{prefix}.unassigned-seats",
                    "Unassigned seats",
                    app["unassigned_seats"],
                    "seats",
                    "calculated",
                    snapshot_period,
                    dims,
                    evidence_id,
                    formula="purchased_seats - assigned_seats",
                )
            )
        utilization = app["utilization_percentage"]
        metrics.append(
            _metric(
                f"{prefix}.utilization-percentage",
                "Active-seat utilization percentage",
                utilization,
                "percent",
                "calculated" if utilization is not None else "unknown",
                snapshot_period,
                dims,
                evidence_id,
                formula=(
                    "active_seats / purchased_seats * 100"
                    if utilization is not None
                    else None
                ),
                unknown_reason=(
                    None
                    if utilization is not None
                    else "utilization requires purchased seats greater than zero, a complete assignment roster, fresh activity evidence, and no unknown activity"
                ),
            )
        )
        metrics.append(
            _metric(
                f"{prefix}.invoice-cost",
                "Observed invoice cost",
                app["invoice_total"],
                "currency",
                "observed",
                invoice_period,
                dims,
                evidence_id,
                currency=analysis["currency"],
            )
        )
        metrics.append(
            _metric(
                f"{prefix}.unit-price",
                "Contract unit price per purchased seat and billing cadence",
                app["unit_price"],
                "currency_per_seat_per_billing_cadence",
                "observed",
                snapshot_period,
                dims,
                evidence_id,
                currency=analysis["currency"],
                additivity="non_additive",
            )
        )
        metrics.append(
            _metric(
                f"{prefix}.commitment",
                "Contract commitment per billing cadence",
                app["commitment"],
                "currency",
                "observed",
                snapshot_period,
                dims,
                evidence_id,
                currency=analysis["currency"],
                additivity="non_additive",
            )
        )
        metrics.append(
            _metric(
                f"{prefix}.monthly-commitment",
                "Normalized monthly contractual commitment",
                app["monthly_commitment"],
                "currency",
                "calculated",
                snapshot_period,
                dims,
                evidence_id,
                currency=analysis["currency"],
                formula="contract commitment / billing cadence months",
                additivity="non_additive",
            )
        )
        metrics.append(
            _metric(
                f"{prefix}.annualized-commitment",
                "Annualized contractual commitment",
                app["annualized_commitment"],
                "currency",
                "calculated",
                snapshot_period,
                dims,
                evidence_id,
                currency=analysis["currency"],
                formula="normalized monthly commitment * 12",
                additivity="non_additive",
            )
        )
        cap_id = f"{prefix}.annual-reduction-capacity"
        capacity = app["estimated_annual_reduction_capacity_at_renewal"]
        if capacity is None:
            metrics.append(
                _metric(
                    cap_id,
                    "Estimated annual reduction capacity at renewal",
                    None,
                    "currency",
                    "unknown",
                    snapshot_period,
                    dims,
                    evidence_id,
                    currency=analysis["currency"],
                    unknown_reason="assignment completeness or activity freshness gate failed",
                    additivity="non_additive",
                )
            )
        else:
            metrics.append(
                _metric(
                    cap_id,
                    "Estimated annual reduction capacity at renewal",
                    capacity,
                    "currency",
                    "estimated",
                    snapshot_period,
                    dims,
                    evidence_id,
                    currency=analysis["currency"],
                    formula="reviewable_seats * normalized monthly unit price * 12",
                    additivity="non_additive",
                )
            )
        finding_ids = []
        if app["owner"].lower() == "unattributed":
            quality_issues.append(
                {
                    "code": f"quality.saas.{slug}.owner",
                    "severity": "warning",
                    "message": f"{app['name']} has unattributed ownership.",
                    "source_id": source_id,
                    "field": "applications.owner",
                    "row_count": 1,
                }
            )
            fid = f"finding.saas.{slug}.owner"
            finding_ids.append(fid)
            findings.append(
                {
                    "id": fid,
                    "finding_type": "allocation",
                    "title": f"Ownership is unattributed for {app['name']}",
                    "description": "Assign an accountable owner before renewal review; unattributed cost is not savings.",
                    "severity": "medium",
                    "status": "open",
                    "metric_ids": [f"{prefix}.annualized-commitment"],
                    "evidence_ids": [evidence_id],
                    "first_observed_at": generated,
                    "last_observed_at": generated,
                }
            )
        if not app["activity_evidence_fresh"] or app["unknown_activity_seats"]:
            quality_issues.append(
                {
                    "code": f"quality.saas.{slug}.activity",
                    "severity": "warning",
                    "message": f"{app['name']} has stale or unknown activity evidence.",
                    "source_id": source_id,
                    "field": "applications.assignments.last_activity_at",
                    "row_count": app["unknown_activity_seats"],
                }
            )
            fid = f"finding.saas.{slug}.activity-evidence"
            finding_ids.append(fid)
            findings.append(
                {
                    "id": fid,
                    "finding_type": "data_quality",
                    "title": f"Activity evidence needs review for {app['name']}",
                    "description": "Unknown or stale activity is not classified as unused and creates no savings claim.",
                    "severity": "medium",
                    "status": "open",
                    "metric_ids": [f"{prefix}.unknown-activity-seats"],
                    "evidence_ids": [evidence_id],
                    "first_observed_at": generated,
                    "last_observed_at": generated,
                }
            )
        if not app["assignment_roster_complete"]:
            quality_issues.append(
                {
                    "code": f"quality.saas.{slug}.roster",
                    "severity": "warning",
                    "message": f"{app['name']} assignment roster is not declared complete.",
                    "source_id": source_id,
                    "field": "applications.assignment_roster_complete",
                    "row_count": None,
                }
            )
            fid = f"finding.saas.{slug}.assignment-roster"
            finding_ids.append(fid)
            findings.append(
                {
                    "id": fid,
                    "finding_type": "data_quality",
                    "title": f"Assignment roster is incomplete for {app['name']}",
                    "description": "Unassigned seats cannot be calculated until roster completeness is established.",
                    "severity": "medium",
                    "status": "open",
                    "metric_ids": [f"{prefix}.unassigned-seats"],
                    "evidence_ids": [evidence_id],
                    "first_observed_at": generated,
                    "last_observed_at": generated,
                }
            )
        if app["reviewable_seats"] is not None and app["reviewable_seats"] > 0:
            annual = app["estimated_annual_reduction_capacity_at_renewal"]
            opportunities.append(
                {
                    "id": f"opportunity.saas.{slug}.renewal-seat-review",
                    "producer": {"name": "saas-cost-analyzer", "version": __version__},
                    "opportunity_type": "saas_reclaim",
                    "title": f"Review {app['reviewable_seats']} {app['name']} seats before renewal",
                    "scope": {
                        "application_id": app["id"],
                        "renewal_date": app["renewal_date"],
                        "notice_deadline": app["notice_deadline"],
                        "reviewable_seats": app["reviewable_seats"],
                    },
                    "estimate": {
                        "basis": "estimated",
                        "period": "annual",
                        "low": 0,
                        "expected": annual,
                        "high": annual,
                        "currency": analysis["currency"],
                        "formula": "0 lower bound; modeled upper capacity = reviewable seats * normalized monthly unit price * 12; realization only after approved renewal change and invoice verification",
                    },
                    "confidence": "medium",
                    "evidence_ids": [evidence_id],
                    "related_finding_ids": finding_ids,
                    "related_opportunity_ids": [],
                    "overlap": {
                        "disposition": "none_known",
                        "group_id": f"overlap.saas.{slug}.renewal-seat-review",
                        "reason": "Inactive assigned and unassigned seat populations are disjoint; this opportunity combines them once for this application renewal.",
                    },
                    "review": {
                        "required": True,
                        "approval_required": True,
                        "rollback_plan_required": True,
                        "verification_required": True,
                        "non_mutating_review_steps": [
                            "Confirm the users and entitlements with the application owner.",
                            "Confirm contractual reduction rights and notice deadline.",
                            "Approve the renewal change outside this tool.",
                            "Verify realized savings against later invoices.",
                        ],
                    },
                    "status": "identified",
                }
            )
    return {
        "contract": CONTRACT,
        "document_type": "tool_result",
        "producer": {"name": "saas-cost-analyzer", "version": __version__},
        "run_id": rid,
        "generated_at": generated,
        "mode": mode,
        "period": period,
        "inputs": inputs,
        "quality": {
            "status": "partial" if quality_issues else "valid",
            "issues": quality_issues,
        },
        "metrics": metrics,
        "findings": findings,
        "opportunities": opportunities,
        "evidence": evidence,
        "extensions": {
            "saas_cost_analyzer": {
                "contract": analysis["schema_version"],
                "analysis": analysis,
                "reconciliation": {
                    "purchased_seats": sum(
                        app["purchased_seats"] for app in analysis["applications"]
                    ),
                    "assigned_seats": sum(
                        app["assigned_seats"] for app in analysis["applications"]
                    ),
                    "classified_assigned_seats": sum(
                        app["active_seats"]
                        + app["inactive_seats"]
                        + app["unknown_activity_seats"]
                        for app in analysis["applications"]
                    ),
                    "observed_invoice_spend": sum(
                        app["invoice_total"] for app in analysis["applications"]
                    ),
                    "normalized_monthly_commitment": sum(
                        app["monthly_commitment"] for app in analysis["applications"]
                    ),
                    "annualized_commitment": sum(
                        app["annualized_commitment"] for app in analysis["applications"]
                    ),
                    "estimated_annual_reduction_capacity": sum(
                        app["estimated_annual_reduction_capacity_at_renewal"] or 0
                        for app in analysis["applications"]
                    ),
                    "opportunity_expected_total": sum(
                        item["estimate"]["expected"] for item in opportunities
                    ),
                    "opportunity_difference": sum(
                        app["estimated_annual_reduction_capacity_at_renewal"] or 0
                        for app in analysis["applications"]
                    )
                    - sum(item["estimate"]["expected"] for item in opportunities),
                    "status": "passed",
                },
                "accounting_boundary": "Invoice cost is observed; commitment is contractual; renewal capacity is estimated and non-additive until verified after implementation.",
            }
        },
    }
