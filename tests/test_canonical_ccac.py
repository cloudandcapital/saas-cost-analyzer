from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from saas_cost_analyzer.ccac import build_result
from saas_cost_analyzer.cli import cli
from saas_cost_analyzer.governance import GovernanceError

DATA = Path(__file__).parents[1] / "saas_cost_analyzer" / "data"
LEGACY = DATA / "illustrative-governance-v2.json"
SCOPE = DATA / "illustrative-governance-v2.1.json"
ANALYSIS = DATA / "illustrative-analysis-v1.json"
LEGACY_SHA256 = "58f0ad3763a0590461f433dddeafcf608e55e3b90d687b703660a6e8a750e370"


def _json(path: Path) -> dict:
    return json.loads(path.read_text())


def _write(tmp_path: Path, value: dict, name: str) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(value))
    return path


def _build(
    bundle: Path = SCOPE,
    analysis: Path = ANALYSIS,
    *,
    mode: str = "illustrative",
) -> dict:
    return build_result(
        bundle,
        mode=mode,
        run_id="123e4567-e89b-12d3-a456-426614174040",
        generated_at="2026-08-04T12:20:00Z",
        contract_version="1.1.0",
        analysis_path=analysis,
        bundled_demo=bundle == SCOPE and analysis == ANALYSIS,
    )


def _scope(payload: dict) -> dict:
    matches = [
        item
        for item in payload["metrics"]
        if item["id"] == "metric.tech-spend.scope.saas"
    ]
    assert len(matches) == 1
    return matches[0]


def _partial_analysis(tmp_path: Path) -> Path:
    analysis = _json(ANALYSIS)
    analysis["completeness"] = {
        "status": "partial",
        "absent_application_charges": "unknown",
        "description": "Custom illustrative inputs do not establish complete SaaS coverage.",
    }
    analysis["eligibility"] = {
        "total_eligible": False,
        "explanation": "Custom illustrative inputs are not eligible for an all-in total.",
    }
    return _write(tmp_path, analysis, "partial-analysis.json")


def _components(payload: dict) -> list[dict]:
    return [
        item for item in payload["metrics"] if item["id"].endswith("period-net-cost")
    ]


def test_default_and_explicit_1_0_remain_byte_identical():
    runner = CliRunner()
    default = runner.invoke(cli, ["ccac", "--demo"])
    explicit = runner.invoke(cli, ["ccac", "--demo", "--contract-version", "1.0.0"])
    assert default.exit_code == explicit.exit_code == 0
    assert default.output == explicit.output
    assert hashlib.sha256(default.output.encode()).hexdigest() == LEGACY_SHA256


def test_exact_bundled_1_0_uses_legacy_provenance():
    payload = json.loads(CliRunner().invoke(cli, ["ccac", "--demo"]).output)
    assert payload["producer"]["version"] == "0.2.0"
    assert payload["inputs"][0]["adapter_version"] == "0.2.0"
    assert {item["producer"]["version"] for item in payload["opportunities"]} == {
        "0.2.0"
    }


def test_custom_and_real_1_0_report_current_provenance(tmp_path: Path):
    custom = _json(LEGACY)
    custom["applications"][0]["name"] = "Custom CRM"
    custom_path = _write(tmp_path, custom, "custom.json")
    illustrative = build_result(custom_path, mode="illustrative")
    assert illustrative["producer"]["version"] == "0.3.0"
    assert illustrative["inputs"][0]["adapter_version"] == "0.3.0"
    custom["mode"] = "real"
    real = build_result(_write(tmp_path, custom, "real.json"), mode="real")
    assert real["producer"]["version"] == "0.3.0"


def test_custom_illustrative_file_cannot_impersonate_legacy_demo():
    payload = build_result(LEGACY, mode="illustrative", compatibility_demo=True)
    assert payload["producer"]["version"] == "0.2.0"
    payload = build_result(LEGACY, mode="illustrative", compatibility_demo=False)
    assert payload["producer"]["version"] == "0.3.0"


def test_explicit_1_1_emits_exact_canonical_saas_scope():
    payload = _build()
    scope = _scope(payload)
    components = _components(payload)
    assert payload["contract"] == "ccac/1.1.0"
    assert payload["producer"]["version"] == "0.3.0"
    assert len(components) == 2
    assert scope["input_metric_ids"] == [item["id"] for item in components]
    assert scope["value"] == sum(item["value"] for item in components) == 736.77
    assert scope["accounting_boundary"]["canonical_owner"] == "saas-cost-analyzer"
    assert (
        scope["accounting_boundary"]["source_channel"] == "saas_invoice_or_entitlement"
    )


def test_reporting_and_service_periods_are_explicit_and_half_open():
    payload = _build()
    assert payload["period"] == {
        "start": "2026-07-01",
        "end": "2026-07-22",
        "timezone": "UTC",
    }
    by_app = {
        item["dimensions"]["application_id"]: item for item in _components(payload)
    }
    assert by_app["crm"]["dimensions"]["service_period_start"] == "2025-12-01"
    assert by_app["crm"]["dimensions"]["service_period_end"] == "2026-12-01"
    assert by_app["design"]["dimensions"]["service_period_start"] == "2026-07-01"
    assert by_app["design"]["dimensions"]["service_period_end"] == "2026-10-01"


def test_decimal_allocation_and_rounding_reconcile_exactly():
    by_app = {
        item["dimensions"]["application_id"]: item for item in _components(_build())
    }
    assert by_app["crm"]["value"] == 497.10
    assert by_app["crm"]["dimensions"]["overlap_days"] == "21"
    assert by_app["crm"]["dimensions"]["service_period_days"] == "365"
    assert by_app["design"]["value"] == 239.67
    assert by_app["design"]["dimensions"]["overlap_days"] == "21"
    assert by_app["design"]["dimensions"]["service_period_days"] == "92"


@pytest.mark.parametrize(
    "service_start,service_end,expected_days",
    [
        ("2026-06-30", "2026-07-02", "1"),
        ("2026-07-21", "2026-07-23", "1"),
        ("2026-07-01", "2026-07-22", "21"),
    ],
)
def test_overlap_day_boundaries(
    tmp_path: Path, service_start: str, service_end: str, expected_days: str
):
    bundle = _json(SCOPE)
    bundle["applications"] = [bundle["applications"][0]]
    contract = bundle["applications"][0]["contract"]
    contract["service_period_start"] = service_start
    contract["service_period_end"] = service_end
    component = _components(
        _build(_write(tmp_path, bundle, "boundary.json"), _partial_analysis(tmp_path))
    )[0]
    assert component["dimensions"]["overlap_days"] == expected_days


@pytest.mark.parametrize(
    "service_start,service_end,error",
    [
        ("2026-06-01", "2026-07-01", "no reporting-period overlap"),
        ("2026-07-22", "2026-08-01", "no reporting-period overlap"),
        ("2026-08-01", "2026-07-01", "must be after"),
    ],
)
def test_zero_or_reversed_service_period_fails(
    tmp_path: Path, service_start: str, service_end: str, error: str
):
    bundle = _json(SCOPE)
    bundle["applications"][0]["contract"]["service_period_start"] = service_start
    bundle["applications"][0]["contract"]["service_period_end"] = service_end
    with pytest.raises(GovernanceError, match=error):
        _build(_write(tmp_path, bundle, "bad-period.json"), _partial_analysis(tmp_path))


@pytest.mark.parametrize(
    "field", ["service_period_start", "service_period_end", "charge_id"]
)
def test_missing_service_period_or_charge_identity_fails(tmp_path: Path, field: str):
    bundle = _json(SCOPE)
    del bundle["applications"][0]["contract"][field]
    with pytest.raises(GovernanceError, match=field):
        _build(_write(tmp_path, bundle, "missing.json"))


def test_duplicate_charge_allocation_fails(tmp_path: Path):
    bundle = _json(SCOPE)
    bundle["applications"][1]["contract"]["charge_id"] = bundle["applications"][0][
        "contract"
    ]["charge_id"]
    with pytest.raises(GovernanceError, match="overlapping service-period allocations"):
        _build(_write(tmp_path, bundle, "duplicate-charge.json"))


def test_invoice_commitment_and_opportunity_metrics_are_not_scope_inputs():
    payload = _build()
    scope = _scope(payload)
    assert all(item.endswith("period-net-cost") for item in scope["input_metric_ids"])
    assert not any(
        token in item
        for item in scope["input_metric_ids"]
        for token in ("invoice", "annualized", "renewal", "capacity", "opportunity")
    )


def test_invoice_and_opportunity_changes_do_not_change_period_spend(tmp_path: Path):
    original_payload = _build()
    original = _scope(original_payload)["value"]
    bundle = _json(SCOPE)
    bundle["applications"][0]["invoices"][0]["amount"] = 1
    bundle["applications"][0]["contract"]["renewal_date"] = "2026-08-15"
    bundle["applications"][0]["assignments"] = []
    changed_payload = _build(
        _write(tmp_path, bundle, "changed-opportunity.json"),
        _partial_analysis(tmp_path),
    )
    assert _scope(changed_payload)["value"] == original
    old_reconciliation = original_payload["extensions"]["saas_cost_analyzer"][
        "reconciliation"
    ]
    new_reconciliation = changed_payload["extensions"]["saas_cost_analyzer"][
        "reconciliation"
    ]
    for field in (
        "supplied_invoice_amount",
        "total_modeled_review_capacity",
        "open_notice_window_eligible_capacity",
        "blocked_notice_window_capacity",
        "opportunity_expected_total",
    ):
        assert old_reconciliation[field] != new_reconciliation[field]


@pytest.mark.parametrize(
    "channel,scope",
    [("direct_ai_vendor", "direct_ai"), ("cloud_provider_billing", "cloud")],
)
def test_non_saas_billing_cannot_enter_scope(tmp_path: Path, channel: str, scope: str):
    bundle = _json(SCOPE)
    bundle["applications"][0]["billing_channel"] = channel
    bundle["applications"][0]["technology_scope"] = scope
    with pytest.raises(GovernanceError, match="cannot enter SaaS scope"):
        _build(
            _write(tmp_path, bundle, "wrong-channel.json"),
            _partial_analysis(tmp_path),
        )


@pytest.mark.parametrize(
    "field", ["vendor", "billing_provider", "billing_channel", "technology_scope"]
)
def test_missing_billing_or_scope_classification_fails(tmp_path: Path, field: str):
    bundle = _json(SCOPE)
    bundle["applications"][0][field] = ""
    with pytest.raises(GovernanceError, match=field):
        _build(_write(tmp_path, bundle, "missing-classification.json"))


def test_contradictory_billing_and_scope_classification_fails(tmp_path: Path):
    bundle = _json(SCOPE)
    bundle["applications"][0]["technology_scope"] = "direct_ai"
    with pytest.raises(GovernanceError, match="contradicts"):
        _build(_write(tmp_path, bundle, "contradiction.json"))


def test_analysis_declaration_owns_run_level_assertions():
    analysis = _json(ANALYSIS)
    bundle = _json(SCOPE)
    for field in (
        "period",
        "accounting_cost_basis",
        "completeness",
        "allocation_convention",
        "rounding_convention",
        "component_treatments",
        "eligibility",
    ):
        assert field in analysis
        assert field not in bundle


def test_custom_illustrative_input_cannot_claim_complete_coverage(tmp_path: Path):
    bundle = _json(SCOPE)
    bundle["applications"][0]["name"] = "Custom CRM"
    with pytest.raises(GovernanceError, match="restricted to the exact bundled"):
        _build(_write(tmp_path, bundle, "custom-complete.json"))


@pytest.mark.parametrize(
    "mutation,error",
    [
        ("missing_completeness", "completeness"),
        ("partial_illustrative", "leave absent charges unknown"),
        ("wrong_basis", "accounting_cost_basis"),
        ("wrong_timezone", "timezone"),
        ("ineligible_illustrative", "must be total eligible"),
    ],
)
def test_missing_or_contradictory_analysis_fails(
    tmp_path: Path, mutation: str, error: str
):
    analysis = _json(ANALYSIS)
    if mutation == "missing_completeness":
        del analysis["completeness"]
    elif mutation == "partial_illustrative":
        analysis["completeness"]["status"] = "partial"
    elif mutation == "wrong_basis":
        analysis["accounting_cost_basis"] = "gross_cost"
    elif mutation == "wrong_timezone":
        analysis["period"]["timezone"] = "America/Los_Angeles"
    else:
        analysis["eligibility"]["total_eligible"] = False
    with pytest.raises(GovernanceError, match=error):
        _build(analysis=_write(tmp_path, analysis, "bad-analysis.json"))


def test_real_local_1_1_is_partial_and_total_ineligible(tmp_path: Path):
    bundle = _json(SCOPE)
    bundle["mode"] = "real"
    analysis = _json(ANALYSIS)
    analysis["mode"] = "real"
    analysis["completeness"] = {
        "status": "partial",
        "absent_application_charges": "unknown",
        "description": "Local files do not establish complete organizational SaaS coverage.",
    }
    analysis["eligibility"] = {
        "total_eligible": False,
        "explanation": "Partial local coverage is not eligible for an all-in total.",
    }
    payload = _build(
        _write(tmp_path, bundle, "real-bundle.json"),
        _write(tmp_path, analysis, "real-analysis.json"),
        mode="real",
    )
    boundary = _scope(payload)["accounting_boundary"]
    assert boundary["coverage"] == "partial"
    assert boundary["total_eligible"] is False


def test_real_local_cannot_invent_complete_coverage(tmp_path: Path):
    bundle = _json(SCOPE)
    bundle["mode"] = "real"
    analysis = _json(ANALYSIS)
    analysis["mode"] = "real"
    with pytest.raises(GovernanceError, match="real analysis must declare partial"):
        _build(
            _write(tmp_path, bundle, "real.json"),
            _write(tmp_path, analysis, "complete-real.json"),
            mode="real",
        )


def test_currency_mismatch_fails(tmp_path: Path):
    bundle = _json(SCOPE)
    bundle["currency"] = "EUR"
    with pytest.raises(GovernanceError, match="requires USD"):
        _build(_write(tmp_path, bundle, "eur.json"), _partial_analysis(tmp_path))


def test_generated_1_1_is_byte_deterministic_and_released_ccac_accepts(tmp_path: Path):
    runner = CliRunner()
    args = ["ccac", "--demo", "--contract-version", "1.1.0"]
    first = runner.invoke(cli, args)
    second = runner.invoke(cli, args)
    assert first.exit_code == second.exit_code == 0
    assert first.output == second.output
    if os.environ.get("REQUIRE_CCAC_RELEASE_VALIDATION") == "1":
        artifact = tmp_path / "saas-1.1.json"
        artifact.write_text(first.output)
        from ccac.validator import validate_document

        assert validate_document(json.loads(first.output)) == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("canonical_owner", "finops-lite"),
        ("relationship", "diagnostic"),
        ("scope", "cloud"),
        ("source_channel", "direct_ai_vendor"),
    ],
)
def test_released_ccac_rejects_wrong_scope_identity(field: str, value: str):
    if os.environ.get("REQUIRE_CCAC_RELEASE_VALIDATION") != "1":
        pytest.skip("released CCAC acceptance validator enabled in CI verification")
    from ccac.validator import validate_document

    payload = _build()
    _scope(payload)["accounting_boundary"][field] = value
    assert validate_document(payload)


def test_released_ccac_rejects_missing_evidence_and_duplicate_scope():
    if os.environ.get("REQUIRE_CCAC_RELEASE_VALIDATION") != "1":
        pytest.skip("released CCAC acceptance validator enabled in CI verification")
    from ccac.validator import validate_document

    payload = _build()
    _scope(payload)["evidence_ids"] = []
    assert validate_document(payload)
    payload = _build()
    payload["metrics"].append(copy.deepcopy(_scope(payload)))
    assert validate_document(payload)


def test_unsupported_contract_selection_fails_clearly():
    result = CliRunner().invoke(cli, ["ccac", "--demo", "--contract-version", "2.0.0"])
    assert result.exit_code == 2
    assert "Invalid value for '--contract-version'" in result.output
