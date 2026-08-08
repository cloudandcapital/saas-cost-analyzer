# SaaS Cost Analyzer

Evidence-aware, read-only SaaS spend and renewal governance. Version 0.3 preserves the released CCAC 1.0 compatibility result and can explicitly emit a period-aligned canonical SaaS scope under CCAC 1.1.

For the complete six-tool demo and roadmap, see [Tech Spend Command Center](https://github.com/cloudandcapital/tech-spend-command-center).

The repository includes two interfaces:

- `governance` and `ccac` are the production-oriented governance and contract paths.
- `analyze`, `normalize`, and `compare` preserve the v0.1 provider-CSV workflow. Those adapters are convenience mappings, not certified FOCUS datasets and not sufficient evidence for renewal decisions.

## Trust semantics

- Blank or stale activity is **unknown**, never unused.
- Unassigned seats are calculated only when the assignment roster is declared complete.
- Invoice spend is observed separately from contractual commitment.
- A unit price is user-supplied or document-backed; the tool does not invent one.
- A reviewable seat produces estimated renewal reduction capacity, not verified savings.
- The estimate has a zero lower bound and requires owner review, approval, rollback planning, and post-change invoice verification.
- Inactive assigned seats and unassigned seats are disjoint and combined once per application renewal.
- No command changes a vendor, cloud, contract, user, or license.

The public demo is credential-free and uses entirely illustrative data.

## Install and run the released CLI

Python 3.10 or later is required.

```bash
pipx install "git+https://github.com/cloudandcapital/saas-cost-analyzer.git@v0.2.0"
saas-cost --version
```

For development from a clone:

```bash
uv sync --locked --extra dev
uv run saas-cost --version
```

The bundled fixture and its default run metadata are deterministic and entirely illustrative. Its application and user names do not describe a customer.

The installed package can produce the same demo without a repository checkout:

```bash
saas-cost ccac --demo --output saas-cost-result.json
```

The command writes `saas-cost-result.json`; rerunning with the same path
replaces that explicitly named local file.

For a real local export, prepare the same strict contract and declare real mode:

```bash
saas-cost ccac --input /path/to/local-governance-bundle.json --mode real
```

Inputs are only read. Output defaults to standard output; `--output` writes only the requested local result file. Real mode marks source data customer-confidential; it does not upload anything.

The default remains the byte-stable `ccac/1.0.0` compatibility result. To emit the canonical SaaS technology-spend scope from the bundled scenario:

```bash
saas-cost ccac --demo --contract-version 1.1.0 --output saas-cost-result-1.1.json
```

For a local CCAC 1.1 run, supply a strict `saas-cost-analyzer/2.1` governance bundle and a separate `saas-cost-analysis/1.0` declaration:

```bash
saas-cost ccac \
  --input /path/to/governance-v2.1.json \
  --analysis /path/to/analysis-v1.json \
  --mode real \
  --contract-version 1.1.0
```

## Canonical input: `saas-cost-analyzer/2.0`

The JSON bundle has top-level `mode`, `as_of`, `currency`, `inactivity_days`, `activity_freshness_days`, and a non-empty `applications` array. `mode` is either `illustrative` or `real` and must match the CCAC command mode. Every application declares:

| Evidence class | Required fields | Meaning |
|---|---|---|
| Inventory | `id`, `name`, `owner` | Application identity and accountable owner |
| Contract | purchased seats, billing cadence, unit price, commitment, renewal date, notice days, evidence status | Commercial terms; separate from billed spend |
| Invoices | unique ID, half-open period, amount | Observed billed amounts |
| Assignments | unique user ID and nullable last activity date | Entitlement assignment and activity stay distinct |
| Completeness | assignment-roster flag and activity-observed-through date | Determines whether unassigned or inactive classifications are allowed |

Dates use `YYYY-MM-DD`. Invoice periods are half-open (`period_start` inclusive, `period_end` exclusive), must not overlap within an application, and renewal dates may equal but not precede `as_of`. Money is finite and non-negative. One result declares one three-letter ISO currency; per-row mixed currencies are intentionally unsupported. Duplicate application, invoice, and assigned-user IDs fail, and assignments cannot exceed purchased seats. See [the illustrative fixture](examples/illustrative-governance-v2.json) for the complete shape.

Contract `evidence_status` is either `verified_document` or `user_supplied`. `verified_document` means the modeled contract terms were taken from a supporting contract document and gives an otherwise eligible opportunity medium confidence; it does not independently verify savings or contract interpretation. `user_supplied` means the terms were entered without that document-backed status and gives an otherwise eligible opportunity low confidence. Both statuses remain visible in analysis, metric dimensions, findings, and opportunity lineage.

`billing_cadence` supports `monthly`, `quarterly`, and `annual`. `unit_price` is the price for one purchased seat for that cadence, while `commitment` is the total contract amount for the same cadence. The strict model requires `commitment = purchased_seats * unit_price`; tiered, discounted, consumption-priced, or mixed-cadence contracts require a future explicit model rather than an inferred adjustment. Monthly values divide by 1, 3, or 12 cadence months, and annualized values multiply the normalized monthly amount by 12.

Classification is evaluated as of the bundle date:

```text
unassigned = purchased - assigned                 only with a complete roster
inactive   = last_activity < as_of - threshold   only with fresh activity evidence
unknown    = missing activity or stale extract
reviewable = inactive + unassigned               only when both evidence gates pass
monthly unit price = cadence unit price / cadence months
annual modeled capacity = reviewable * monthly unit price * 12
```

The notice deadline is `renewal_date - notice_days`. A notice window is `open` while that deadline is on or after `as_of`, including the deadline itself. It is `closed` after the deadline has passed, and `renewal_due` when the renewal date equals `as_of`. Only open windows can emit an actionable renewal opportunity. Closed or renewal-due windows retain modeled capacity as informational, classify it as blocked for reconciliation, and emit a timing finding; the tool does not move the capacity to a future renewal or infer a new deadline.

The inactivity threshold is the declared `inactivity_days`; there is no hidden three-day rule. Activity exactly on the cutoff is active, while activity strictly before it is inactive. `activity_freshness_days` separately controls whether the activity extract is current enough to classify.

Seat states are deliberately distinct:

- **Purchased seats** are contractual entitlements.
- **Assigned seats** are assignment records and reconcile to active + inactive + unknown-activity seats.
- **Active seats** have activity on or after the declared cutoff with fresh evidence.
- **Inactive seats** are assigned seats with fresh activity strictly before the cutoff.
- **Unassigned seats** are purchased minus assigned, but only for a declared-complete roster.
- **Unused** is not a canonical v0.2 seat class. The legacy CSV command uses that word only for explicit numeric zero usage.
- **Underutilized** is not inferred from login recency alone. This version emits active-seat utilization only when purchased seats are positive, the roster is complete, activity is fresh, and no assigned activity is unknown.

Utilization is `active seats / purchased seats * 100`. When those evidence gates fail or purchased seats are zero, utilization is unknown rather than a misleading zero.

Supplied invoice amount is the observed sum of only the invoice rows present in the input over their explicit half-open periods. Invoice-ledger completeness is not asserted, so this value must not be interpreted as complete current spend. Annualized spend is the cadence-normalized contractual commitment, not an annualization of arbitrary invoice rows. Renewal exposure is that annualized commitment at the explicit renewal date; estimated avoidable capacity is only the reviewable-seat subset at the normalized unit rate. Renewal exposure and annualized commitment are the same accounting amount and must not be added together.

The modeled capacity is non-additive at the executive-report boundary until an approved change is implemented and later invoices verify the outcome.

## CCAC output

`saas-cost ccac` emits a `ccac/1.0.0` `tool_result` containing traceable metrics, data-quality findings, review-first opportunities, evidence hashes, input access classification, and the full SaaS analysis extension. Every metric references evidence. Unknown values use `null` with an explicit reason; there is no silent zero fallback.

Demo mode supplies fixed illustrative `run_id` and `generated_at` values so repeated runs are byte-identical. A pipeline orchestrator can override them with `--run-id` and `--generated-at`; real-input runs generate current metadata unless those options are supplied.

### Canonical SaaS scope under CCAC 1.1

The versioned `saas-cost-analyzer/2.1` bundle adds explicit application vendor, billing provider, billing channel, technology scope, charge identity, and half-open contract service periods. The separate `saas-cost-analysis/1.0` declaration owns the reporting period, accounting basis, coverage, absent-charge treatment, allocation and rounding conventions, component treatments, and total-eligibility claim. Applications are never classified from display names.

The bundled scenario reports `2026-07-01` through `2026-07-22`, start-inclusive and end-exclusive, in UTC. Each application period-net-cost component is calculated with `Decimal` as:

```text
contract commitment × overlapping calendar days ÷ service-period calendar days
```

Each component is rounded once to the USD minor unit using round-half-up. The CRM component allocates `8640 × 21 ÷ 365` to USD `497.10`; Design allocates `1050 × 21 ÷ 92` to USD `239.67`. Their exact serialized sum is the canonical `metric.tech-spend.scope.saas` value of USD `736.77`.

The canonical scope includes only explicitly classified `saas_invoice_or_entitlement` components. Full invoice totals, annualized commitments, renewal exposure, estimated renewal-reduction capacity, direct-AI vendor billing, and cloud-provider billing are excluded. Renewal and opportunity calculations remain independent diagnostics and cannot change period spend without a change to the underlying contract commitment or service period.

Only the exact bundled illustrative governance and analysis fixtures may declare complete coverage and `total_eligible: true`. Real or custom inputs must declare partial coverage, unknown absent charges, and `total_eligible: false`; local files do not establish complete organizational SaaS coverage.

## Legacy provider CSV interface

Auto-detected adapters exist for Salesforce-shaped, Snowflake-shaped, Databricks-shaped, Slack-shaped, GitHub-shaped, Zoom-shaped, Adobe-shaped, Stripe-shaped, and generic invoice CSVs.

```bash
saas-cost analyze --file examples/slack-sample.csv --group-by product --format json
saas-cost normalize --file examples/generic-invoice-sample.csv
saas-cost compare --baseline old.csv --proposed new.csv --format table
```

Legacy `--unused` reports only explicit numeric zero usage. Blank usage is excluded as unknown. Malformed costs fail instead of becoming zero. The legacy forecast is a simple descriptive linear projection and is not a budget, commitment, renewal, or savings forecast.

## Test and validate

```bash
uv run --with pytest pytest -q
uv build
```

CI verifies the exact released CCAC 0.2.0 wheel before using it to validate the CCAC 1.1 result. The validator remains a CI/test acceptance dependency, not a production runtime dependency.

## Pipeline compatibility

SaaS Cost Analyzer `0.3.x` preserves the `ccac/1.0.0` compatibility path and can explicitly emit its canonical SaaS scope through `ccac/1.1.0`. Invoice costs remain observed, contractual commitments remain separate, and renewal capacity remains estimated and non-additive until a later verified outcome. Consumers and other producers remain unchanged.

## Deliberate limits

- No vendor API connectors or direct contract-system integrations are claimed yet.
- `evidence_status=user_supplied` describes the assertion; it is not independent verification.
- Invoice-to-contract variance analysis is not performed because invoice rows may include credits, taxes, or adjustments that this strict bundle does not model.
- Tiered, discounted, consumption-priced, and mixed-cadence contracts are rejected by the strict per-seat model rather than approximated.
- Currency conversion is not performed.
- Identity matching across SaaS applications is not performed.
- Realized savings require a later verified outcome; this producer emits no verified savings.

Licensed under MIT. Public findings and examples must remain non-mutating and clearly illustrative.
