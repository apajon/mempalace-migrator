"""Pipeline orchestration with strict scope enforcement."""

from __future__ import annotations

from collections.abc import Callable

from mempalace_migrator.core.context import AnomalyEvidence, AnomalyLocation, AnomalyType, MigrationContext, Severity
from mempalace_migrator.core.errors import MigratorError, PipelineAbort, TransformError
from mempalace_migrator.detection.format_detector import (
    CHROMA_0_6,
    MIN_ACCEPT_CONFIDENCE,
    SUPPORTED_VERSION_PAIRS,
    detect_palace_format,
)
from mempalace_migrator.extraction.chroma_06_reader import extract
from mempalace_migrator.reporting.report_builder import build_report
from mempalace_migrator.transformation import transform
from mempalace_migrator.validation import validate

Step = Callable[[MigrationContext], None]


def step_detect(ctx: MigrationContext) -> None:
    result = detect_palace_format(ctx.source_path)
    ctx.detected_format = result

    if result.classification != CHROMA_0_6:
        ctx.add_anomaly(
            type=AnomalyType.UNSUPPORTED_SOURCE_FORMAT,
            severity=Severity.CRITICAL,
            message=f"source classification {result.classification!r} is not {CHROMA_0_6!r}",
            location=AnomalyLocation(
                stage="detect",
                source="detection",
                path=str(ctx.source_path),
            ),
            evidence=[
                AnomalyEvidence(
                    kind="detection_result",
                    detail=(f"classification={result.classification!r} " f"confidence={result.confidence:.3f}"),
                    data={
                        "classification": result.classification,
                        "confidence": round(result.confidence, 3),
                        "confidence_band": result.confidence_band,
                    },
                ),
            ],
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
            type=AnomalyType.INSUFFICIENT_DETECTION_CONFIDENCE,
            severity=Severity.CRITICAL,
            message=f"confidence {result.confidence:.2f} < required {MIN_ACCEPT_CONFIDENCE}",
            location=AnomalyLocation(
                stage="detect",
                source="detection",
                path=str(ctx.source_path),
            ),
            evidence=[
                AnomalyEvidence(
                    kind="confidence",
                    detail=(
                        f"observed={result.confidence:.3f} "
                        f"band={result.confidence_band} "
                        f"required>={MIN_ACCEPT_CONFIDENCE}"
                    ),
                    data={
                        "confidence": round(result.confidence, 3),
                        "confidence_band": result.confidence_band,
                        "contradictions": [c.to_dict() for c in result.contradictions],
                    },
                ),
            ],
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
            type=AnomalyType.UNSUPPORTED_VERSION,
            severity=Severity.CRITICAL,
            message=f"source version {result.source_version!r} not in supported list",
            location=AnomalyLocation(
                stage="detect",
                source="detection",
                path=str(ctx.source_path),
            ),
            evidence=[
                AnomalyEvidence(
                    kind="version",
                    detail=f"source_version={result.source_version!r} supported=({supported})",
                    data={
                        "source_version": result.source_version,
                        "supported_pairs": list(SUPPORTED_VERSION_PAIRS),
                    },
                ),
            ],
        )
        raise PipelineAbort(
            stage="detect",
            code="unsupported_version",
            summary=(f"source chromadb_version={result.source_version!r} " f"not in supported pairs ({supported})"),
        )


def step_extract(ctx: MigrationContext) -> None:
    ctx.extracted_data = extract(ctx.source_path, ctx)


def step_transform(ctx: MigrationContext) -> None:
    if ctx.extracted_data is None:
        ctx.add_anomaly(
            type=AnomalyType.TRANSFORM_INPUT_MISSING,
            severity=Severity.CRITICAL,
            message="extraction did not produce a result; cannot transform",
            location=AnomalyLocation(stage="transform", source="pipeline"),
            evidence=[
                AnomalyEvidence(
                    kind="observation",
                    detail="ctx.extracted_data is None at transform entry",
                ),
            ],
        )
        raise TransformError(
            stage="transform",
            code="transform_input_missing",
            summary="extraction did not produce a result; cannot transform",
        )
    ctx.transformed_data = transform(ctx)


def step_reconstruct(ctx: MigrationContext) -> None:
    ctx.add_anomaly(
        type=AnomalyType.NOT_IMPLEMENTED,
        severity=Severity.LOW,
        message="reconstruction step is a stub; no target palace created",
        location=AnomalyLocation(stage="reconstruct", source="pipeline"),
        evidence=[
            AnomalyEvidence(
                kind="observation",
                detail="stub stage executed; no work performed",
            ),
        ],
    )


def step_validate(ctx: MigrationContext) -> None:
    """Run validation checks. Never raises MigratorError.

    If extraction did not run, emits NOT_IMPLEMENTED/LOW (consistent with
    other stub stages) and leaves ctx.validation_result as None so the
    stages section marks it 'skipped'.

    When extraction is available, runs structural, consistency, and
    heuristic checks; sets ctx.validation_result to the result.
    Anomalies are emitted by each check family directly into ctx.
    """
    if ctx.extracted_data is None:
        ctx.add_anomaly(
            type=AnomalyType.NOT_IMPLEMENTED,
            severity=Severity.LOW,
            message="validation step skipped; extraction result not available",
            location=AnomalyLocation(stage="validate", source="pipeline"),
            evidence=[
                AnomalyEvidence(
                    kind="observation",
                    detail="extracted_data is None; no validation checks run",
                ),
            ],
        )
        return

    ctx.validation_result = validate(ctx)


ANALYZE_PIPELINE: tuple[Step, ...] = (step_detect, step_extract)
FULL_PIPELINE: tuple[Step, ...] = (
    step_detect,
    step_extract,
    step_transform,
    step_reconstruct,
    step_validate,
)

# Registry mapping CLI subcommand names to their pipeline tuples.
# The CLI uses these references; it does not assemble pipelines itself.
PIPELINES: dict[str, tuple[Step, ...]] = {
    "analyze": ANALYZE_PIPELINE,
    "inspect": FULL_PIPELINE,
}


def run_pipeline(ctx: MigrationContext, steps: tuple[Step, ...]) -> MigrationContext:
    failure: MigratorError | None = None

    for step in steps:
        try:
            step(ctx)
        except MigratorError as exc:
            failure = exc
            # Critical anomaly may already be recorded by the step; record
            # a generic one if not.
            if not any(a.stage == exc.stage and a.severity == Severity.CRITICAL for a in ctx.anomalies):
                ctx.add_anomaly(
                    type=exc.code,
                    severity=Severity.CRITICAL,
                    message=exc.summary,
                    location=AnomalyLocation(
                        stage=exc.stage,
                        source="pipeline",
                        path=str(ctx.source_path),
                    ),
                    evidence=[
                        AnomalyEvidence(
                            kind="exception",
                            detail=exc.summary,
                            data={
                                "code": exc.code,
                                "details": list(exc.details),
                            },
                        ),
                    ],
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
