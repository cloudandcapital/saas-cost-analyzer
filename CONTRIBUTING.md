# Contributing

Run the test suite with `pytest tests/ -q` before submitting a pull request. All providers must produce valid FOCUS 1.0 output columns (`BilledCost`, `ResourceId`, `ServiceName`, `ChargePeriodStart`, `ChargePeriodEnd`, `ChargeType`, `provider`, `currency`, `usage_amount`, `usage_unit`) — this is the output contract the pipeline depends on. To add a new provider, extend `detect_provider()` in `saas_cost_analyzer/providers/detector.py`, add a normalizer function, add a sample CSV in `examples/`, and cover both detection and normalization in `tests/test_saas_cost.py`.
