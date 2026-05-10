# SaaS Cost Analyzer

[![MIT License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![SaaS](https://img.shields.io/badge/SaaS-license%20governance-blueviolet)](https://github.com/cloudandcapital/saas-cost-analyzer)
[![FOCUS 2026](https://img.shields.io/badge/FOCUS-2026-brightgreen)](https://focus.finops.org)

**SaaS license utilization, per-seat cost analysis, and renewal forecasting for finance teams.**

Part of the [Cloud & Capital](https://github.com/cloudandcapital) FinOps pipeline.  
SaaS spend feeds into [Cloud Cost Guard](https://github.com/cloudandcapital/cloud-cost-guard) — the unified FinOps dashboard.

---

**Features:**
- Per-tool and per-seat cost analysis from billing CSV or API export
- Unused license detection — flag seats with no logins in 30/60/90 days
- Renewal forecasting — project annual SaaS spend and highlight renewal risk
- Waste estimation — dollar cost of unused seats across all tools
- FOCUS 2026 compliant export — SaaS spend in the same schema as cloud
- JSON output compatible with Cloud Cost Guard's `saas_spend` report section

---

## Install

```bash
pip install "git+https://github.com/cloudandcapital/saas-cost-analyzer.git"
# or
pipx install .
```

---

## Usage

```bash
# Analyze SaaS spend from a license CSV
saas-cost-analyzer analyze --input saas-licenses.csv

# Flag unused seats (no login in 90 days)
saas-cost-analyzer unused --threshold 0 --days 90

# Renewal forecast for next 12 months
saas-cost-analyzer forecast --months 12

# Export FOCUS 2026 CSV
saas-cost-analyzer export --format focus2026 --output saas-spend-focus2026.csv

# JSON for Cloud Cost Guard
saas-cost-analyzer analyze --input saas-licenses.csv --format json > saas_spend.json
```

---

## Input CSV Format

| Column | Description |
|--------|-------------|
| `tool` | SaaS tool name (e.g. Notion, Figma) |
| `monthly_cost` | Monthly contract cost in USD |
| `seats_licensed` | Total licensed seats |
| `seats_active` | Actively used seats (last 30 days) |
| `renewal_date` *(optional)* | ISO date of next renewal |
| `contract_term` *(optional)* | `monthly`, `annual` |

---

## Output (JSON)

```json
{
  "total_cost": 35500.00,
  "tool_count": 8,
  "total_unused_licenses": 54,
  "estimated_waste": 6840.00,
  "trend": { "change_percentage": 3.2, "change_amount": 1100.00 },
  "tools": [
    {
      "tool": "Salesforce",
      "cost": 12800.00,
      "seats_licensed": 120,
      "seats_active": 94,
      "unused": 26
    }
  ]
}
```

---

## Part of the Cloud & Capital Pipeline

| Tool | Role |
|------|------|
| [FinOps Lite](https://github.com/cloudandcapital/finops-lite) | Cost pull + FOCUS 2026 export |
| [FinOps Watchdog](https://github.com/cloudandcapital/finops-watchdog) | Anomaly detection |
| [Recovery Economics](https://github.com/cloudandcapital/recovery-economics) | Resilience cost modeling |
| [AI Cost Lens](https://github.com/cloudandcapital/ai-cost-lens) | AI/LLM spend tracking |
| **SaaS Cost Analyzer** | SaaS license governance |
| [Cloud Cost Guard](https://github.com/cloudandcapital/cloud-cost-guard) | Unified dashboard |
| [Tech Spend Command Center](https://github.com/cloudandcapital/tech-spend-command-center) | Executive reporting |

---

## License

MIT © 2025 Diana Molski, Cloud & Capital
