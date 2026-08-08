from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from saas_cost_analyzer.analysis.engine import flag_unused, group_by_product
from saas_cost_analyzer.ccac import build_result
from saas_cost_analyzer.cli import cli
from saas_cost_analyzer.governance import GovernanceError, analyze_bundle, load_bundle
from saas_cost_analyzer.providers.detector import FocusRecord

DEMO = Path(__file__).parents[1] / "examples" / "illustrative-governance-v2.json"


def _bundle_value() -> dict:
    return json.loads(DEMO.read_text())


def _write_bundle(tmp_path: Path, value: dict, name: str = "bundle.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(value))
    return path


def test_demo_separates_inactive_unassigned_and_unknown():
    result = analyze_bundle(load_bundle(DEMO))
    crm = result["applications"][0]
    assert (
        crm["active_seats"],
        crm["inactive_seats"],
        crm["unknown_activity_seats"],
        crm["unassigned_seats"],
    ) == (3, 1, 0, 2)
    assert crm["utilization_percentage"] == 50
    assert crm["billing_cadence"] == "annual"
    assert crm["unit_price_monthly"] == 120
    assert crm["monthly_commitment"] == 720
    assert crm["annualized_commitment"] == 8640
    assert crm["reviewable_seats"] == 3
    assert crm["estimated_annual_reduction_capacity_at_renewal"] == 4320
    design = result["applications"][1]
    assert design["unassigned_seats"] is None
    assert design["reviewable_seats"] is None
    assert design["estimated_annual_reduction_capacity_at_renewal"] is None


def test_stale_activity_never_creates_inactive_seats():
    result = analyze_bundle(load_bundle(DEMO))["applications"][1]
    assert result["activity_evidence_fresh"] is False
    assert result["inactive_seats"] == 0
    assert result["unknown_activity_seats"] == 2


def test_ccac_is_deterministic_and_validatable():
    result = build_result(
        DEMO,
        mode="illustrative",
        run_id="11111111-1111-4111-8111-111111111111",
        generated_at="2026-08-04T12:00:00Z",
    )
    assert result["contract"] == "ccac/1.0.0"
    assert result["mode"] == "illustrative"
    assert result["quality"]["status"] == "partial"
    assert result["inputs"][0]["data_classification"] == "public_illustrative"
    assert len(result["opportunities"]) == 1
    opportunity = result["opportunities"][0]
    assert opportunity["estimate"]["low"] == 0
    assert opportunity["confidence"] == "low"
    assert opportunity["scope"]["notice_window_status"] == "open"
    assert opportunity["scope"]["contract_evidence_status"] == "user_supplied"
    assert opportunity["review"]["approval_required"] is True
    assert "verified savings" not in opportunity["title"].lower()
    assert any(
        metric["value"] is None and metric["basis"] == "unknown"
        for metric in result["metrics"]
    )
    reconciliation = result["extensions"]["saas_cost_analyzer"]["reconciliation"]
    assert reconciliation["supplied_invoice_amount"] == 9690
    assert reconciliation["invoice_completeness"] == "not_asserted"
    assert reconciliation["normalized_monthly_commitment"] == 1070
    assert reconciliation["annualized_commitment"] == 12840
    assert reconciliation["total_modeled_review_capacity"] == 4320
    assert reconciliation["open_notice_window_eligible_capacity"] == 4320
    assert reconciliation["blocked_notice_window_capacity"] == 0
    assert reconciliation["opportunity_expected_total"] == 4320
    assert reconciliation["eligible_capacity_to_opportunity_difference"] == 0
    assert reconciliation["modeled_capacity_balance_difference"] == 0
    assert reconciliation["assigned_seats"] == 6
    assert reconciliation["classified_assigned_seats"] == 6
    assert opportunity["overlap"]["group_id"].startswith("overlap.saas.crm-")


def test_cli_governance_and_ccac():
    runner = CliRunner()
    analyzed = runner.invoke(
        cli, ["governance", "--input", str(DEMO), "--format", "json"]
    )
    assert analyzed.exit_code == 0
    assert json.loads(analyzed.output)["schema_version"] == "saas-cost-analyzer/2.0"
    ccac = runner.invoke(cli, ["ccac", "--input", str(DEMO), "--mode", "illustrative"])
    assert ccac.exit_code == 0
    assert json.loads(ccac.output)["producer"]["version"] == "0.3.0"


def test_packaged_demo_cli_requires_no_repo_fixture():
    first = CliRunner().invoke(cli, ["ccac", "--demo"])
    second = CliRunner().invoke(cli, ["ccac", "--demo"])
    assert first.exit_code == 0
    assert second.exit_code == 0
    assert first.output == second.output
    payload = json.loads(first.output)
    assert payload["mode"] == "illustrative"
    assert payload["run_id"] == "123e4567-e89b-12d3-a456-426614174040"
    assert payload["generated_at"] == "2026-08-04T12:20:00Z"


def test_ccac_requires_exactly_one_input_mode():
    result = CliRunner().invoke(cli, ["ccac", "--mode", "real"])
    assert result.exit_code == 4
    assert "exactly one" in result.output


def test_rejects_duplicate_users(tmp_path: Path):
    value = json.loads(DEMO.read_text())
    value["applications"][0]["assignments"].append(
        value["applications"][0]["assignments"][0]
    )
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(value))
    with pytest.raises(GovernanceError, match="duplicate assigned user"):
        load_bundle(path)


def test_rejects_assignments_above_entitlement(tmp_path: Path):
    value = _bundle_value()
    value["applications"][0]["contract"]["purchased_seats"] = 1
    value["applications"][0]["contract"]["commitment"] = 1440
    path = _write_bundle(tmp_path, value)
    with pytest.raises(GovernanceError, match="assignments exceed"):
        load_bundle(path)


def test_rejects_unknown_contract_fields(tmp_path: Path):
    value = json.loads(DEMO.read_text())
    value["applications"][0]["contract"]["magic_savings_rate"] = 0.15
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(value))
    with pytest.raises(GovernanceError, match="unknown fields"):
        load_bundle(path)


@pytest.mark.parametrize(
    "cadence,unit_price,commitment",
    [
        ("monthly", 120, 720),
        ("quarterly", 360, 2160),
        ("annual", 1440, 8640),
    ],
)
def test_billing_cadences_normalize_and_reconcile_exactly(
    tmp_path: Path, cadence: str, unit_price: int, commitment: int
):
    value = _bundle_value()
    contract = value["applications"][0]["contract"]
    contract["billing_cadence"] = cadence
    contract["unit_price"] = unit_price
    contract["commitment"] = commitment
    result = analyze_bundle(load_bundle(_write_bundle(tmp_path, value)))[
        "applications"
    ][0]
    assert result["unit_price_monthly"] == 120
    assert result["monthly_commitment"] == 720
    assert result["annualized_commitment"] == 8640
    assert result["estimated_annual_reduction_capacity_at_renewal"] == 4320


def test_rejects_unsupported_cadence_and_inconsistent_commitment(tmp_path: Path):
    value = _bundle_value()
    value["applications"][0]["contract"]["billing_cadence"] = "weekly"
    with pytest.raises(GovernanceError, match="billing_cadence"):
        load_bundle(_write_bundle(tmp_path, value, "cadence.json"))
    value = _bundle_value()
    value["applications"][0]["contract"]["commitment"] = 1
    with pytest.raises(GovernanceError, match=r"purchased_seats \* unit_price"):
        load_bundle(_write_bundle(tmp_path, value, "commitment.json"))


def test_inactivity_threshold_is_configurable_and_boundary_is_active(tmp_path: Path):
    value = _bundle_value()
    value["inactivity_days"] = 60
    app = value["applications"][0]
    app["assignments"] = [
        {"user_id": "boundary", "last_activity_at": "2026-06-01"},
        {"user_id": "inactive", "last_activity_at": "2026-05-31"},
    ]
    result = analyze_bundle(load_bundle(_write_bundle(tmp_path, value)))[
        "applications"
    ][0]
    assert result["active_seats"] == 1
    assert result["inactive_seats"] == 1
    assert result["unassigned_seats"] == 4


def test_renewal_boundary_is_inclusive_and_past_date_rejected(tmp_path: Path):
    value = _bundle_value()
    value["applications"][0]["contract"]["renewal_date"] = value["as_of"]
    result = analyze_bundle(
        load_bundle(_write_bundle(tmp_path, value, "boundary.json"))
    )
    assert result["applications"][0]["days_to_notice_deadline"] == -60
    value["applications"][0]["contract"]["renewal_date"] = "2026-07-30"
    with pytest.raises(GovernanceError, match="before as_of"):
        load_bundle(_write_bundle(tmp_path, value, "past.json"))


def test_open_notice_window_before_deadline_emits_opportunity():
    result = build_result(DEMO, mode="illustrative")
    crm = result["extensions"]["saas_cost_analyzer"]["analysis"]["applications"][0]
    assert crm["notice_window_status"] == "open"
    assert crm["days_to_notice_deadline"] > 0
    assert len(result["opportunities"]) == 1


def test_notice_deadline_is_inclusive_for_opportunity(tmp_path: Path):
    value = _bundle_value()
    value["applications"][0]["contract"]["renewal_date"] = "2026-09-29"
    result = build_result(_write_bundle(tmp_path, value), mode="illustrative")
    crm = result["extensions"]["saas_cost_analyzer"]["analysis"]["applications"][0]
    assert crm["notice_deadline"] == value["as_of"]
    assert crm["notice_window_status"] == "open"
    assert any(
        opportunity["scope"]["application_id"] == "crm"
        for opportunity in result["opportunities"]
    )


@pytest.mark.parametrize(
    "renewal_date,expected_status",
    [("2026-08-30", "closed"), ("2026-07-31", "renewal_due")],
)
def test_blocked_notice_windows_emit_finding_not_opportunity(
    tmp_path: Path, renewal_date: str, expected_status: str
):
    value = _bundle_value()
    value["applications"][0]["contract"]["renewal_date"] = renewal_date
    result = build_result(_write_bundle(tmp_path, value), mode="illustrative")
    crm = result["extensions"]["saas_cost_analyzer"]["analysis"]["applications"][0]
    assert crm["notice_window_status"] == expected_status
    assert crm["estimated_annual_reduction_capacity_at_renewal"] == 4320
    assert crm["eligible_annual_reduction_capacity_at_renewal"] == 0
    assert crm["blocked_annual_reduction_capacity_at_renewal"] == 4320
    assert not any(
        opportunity["scope"]["application_id"] == "crm"
        for opportunity in result["opportunities"]
    )
    assert any(
        expected_status in finding["id"]
        and "no actionable opportunity" in finding["description"]
        for finding in result["findings"]
    )
    reconciliation = result["extensions"]["saas_cost_analyzer"]["reconciliation"]
    assert reconciliation["total_modeled_review_capacity"] == 4320
    assert reconciliation["open_notice_window_eligible_capacity"] == 0
    assert reconciliation["blocked_notice_window_capacity"] == 4320
    assert reconciliation["opportunity_expected_total"] == 0
    assert reconciliation["eligible_capacity_to_opportunity_difference"] == 0
    assert reconciliation["modeled_capacity_balance_difference"] == 0
    assert reconciliation["status"] == "passed"


@pytest.mark.parametrize(
    "evidence_status,expected_confidence",
    [("user_supplied", "low"), ("verified_document", "medium")],
)
def test_contract_evidence_status_is_preserved_and_controls_confidence(
    tmp_path: Path, evidence_status: str, expected_confidence: str
):
    value = _bundle_value()
    value["applications"][0]["contract"]["evidence_status"] = evidence_status
    result = build_result(_write_bundle(tmp_path, value), mode="illustrative")
    crm = result["extensions"]["saas_cost_analyzer"]["analysis"]["applications"][0]
    assert crm["contract_evidence_status"] == evidence_status
    metric = next(
        metric
        for metric in result["metrics"]
        if metric["id"].endswith(".annual-reduction-capacity")
        and metric["dimensions"]["application_id"] == "crm"
    )
    assert metric["dimensions"]["contract_evidence_status"] == evidence_status
    opportunity = next(
        opportunity
        for opportunity in result["opportunities"]
        if opportunity["scope"]["application_id"] == "crm"
    )
    assert opportunity["scope"]["contract_evidence_status"] == evidence_status
    assert opportunity["confidence"] == expected_confidence

    value["applications"][0]["contract"]["renewal_date"] = "2026-08-30"
    blocked_result = build_result(
        _write_bundle(tmp_path, value, f"{evidence_status}-blocked.json"),
        mode="illustrative",
    )
    timing_finding = next(
        finding
        for finding in blocked_result["findings"]
        if "notice-window-closed" in finding["id"]
    )
    assert evidence_status in timing_finding["description"]


def test_supplied_invoice_metric_does_not_claim_ledger_completeness():
    result = build_result(DEMO, mode="illustrative")
    metric = next(
        metric
        for metric in result["metrics"]
        if metric["dimensions"]["application_id"] == "crm"
        and metric["id"].endswith(".invoice-cost")
    )
    assert metric["name"] == "Supplied invoice amount"
    assert "supplied non-overlapping invoice rows only" in metric["formula"]
    assert "completeness is not asserted" in metric["formula"]
    assert metric["period"] == {
        "start": "2026-01-01",
        "end": "2027-01-01",
        "timezone": "UTC",
    }
    extension = result["extensions"]["saas_cost_analyzer"]
    assert (
        extension["analysis"]["applications"][0]["invoice_completeness"]
        == "not_asserted"
    )
    assert extension["reconciliation"]["invoice_completeness"] == "not_asserted"


def test_zero_cost_and_zero_seat_application_is_valid(tmp_path: Path):
    value = _bundle_value()
    app = value["applications"][0]
    app["contract"].update({"purchased_seats": 0, "unit_price": 0, "commitment": 0})
    app["assignments"] = []
    app["invoices"][0]["amount"] = 0
    path = _write_bundle(tmp_path, value)
    analysis = analyze_bundle(load_bundle(path))["applications"][0]
    assert analysis["purchased_seats"] == 0
    assert analysis["utilization_percentage"] is None
    assert analysis["reviewable_seats"] == 0
    assert analysis["estimated_annual_reduction_capacity_at_renewal"] == 0
    result = build_result(path, mode="illustrative")
    assert all(
        opportunity["scope"]["application_id"] != "crm"
        for opportunity in result["opportunities"]
    )


@pytest.mark.parametrize("bad", ["", -1, "NaN", "Infinity"])
def test_rejects_malformed_negative_nan_and_infinite_money(tmp_path: Path, bad):
    value = _bundle_value()
    value["applications"][0]["contract"]["unit_price"] = bad
    with pytest.raises(GovernanceError):
        load_bundle(_write_bundle(tmp_path, value))


def test_rejects_duplicate_and_overlapping_invoices(tmp_path: Path):
    value = _bundle_value()
    value["applications"][0]["invoices"].append(value["applications"][0]["invoices"][0])
    with pytest.raises(GovernanceError, match="duplicate invoice"):
        load_bundle(_write_bundle(tmp_path, value, "duplicate.json"))
    value = _bundle_value()
    value["applications"][0]["invoices"].append(
        {
            "id": "overlap",
            "period_start": "2026-07-15",
            "period_end": "2026-08-15",
            "amount": 720,
        }
    )
    with pytest.raises(GovernanceError, match="invoice periods overlap"):
        load_bundle(_write_bundle(tmp_path, value, "overlap.json"))


def test_mode_mismatch_fails_and_real_mode_is_customer_confidential(tmp_path: Path):
    with pytest.raises(GovernanceError, match="bundle mode"):
        build_result(DEMO, mode="real")
    value = _bundle_value()
    value["mode"] = "real"
    result = build_result(_write_bundle(tmp_path, value), mode="real")
    assert result["inputs"][0]["access"] == "local_read_only"
    assert result["inputs"][0]["data_classification"] == "customer_confidential"


def test_unattributed_owner_remains_visible(tmp_path: Path):
    value = _bundle_value()
    value["applications"][0]["owner"] = "unattributed"
    result = build_result(_write_bundle(tmp_path, value), mode="illustrative")
    assert any(
        metric["dimensions"]["owner"] == "unattributed" for metric in result["metrics"]
    )
    assert any(
        finding["finding_type"] == "allocation"
        and "not savings" in finding["description"]
        for finding in result["findings"]
    )


def test_rejects_duplicate_applications_and_invalid_currency(tmp_path: Path):
    value = _bundle_value()
    value["applications"].append(value["applications"][0])
    with pytest.raises(GovernanceError, match="duplicate application"):
        load_bundle(_write_bundle(tmp_path, value, "duplicate-app.json"))
    value = _bundle_value()
    value["currency"] = "US1"
    with pytest.raises(GovernanceError, match="three-letter ISO"):
        load_bundle(_write_bundle(tmp_path, value, "currency.json"))


@pytest.mark.parametrize("bad", ["", -1, "NaN", "Infinity", 1.5])
def test_rejects_invalid_purchased_seat_counts(tmp_path: Path, bad):
    value = _bundle_value()
    value["applications"][0]["contract"]["purchased_seats"] = bad
    with pytest.raises(GovernanceError):
        load_bundle(_write_bundle(tmp_path, value))


def test_deterministic_ids_do_not_collide_for_similar_application_ids(tmp_path: Path):
    value = _bundle_value()
    first = value["applications"][0]
    second = json.loads(json.dumps(first))
    first["id"] = "a b"
    second["id"] = "a-b"
    second["invoices"][0]["id"] = "second"
    value["applications"] = [first, second]
    result = build_result(_write_bundle(tmp_path, value), mode="illustrative")
    ids = [metric["id"] for metric in result["metrics"]]
    assert len(ids) == len(set(ids))


def test_public_commands_expose_no_mutating_actions():
    forbidden = {"cancel", "delete", "email", "purchase", "remove", "revoke"}
    assert forbidden.isdisjoint(cli.commands)


def test_blank_legacy_usage_is_unknown_not_unused():
    record = FocusRecord(
        "10",
        "u1",
        "App",
        "2026-01-01",
        "2026-02-01",
        "License",
        "generic",
        "USD",
        "",
        "seats",
    )
    assert flag_unused([record]) == []


def test_malformed_legacy_cost_never_becomes_zero():
    record = FocusRecord(
        "not-money",
        "u1",
        "App",
        "2026-01-01",
        "2026-02-01",
        "License",
        "generic",
        "USD",
        "1",
        "seats",
    )
    with pytest.raises(ValueError, match="invalid numeric"):
        group_by_product([record])
