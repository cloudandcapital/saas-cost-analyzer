"""Tests for SaaS Cost Analyzer — provider detection, normalization, and CLI commands."""

from __future__ import annotations

import csv
import io
import json
import textwrap
from pathlib import Path

import pytest
from click.testing import CliRunner

from saas_cost_analyzer.cli import cli
from saas_cost_analyzer.providers.detector import (
    FOCUS_FIELDNAMES,
    detect_provider,
    load_and_normalize,
)
from saas_cost_analyzer.analysis.engine import (
    flag_unused,
    forecast_next_month,
    group_by_month,
    group_by_product,
    group_by_user,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_csv(tmp_path: Path, filename: str, content: str) -> Path:
    p = tmp_path / filename
    p.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")
    return p


@pytest.fixture()
def runner():
    return CliRunner()


# ---------------------------------------------------------------------------
# Sample CSVs
# ---------------------------------------------------------------------------

SALESFORCE_CSV = """\
OrgId,ContractId,UserEmail,LicenseType,BilledAmount,Currency,BillingPeriodStart,BillingPeriodEnd,SeatsLicensed,SeatsActive
00D000001,CNT-001,alice@example.com,Salesforce Enterprise,250.00,USD,2026-03-01,2026-03-31,1,1
00D000001,CNT-001,bob@example.com,Salesforce Enterprise,250.00,USD,2026-03-01,2026-03-31,1,0
00D000001,CNT-001,carol@example.com,Salesforce Enterprise,250.00,USD,2026-03-01,2026-03-31,1,1
"""

SNOWFLAKE_CSV = """\
WAREHOUSE_NAME,START_TIME,END_TIME,CREDITS_USED,CREDITS_BILLED,COST_USD,CURRENCY
COMPUTE_WH,2026-03-01,2026-03-02,12.5,13.0,39.00,USD
ANALYTICS_WH,2026-03-01,2026-03-02,8.2,9.0,27.00,USD
COMPUTE_WH,2026-03-02,2026-03-03,10.1,11.0,33.00,USD
"""

DATABRICKS_CSV = """\
workspace_id,cluster_id,start_time,end_time,dbu,cost_usd,currency
ws-001,cluster-a,2026-03-01,2026-03-02,45.5,18.20,USD
ws-001,cluster-b,2026-03-01,2026-03-02,30.2,12.08,USD
"""

SLACK_CSV = """\
workspace,billing_period_start,billing_period_end,active_members,total_members,cost_per_seat,total_cost,currency
example-corp,2026-03-01,2026-03-31,42,50,8.75,437.50,USD
example-corp,2026-02-01,2026-02-28,40,50,8.75,437.50,USD
"""

GITHUB_CSV = """\
org,billing_period_start,billing_period_end,seats,active_committers,cost_usd,currency
myorg,2026-03-01,2026-03-31,25,20,1050.00,USD
"""

STRIPE_CSV = """\
invoice_id,customer_id,invoice_date,due_date,amount,currency,description
INV-001,cus_abc123,2026-03-01,2026-03-31,500.00,USD,Monthly subscription
INV-002,cus_def456,2026-03-01,2026-03-31,250.00,USD,Monthly subscription
"""

GENERIC_CSV = """\
vendor,invoice_id,invoice_date,due_date,amount,currency,description,category
Notion,INV-2026-001,2026-03-01,2026-03-31,96.00,USD,Team plan - 10 seats,Productivity
Figma,INV-2026-002,2026-03-01,2026-03-31,225.00,USD,Organization plan,Design
Linear,INV-2026-003,2026-03-01,2026-03-31,144.00,USD,Business plan - 12 seats,Project Management
"""

SLACK_TWO_MONTHS_CSV = """\
workspace,billing_period_start,billing_period_end,active_members,total_members,cost_per_seat,total_cost,currency
example-corp,2026-01-01,2026-01-31,38,50,8.75,332.50,USD
example-corp,2026-02-01,2026-02-28,40,50,8.75,350.00,USD
example-corp,2026-03-01,2026-03-31,42,50,8.75,367.50,USD
"""

SALESFORCE_UNUSED_CSV = """\
OrgId,ContractId,UserEmail,LicenseType,BilledAmount,Currency,BillingPeriodStart,BillingPeriodEnd,SeatsLicensed,SeatsActive
00D000001,CNT-001,alice@example.com,Salesforce Enterprise,250.00,USD,2026-03-01,2026-03-31,1,1
00D000001,CNT-001,bob@example.com,Salesforce Enterprise,250.00,USD,2026-03-01,2026-03-31,1,0
"""


# ---------------------------------------------------------------------------
# Provider detection tests
# ---------------------------------------------------------------------------

def test_detect_salesforce():
    assert detect_provider({"OrgId", "ContractId", "UserEmail"}) == "salesforce"


def test_detect_salesforce_via_contract():
    assert detect_provider({"Contract", "BilledAmount", "Currency"}) == "salesforce"


def test_detect_snowflake():
    assert detect_provider({"WAREHOUSE_NAME", "START_TIME", "CREDITS_USED"}) == "snowflake"


def test_detect_databricks():
    assert detect_provider({"workspace_id", "dbu", "cost_usd"}) == "databricks"


def test_detect_slack():
    assert detect_provider({"workspace", "active_members", "total_cost"}) == "slack"


def test_detect_github():
    assert detect_provider({"org", "seats", "cost_usd"}) == "github"


def test_detect_stripe():
    assert detect_provider({"invoice_id", "customer_id", "amount", "invoice_id"}) == "stripe"


def test_detect_generic_invoice():
    assert detect_provider({"vendor", "amount", "invoice_date"}) == "generic"


def test_detect_unrecognized_raises_value_error():
    with pytest.raises(ValueError, match="Cannot detect provider"):
        detect_provider({"foo", "bar", "baz"})


def test_detect_empty_raises_value_error():
    with pytest.raises(ValueError):
        detect_provider(set())


# ---------------------------------------------------------------------------
# Normalization tests
# ---------------------------------------------------------------------------

def test_normalize_has_focus_columns(tmp_path):
    p = _write_csv(tmp_path, "sf.csv", SALESFORCE_CSV)
    records = load_and_normalize(p)
    assert len(records) == 3
    for r in records:
        d = r.as_dict()
        for col in FOCUS_FIELDNAMES:
            assert col in d, f"Missing FOCUS column: {col}"


def test_normalize_salesforce(tmp_path):
    p = _write_csv(tmp_path, "sf.csv", SALESFORCE_CSV)
    records = load_and_normalize(p)
    assert len(records) == 3
    assert records[0].provider == "salesforce"
    assert records[0].ServiceName == "Salesforce"
    assert records[0].ChargeType == "License"
    assert records[0].BilledCost == "250.00"
    assert records[0].ResourceId == "alice@example.com"


def test_normalize_snowflake(tmp_path):
    p = _write_csv(tmp_path, "sf.csv", SNOWFLAKE_CSV)
    records = load_and_normalize(p)
    assert len(records) == 3
    assert records[0].provider == "snowflake"
    assert records[0].ServiceName == "Snowflake"
    assert records[0].ChargeType == "Usage"
    assert records[0].usage_unit == "credits"


def test_normalize_databricks(tmp_path):
    p = _write_csv(tmp_path, "db.csv", DATABRICKS_CSV)
    records = load_and_normalize(p)
    assert len(records) == 2
    assert records[0].provider == "databricks"
    assert records[0].ServiceName == "Databricks"
    assert records[0].usage_unit == "DBU"


def test_normalize_slack(tmp_path):
    p = _write_csv(tmp_path, "slack.csv", SLACK_CSV)
    records = load_and_normalize(p)
    assert len(records) == 2
    assert records[0].provider == "slack"
    assert records[0].ServiceName == "Slack"
    assert records[0].ChargeType == "License"


def test_normalize_generic(tmp_path):
    p = _write_csv(tmp_path, "generic.csv", GENERIC_CSV)
    records = load_and_normalize(p)
    assert len(records) == 3
    assert records[0].provider == "generic"
    # ServiceName should be the vendor name
    assert records[0].ServiceName in ("Notion", "Figma", "Linear")


def test_normalize_stripe(tmp_path):
    p = _write_csv(tmp_path, "stripe.csv", STRIPE_CSV)
    records = load_and_normalize(p)
    assert len(records) == 2
    assert records[0].provider == "stripe"
    assert records[0].ServiceName == "Stripe"


def test_normalize_empty_csv_raises(tmp_path):
    p = tmp_path / "empty.csv"
    p.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="empty or missing"):
        load_and_normalize(p)


# ---------------------------------------------------------------------------
# Analysis engine tests
# ---------------------------------------------------------------------------

def test_group_by_product(tmp_path):
    p = _write_csv(tmp_path, "sf.csv", SALESFORCE_CSV)
    records = load_and_normalize(p)
    rows = group_by_product(records)
    assert len(rows) == 1
    assert rows[0]["key"] == "Salesforce"
    assert abs(rows[0]["cost"] - 750.0) < 0.01


def test_group_by_user(tmp_path):
    p = _write_csv(tmp_path, "sf.csv", SALESFORCE_CSV)
    records = load_and_normalize(p)
    rows = group_by_user(records)
    assert len(rows) == 3
    keys = [r["key"] for r in rows]
    assert "alice@example.com" in keys
    assert "bob@example.com" in keys


def test_group_by_month(tmp_path):
    p = _write_csv(tmp_path, "slack.csv", SLACK_TWO_MONTHS_CSV)
    records = load_and_normalize(p)
    rows = group_by_month(records)
    months = [r["key"] for r in rows]
    assert "2026-01" in months
    assert "2026-02" in months
    assert "2026-03" in months


def test_unused_flag_identifies_zero_usage(tmp_path):
    p = _write_csv(tmp_path, "sf.csv", SALESFORCE_UNUSED_CSV)
    records = load_and_normalize(p)
    unused = flag_unused(records)
    # bob has SeatsActive=0
    assert len(unused) == 1
    assert unused[0]["ResourceId"] == "bob@example.com"
    assert unused[0]["unused"] is True


def test_forecast_produces_projected_cost(tmp_path):
    p = _write_csv(tmp_path, "slack.csv", SLACK_TWO_MONTHS_CSV)
    records = load_and_normalize(p)
    result = forecast_next_month(records)
    assert "projected_cost" in result
    assert isinstance(result["projected_cost"], float)
    assert result["projected_cost"] >= 0.0


def test_forecast_single_period_repeat(tmp_path):
    p = _write_csv(tmp_path, "sf.csv", SALESFORCE_CSV)
    records = load_and_normalize(p)
    result = forecast_next_month(records)
    assert result["method"] == "single_period_repeat"
    assert result["projected_cost"] > 0.0


def test_forecast_linear_trend_method(tmp_path):
    p = _write_csv(tmp_path, "slack.csv", SLACK_TWO_MONTHS_CSV)
    records = load_and_normalize(p)
    result = forecast_next_month(records)
    assert result["method"] == "linear_trend"


# ---------------------------------------------------------------------------
# CLI: analyze
# ---------------------------------------------------------------------------

def test_analyze_table_default(runner, tmp_path):
    p = _write_csv(tmp_path, "sf.csv", SALESFORCE_CSV)
    result = runner.invoke(cli, ["analyze", "--file", str(p)])
    assert result.exit_code == 0
    assert "Salesforce" in result.output
    assert "TOTAL" in result.output


def test_analyze_group_by_product(runner, tmp_path):
    p = _write_csv(tmp_path, "sf.csv", SALESFORCE_CSV)
    result = runner.invoke(cli, ["analyze", "--file", str(p), "--group-by", "product"])
    assert result.exit_code == 0
    assert "Salesforce" in result.output
    assert "TOTAL" in result.output


def test_analyze_group_by_user(runner, tmp_path):
    p = _write_csv(tmp_path, "sf.csv", SALESFORCE_CSV)
    result = runner.invoke(cli, ["analyze", "--file", str(p), "--group-by", "user"])
    assert result.exit_code == 0
    assert "alice@example.com" in result.output


def test_analyze_group_by_month(runner, tmp_path):
    p = _write_csv(tmp_path, "slack.csv", SLACK_TWO_MONTHS_CSV)
    result = runner.invoke(cli, ["analyze", "--file", str(p), "--group-by", "month"])
    assert result.exit_code == 0
    assert "2026-01" in result.output
    assert "2026-03" in result.output


def test_analyze_format_json(runner, tmp_path):
    p = _write_csv(tmp_path, "sf.csv", SALESFORCE_CSV)
    result = runner.invoke(cli, ["analyze", "--file", str(p), "--format", "json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["schema_version"] == "1.0"
    assert data["total_cost"] > 0
    assert any(r["key"] == "Salesforce" for r in data["rows"])


def test_analyze_format_csv(runner, tmp_path):
    p = _write_csv(tmp_path, "sf.csv", SALESFORCE_CSV)
    result = runner.invoke(cli, ["analyze", "--file", str(p), "--format", "csv"])
    assert result.exit_code == 0
    assert "key,cost" in result.output
    assert "Salesforce" in result.output


def test_analyze_format_table_has_total(runner, tmp_path):
    p = _write_csv(tmp_path, "sf.csv", SALESFORCE_CSV)
    result = runner.invoke(cli, ["analyze", "--file", str(p), "--format", "table"])
    assert result.exit_code == 0
    assert "TOTAL" in result.output


def test_analyze_unused_flag(runner, tmp_path):
    p = _write_csv(tmp_path, "sf.csv", SALESFORCE_UNUSED_CSV)
    result = runner.invoke(cli, ["analyze", "--file", str(p), "--unused"])
    assert result.exit_code == 0
    assert "bob@example.com" in result.output


def test_analyze_forecast_flag(runner, tmp_path):
    p = _write_csv(tmp_path, "slack.csv", SLACK_TWO_MONTHS_CSV)
    result = runner.invoke(cli, ["analyze", "--file", str(p), "--forecast"])
    assert result.exit_code == 0
    assert "Forecast" in result.output or "projected" in result.output.lower()


def test_analyze_forecast_json_has_projected_cost(runner, tmp_path):
    p = _write_csv(tmp_path, "slack.csv", SLACK_TWO_MONTHS_CSV)
    result = runner.invoke(cli, ["analyze", "--file", str(p), "--format", "json", "--forecast"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "forecast" in data
    assert "projected_cost" in data["forecast"]


# ---------------------------------------------------------------------------
# CLI: compare
# ---------------------------------------------------------------------------

def test_compare_shows_delta(runner, tmp_path):
    b = _write_csv(tmp_path, "baseline.csv", SALESFORCE_CSV)
    p = _write_csv(tmp_path, "proposed.csv", GENERIC_CSV)
    result = runner.invoke(cli, ["compare", "--baseline", str(b), "--proposed", str(p)])
    assert result.exit_code == 0
    assert "Comparison:" in result.output
    assert "TOTAL" in result.output
    assert "Delta" in result.output


def test_compare_json_has_schema_version(runner, tmp_path):
    b = _write_csv(tmp_path, "baseline.csv", SALESFORCE_CSV)
    p = _write_csv(tmp_path, "proposed.csv", SLACK_CSV)
    result = runner.invoke(cli, ["compare", "--baseline", str(b), "--proposed", str(p), "--format", "json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["schema_version"] == "1.0"
    assert "total_delta" in data


# ---------------------------------------------------------------------------
# CLI: normalize
# ---------------------------------------------------------------------------

def test_normalize_writes_focus_csv(runner, tmp_path):
    p = _write_csv(tmp_path, "sf.csv", SALESFORCE_CSV)
    result = runner.invoke(cli, ["normalize", "--file", str(p)])
    assert result.exit_code == 0
    reader = csv.DictReader(io.StringIO(result.output))
    assert set(FOCUS_FIELDNAMES).issubset(set(reader.fieldnames or []))
    rows = list(reader)
    assert len(rows) == 3


# ---------------------------------------------------------------------------
# CLI: exit codes
# ---------------------------------------------------------------------------

def test_exit_code_3_on_missing_file(runner, tmp_path):
    result = runner.invoke(cli, ["analyze", "--file", str(tmp_path / "nonexistent.csv")])
    assert result.exit_code == 3


def test_exit_code_4_on_unrecognized_csv(runner, tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("col_a,col_b,col_c\n1,2,3\n", encoding="utf-8")
    result = runner.invoke(cli, ["analyze", "--file", str(p)])
    assert result.exit_code == 4
