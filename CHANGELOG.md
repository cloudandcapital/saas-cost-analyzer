# Changelog

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
