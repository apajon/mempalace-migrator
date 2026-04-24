"""M19 — Packaging smoke test (phase 22, task 22.5).

Seven assertions from M19_PACKAGING_DESIGN.md §5.3:

  1. python -m build produces exactly one *.whl and one *.tar.gz, exit 0,
     stderr clean of WARNING/ERROR.
  2. Wheel contains only mempalace_migrator/ and *.dist-info/ at top level;
     no forbidden paths (tests/, examples/, .venv/, .github/, *_DESIGN.md).
  3. Wheel METADATA contains required fields and only click + chromadb in
     Requires-Dist.
  4. Create an isolated venv and pip-install the wheel into it (no -e).
  5. mempalace-migrator --help exits 0 and lists the four subcommands.
  6. __version__ from the installed package equals TOOL_VERSION while the
     working directory contains no source tree on sys.path.
  7. Happy-path smoke: build a sample palace and migrate it end-to-end via
     the installed console script; assert exit 0 and output artefact exists.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import venv
import zipfile
from pathlib import Path

import pytest

from mempalace_migrator.reporting.report_builder import TOOL_VERSION

_REPO_ROOT = Path(__file__).parents[2]
_PYPROJECT = _REPO_ROOT / "pyproject.toml"
_EXAMPLES = _REPO_ROOT / "examples"

pytestmark = pytest.mark.slow

# ---------------------------------------------------------------------------
# Shared fixture — build once, reuse across all seven assertions.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def dist_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Run `python -m build` and return the dist/ directory path."""
    dist = tmp_path_factory.mktemp("dist")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--sdist",
            "--wheel",
            "--no-isolation",
            "--outdir",
            str(dist),
            str(_REPO_ROOT),
        ],
        capture_output=True,
        text=True,
    )
    # Store for assertion 1
    _dist_dir.__dict__["_result"] = result
    _dist_dir.__dict__["_dist"] = dist
    return dist


# Work around module-scope fixture needing to expose subprocess result.
# Use a plain dict attached to the module instead.
_build_cache: dict[str, object] = {}


@pytest.fixture(scope="module")
def build_result(tmp_path_factory: pytest.TempPathFactory) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Run build once and expose both the CompletedProcess and dist path."""
    if "result" in _build_cache:
        return _build_cache["result"], _build_cache["dist"]  # type: ignore[return-value]
    dist = tmp_path_factory.mktemp("packaging_dist")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--sdist",
            "--wheel",
            "--no-isolation",
            "--outdir",
            str(dist),
            str(_REPO_ROOT),
        ],
        capture_output=True,
        text=True,
    )
    _build_cache["result"] = result
    _build_cache["dist"] = dist
    return result, dist


@pytest.fixture(scope="module")
def wheel_path(build_result: tuple[subprocess.CompletedProcess[str], Path]) -> Path:
    _, dist = build_result
    wheels = list(dist.glob("*.whl"))
    assert len(wheels) == 1, f"Expected 1 wheel, found: {wheels}"
    return wheels[0]


@pytest.fixture(scope="module")
def installed_venv(
    tmp_path_factory: pytest.TempPathFactory,
    wheel_path: Path,
) -> Path:
    """Create an isolated venv, install the wheel (no editable, no source tree)."""
    venv_dir = tmp_path_factory.mktemp("packaging_venv")
    builder = venv.EnvBuilder(with_pip=True, clear=True)
    builder.create(str(venv_dir))

    python = _venv_python(venv_dir)
    subprocess.run(
        [str(python), "-m", "pip", "install", "--quiet", str(wheel_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return venv_dir


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _venv_python(venv_dir: Path) -> Path:
    if sys.platform == "win32":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _venv_script(venv_dir: Path, name: str) -> Path:
    if sys.platform == "win32":
        return venv_dir / "Scripts" / f"{name}.exe"
    return venv_dir / "bin" / name


# ---------------------------------------------------------------------------
# Assertion 1 — build exits 0, stderr clean of WARNING/ERROR
# ---------------------------------------------------------------------------


def test_build_exits_zero_and_produces_artefacts(
    build_result: tuple[subprocess.CompletedProcess[str], Path],
) -> None:
    result, dist = build_result
    assert result.returncode == 0, (
        f"python -m build failed (exit {result.returncode}).\n" f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    wheels = list(dist.glob("*.whl"))
    sdists = list(dist.glob("*.tar.gz"))
    assert len(wheels) == 1, f"Expected exactly 1 wheel, got: {wheels}"
    assert len(sdists) == 1, f"Expected exactly 1 sdist, got: {sdists}"

    bad_lines = [line for line in result.stderr.splitlines() if "WARNING" in line.upper() or "ERROR" in line.upper()]
    assert not bad_lines, "python -m build emitted WARNING/ERROR lines:\n" + "\n".join(bad_lines)


# ---------------------------------------------------------------------------
# Assertion 2 — wheel contains only permitted top-level directories
# ---------------------------------------------------------------------------

_FORBIDDEN_PREFIXES = (
    "tests/",
    "tests_",
    "examples/",
    ".venv/",
    "__pycache__/",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    ".github/",
)
_FORBIDDEN_SUFFIXES = ("_DESIGN.md",)


def test_wheel_contents_are_minimal(wheel_path: Path) -> None:
    with zipfile.ZipFile(wheel_path) as zf:
        names = zf.namelist()

    forbidden = [
        n
        for n in names
        if any(n.startswith(p) for p in _FORBIDDEN_PREFIXES) or any(n.endswith(s) for s in _FORBIDDEN_SUFFIXES)
    ]
    assert not forbidden, f"Wheel contains forbidden paths:\n" + "\n".join(forbidden)

    top_level = {n.split("/")[0] for n in names if "/" in n}
    for tld in top_level:
        assert tld == "mempalace_migrator" or tld.endswith(".dist-info"), (
            f"Unexpected top-level directory in wheel: {tld!r}. "
            "Only mempalace_migrator/ and *.dist-info/ are allowed."
        )


# ---------------------------------------------------------------------------
# Assertion 3 — wheel METADATA is complete
# ---------------------------------------------------------------------------


def test_wheel_metadata(wheel_path: Path) -> None:
    with zipfile.ZipFile(wheel_path) as zf:
        metadata_names = [n for n in zf.namelist() if n.endswith("/METADATA")]
        assert metadata_names, "No METADATA file found in wheel"
        metadata = zf.read(metadata_names[0]).decode("utf-8")

    assert "Name: mempalace-migrator" in metadata, "Name missing from METADATA"
    assert f"Version: {TOOL_VERSION}" in metadata, f"Version {TOOL_VERSION!r} missing from METADATA"
    assert "Requires-Python: >=3.12" in metadata, "Requires-Python missing"
    assert "License" in metadata, "License missing from METADATA"

    requires_dist = [
        line.removeprefix("Requires-Dist:").strip()
        for line in metadata.splitlines()
        if line.startswith("Requires-Dist:") and "extra ==" not in line
    ]
    assert len(requires_dist) == 2, f"Expected exactly 2 runtime Requires-Dist entries, got: {requires_dist}"
    click_present = any("click" in r for r in requires_dist)
    chromadb_present = any("chromadb" in r for r in requires_dist)
    assert click_present, f"click missing from Requires-Dist: {requires_dist}"
    assert chromadb_present, f"chromadb missing from Requires-Dist: {requires_dist}"


# ---------------------------------------------------------------------------
# Assertion 4 — isolated venv install (fixture already does the install)
# ---------------------------------------------------------------------------


def test_isolated_venv_install_succeeds(installed_venv: Path) -> None:
    assert installed_venv.exists(), "venv directory was not created"
    python = _venv_python(installed_venv)
    assert python.exists(), f"venv python not found at {python}"
    script = _venv_script(installed_venv, "mempalace-migrator")
    assert script.exists(), f"console script not found at {script}"


# ---------------------------------------------------------------------------
# Assertion 5 — mempalace-migrator --help exits 0 and lists subcommands
# ---------------------------------------------------------------------------


def test_console_script_help(installed_venv: Path) -> None:
    script = _venv_script(installed_venv, "mempalace-migrator")
    result = subprocess.run(
        [str(script), "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"mempalace-migrator --help exited {result.returncode}.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    for subcommand in ("analyze", "inspect", "migrate", "report"):
        assert subcommand in result.stdout, f"Subcommand {subcommand!r} not listed in --help output:\n{result.stdout}"


# ---------------------------------------------------------------------------
# Assertion 6 — __version__ from installed wheel == TOOL_VERSION
#               (no source tree on sys.path)
# ---------------------------------------------------------------------------


def test_installed_version_matches_tool_version(
    installed_venv: Path,
    tmp_path: Path,
) -> None:
    """Run the version check from a tmp dir that has no src/ on sys.path."""
    python = _venv_python(installed_venv)
    code = "import importlib.metadata; " "v = importlib.metadata.version('mempalace-migrator'); " "print(v)"
    result = subprocess.run(
        [str(python), "-c", code],
        capture_output=True,
        text=True,
        cwd=str(tmp_path),  # neutral cwd — no src/ leak
        env={**os.environ, "PYTHONPATH": ""},  # strip any inherited PYTHONPATH
    )
    assert result.returncode == 0, f"importlib.metadata.version() failed:\n{result.stderr}"
    installed_version = result.stdout.strip()
    assert installed_version == TOOL_VERSION, (
        f"Installed wheel reports version {installed_version!r} " f"but TOOL_VERSION is {TOOL_VERSION!r}"
    )


# ---------------------------------------------------------------------------
# Assertion 7 — happy-path end-to-end smoke via installed console script
# ---------------------------------------------------------------------------


def test_e2e_happy_path_via_installed_script(
    installed_venv: Path,
    tmp_path: Path,
) -> None:
    src_palace = tmp_path / "src_palace"
    tgt_palace = tmp_path / "tgt_palace"

    # Build a sample palace by importing the builder module and calling build().
    import importlib.util

    spec = importlib.util.spec_from_file_location("make_sample_palace", _EXAMPLES / "make_sample_palace.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    mod.build(src_palace)  # type: ignore[attr-defined]

    assert (src_palace / "chroma.sqlite3").exists(), "Sample palace creation failed"

    script = _venv_script(installed_venv, "mempalace-migrator")
    result = subprocess.run(
        [
            str(script),
            "migrate",
            str(src_palace),
            "--target",
            str(tgt_palace),
        ],
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
        env={**os.environ, "PYTHONPATH": ""},
    )
    assert result.returncode == 0, (
        f"migrate exited {result.returncode}.\n" f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert (tgt_palace / "chroma.sqlite3").exists(), (
        f"Expected tgt_palace/chroma.sqlite3 after migrate, but it is missing. "
        f"Contents of {tgt_palace}: {list(tgt_palace.iterdir()) if tgt_palace.exists() else 'directory does not exist'}"
    )
