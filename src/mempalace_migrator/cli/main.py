"""Minimal CLI: only `analyze` is exposed in this foundation."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click
from mempalace_migrator.core.context import MigrationContext
from mempalace_migrator.core.errors import MigratorError
from mempalace_migrator.core.pipeline import ANALYZE_PIPELINE, run_pipeline

EXIT_OK = 0
EXIT_DETECTION_FAILED = 2
EXIT_EXTRACTION_FAILED = 3
EXIT_REPORT_FAILED = 6
EXIT_UNEXPECTED = 10

_EXIT_BY_STAGE = {
    "detect": EXIT_DETECTION_FAILED,
    "extract": EXIT_EXTRACTION_FAILED,
    "report": EXIT_REPORT_FAILED,
}


@click.group()
@click.option("--debug", is_flag=True, default=False)
@click.pass_context
def cli(click_ctx: click.Context, debug: bool) -> None:
    click_ctx.ensure_object(dict)
    click_ctx.obj["debug"] = debug


@cli.command()
@click.argument(
    "source",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
)
@click.option("--json-output", is_flag=True, default=False)
@click.pass_context
def analyze(click_ctx: click.Context, source: Path, json_output: bool) -> None:
    """Read a palace and report what would happen on migration. No writes."""
    ctx = MigrationContext(source_path=source)
    debug = bool(click_ctx.obj.get("debug"))

    try:
        run_pipeline(ctx, ANALYZE_PIPELINE)
    except MigratorError as exc:
        _emit_report(ctx, json_output)
        click.echo(
            f"[migrator:{ctx.short_run_id}] [{exc.stage}] ERROR: {exc.summary}",
            err=True,
        )
        for d in exc.details:
            click.echo(f"        - {d}", err=True)
        if debug:
            raise
        sys.exit(_EXIT_BY_STAGE.get(exc.stage, EXIT_UNEXPECTED))

    _emit_report(ctx, json_output)
    sys.exit(EXIT_OK)


def _emit_report(ctx: MigrationContext, json_output: bool) -> None:
    if json_output:
        click.echo(json.dumps(ctx.report, indent=2, default=str))
        return

    rep = ctx.report
    if not rep:
        return

    click.echo(f"run_id: {rep['run_id']}")
    click.echo(f"outcome: {rep['outcome']}")

    detection = rep.get("detection")
    if detection:
        click.echo(
            f"detection: {detection['classification']} "
            f"(confidence={detection['confidence']}, "
            f"source_version={detection.get('source_version')})"
        )

    stats = rep.get("extraction_stats")
    if stats:
        click.echo(
            f"extraction_stats: total={stats['total_rows']} "
            f"parsed={stats['parsed_rows']} failed={stats['failed_rows']} "
            f"rate={stats['parse_rate']}"
        )

    summary = rep["anomaly_summary"]
    click.echo(
        f"anomalies: total={summary['total']} "
        f"critical={summary['by_severity']['critical']} "
        f"high={summary['by_severity']['high']} "
        f"medium={summary['by_severity']['medium']} "
        f"low={summary['by_severity']['low']}"
    )
    for a in rep["anomalies"]:
        click.echo(f"  - [{a['severity']}/{a['stage']}/{a['type']}] {a['message']}")

    click.echo(
        f"explicitly_not_checked: {len(rep['explicitly_not_checked'])} items"
    )


def main() -> None:
    cli(obj={})


if __name__ == "__main__":
    main()
