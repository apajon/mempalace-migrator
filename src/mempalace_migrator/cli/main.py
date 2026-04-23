"""CLI entry points: analyze, inspect, migrate, report.

Four subcommands. ``migrate`` runs the full pipeline and writes a target
palace. ``analyze`` and ``inspect`` are read-only. ``report`` re-renders
a saved JSON report.

Exit codes (pinned; never reuse a value):
  0   success, no critical anomalies
  1   Click usage error (bad arguments, missing path) — ENFORCED in main()
  2   detection failed
  3   extraction failed
  4   transform failed
  5   reconstruct failed
  6   report-builder pipeline error (MigratorError from report stage)
  7   validation failed (validate() records anomalies but never raises)
  8   outcome==success but CRITICAL anomaly recorded ("silent failure" guard)
  9   report file unreadable or not valid JSON (report subcommand only)
  10  unexpected / unrecognised stage
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import click

from mempalace_migrator.core.context import MigrationContext
from mempalace_migrator.core.errors import MigratorError
from mempalace_migrator.core.pipeline import (ANALYZE_PIPELINE, FULL_PIPELINE,
                                              MIGRATE_PIPELINE, run_pipeline)
from mempalace_migrator.reporting.text_renderer import render_text

# --- Exit codes -----------------------------------------------------------

EXIT_OK = 0
EXIT_USAGE_ERROR = 1  # Click input-validation (bad args, missing path)
EXIT_DETECTION_FAILED = 2
EXIT_EXTRACTION_FAILED = 3
EXIT_TRANSFORM_FAILED = 4
EXIT_RECONSTRUCT_FAILED = 5
EXIT_REPORT_FAILED = 6  # MigratorError from the report pipeline stage
EXIT_VALIDATE_FAILED = 7
EXIT_CRITICAL_ANOMALY = 8
EXIT_REPORT_FILE_ERROR = 9  # report subcommand: file unreadable / not valid JSON
EXIT_UNEXPECTED = 10

_EXIT_BY_STAGE: dict[str, int] = {
    "detect": EXIT_DETECTION_FAILED,
    "extract": EXIT_EXTRACTION_FAILED,
    "transform": EXIT_TRANSFORM_FAILED,
    "reconstruct": EXIT_RECONSTRUCT_FAILED,
    "report": EXIT_REPORT_FAILED,
    "validate": EXIT_VALIDATE_FAILED,
}


# --- Exit-code logic (pure; no I/O) ---------------------------------------


def _decide_exit_code(
    report: dict[str, Any] | None,
    raised: MigratorError | None,
) -> int:
    """Single authority on process exit code.

    Invariant:
      0  ⟺  outcome == "success" AND top_severity not "critical"
      8   = outcome == "success" but CRITICAL anomaly in report
      2-7 = stage-attributed MigratorError raised
      10  = raised with unrecognised stage, or report is absent/broken
    """
    if raised is not None:
        return _EXIT_BY_STAGE.get(raised.stage, EXIT_UNEXPECTED)
    if report is None:
        return EXIT_UNEXPECTED
    if report.get("outcome") != "success":
        # failure outcome without a raised exception is unexpected
        return EXIT_UNEXPECTED
    top_sev = (report.get("anomaly_summary") or {}).get("top_severity", "none")
    if top_sev == "critical":
        return EXIT_CRITICAL_ANOMALY
    return EXIT_OK


# --- Click group ----------------------------------------------------------


_EXIT_CODE_EPILOG = """
\b
Exit codes:
  0   success (no critical anomalies)
  1   CLI usage error (bad arguments, missing path)
  2   detection failed
  3   extraction failed
  4   transform failed
  5   reconstruct failed
  6   report-builder pipeline error
  7   validation failed
  8   success but CRITICAL anomaly recorded
  9   report file unreadable or not parseable as JSON
  10  unexpected error
"""


@click.group(epilog=_EXIT_CODE_EPILOG)
@click.option("--debug", is_flag=True, default=False, help="Re-raise exceptions instead of swallowing.")
@click.option(
    "--json-output",
    is_flag=True,
    default=False,
    help="Emit report as JSON to stdout. Banner/errors still go to stderr.",
)
@click.option("--quiet", is_flag=True, default=False, help="Suppress report output; honour exit codes only.")
@click.pass_context
def cli(click_ctx: click.Context, debug: bool, json_output: bool, quiet: bool) -> None:
    click_ctx.ensure_object(dict)
    click_ctx.obj["debug"] = debug
    click_ctx.obj["json_output"] = json_output
    click_ctx.obj["quiet"] = quiet


# --- Subcommands ----------------------------------------------------------


@cli.command()
@click.argument(
    "source",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
)
@click.pass_context
def analyze(click_ctx: click.Context, source: Path) -> None:
    """Detect format and extract records. Read-only; no writes."""
    obj = click_ctx.obj
    code = _run_pipeline_command(
        source=source,
        pipeline=ANALYZE_PIPELINE,
        json_output=obj["json_output"],
        quiet=obj["quiet"],
        debug=obj["debug"],
    )
    sys.exit(code)


@cli.command()
@click.argument(
    "source",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
)
@click.pass_context
def inspect(click_ctx: click.Context, source: Path) -> None:
    """Detect, extract, transform, and validate without writing a target palace.

    Reconstruction is skipped because no target path is provided. Parity
    checks are listed as not-performed in the report. Read-only.

    WARNING: validation output is advisory. Absence of check failures does not
    imply correctness.
    """
    obj = click_ctx.obj
    code = _run_pipeline_command(
        source=source,
        pipeline=FULL_PIPELINE,
        json_output=obj["json_output"],
        quiet=obj["quiet"],
        debug=obj["debug"],
    )
    sys.exit(code)


@cli.command()
@click.argument(
    "source",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
)
@click.option(
    "--target",
    required=True,
    type=click.Path(file_okay=True, dir_okay=True, path_type=Path),
    help="Destination directory for the new ChromaDB 1.x palace. Must not exist (or be empty).",
)
@click.pass_context
def migrate(click_ctx: click.Context, source: Path, target: Path) -> None:
    """Migrate SOURCE palace to a ChromaDB 1.x palace at TARGET.

    Runs the full pipeline: detect → extract → transform → reconstruct → validate.
    The source palace is never modified. TARGET must not exist (or be empty); any
    partial write is rolled back on failure.

    Exit codes follow the standard table (see --help at group level).
    """
    obj = click_ctx.obj
    ctx = MigrationContext(source_path=source, target_path=target)
    raised: MigratorError | None = None
    try:
        run_pipeline(ctx, MIGRATE_PIPELINE)
    except MigratorError as exc:
        raised = exc
        click.echo(
            f"[migrator:{ctx.short_run_id}] [{exc.stage}] ERROR: {exc.summary}",
            err=True,
        )
        for d in exc.details:
            click.echo(f"        - {d}", err=True)
        if obj["debug"]:
            raise

    if not obj["quiet"]:
        _emit_report(ctx, obj["json_output"])

    sys.exit(_decide_exit_code(ctx.report, raised))


@cli.command("report")
@click.argument(
    "report_file",
    type=click.Path(exists=True, file_okay=True, dir_okay=False, path_type=Path),
)
@click.pass_context
def report_cmd(click_ctx: click.Context, report_file: Path) -> None:
    """Re-render a JSON report produced by analyze or inspect as text.

    No pipeline execution. Pure formatting. Useful for piping or re-inspection.
    """
    obj = click_ctx.obj
    try:
        raw = report_file.read_text(encoding="utf-8")
        rep: dict[str, Any] = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        click.echo(f"[migrator] report: cannot read {report_file}: {exc}", err=True)
        sys.exit(EXIT_REPORT_FILE_ERROR)

    if not obj["quiet"]:
        click.echo(render_text(rep))
    sys.exit(_decide_exit_code(rep, None))


# --- Shared pipeline runner -----------------------------------------------


def _run_pipeline_command(
    *,
    source: Path,
    pipeline: tuple,
    json_output: bool,
    quiet: bool,
    debug: bool,
) -> int:
    ctx = MigrationContext(source_path=source)
    raised: MigratorError | None = None
    try:
        run_pipeline(ctx, pipeline)
    except MigratorError as exc:
        raised = exc
        click.echo(
            f"[migrator:{ctx.short_run_id}] [{exc.stage}] ERROR: {exc.summary}",
            err=True,
        )
        for d in exc.details:
            click.echo(f"        - {d}", err=True)
        if debug:
            raise

    if not quiet:
        _emit_report(ctx, json_output)

    return _decide_exit_code(ctx.report, raised)


def _emit_report(ctx: MigrationContext, json_output: bool) -> None:
    rep = ctx.report
    if not rep:
        return
    if json_output:
        click.echo(json.dumps(rep, indent=2))
        return
    click.echo(render_text(rep))


def main() -> None:
    """Entry point. Uses standalone_mode=False to remap Click's UsageError
    exit code (2) to EXIT_USAGE_ERROR (1), keeping it distinct from
    EXIT_DETECTION_FAILED (2).
    """
    try:
        cli(standalone_mode=False, obj={})
    except click.UsageError as exc:
        exc.show()
        sys.exit(EXIT_USAGE_ERROR)
    except click.exceptions.Exit as exc:
        sys.exit(exc.code)
    except click.Abort:
        click.echo("Aborted.", err=True)
        sys.exit(EXIT_USAGE_ERROR)


if __name__ == "__main__":
    main()
