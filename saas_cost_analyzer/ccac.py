"""CCAC producer for SaaS governance results."""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from . import __version__
from .governance import (
    SCOPE_CONTRACT_VERSION,
    AnalysisDeclaration,
    Bundle,
    GovernanceError,
    analyze_bundle,
    load_analysis,
    load_bundle,
)

CONTRACT = "ccac/1.0.0"
SCOPE_CONTRACT = "ccac/1.1.0"
LEGACY_DEMO_SHA256 = "06f6ad7a437a018c1492339f46262af6a46c26d43192fdae46b64ddb9f28f3f2"
SCOPE_DEMO_SHA256 = "efa458192923f5b4c274af8674fc54ce46196ac6b2c5125742716402fe942022"
SCOPE_ANALYSIS_SHA256 = (
    "18cd7fdf491195b48604f974ba0cb203d85a8598c4a6eb45d053da3d8d28fcf8"
)


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


def _add_saas_scope(
    result: dict[str, Any],
    bundle: Bundle,
    declaration: AnalysisDeclaration,
    evidence_id: str,
    analysis_evidence_id: str,
) -> None:
    declared = declaration.raw
    report_start = date.fromisoformat(declared["period"]["start"])
    report_end = date.fromisoformat(declared["period"]["end"])
    report_period = {**declared["period"]}
    currency = bundle.raw["currency"]
    if currency != "USD":
        raise GovernanceError("CCAC 1.1 canonical SaaS scope currently requires USD")
    components: list[dict[str, Any]] = []
    component_values: list[Decimal] = []
    component_ids: set[str] = set()
    for app in bundle.raw["applications"]:
        if app["billing_channel"] != "saas_invoice_or_entitlement":
            raise GovernanceError(
                f"{app['id']} cannot enter SaaS scope from billing channel {app['billing_channel']}"
            )
        if app["technology_scope"] != "saas":
            raise GovernanceError(f"{app['id']} is not classified as SaaS")
        contract = app["contract"]
        service_start = date.fromisoformat(contract["service_period_start"])
        service_end = date.fromisoformat(contract["service_period_end"])
        overlap_start = max(report_start, service_start)
        overlap_end = min(report_end, service_end)
        overlap_days = (overlap_end - overlap_start).days
        service_days = (service_end - service_start).days
        if overlap_days <= 0:
            raise GovernanceError(
                f"{app['id']} service period has no reporting-period overlap"
            )
        commitment = Decimal(str(contract["commitment"]))
        period_cost = (
            commitment * Decimal(overlap_days) / Decimal(service_days)
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        mid = f"metric.saas.{_slug(app['id'])}.period-net-cost"
        if mid in component_ids:
            raise GovernanceError(f"duplicate SaaS period-cost component: {mid}")
        component_ids.add(mid)
        component_values.append(period_cost)
        components.append(
            {
                "id": mid,
                "name": f"{app['name']} reporting-period net cost",
                "value": float(period_cost),
                "unknown_reason": None,
                "unit": "currency",
                "currency": currency,
                "basis": "calculated",
                "additivity": "additive",
                "period": report_period,
                "dimensions": {
                    "scope": "saas",
                    "application_id": app["id"],
                    "application": app["name"],
                    "vendor": app["vendor"],
                    "billing_provider": app["billing_provider"],
                    "billing_channel": app["billing_channel"],
                    "technology_scope": app["technology_scope"],
                    "service_period_start": service_start.isoformat(),
                    "service_period_end": service_end.isoformat(),
                    "overlap_start": overlap_start.isoformat(),
                    "overlap_end": overlap_end.isoformat(),
                    "commitment_amount": str(commitment),
                    "charge_id": contract["charge_id"],
                    "overlap_days": str(overlap_days),
                    "service_period_days": str(service_days),
                    "cost_basis": "net_cost",
                },
                "formula": "contract commitment * overlapping calendar days / service-period calendar days; round half up to currency minor unit",
                "input_metric_ids": [],
                "evidence_ids": [evidence_id, analysis_evidence_id],
                "quality_status": "valid",
            }
        )
    total = sum(component_values, Decimal("0.00"))
    canonical = {
        "id": "metric.tech-spend.scope.saas",
        "name": "Canonical SaaS scope spend",
        "value": float(total),
        "unknown_reason": None,
        "unit": "currency",
        "currency": currency,
        "basis": "calculated",
        "additivity": "additive",
        "period": report_period,
        "dimensions": {
            "scope": "saas",
            "billing_channel": "saas_invoice_or_entitlement",
            "cost_basis": "net_cost",
        },
        "formula": "sum rounded application reporting-period net-cost components exactly once",
        "input_metric_ids": [item["id"] for item in components],
        "evidence_ids": [evidence_id, analysis_evidence_id],
        "quality_status": "valid",
        "accounting_boundary": {
            "relationship": "canonical_scope_spend",
            "scope": "saas",
            "canonical_owner": "saas-cost-analyzer",
            "source_channel": "saas_invoice_or_entitlement",
            "cost_basis": "net_cost",
            "coverage": declared["completeness"]["status"],
            "total_eligible": declared["eligibility"]["total_eligible"],
            "eligibility_reason": declared["eligibility"]["explanation"],
            "currency_minor_unit": 0.01,
            "allocation_of_metric_id": None,
            "component_treatments": declared["component_treatments"],
            "inclusion_rules": [
                "Include explicitly classified saas_invoice_or_entitlement application commitments allocated to the reporting-period overlap."
            ],
            "exclusion_rules": [
                "Exclude direct-AI vendor and cloud-provider billing.",
                "Exclude invoice totals, annualized commitments, renewal exposure, and estimated renewal-reduction capacity.",
            ],
            "cross_scope_treatments": {
                "direct_ai_vendor": "excluded",
                "provider_billed_ai": "excluded",
            },
            "overlap": {
                "disposition": "resolved",
                "treatment": "Each application charge has one explicit half-open service period and enters the canonical scope once.",
            },
        },
    }
    result["metrics"].extend(components)
    result["metrics"].append(canonical)
    result["extensions"]["saas_cost_analyzer"]["period_cost_reconciliation"] = {
        "allocation_convention": declared["allocation_convention"],
        "rounding_convention": declared["rounding_convention"],
        "net_cost_definition": declared["net_cost_definition"],
        "component_metric_ids": canonical["input_metric_ids"],
        "component_total": float(total),
        "canonical_scope_total": float(total),
        "difference": 0.0,
        "status": "passed",
    }


def build_result(
    path: Path,
    *,
    mode: str,
    run_id: str | None = None,
    generated_at: str | None = None,
    contract_version: str = "1.0.0",
    analysis_path: Path | None = None,
    compatibility_demo: bool = False,
    bundled_demo: bool = False,
) -> dict[str, Any]:
    if mode not in {"illustrative", "real"}:
        raise GovernanceError("mode must be illustrative or real")
    bundle: Bundle = load_bundle(path)
    if contract_version not in {"1.0.0", "1.1.0"}:
        raise GovernanceError("contract_version must be 1.0.0 or 1.1.0")
    declaration = None
    if contract_version == "1.1.0":
        if bundle.raw["schema_version"] != SCOPE_CONTRACT_VERSION:
            raise GovernanceError(
                f"CCAC 1.1 requires governance bundle {SCOPE_CONTRACT_VERSION}"
            )
        if analysis_path is None:
            raise GovernanceError("CCAC 1.1 requires --analysis")
        declaration = load_analysis(analysis_path)
        if declaration.raw["mode"] != mode:
            raise GovernanceError("analysis mode contradicts requested mode")
        if (
            declaration.raw["completeness"]["status"] == "complete"
            or declaration.raw["eligibility"]["total_eligible"] is True
        ) and not (
            bundled_demo
            and bundle.content_hash == SCOPE_DEMO_SHA256
            and declaration.content_hash == SCOPE_ANALYSIS_SHA256
        ):
            raise GovernanceError(
                "complete, total-eligible SaaS coverage is restricted to the exact bundled illustrative scenario"
            )
    if bundle.raw["mode"] != mode:
        raise GovernanceError(
            f"bundle mode {bundle.raw['mode']!r} cannot be used for {mode!r} analysis"
        )
    analysis = analyze_bundle(bundle)
    legacy_demo = (
        compatibility_demo
        and contract_version == "1.0.0"
        and mode == "illustrative"
        and bundle.content_hash == LEGACY_DEMO_SHA256
    )
    producer_version = "0.2.0" if legacy_demo else __version__
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
    if declaration is not None:
        period = {**declaration.raw["period"]}
    source_id = "source.saas-governance.bundle"
    evidence_id = "evidence.saas-governance.bundle"
    inputs = [
        {
            "id": source_id,
            "source_type": "saas_governance_bundle",
            "source_version": analysis["schema_version"],
            "adapter_version": producer_version,
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
    analysis_evidence_id = "evidence.saas-governance.analysis"
    if declaration is not None:
        analysis_source_id = "source.saas-governance.analysis"
        inputs.append(
            {
                "id": analysis_source_id,
                "source_type": "saas_cost_analysis_declaration",
                "source_version": declaration.raw["schema_version"],
                "adapter_version": producer_version,
                "content_sha256": declaration.content_hash,
                "access": (
                    "illustrative_fixture"
                    if mode == "illustrative"
                    else "local_read_only"
                ),
                "data_classification": (
                    "public_illustrative"
                    if mode == "illustrative"
                    else "customer_confidential"
                ),
                "lossy_mapping": False,
                "mapping_notes": [
                    "Reporting period, accounting basis, completeness, allocation, rounding, and eligibility remain separate from contract and invoice evidence."
                ],
            }
        )
        evidence.append(
            {
                "id": analysis_evidence_id,
                "kind": "contract",
                "source_ids": [analysis_source_id],
                "description": declaration.raw["completeness"]["description"],
                "locator": "json:period,completeness,eligibility",
                "observed_at": generated,
                "content_sha256": declaration.content_hash,
            }
        )
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
            "contract_evidence_status": app["contract_evidence_status"],
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
                "Supplied invoice amount",
                app["supplied_invoice_amount"],
                "currency",
                "observed",
                invoice_period,
                dims,
                evidence_id,
                currency=analysis["currency"],
                formula="sum supplied non-overlapping invoice rows only; invoice ledger completeness is not asserted",
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
        if app["notice_window_status"] in {"closed", "renewal_due"}:
            fid = f"finding.saas.{slug}.notice-window-{app['notice_window_status']}"
            finding_ids.append(fid)
            timing = (
                f"The contractual notice deadline {app['notice_deadline']} has passed"
                if app["notice_window_status"] == "closed"
                else f"The renewal date {app['renewal_date']} is due as of the analysis date"
            )
            findings.append(
                {
                    "id": fid,
                    "finding_type": "saas_governance",
                    "title": f"Renewal notice window is {app['notice_window_status']} for {app['name']}",
                    "description": (
                        f"{timing}. Modeled review capacity remains informational and no actionable opportunity is emitted for this renewal. "
                        f"Contract terms are {app['contract_evidence_status']}."
                    ),
                    "severity": "high",
                    "status": "open",
                    "metric_ids": [cap_id],
                    "evidence_ids": [evidence_id],
                    "first_observed_at": generated,
                    "last_observed_at": generated,
                }
            )
        if (
            app["notice_window_status"] == "open"
            and app["reviewable_seats"] is not None
            and app["reviewable_seats"] > 0
        ):
            annual = app["eligible_annual_reduction_capacity_at_renewal"]
            opportunities.append(
                {
                    "id": f"opportunity.saas.{slug}.renewal-seat-review",
                    "producer": {
                        "name": "saas-cost-analyzer",
                        "version": producer_version,
                    },
                    "opportunity_type": "saas_reclaim",
                    "title": f"Review {app['reviewable_seats']} {app['name']} seats by the open notice deadline",
                    "scope": {
                        "application_id": app["id"],
                        "renewal_date": app["renewal_date"],
                        "notice_deadline": app["notice_deadline"],
                        "notice_window_status": "open",
                        "contract_evidence_status": app["contract_evidence_status"],
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
                    "confidence": (
                        "medium"
                        if app["contract_evidence_status"] == "verified_document"
                        else "low"
                    ),
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
                            f"Confirm the notice window remains open through {app['notice_deadline']} for the stated renewal.",
                            "Confirm the users and entitlements with the application owner.",
                            "Confirm contractual reduction rights and notice deadline.",
                            "Approve the renewal change outside this tool.",
                            "Verify realized savings against later invoices.",
                        ],
                    },
                    "status": "identified",
                }
            )
    modeled_capacity = round(
        sum(
            app["estimated_annual_reduction_capacity_at_renewal"] or 0
            for app in analysis["applications"]
        ),
        6,
    )
    eligible_capacity = round(
        sum(
            app["eligible_annual_reduction_capacity_at_renewal"] or 0
            for app in analysis["applications"]
        ),
        6,
    )
    blocked_capacity = round(
        sum(
            app["blocked_annual_reduction_capacity_at_renewal"] or 0
            for app in analysis["applications"]
        ),
        6,
    )
    opportunity_total = round(
        sum(item["estimate"]["expected"] for item in opportunities), 6
    )
    eligible_difference = round(eligible_capacity - opportunity_total, 6)
    capacity_balance_difference = round(
        modeled_capacity - eligible_capacity - blocked_capacity, 6
    )
    assigned_seats = sum(app["assigned_seats"] for app in analysis["applications"])
    classified_assigned_seats = sum(
        app["active_seats"] + app["inactive_seats"] + app["unknown_activity_seats"]
        for app in analysis["applications"]
    )
    reconciliation_status = (
        "passed"
        if eligible_difference == 0
        and capacity_balance_difference == 0
        and assigned_seats == classified_assigned_seats
        else "failed"
    )
    result = {
        "contract": CONTRACT if contract_version == "1.0.0" else SCOPE_CONTRACT,
        "document_type": "tool_result",
        "producer": {"name": "saas-cost-analyzer", "version": producer_version},
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
                    "assigned_seats": assigned_seats,
                    "classified_assigned_seats": classified_assigned_seats,
                    "supplied_invoice_amount": sum(
                        app["supplied_invoice_amount"]
                        for app in analysis["applications"]
                    ),
                    "invoice_completeness": "not_asserted",
                    "normalized_monthly_commitment": sum(
                        app["monthly_commitment"] for app in analysis["applications"]
                    ),
                    "annualized_commitment": sum(
                        app["annualized_commitment"] for app in analysis["applications"]
                    ),
                    "total_modeled_review_capacity": modeled_capacity,
                    "open_notice_window_eligible_capacity": eligible_capacity,
                    "blocked_notice_window_capacity": blocked_capacity,
                    "opportunity_expected_total": opportunity_total,
                    "eligible_capacity_to_opportunity_difference": eligible_difference,
                    "modeled_capacity_balance_difference": capacity_balance_difference,
                    "status": reconciliation_status,
                },
                "accounting_boundary": "Supplied invoice rows are observed but completeness is not asserted; commitment is contractual; renewal capacity is estimated and non-additive until verified after implementation.",
            }
        },
    }
    if declaration is not None:
        _add_saas_scope(
            result,
            bundle,
            declaration,
            evidence_id,
            analysis_evidence_id,
        )
    return result
