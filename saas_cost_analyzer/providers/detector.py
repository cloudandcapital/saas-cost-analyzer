"""
Auto-detect SaaS billing CSV provider and normalize to FOCUS 1.0 records.

Detection uses CSV column-name signatures — no --provider flag required.
Supported providers: Salesforce, Snowflake, Databricks, Slack, GitHub,
Zoom, Adobe, Stripe, and a generic invoice fallback.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, List, Set


# ---------------------------------------------------------------------------
# FOCUS 1.0 record
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FocusRecord:
    BilledCost: str
    ResourceId: str
    ServiceName: str
    ChargePeriodStart: str
    ChargePeriodEnd: str
    ChargeType: str
    provider: str
    currency: str
    usage_amount: str
    usage_unit: str

    def as_dict(self) -> dict:
        return {
            "BilledCost": self.BilledCost,
            "ResourceId": self.ResourceId,
            "ServiceName": self.ServiceName,
            "ChargePeriodStart": self.ChargePeriodStart,
            "ChargePeriodEnd": self.ChargePeriodEnd,
            "ChargeType": self.ChargeType,
            "provider": self.provider,
            "currency": self.currency,
            "usage_amount": self.usage_amount,
            "usage_unit": self.usage_unit,
        }


FOCUS_FIELDNAMES = list(FocusRecord.__dataclass_fields__.keys())


# ---------------------------------------------------------------------------
# Provider detection
# ---------------------------------------------------------------------------

def detect_provider(columns: Set[str]) -> str:
    """
    Detect SaaS provider from the set of CSV column names.

    Returns one of: 'salesforce', 'snowflake', 'databricks', 'slack',
    'github', 'zoom', 'adobe', 'stripe', 'generic'.

    Raises ValueError for unrecognized column sets.
    """
    cols_lower = {c.lower() for c in columns}
    cols_orig = columns

    # Salesforce: OrgId or Contract or Salesforce in column names
    if any(c in cols_orig for c in ("OrgId", "ContractId", "Contract")) or \
       any("salesforce" in c.lower() for c in cols_orig) or \
       "orgid" in cols_lower or "contract" in cols_lower:
        return "salesforce"

    # Snowflake: WAREHOUSE_NAME or CREDITS_USED
    if "WAREHOUSE_NAME" in cols_orig or "CREDITS_USED" in cols_orig or \
       "warehouse_name" in cols_lower or "credits_used" in cols_lower:
        return "snowflake"

    # Databricks: workspace_id or dbu
    if "workspace_id" in cols_lower or "dbu" in cols_lower:
        return "databricks"

    # Slack: workspace + (members or active_members)
    if "workspace" in cols_lower and (
        "members" in cols_lower or "active_members" in cols_lower or
        "total_members" in cols_lower
    ):
        return "slack"

    # GitHub: (org or organization) + (seats or active_committers)
    has_org = "org" in cols_lower or "organization" in cols_lower
    has_seats = "seats" in cols_lower or "active_committers" in cols_lower
    if has_org and has_seats:
        return "github"

    # Zoom: host_email or zoom_license
    if "host_email" in cols_lower or "zoom_license" in cols_lower:
        return "zoom"

    # Adobe: 'adobe' anywhere in any column name (case-insensitive) or creative_cloud
    if any("adobe" in c.lower() for c in cols_orig) or "creative_cloud" in cols_lower:
        return "adobe"

    # Stripe: amount + customer_id + invoice_id
    if "amount" in cols_lower and "customer_id" in cols_lower and "invoice_id" in cols_lower:
        return "stripe"

    # Generic invoice fallback: vendor + amount + invoice_date
    if "vendor" in cols_lower and "amount" in cols_lower and "invoice_date" in cols_lower:
        return "generic"

    raise ValueError(
        f"Cannot detect provider from columns: {sorted(cols_orig)}. "
        "Expected column signatures for Salesforce, Snowflake, Databricks, "
        "Slack, GitHub, Zoom, Adobe, Stripe, or generic invoice."
    )


# ---------------------------------------------------------------------------
# Load + normalize
# ---------------------------------------------------------------------------

def load_and_normalize(path: Path) -> List[FocusRecord]:
    """Read a SaaS billing CSV, auto-detect provider, return FOCUS 1.0 records."""
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise ValueError(f"CSV file is empty or missing a header row: {path}")
        columns: Set[str] = set(reader.fieldnames)
        rows = list(reader)

    if not columns:
        raise ValueError(f"CSV file is empty or missing a header row: {path}")

    provider = detect_provider(columns)

    normalizers = {
        "salesforce": _normalize_salesforce,
        "snowflake": _normalize_snowflake,
        "databricks": _normalize_databricks,
        "slack": _normalize_slack,
        "github": _normalize_github,
        "zoom": _normalize_zoom,
        "adobe": _normalize_adobe,
        "stripe": _normalize_stripe,
        "generic": _normalize_generic,
    }
    return list(normalizers[provider](rows))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get(row: dict, *keys: str, default: str = "") -> str:
    """Return first non-empty value matching any of the given keys."""
    for k in keys:
        v = row.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return default


# ---------------------------------------------------------------------------
# Provider-specific normalizers
# ---------------------------------------------------------------------------

def _normalize_salesforce(rows: list[dict]) -> Iterator[FocusRecord]:
    for row in rows:
        yield FocusRecord(
            BilledCost=_get(row, "BilledAmount", "Amount", "billed_amount"),
            ResourceId=_get(row, "UserEmail", "user_email", "ContractId", "contract_id"),
            ServiceName="Salesforce",
            ChargePeriodStart=_get(row, "BillingPeriodStart", "billing_period_start"),
            ChargePeriodEnd=_get(row, "BillingPeriodEnd", "billing_period_end"),
            ChargeType="License",
            provider="salesforce",
            currency=_get(row, "Currency", "currency", default="USD"),
            usage_amount=_get(row, "SeatsActive", "seats_active", "seats"),
            usage_unit="seats",
        )


def _normalize_snowflake(rows: list[dict]) -> Iterator[FocusRecord]:
    for row in rows:
        yield FocusRecord(
            BilledCost=_get(row, "COST_USD", "cost_usd", "Cost", "cost"),
            ResourceId=_get(row, "WAREHOUSE_NAME", "warehouse_name"),
            ServiceName="Snowflake",
            ChargePeriodStart=_get(row, "START_TIME", "start_time"),
            ChargePeriodEnd=_get(row, "END_TIME", "end_time"),
            ChargeType="Usage",
            provider="snowflake",
            currency=_get(row, "CURRENCY", "currency", default="USD"),
            usage_amount=_get(row, "CREDITS_USED", "credits_used"),
            usage_unit="credits",
        )


def _normalize_databricks(rows: list[dict]) -> Iterator[FocusRecord]:
    for row in rows:
        yield FocusRecord(
            BilledCost=_get(row, "cost_usd", "cost", "amount"),
            ResourceId=_get(row, "workspace_id", "cluster_id"),
            ServiceName="Databricks",
            ChargePeriodStart=_get(row, "start_time", "date", "billing_period_start"),
            ChargePeriodEnd=_get(row, "end_time", "billing_period_end"),
            ChargeType="Usage",
            provider="databricks",
            currency=_get(row, "currency", default="USD"),
            usage_amount=_get(row, "dbu", "dbus_used"),
            usage_unit="DBU",
        )


def _normalize_slack(rows: list[dict]) -> Iterator[FocusRecord]:
    for row in rows:
        yield FocusRecord(
            BilledCost=_get(row, "total_cost", "cost", "amount"),
            ResourceId=_get(row, "workspace", "workspace_id"),
            ServiceName="Slack",
            ChargePeriodStart=_get(row, "billing_period_start"),
            ChargePeriodEnd=_get(row, "billing_period_end"),
            ChargeType="License",
            provider="slack",
            currency=_get(row, "currency", default="USD"),
            usage_amount=_get(row, "active_members", "members"),
            usage_unit="seats",
        )


def _normalize_github(rows: list[dict]) -> Iterator[FocusRecord]:
    for row in rows:
        yield FocusRecord(
            BilledCost=_get(row, "cost_usd", "total_cost", "amount"),
            ResourceId=_get(row, "org", "organization"),
            ServiceName="GitHub",
            ChargePeriodStart=_get(row, "billing_period_start", "date"),
            ChargePeriodEnd=_get(row, "billing_period_end"),
            ChargeType="License",
            provider="github",
            currency=_get(row, "currency", default="USD"),
            usage_amount=_get(row, "seats", "active_committers"),
            usage_unit="seats",
        )


def _normalize_zoom(rows: list[dict]) -> Iterator[FocusRecord]:
    for row in rows:
        yield FocusRecord(
            BilledCost=_get(row, "amount", "cost_usd", "total_cost"),
            ResourceId=_get(row, "host_email", "zoom_license", "license_id"),
            ServiceName="Zoom",
            ChargePeriodStart=_get(row, "billing_period_start", "date"),
            ChargePeriodEnd=_get(row, "billing_period_end"),
            ChargeType="License",
            provider="zoom",
            currency=_get(row, "currency", default="USD"),
            usage_amount=_get(row, "minutes_used", "usage"),
            usage_unit="licenses",
        )


def _normalize_adobe(rows: list[dict]) -> Iterator[FocusRecord]:
    for row in rows:
        yield FocusRecord(
            BilledCost=_get(row, "amount", "cost_usd", "total_cost"),
            ResourceId=_get(row, "user_email", "adobe_id", "license_id"),
            ServiceName="Adobe",
            ChargePeriodStart=_get(row, "billing_period_start", "date"),
            ChargePeriodEnd=_get(row, "billing_period_end"),
            ChargeType="License",
            provider="adobe",
            currency=_get(row, "currency", default="USD"),
            usage_amount=_get(row, "seats", "licenses"),
            usage_unit="licenses",
        )


def _normalize_stripe(rows: list[dict]) -> Iterator[FocusRecord]:
    for row in rows:
        yield FocusRecord(
            BilledCost=_get(row, "amount", "total_amount"),
            ResourceId=_get(row, "customer_id"),
            ServiceName="Stripe",
            ChargePeriodStart=_get(row, "invoice_date", "date"),
            ChargePeriodEnd=_get(row, "due_date", "period_end"),
            ChargeType="Usage",
            provider="stripe",
            currency=_get(row, "currency", default="USD"),
            usage_amount=_get(row, "quantity", "units"),
            usage_unit="transactions",
        )


def _normalize_generic(rows: list[dict]) -> Iterator[FocusRecord]:
    for row in rows:
        yield FocusRecord(
            BilledCost=_get(row, "amount", "total_amount", "cost"),
            ResourceId=_get(row, "invoice_id", "vendor"),
            ServiceName=_get(row, "vendor", "service_name", "product"),
            ChargePeriodStart=_get(row, "invoice_date", "date", "billing_period_start"),
            ChargePeriodEnd=_get(row, "due_date", "billing_period_end"),
            ChargeType="License",
            provider="generic",
            currency=_get(row, "currency", default="USD"),
            usage_amount=_get(row, "quantity", "seats", "units"),
            usage_unit=_get(row, "unit", "usage_unit", default="licenses"),
        )
