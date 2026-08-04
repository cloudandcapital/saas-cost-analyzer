"""SaaS Cost Analyzer CLI — FOCUS 1.0 normalization for SaaS billing exports."""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import click

from .analysis.engine import (
    flag_unused,
    forecast_next_month,
    group_by_month,
    group_by_product,
    group_by_user,
)
from .ccac import build_result
from .governance import GovernanceError, analyze_bundle, load_bundle
from .providers.detector import FOCUS_FIELDNAMES, FocusRecord, load_and_normalize

SCHEMA_VERSION = "1.0"
EXIT_SUCCESS = 0
EXIT_USAGE_ERROR = 2
EXIT_INPUT_FILE_ERROR = 3
EXIT_SCHEMA_DATA_ERROR = 4
EXIT_INTERNAL_ERROR = 5


class InputFileError(Exception):
    pass


class SchemaDataError(Exception):
    pass


@click.group()
@click.version_option(package_name="saas-cost-analyzer")
def cli() -> None:
    """SaaS Cost Analyzer — evidence-aware SaaS spend and renewal governance."""


@cli.command("governance")
@click.option(
    "--input",
    "input_path",
    type=click.Path(path_type=Path, dir_okay=False),
    required=True,
    help="Strict saas-cost-analyzer/2.0 JSON bundle.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["json", "table"]),
    default="table",
    show_default=True,
)
@click.pass_context
def governance(ctx: click.Context, input_path: Path, output_format: str) -> None:
    """Classify seats and renewal evidence without treating unknown activity as unused."""
    try:
        result = analyze_bundle(load_bundle(input_path))
        if output_format == "json":
            click.echo(json.dumps(result, indent=2))
            return
        click.echo(f"SaaS governance as of {result['as_of']} ({result['currency']})")
        for app in result["applications"]:
            unassigned = (
                "unknown"
                if app["unassigned_seats"] is None
                else str(app["unassigned_seats"])
            )
            click.echo(
                f"{app['name']}: purchased={app['purchased_seats']} assigned={app['assigned_seats']} active={app['active_seats']} inactive={app['inactive_seats']} unknown_activity={app['unknown_activity_seats']} unassigned={unassigned}"
            )
            capacity = app["estimated_annual_reduction_capacity_at_renewal"]
            capacity_text = (
                "unknown (evidence gates failed)"
                if capacity is None
                else f"{capacity:.2f} (not verified savings)"
            )
            click.echo(
                f"  renewal={app['renewal_date']} notice_deadline={app['notice_deadline']} estimated_annual_capacity={capacity_text}"
            )
    except GovernanceError as exc:
        click.echo(f"Schema/data error: {exc}", err=True)
        ctx.exit(EXIT_SCHEMA_DATA_ERROR)


@cli.command("ccac")
@click.option(
    "--input",
    "input_path",
    type=click.Path(path_type=Path, dir_okay=False),
    help="Strict saas-cost-analyzer/2.0 JSON bundle.",
)
@click.option(
    "--demo", is_flag=True, help="Use the packaged deterministic illustrative bundle."
)
@click.option(
    "--mode",
    type=click.Choice(["illustrative", "real"]),
    help="Required with --input; demo is always illustrative.",
)
@click.option(
    "--run-id", default=None, help="Optional UUID for deterministic pipeline runs."
)
@click.option("--generated-at", default=None, help="Optional RFC3339 timestamp.")
@click.option(
    "--output",
    "output_path",
    type=click.Path(path_type=Path, dir_okay=False),
    default=None,
    help="Write CCAC JSON instead of stdout.",
)
@click.pass_context
def ccac(
    ctx: click.Context,
    input_path: Path | None,
    demo: bool,
    mode: str | None,
    run_id: str | None,
    generated_at: str | None,
    output_path: Path | None,
) -> None:
    """Emit one CCAC 1.0 tool result to stdout; input access is read-only."""
    try:
        if demo == (input_path is not None):
            raise GovernanceError("choose exactly one of --demo or --input")
        if demo:
            input_path = (
                Path(__file__).parent / "data" / "illustrative-governance-v2.json"
            )
            mode = "illustrative"
            run_id = run_id or "123e4567-e89b-12d3-a456-426614174040"
            generated_at = generated_at or "2026-08-04T12:20:00Z"
        elif mode is None:
            raise GovernanceError("--mode is required with --input")
        assert input_path is not None and mode is not None
        content = (
            json.dumps(
                build_result(
                    input_path, mode=mode, run_id=run_id, generated_at=generated_at
                ),
                indent=2,
            )
            + "\n"
        )
        if output_path is None:
            click.echo(content, nl=False)
        else:
            output_path.write_text(content, encoding="utf-8")
    except GovernanceError as exc:
        click.echo(f"Schema/data error: {exc}", err=True)
        ctx.exit(EXIT_SCHEMA_DATA_ERROR)
    except OSError as exc:
        click.echo(f"Input file error: {exc}", err=True)
        ctx.exit(EXIT_INPUT_FILE_ERROR)


# ---------------------------------------------------------------------------
# analyze
# ---------------------------------------------------------------------------


@cli.command("analyze")
@click.option(
    "--file",
    "file_path",
    type=click.Path(path_type=Path, dir_okay=False),
    required=True,
    help="Path to SaaS billing CSV export.",
)
@click.option(
    "--group-by",
    type=click.Choice(["product", "user", "month"], case_sensitive=False),
    default="product",
    show_default=True,
    help="Aggregate by product, user (ResourceId), or calendar month.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["json", "csv", "table"], case_sensitive=False),
    default="table",
    show_default=True,
    help="Output format.",
)
@click.option(
    "--unused",
    is_flag=True,
    default=False,
    help="Legacy report of rows with explicitly numeric zero usage; blank usage remains unknown.",
)
@click.option(
    "--forecast",
    is_flag=True,
    default=False,
    help="Project next-month cost using linear trend from available data.",
)
@click.pass_context
def analyze(
    ctx: click.Context,
    file_path: Path,
    group_by: str,
    output_format: str,
    unused: bool,
    forecast: bool,
) -> None:
    """Read a SaaS billing CSV, auto-detect provider, and produce FOCUS 1.0 cost analysis."""
    try:
        records = _load(file_path)
        group_key = group_by.lower()
        fmt = output_format.lower()

        rows = _aggregate(records, group_key)
        unused_rows = flag_unused(records) if unused else []
        forecast_data = forecast_next_month(records) if forecast else None

        _emit_analyze(rows, group_key, fmt, unused_rows, forecast_data, sys.stdout)

    except InputFileError as exc:
        click.echo(f"Input file error: {exc}", err=True)
        ctx.exit(EXIT_INPUT_FILE_ERROR)
    except SchemaDataError as exc:
        click.echo(f"Schema/data error: {exc}", err=True)
        ctx.exit(EXIT_SCHEMA_DATA_ERROR)
    except Exception as exc:
        click.echo(f"Internal error: {exc}", err=True)
        ctx.exit(EXIT_INTERNAL_ERROR)


# ---------------------------------------------------------------------------
# compare
# ---------------------------------------------------------------------------


@cli.command("compare")
@click.option(
    "--baseline",
    "baseline_path",
    type=click.Path(path_type=Path, dir_okay=False),
    required=True,
    help="Path to baseline period billing CSV.",
)
@click.option(
    "--proposed",
    "proposed_path",
    type=click.Path(path_type=Path, dir_okay=False),
    required=True,
    help="Path to proposed/comparison period billing CSV.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["json", "csv", "table"], case_sensitive=False),
    default="table",
    show_default=True,
    help="Output format.",
)
@click.pass_context
def compare(
    ctx: click.Context,
    baseline_path: Path,
    proposed_path: Path,
    output_format: str,
) -> None:
    """Compare SaaS spend between two billing periods side by side."""
    try:
        baseline_records = _load(baseline_path)
        proposed_records = _load(proposed_path)
        fmt = output_format.lower()

        baseline_rows = _aggregate(baseline_records, "product")
        proposed_rows = _aggregate(proposed_records, "product")

        _emit_compare(
            baseline_rows,
            proposed_rows,
            str(baseline_path),
            str(proposed_path),
            fmt,
            sys.stdout,
        )

    except InputFileError as exc:
        click.echo(f"Input file error: {exc}", err=True)
        ctx.exit(EXIT_INPUT_FILE_ERROR)
    except SchemaDataError as exc:
        click.echo(f"Schema/data error: {exc}", err=True)
        ctx.exit(EXIT_SCHEMA_DATA_ERROR)
    except Exception as exc:
        click.echo(f"Internal error: {exc}", err=True)
        ctx.exit(EXIT_INTERNAL_ERROR)


# ---------------------------------------------------------------------------
# normalize
# ---------------------------------------------------------------------------


@cli.command("normalize")
@click.option(
    "--file",
    "file_path",
    type=click.Path(path_type=Path, dir_okay=False),
    required=True,
    help="Path to SaaS billing CSV export.",
)
@click.pass_context
def normalize(ctx: click.Context, file_path: Path) -> None:
    """Write raw FOCUS 1.0 normalized CSV to stdout."""
    try:
        records = _load(file_path)
        writer = csv.DictWriter(
            sys.stdout,
            fieldnames=FOCUS_FIELDNAMES,
            lineterminator="\n",
        )
        writer.writeheader()
        for r in records:
            writer.writerow(r.as_dict())
    except InputFileError as exc:
        click.echo(f"Input file error: {exc}", err=True)
        ctx.exit(EXIT_INPUT_FILE_ERROR)
    except SchemaDataError as exc:
        click.echo(f"Schema/data error: {exc}", err=True)
        ctx.exit(EXIT_SCHEMA_DATA_ERROR)
    except Exception as exc:
        click.echo(f"Internal error: {exc}", err=True)
        ctx.exit(EXIT_INTERNAL_ERROR)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load(path: Path) -> List[FocusRecord]:
    if not path.exists():
        raise InputFileError(f"File not found: {path}")
    try:
        return load_and_normalize(path)
    except ValueError as exc:
        raise SchemaDataError(str(exc)) from exc
    except PermissionError as exc:
        raise InputFileError(f"File not readable: {path}") from exc


def _aggregate(records: List[FocusRecord], group_by: str) -> List[Dict[str, Any]]:
    if group_by == "product":
        return group_by_product(records)
    if group_by == "user":
        return group_by_user(records)
    return group_by_month(records)


# ---------------------------------------------------------------------------
# Emit: analyze
# ---------------------------------------------------------------------------


def _emit_analyze(
    rows: List[Dict[str, Any]],
    group_by: str,
    fmt: str,
    unused_rows: List[Dict[str, Any]],
    forecast_data: Dict[str, Any] | None,
    out,
) -> None:
    label_map = {"product": "Product", "user": "User", "month": "Month"}
    label = label_map.get(group_by, "Product")

    if fmt == "json":
        payload: Dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "group_by": group_by,
            "total_cost": round(sum(r["cost"] for r in rows), 4),
            "rows": rows,
        }
        if unused_rows:
            payload["unused_licenses"] = unused_rows
        if forecast_data is not None:
            payload["forecast"] = forecast_data
        json.dump(payload, out, indent=2)
        out.write("\n")

    elif fmt == "csv":
        writer = csv.DictWriter(
            out,
            fieldnames=["key", "cost", "usage_amount", "provider"],
            lineterminator="\n",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        if unused_rows:
            out.write("\n# Unused licenses\n")
            u_writer = csv.DictWriter(
                out,
                fieldnames=list(unused_rows[0].keys()),
                lineterminator="\n",
            )
            u_writer.writeheader()
            for row in unused_rows:
                u_writer.writerow(row)
        if forecast_data is not None:
            out.write(
                f"\n# Forecast\nprojected_cost,{forecast_data['projected_cost']}\n"
            )

    else:
        _print_table(rows, label, out)
        if unused_rows:
            out.write(f"\nUnused licenses ({len(unused_rows)} row(s)):\n")
            for ur in unused_rows:
                out.write(
                    f"  {ur.get('ResourceId', '')}  {ur.get('ServiceName', '')}  cost={ur.get('BilledCost', '')}\n"
                )
        if forecast_data is not None:
            proj = forecast_data["projected_cost"]
            method = forecast_data["method"]
            out.write(f"\nForecast (next month): ${proj:.4f}  [{method}]\n")


def _print_table(rows: List[Dict[str, Any]], label: str, out) -> None:
    if not rows:
        out.write("No data.\n")
        return
    key_w = max(len(label), max(len(str(r["key"])) for r in rows))
    prov_w = max(8, max(len(str(r.get("provider", ""))) for r in rows))
    header = f"  {label:<{key_w}}  {'Cost':>12}  {'Usage':>10}  {'Provider':<{prov_w}}"
    sep = "  " + "-" * (len(header) - 2)
    out.write(f"{sep}\n{header}\n{sep}\n")
    total_cost = 0.0
    for r in rows:
        out.write(
            f"  {str(r['key']):<{key_w}}  ${r['cost']:>11.4f}  "
            f"{r.get('usage_amount', 0):>10.2f}  {str(r.get('provider', '')):<{prov_w}}\n"
        )
        total_cost += r["cost"]
    out.write(f"{sep}\n")
    out.write(f"  {'TOTAL':<{key_w}}  ${total_cost:>11.4f}\n")


# ---------------------------------------------------------------------------
# Emit: compare
# ---------------------------------------------------------------------------


def _emit_compare(
    baseline: List[Dict],
    proposed: List[Dict],
    baseline_name: str,
    proposed_name: str,
    fmt: str,
    out,
) -> None:
    b_map = {r["key"]: r["cost"] for r in baseline}
    p_map = {r["key"]: r["cost"] for r in proposed}
    all_keys = sorted(set(b_map) | set(p_map))

    comparison_rows = []
    for key in all_keys:
        b = b_map.get(key, 0.0)
        p = p_map.get(key, 0.0)
        comparison_rows.append(
            {
                "key": key,
                "baseline": round(b, 4),
                "proposed": round(p, 4),
                "delta": round(p - b, 4),
            }
        )
    comparison_rows.sort(key=lambda r: abs(r["delta"]), reverse=True)

    total_b = sum(r["baseline"] for r in comparison_rows)
    total_p = sum(r["proposed"] for r in comparison_rows)
    total_d = total_p - total_b

    if fmt == "json":
        payload = {
            "schema_version": SCHEMA_VERSION,
            "baseline": baseline_name,
            "proposed": proposed_name,
            "total_baseline": round(total_b, 4),
            "total_proposed": round(total_p, 4),
            "total_delta": round(total_d, 4),
            "rows": comparison_rows,
        }
        json.dump(payload, out, indent=2)
        out.write("\n")
        return

    if fmt == "csv":
        writer = csv.DictWriter(
            out,
            fieldnames=["key", "baseline", "proposed", "delta"],
            lineterminator="\n",
        )
        writer.writeheader()
        for row in comparison_rows:
            writer.writerow(row)
        out.write(
            f"TOTAL,{round(total_b, 4)},{round(total_p, 4)},{round(total_d, 4)}\n"
        )
        return

    # table
    label = "Product"
    key_w = max(
        len(label), max((len(str(r["key"])) for r in comparison_rows), default=8)
    )
    header = f"  {label:<{key_w}}  {'Baseline':>12}  {'Proposed':>12}  {'Delta':>12}"
    sep = "  " + "-" * (len(header) - 2)

    out.write(f"Comparison: {baseline_name}  vs  {proposed_name}\n")
    out.write(f"{sep}\n{header}\n{sep}\n")
    for row in comparison_rows:
        sign = "+" if row["delta"] >= 0 else ""
        out.write(
            f"  {str(row['key']):<{key_w}}  ${row['baseline']:>11.4f}  "
            f"${row['proposed']:>11.4f}  {sign}${row['delta']:>10.4f}\n"
        )
    sign = "+" if total_d >= 0 else ""
    out.write(f"{sep}\n")
    out.write(
        f"  {'TOTAL':<{key_w}}  ${total_b:>11.4f}  ${total_p:>11.4f}  {sign}${total_d:>10.4f}\n"
    )


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
