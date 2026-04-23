"""M15 red-path proof — deliberate regression.

This file exists solely to demonstrate that the CI workflow correctly fails
when a test fails. It will be discarded (branch deleted, PR closed) once the
failing run URL is recorded in tests/M15_CI_BASELINE_DESIGN.md §11.

See: tests/M15_CI_BASELINE_DESIGN.md §9 item 10.
"""


def test_deliberate_regression() -> None:
    """Deliberate failure — M15 red-path CI proof. Must NOT be merged."""
    raise AssertionError(
        "M15 red-path proof: this test always fails. "
        "The CI run that fails here proves the gate is real, not a no-op workflow."
    )
