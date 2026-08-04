# Changelog

## 0.2.0

- Add strict `saas-cost-analyzer/2.0` governance bundles and CCAC 1.0 output.
- Separate contracts, invoices, assignments, activity, evidence freshness, and roster completeness.
- Emit review-first renewal capacity only when evidence gates pass.
- Treat missing or stale activity as unknown and reject silent malformed-money zeros.
- Add a deterministic public illustrative fixture and independent contract-validation path.
- Package the illustrative bundle and expose it through `saas-cost ccac --demo`.
- Make the installed demo byte-identical across separate invocations by fixing its default illustrative run metadata.
- Normalize monthly, quarterly, and annual per-seat contracts with explicit utilization, renewal exposure, and reconciliation lineage.
- Classify notice windows at the exact deadline and renewal boundaries, emitting opportunities only while action remains timely.
- Preserve contract evidence status in output lineage and label supplied invoice amounts without asserting ledger completeness.

## [0.1.0] - 2026-04-21
### Added
- Initial release
- Auto-detection for Salesforce, Snowflake, Databricks, Slack, GitHub, Zoom, Adobe, Stripe, and generic invoice CSV formats
- FOCUS 1.0 normalization for all supported providers
- `analyze` command with `--group-by`, `--unused`, `--forecast`, and `--format` flags
- `compare` command for period-over-period comparison
- `normalize` command for raw FOCUS 1.0 CSV output
- JSON, CSV, and table output formats
- Exit codes: 0 success, 2 validation, 3 file not found, 4 schema error, 5 internal
