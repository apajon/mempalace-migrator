"""Pipeline orchestration with strict scope enforcement."""

from __future__ import annotations

from collections.abc import Callable

from mempalace_migrator.core.context import MigrationContext
from mempalace_migrator.core.errors import MigratorError, PipelineAbort
from mempalace_migrator.detection.format_detector import (
    CHROMA_0_6,
    MIN_ACCEPT_CONFIDENCE,
    SUPPORTED_VERSION_PAIRS,
    detect_palace_format,
)
from mempalace_migrator.extraction.chroma_06_reader import extract
from mempalace_migrator.reporting.report_builder import build_report

Step = Callable[[MigrationContext], None]


def step_detect(ctx: MigrationContext) -> None:
    result = detect_palace_format(ctx.source_path)
    ctx.detected_format = result

    if result.classification != CHROMA_0_6:
        ctx.add_anomaly(
            type="unsupported_source_format",
            severity="critical",
            stage="detect",
            message=f"source classification {result.classification!r} is not {CHROMA_0_6!r}",
            context={"confidence": round(result.confidence, 3)},
        )
        raise PipelineAbort(
            stage="detect",
            code="unsupported_source_format",
            summary=f"source classification is {result.classification!r}; only {CHROMA_0_6!r} accepted",
            details=[f"confidence={result.confidence:.2f}"]
            + [f"{e.source}/{e.kind}: {e.detail}" for e in result.evidence],
        )

    if result.confidence < MIN_ACCEPT_CONFIDENCE:
        ctx.add_anomaly(
            type="insufficient_detection_confidence",
            severity="critical",
            stage="detect",
            message=f"confidence {result.confidence:.2f} < required {MIN_ACCEPT_CONFIDENCE}",
            context={
                "confidence": round(result.confidence, 3),
                "confidence_band": result.confidence_band,
                "contradictions": [c.to_dict() for c in result.contradictions],
            },
        )
        raise PipelineAbort(
            stage="detect",
            code="insufficient_detection_confidence",
            summary=(
                f"detection confidence {result.confidence:.2f} below required "
                f"{MIN_ACCEPT_CONFIDENCE}; manifest with chromadb_version is required"
            ),
            details=[f"{e.source}/{e.kind}: {e.detail}" for e in result.evidence],
        )

    if not result.is_supported_pair():
        supported = ", ".join(f"{s}->{t}" for s, t in SUPPORTED_VERSION_PAIRS)
        ctx.add_anomaly(
            type="unsupported_version",
            severity="critical",
            stage="detect",
            message=f"source version {result.source_version!r} not in supported list",
            context={"supported_pairs": list(SUPPORTED_VERSION_PAIRS)},
        )
        raise PipelineAbort(
            stage="detect",
            code="unsupported_version",
            summary=(f"source chromadb_version={result.source_version!r} " f"not in supported pairs ({supported})"),
        )


def step_extract(ctx: MigrationContext) -> None:
    ctx.extracted_data = extract(ctx.source_path, ctx)


def step_transform(ctx: MigrationContext) -> None:
    ctx.add_anomaly(
        type="not_implemented",
        severity="low",
        stage="transform",
        message="transformation step is a stub; no transformation performed",
    )


def step_reconstruct(ctx: MigrationContext) -> None:
    ctx.add_anomaly(
        type="not_implemented",
        severity="low",
        stage="reconstruct",
        message="reconstruction step is a stub; no target palace created",
    )


def step_validate(ctx: MigrationContext) -> None:
    ctx.add_anomaly(
        type="not_implemented",
        severity="low",
        stage="validate",
        message="validation step is a stub; no validation performed",
    )


ANALYZE_PIPELINE: tuple[Step, ...] = (step_detect, step_extract)
FULL_PIPELINE: tuple[Step, ...] = (
    step_detect,
    step_extract,
    step_transform,
    step_reconstruct,
    step_validate,
)


def run_pipeline(ctx: MigrationContext, steps: tuple[Step, ...]) -> MigrationContext:
    failure: MigratorError | None = None

    for step in steps:
        try:
            step(ctx)
        except MigratorError as exc:
            failure = exc
            # Critical anomaly may already be recorded by the step; record
            # a generic one if not.
            if not any(a.stage == exc.stage and a.severity == "critical" for a in ctx.anomalies):
                ctx.add_anomaly(
                    type=exc.code,
                    severity="critical",
                    stage=exc.stage,
                    message=exc.summary,
                )
            break

    try:
        ctx.report = build_report(ctx, failure=failure)
    except Exception as report_exc:
        raise MigratorError(
            stage="report",
            code="report_build_failed",
            summary=f"report builder raised: {report_exc!r}",
        ) from report_exc

    if failure is not None:
        raise failure
    return ctx
