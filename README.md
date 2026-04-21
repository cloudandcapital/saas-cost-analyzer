# SaaS Cost Analyzer

[![CI](https://github.com/dianuhs/saas-cost-analyzer/actions/workflows/test.yml/badge.svg)](https://github.com/dianuhs/saas-cost-analyzer/actions/workflows/test.yml)

**The SaaS spend layer of a Cloud+ FinOps practice — Cloud + AI + SaaS = full tech spend coverage.**

| Stage | Tool | What it does |
|-------|------|-------------|
| **Visibility** | [FinOps Lite](https://github.com/dianuhs/finops-lite) | AWS/Azure/GCP cost visibility, FOCUS 1.0 export |
| **Variance** | [FinOps Watchdog](https://github.com/dianuhs/finops-watchdog) | Anomaly detection from any cost CSV |
| **Tradeoffs** | [Recovery Economics](https://github.com/dianuhs/recovery-economics) | Resilience cost modeling, scenario comparison |
| **AI Spend** | [AI Cost Lens](https://github.com/dianuhs/ai-cost-lens) | OpenAI/Anthropic/Bedrock billing → FOCUS 1.0 |
| **SaaS Spend** | [SaaS Cost Analyzer](https://github.com/dianuhs/saas-cost-analyzer) | SaaS billing → FOCUS 1.0, unused licenses, forecasting |
| **Dashboard** | [Cloud Cost Guard](https://github.com/dianuhs/cloud-cost-guard) | Unified spend dashboard |

Most FinOps practices stop at cloud infrastructure. This pipeline extends to the full tech spend picture: infrastructure costs (FinOps Lite), AI model costs (AI Cost Lens), and SaaS subscription costs (SaaS Cost Analyzer) — all normalized to FOCUS 1.0 so they can be aggregated, compared, and surfaced in a single dashboard.

---

**SaaS Cost Analyzer** is a CLI tool that reads SaaS billing exports, auto-detects the provider from CSV column signatures, normalizes to FOCUS 1.0, and surfaces unused licenses and cost trends.

## What It Does

- Reads billing CSV exports from **Salesforce, Snowflake, Databricks, Slack, GitHub, Zoom, Adobe, Stripe**, and generic invoices
- **Auto-detects provider** from CSV column signatures — no `--provider` flag needed
- Normalizes to FOCUS 1.0 output columns: `BilledCost`, `ResourceId`, `ServiceName`, `ChargePeriodStart`, `ChargePeriodEnd`, `ChargeType`, `provider`, `currency`, `usage_amount`, `usage_unit`
- `--group-by product` — rank spend by SaaS tool
- `--group-by user` — rank spend by seat/license holder (ResourceId)
- `--group-by month` — show monthly cost trends
- `--unused` — flag rows where usage_amount is 0 (unused licenses)
- `--forecast` — project next-month cost using linear trend
- `--format json/csv/table` — machine-readable or human-readable output
- `compare` — period-over-period comparison of two billing CSVs
- `normalize` — dump raw FOCUS 1.0 CSV to stdout

## Install

```bash
pip install -e .
# or
pipx install "git+https://github.com/dianuhs/saas-cost-analyzer.git"
```

## Provider Support

| Provider | Detection columns |
|----------|------------------|
| Salesforce | `OrgId` or `ContractId` or `Contract` |
| Snowflake | `WAREHOUSE_NAME` or `CREDITS_USED` |
| Databricks | `workspace_id` or `dbu` |
| Slack | `workspace` + `members` or `active_members` |
| GitHub | `org`/`organization` + `seats`/`active_committers` |
| Zoom | `host_email` or `zoom_license` |
| Adobe | `adobe` anywhere in column names, or `creative_cloud` |
| Stripe | `amount` + `customer_id` + `invoice_id` |
| Generic invoice | `vendor` + `amount` + `invoice_date` |

## Quickstart

### Analyze by product

```bash
saas-cost analyze --file examples/salesforce-sample.csv --group-by product --format table
```

```
  ---------------------------------------------------
  Product       Cost  Usage  Provider
  ---------------------------------------------------
  Salesforce  $750.0000    3.00  salesforce
  ---------------------------------------------------
  TOTAL       $750.0000
```

### Find unused licenses

```bash
saas-cost analyze --file examples/salesforce-sample.csv --unused
```

### Forecast next month

```bash
saas-cost analyze --file examples/slack-sample.csv --forecast --format json
```

### Compare two periods

```bash
saas-cost compare \
  --baseline examples/slack-sample.csv \
  --proposed examples/salesforce-sample.csv \
  --format table
```

### Export raw FOCUS 1.0 CSV

```bash
saas-cost normalize --file examples/generic-invoice-sample.csv > focus-output.csv
```

### Machine-readable pipeline

```bash
saas-cost analyze --file billing.csv --format json | jq '.rows[] | select(.cost > 100)'
saas-cost analyze --file billing.csv --format csv > saas-spend.csv
```

## Exit Codes

| Code | Meaning |
|------|---------|
| `0` | Success |
| `2` | CLI usage / validation error |
| `3` | File not found |
| `4` | Unrecognized CSV format |
| `5` | Internal error |

## Examples

See [`examples/`](examples/) for sample billing CSVs for Salesforce, Snowflake, Slack, and generic invoices.

## License

MIT
