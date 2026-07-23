from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import shutil
import tarfile
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest

from agent_physics.release_candidate import (
    CHECKSUM_FILENAME,
    EXACT_LIMITATIONS,
    PROVENANCE_FILENAME,
    SBOM_FILENAME,
    VALIDATION_FILENAME,
    ReleaseCandidateError,
    _build_sbom,
    _read_canonical_json,
    _read_checksums,
    _validate_archive_names,
    _verify_core_metadata,
    canonical_json_bytes,
    generate_release_candidate,
    normalize_sdist,
    sha256_file,
    validate_distributions,
    verify_release_candidate,
)


REVISION = "a" * 40
SOURCE_DATE_EPOCH = 1_700_000_000


def _metadata() -> bytes:
    return (
        "Metadata-Version: 2.4\n"
        "Name: finite-demo\n"
        "Version: 1.2.3\n"
        "Requires-Python: >=3.11\n"
        "Requires-Dist: PyYAML<7,>=6.0.2\n"
        'Requires-Dist: demo-extra==2.0; extra == "test"\n'
        "\n"
    ).encode()


def _wheel_record(files: dict[str, bytes], record_name: str) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    for name in sorted(files):
        payload = files[name]
        digest = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=").decode()
        writer.writerow((name, f"sha256={digest}", str(len(payload))))
    writer.writerow((record_name, "", ""))
    return output.getvalue().encode()


def _write_tar_file(archive: tarfile.TarFile, name: str, payload: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.mode = 0o644
    info.mtime = 0
    info.size = len(payload)
    archive.addfile(info, io.BytesIO(payload))


def _write_tar_directory(archive: tarfile.TarFile, name: str) -> None:
    info = tarfile.TarInfo(name)
    info.type = tarfile.DIRTYPE
    info.mode = 0o755
    info.mtime = 0
    archive.addfile(info)


def _rewrite_wheel(path: Path, mutation: Callable[[dict[str, bytes]], None]) -> None:
    with zipfile.ZipFile(path) as archive:
        files = {name: archive.read(name) for name in archive.namelist() if not name.endswith("/")}
    record_name = next(name for name in files if name.endswith(".dist-info/RECORD"))
    files.pop(record_name)
    mutation(files)
    files[record_name] = _wheel_record(files, record_name)
    with zipfile.ZipFile(path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in files.items():
            archive.writestr(name, payload)


def _rewrite_sdist_file(path: Path, filename: str, payload: bytes) -> None:
    with tarfile.open(path, mode="r:gz") as archive:
        files: dict[str, bytes] = {}
        for member in archive.getmembers():
            if not member.isfile():
                continue
            extracted = archive.extractfile(member)
            assert extracted is not None
            files[member.name] = extracted.read()
    files[filename] = payload
    with tarfile.open(path, mode="w:gz") as archive:
        for name, value in files.items():
            _write_tar_file(archive, name, value)


def _refresh_candidate_checksum(candidate: Path, filename: str) -> None:
    checksum_path = candidate / CHECKSUM_FILENAME
    records = {
        line.split("  ", 1)[1]: line.split("  ", 1)[0]
        for line in checksum_path.read_text(encoding="ascii").splitlines()
    }
    records[filename] = sha256_file(candidate / filename)
    checksum_path.write_text(
        "".join(f"{records[name]}  {name}\n" for name in sorted(records)),
        encoding="ascii",
        newline="\n",
    )


def _mutate_candidate_json(
    candidate: Path,
    filename: str,
    mutation: Callable[[dict[str, object]], None],
) -> None:
    path = candidate / filename
    value = json.loads(path.read_bytes())
    mutation(value)
    path.write_bytes(canonical_json_bytes(value))
    _refresh_candidate_checksum(candidate, filename)


def _fixture_project(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "project"
    package = root / "src" / "finite_demo"
    package.mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        "[project]\nname = \"finite-demo\"\nversion = \"1.2.3\"\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text("# demo\n", encoding="utf-8")
    (root / "LICENSE").write_text("test license\n", encoding="utf-8")
    (package / "__init__.py").write_text('__version__ = "1.2.3"\n', encoding="utf-8")
    (package / "worker.py").write_text("VALUE = 1\n", encoding="utf-8")

    dist = root / "dist"
    dist.mkdir()
    dist_info = "finite_demo-1.2.3.dist-info"
    record_name = f"{dist_info}/RECORD"
    wheel_files = {
        "finite_demo/__init__.py": (package / "__init__.py").read_bytes(),
        "finite_demo/worker.py": (package / "worker.py").read_bytes(),
        f"{dist_info}/METADATA": _metadata(),
        f"{dist_info}/WHEEL": (
            b"Wheel-Version: 1.0\nGenerator: fixture\n"
            b"Root-Is-Purelib: true\nTag: py3-none-any\n\n"
        ),
        f"{dist_info}/entry_points.txt": b"[console_scripts]\nfinite-demo=finite_demo:main\n",
        f"{dist_info}/licenses/LICENSE": b"test license\n",
    }
    wheel_files[record_name] = _wheel_record(wheel_files, record_name)
    wheel_path = dist / "finite_demo-1.2.3-py3-none-any.whl"
    with zipfile.ZipFile(wheel_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in wheel_files.items():
            archive.writestr(name, payload)

    prefix = "finite_demo-1.2.3"
    sdist_files = {
        f"{prefix}/LICENSE": (root / "LICENSE").read_bytes(),
        f"{prefix}/README.md": (root / "README.md").read_bytes(),
        f"{prefix}/pyproject.toml": (root / "pyproject.toml").read_bytes(),
        f"{prefix}/PKG-INFO": _metadata(),
        f"{prefix}/src/finite_demo/__init__.py": (package / "__init__.py").read_bytes(),
        f"{prefix}/src/finite_demo/worker.py": (package / "worker.py").read_bytes(),
    }
    with tarfile.open(dist / "finite_demo-1.2.3.tar.gz", mode="w:gz") as archive:
        for directory in (
            prefix,
            f"{prefix}/src",
            f"{prefix}/src/finite_demo",
        ):
            _write_tar_directory(archive, directory)
        for name, payload in sdist_files.items():
            _write_tar_file(archive, name, payload)
    return root, dist


def test_validates_wheel_record_sdist_contents_and_metadata(tmp_path: Path) -> None:
    root, dist = _fixture_project(tmp_path)

    result = validate_distributions(dist, root)

    assert result["classification"] == "release-candidate"
    assert result["release_ready"] is False
    assert result["project"] == {"name": "finite-demo", "version": "1.2.3"}
    assert tuple(result["limitations"]) == EXACT_LIMITATIONS
    assert result["source_module_count"] == 2
    assert result["requires_dist"] == [
        'demo-extra==2.0; extra == "test"',
        "PyYAML<7,>=6.0.2",
    ]
    wheel = next(item for item in result["distributions"] if item["kind"] == "wheel")
    assert wheel["record_verified"] is True


def test_candidate_outputs_are_deterministic_self_checking_and_explicitly_nonrelease(
    tmp_path: Path,
) -> None:
    root, dist = _fixture_project(tmp_path)
    first = tmp_path / "candidate-a"
    second = tmp_path / "candidate-b"

    generate_release_candidate(
        dist_dir=dist,
        output_dir=first,
        project_root=root,
        source_revision=REVISION,
        source_date_epoch=SOURCE_DATE_EPOCH,
        source_state="dirty",
    )
    generate_release_candidate(
        dist_dir=dist,
        output_dir=second,
        project_root=root,
        source_revision=REVISION,
        source_date_epoch=SOURCE_DATE_EPOCH,
        source_state="dirty",
    )

    assert {path.name for path in first.iterdir()} == {path.name for path in second.iterdir()}
    for first_path in first.iterdir():
        assert first_path.read_bytes() == (second / first_path.name).read_bytes()

    verified = verify_release_candidate(first)
    assert verified["release_ready"] is False
    assert verified["classification"] == "release-candidate"
    assert CHECKSUM_FILENAME in verified["files"]

    validation = json.loads((first / VALIDATION_FILENAME).read_bytes())
    assert validation["source"]["state"] == "dirty"
    assert validation["source"]["state_is_caller_asserted"] is True
    assert tuple(validation["limitations"]) == EXACT_LIMITATIONS
    sbom = json.loads((first / SBOM_FILENAME).read_bytes())
    assert sbom["bomFormat"] == "CycloneDX"
    assert sbom["metadata"]["timestamp"] == "2023-11-14T22:13:20Z"
    provenance = json.loads((first / PROVENANCE_FILENAME).read_bytes())
    parameters = provenance["predicate"]["buildDefinition"]["externalParameters"]
    assert parameters["releaseReady"] is False
    assert parameters["sourceStateIsCallerAsserted"] is True
    assert tuple(parameters["limitations"]) == EXACT_LIMITATIONS
    assert provenance["predicate"]["buildDefinition"]["resolvedDependencies"] == []


def test_candidate_verifier_rejects_tampering_and_stale_output(tmp_path: Path) -> None:
    root, dist = _fixture_project(tmp_path)
    output = tmp_path / "candidate"
    generate_release_candidate(
        dist_dir=dist,
        output_dir=output,
        project_root=root,
        source_revision=REVISION,
        source_date_epoch=SOURCE_DATE_EPOCH,
        source_state="clean",
    )

    wheel = next(output.glob("*.whl"))
    wheel.write_bytes(wheel.read_bytes() + b"tampered")
    with pytest.raises(ReleaseCandidateError, match="checksum mismatch"):
        verify_release_candidate(output)

    with pytest.raises(ReleaseCandidateError, match="must be empty"):
        generate_release_candidate(
            dist_dir=dist,
            output_dir=output,
            project_root=root,
            source_revision=REVISION,
            source_date_epoch=SOURCE_DATE_EPOCH,
            source_state="clean",
        )


def test_normalized_sdist_and_evidence_are_byte_deterministic(tmp_path: Path) -> None:
    root, dist = _fixture_project(tmp_path)
    source = next(dist.glob("*.tar.gz"))
    first_dir = tmp_path / "normalized-a"
    second_dir = tmp_path / "normalized-b"
    first_dir.mkdir()
    second_dir.mkdir()

    first = first_dir / source.name
    second = second_dir / source.name
    first_result = normalize_sdist(source, first, source_date_epoch=SOURCE_DATE_EPOCH)
    second_result = normalize_sdist(source, second, source_date_epoch=SOURCE_DATE_EPOCH)

    assert first.read_bytes() == second.read_bytes()
    assert first_result == second_result
    shutil.copy2(next(dist.glob("*.whl")), first_dir)
    assert validate_distributions(first_dir, root)["release_ready"] is False


def test_recomputed_record_cannot_hide_changed_or_extra_importable_code(tmp_path: Path) -> None:
    root, dist = _fixture_project(tmp_path / "changed")
    wheel = next(dist.glob("*.whl"))
    _rewrite_wheel(wheel, lambda files: files.__setitem__("finite_demo/worker.py", b"VALUE=9\n"))
    with pytest.raises(ReleaseCandidateError, match="module bytes differ"):
        validate_distributions(dist, root)

    root, dist = _fixture_project(tmp_path / "extra")
    wheel = next(dist.glob("*.whl"))
    _rewrite_wheel(
        wheel,
        lambda files: files.__setitem__("unexpected_package/__init__.py", b"SURPRISE=True\n"),
    )
    with pytest.raises(ReleaseCandidateError, match="undeclared package payloads"):
        validate_distributions(dist, root)


def test_sdist_source_bytes_and_distribution_tags_are_bound(tmp_path: Path) -> None:
    root, dist = _fixture_project(tmp_path / "sdist")
    sdist = next(dist.glob("*.tar.gz"))
    _rewrite_sdist_file(
        sdist,
        "finite_demo-1.2.3/src/finite_demo/worker.py",
        b"VALUE=9\n",
    )
    with pytest.raises(ReleaseCandidateError, match="sdist module bytes differ"):
        validate_distributions(dist, root)

    root, dist = _fixture_project(tmp_path / "tag")
    wheel = next(dist.glob("*.whl"))
    wheel.rename(wheel.with_name("finite_demo-1.2.3-py2-none-any.whl"))
    with pytest.raises(ReleaseCandidateError, match="py3-none-any"):
        validate_distributions(dist, root)


def test_distribution_filenames_discovery_and_dependency_metadata_fail_closed(
    tmp_path: Path,
) -> None:
    root, dist = _fixture_project(tmp_path / "missing-dir")
    with pytest.raises(ReleaseCandidateError, match="does not exist"):
        validate_distributions(tmp_path / "absent", root)

    root, dist = _fixture_project(tmp_path / "duplicate")
    wheel = next(dist.glob("*.whl"))
    shutil.copy2(wheel, dist / "second-1.0-py3-none-any.whl")
    with pytest.raises(ReleaseCandidateError, match="exactly one wheel"):
        validate_distributions(dist, root)

    for folder, filename, message in (
        ("invalid", "bad.whl", "invalid distribution filename"),
        ("name", "other-1.2.3-py3-none-any.whl", "project name"),
        ("version", "finite_demo-9.0-py3-none-any.whl", "project version"),
    ):
        root, dist = _fixture_project(tmp_path / folder)
        wheel = next(dist.glob("*.whl"))
        wheel.rename(wheel.with_name(filename))
        with pytest.raises(ReleaseCandidateError, match=message):
            validate_distributions(dist, root)

    root, dist = _fixture_project(tmp_path / "dependencies")
    sdist = next(dist.glob("*.tar.gz"))
    changed_metadata = _metadata().replace(
        b"Requires-Dist: PyYAML<7,>=6.0.2\n",
        b"Requires-Dist: changed-package==9\n",
    )
    _rewrite_sdist_file(sdist, "finite_demo-1.2.3/PKG-INFO", changed_metadata)
    with pytest.raises(ReleaseCandidateError, match="dependency metadata differ"):
        validate_distributions(dist, root)


def test_wheel_structure_and_archive_corruption_fail_closed(tmp_path: Path) -> None:
    root, dist = _fixture_project(tmp_path / "purelib")
    wheel = next(dist.glob("*.whl"))

    def disable_purelib(files: dict[str, bytes]) -> None:
        name = next(item for item in files if item.endswith(".dist-info/WHEEL"))
        files[name] = files[name].replace(b"Root-Is-Purelib: true", b"Root-Is-Purelib: false")

    _rewrite_wheel(wheel, disable_purelib)
    with pytest.raises(ReleaseCandidateError, match="Root-Is-Purelib"):
        validate_distributions(dist, root)

    root, dist = _fixture_project(tmp_path / "missing-module")
    wheel = next(dist.glob("*.whl"))
    _rewrite_wheel(wheel, lambda files: files.pop("finite_demo/worker.py"))
    with pytest.raises(ReleaseCandidateError, match="modules do not match"):
        validate_distributions(dist, root)

    root, dist = _fixture_project(tmp_path / "missing-entrypoint")
    wheel = next(dist.glob("*.whl"))

    def remove_entrypoint(files: dict[str, bytes]) -> None:
        files.pop(next(item for item in files if item.endswith(".dist-info/entry_points.txt")))

    _rewrite_wheel(wheel, remove_entrypoint)
    with pytest.raises(ReleaseCandidateError, match="wheel entry points"):
        validate_distributions(dist, root)

    root, dist = _fixture_project(tmp_path / "bad-zip")
    next(dist.glob("*.whl")).write_bytes(b"not a zip")
    with pytest.raises(ReleaseCandidateError, match="cannot inspect wheel"):
        validate_distributions(dist, root)


def test_project_parse_sbom_and_json_reader_errors_are_explicit(tmp_path: Path) -> None:
    root, dist = _fixture_project(tmp_path / "valid")
    with pytest.raises(ReleaseCandidateError, match="cannot read project metadata"):
        validate_distributions(tmp_path / "unused", tmp_path / "no-project")

    with pytest.raises(ReleaseCandidateError, match="Requires-Dist"):
        _build_sbom(
            {"project": {"name": "demo", "version": "1"}, "requires_dist": ["???"]},
            "2026-01-01T00:00:00Z",
        )

    invalid = tmp_path / "invalid.json"
    invalid.write_text("not-json", encoding="utf-8")
    with pytest.raises(ReleaseCandidateError, match="cannot read"):
        _read_canonical_json(invalid)
    invalid.write_text("[]\n", encoding="utf-8")
    with pytest.raises(ReleaseCandidateError, match="JSON object"):
        _read_canonical_json(invalid)
    invalid.write_text('{\n  "value": 1\n}\n', encoding="utf-8")
    with pytest.raises(ReleaseCandidateError, match="not canonical"):
        _read_canonical_json(invalid)
    assert root.is_dir() and dist.is_dir()


def test_archive_path_and_revision_validation_fail_closed(tmp_path: Path) -> None:
    root, dist = _fixture_project(tmp_path)
    wheel = next(dist.glob("*.whl"))
    with zipfile.ZipFile(wheel, mode="a") as archive:
        archive.writestr(".env", "SECRET=value")
    with pytest.raises(ReleaseCandidateError, match="environment file"):
        validate_distributions(dist, root)

    with pytest.raises(ReleaseCandidateError, match="40-character"):
        generate_release_candidate(
            dist_dir=dist,
            output_dir=tmp_path / "candidate",
            project_root=root,
            source_revision="main",
            source_date_epoch=SOURCE_DATE_EPOCH,
            source_state="clean",
        )


@pytest.mark.parametrize(
    ("names", "message"),
    [
        ([""], "control characters"),
        (["safe/line\nfeed"], "control characters"),
        ([r"safe\backslash"], "backslash"),
        (["../escape"], "unsafe"),
        (["C:/drive"], "drive-qualified"),
        (["root/.git/config"], "forbidden path"),
        (["root/ spaced"], "cross-platform normalized"),
        (["root/CON.txt"], "reserved device"),
        (["root/.env.production"], "environment file"),
        (["root/private.pem"], "forbidden file type"),
        (["root/Value", "root/value"], "duplicate path"),
    ],
)
def test_archive_member_names_fail_closed(names: list[str], message: str) -> None:
    with pytest.raises(ReleaseCandidateError, match=message):
        _validate_archive_names(names, archive_kind="test")


@pytest.mark.parametrize("value", [{"bad": {1}}, float("nan")])
def test_canonical_json_rejects_non_json_values(value: object) -> None:
    with pytest.raises(ReleaseCandidateError, match="canonical JSON"):
        canonical_json_bytes(value)


def test_core_metadata_name_and_version_are_bound() -> None:
    from email.parser import BytesParser

    project = {"name": "finite-demo", "version": "1.2.3"}
    wrong_name = BytesParser().parsebytes(b"Name: other\nVersion: 1.2.3\n\n")
    with pytest.raises(ReleaseCandidateError, match="project name"):
        _verify_core_metadata(wrong_name, project, "fixture")
    wrong_version = BytesParser().parsebytes(b"Name: finite-demo\nVersion: 9.0\n\n")
    with pytest.raises(ReleaseCandidateError, match="version"):
        _verify_core_metadata(wrong_version, project, "fixture")


def test_normalizer_rejects_invalid_inputs_and_special_members(tmp_path: Path) -> None:
    root, dist = _fixture_project(tmp_path / "valid")
    source = next(dist.glob("*.tar.gz"))
    destination = tmp_path / "out" / source.name

    for epoch in (-1, True, 0x1_0000_0000):
        with pytest.raises(ReleaseCandidateError, match="gzip timestamp"):
            normalize_sdist(source, destination, source_date_epoch=epoch)
    with pytest.raises(ReleaseCandidateError, match="must differ"):
        normalize_sdist(source, source, source_date_epoch=1)
    with pytest.raises(ReleaseCandidateError, match="preserve"):
        normalize_sdist(source, tmp_path / "other.tar.gz", source_date_epoch=1)
    with pytest.raises(ReleaseCandidateError, match="regular file"):
        normalize_sdist(
            tmp_path / "missing.tar.gz",
            tmp_path / "missing-out" / "missing.tar.gz",
            source_date_epoch=1,
        )

    destination.parent.mkdir()
    destination.write_bytes(b"occupied")
    with pytest.raises(ReleaseCandidateError, match="must not already exist"):
        normalize_sdist(source, destination, source_date_epoch=1)

    special_root = tmp_path / "special"
    special_root.mkdir()
    special = special_root / source.name
    with tarfile.open(special, mode="w:gz") as archive:
        info = tarfile.TarInfo("finite_demo-1.2.3/link")
        info.type = tarfile.SYMTYPE
        info.linkname = "target"
        archive.addfile(info)
    with pytest.raises(ReleaseCandidateError, match="links or special files"):
        normalize_sdist(
            special,
            tmp_path / "special-out" / special.name,
            source_date_epoch=1,
        )
    assert root.is_dir()


def test_generation_argument_and_project_shape_errors_are_rejected(tmp_path: Path) -> None:
    root, dist = _fixture_project(tmp_path / "valid")
    common = {
        "dist_dir": dist,
        "output_dir": tmp_path / "candidate",
        "project_root": root,
        "source_revision": REVISION,
        "source_date_epoch": SOURCE_DATE_EPOCH,
        "source_state": "clean",
    }
    for field, value, message in (
        ("source_date_epoch", True, "non-negative integer"),
        ("source_date_epoch", 10**100, "outside the supported range"),
        ("source_state", "unknown", "source_state"),
        ("output_dir", dist, "separate from dist_dir"),
    ):
        arguments = dict(common)
        arguments[field] = value
        with pytest.raises(ReleaseCandidateError, match=message):
            generate_release_candidate(**arguments)  # type: ignore[arg-type]

    broken = tmp_path / "broken-project"
    broken.mkdir()
    (broken / "pyproject.toml").write_text("[project]\nname=1\nversion=\"x\"\n")
    with pytest.raises(ReleaseCandidateError, match="non-empty strings"):
        validate_distributions(dist, broken)
    missing_package = tmp_path / "missing-package"
    missing_package.mkdir()
    (missing_package / "pyproject.toml").write_text(
        '[project]\nname="finite-demo"\nversion="1.2.3"\n'
    )
    with pytest.raises(ReleaseCandidateError, match="missing or empty"):
        validate_distributions(dist, missing_package)


def test_verifier_rejects_shape_classification_and_subject_tampering(tmp_path: Path) -> None:
    def candidate(name: str) -> Path:
        root, dist = _fixture_project(tmp_path / name)
        output = tmp_path / name / "candidate"
        generate_release_candidate(
            dist_dir=dist,
            output_dir=output,
            project_root=root,
            source_revision=REVISION,
            source_date_epoch=SOURCE_DATE_EPOCH,
            source_state="clean",
        )
        return output

    output = candidate("extra")
    (output / "unexpected").mkdir()
    with pytest.raises(ReleaseCandidateError, match="exactly"):
        verify_release_candidate(output)

    output = candidate("coverage")
    checksum = output / CHECKSUM_FILENAME
    checksum.write_text(
        "\n".join(checksum.read_text().splitlines()[1:]) + "\n",
        encoding="ascii",
        newline="\n",
    )
    with pytest.raises(ReleaseCandidateError, match="exact candidate file set"):
        verify_release_candidate(output)

    output = candidate("records")
    _mutate_candidate_json(
        output,
        VALIDATION_FILENAME,
        lambda value: value.__setitem__("distributions", "wrong"),
    )
    with pytest.raises(ReleaseCandidateError, match="must be an array"):
        verify_release_candidate(output)

    output = candidate("classification")
    _mutate_candidate_json(
        output,
        VALIDATION_FILENAME,
        lambda value: value.__setitem__("classification", "release"),
    )
    with pytest.raises(ReleaseCandidateError, match="classification"):
        verify_release_candidate(output)

    output = candidate("sbom")
    _mutate_candidate_json(
        output,
        SBOM_FILENAME,
        lambda value: value.__setitem__("metadata", {}),
    )
    with pytest.raises(ReleaseCandidateError, match="structure is incomplete"):
        verify_release_candidate(output)

    output = candidate("subjects")
    _mutate_candidate_json(
        output,
        PROVENANCE_FILENAME,
        lambda value: value.__setitem__("subject", "wrong"),
    )
    with pytest.raises(ReleaseCandidateError, match="subject must be an array"):
        verify_release_candidate(output)


def test_checksum_parser_rejects_encoding_format_duplicates_and_order(tmp_path: Path) -> None:
    checksum = tmp_path / "SHA256SUMS"
    cases = (
        (b"not-a-checksum\n", "malformed"),
        (("0" * 64 + "  z\n" + "1" * 64 + "  a\n").encode(), "sorted"),
        (("0" * 64 + "  a\n" + "1" * 64 + "  a\n").encode(), "duplicate"),
        (("0" * 64 + "  a\r\n").encode(), "must use LF"),
    )
    for payload, message in cases:
        checksum.write_bytes(payload)
        with pytest.raises(ReleaseCandidateError, match=message):
            _read_checksums(checksum)


@pytest.mark.parametrize("filename", ["release-tools.txt", "test-tools.txt"])
def test_release_gate_tooling_is_directly_pinned(filename: str) -> None:
    root = Path(__file__).resolve().parents[1]
    lines = (root / "requirements" / filename).read_text(encoding="utf-8").splitlines()
    requirements = [line for line in lines if line and not line.startswith("#")]

    assert requirements
    assert all(line.count("==") == 1 for line in requirements)
    assert requirements == sorted(requirements, key=str.casefold)


def test_repository_normalizes_text_for_cross_platform_rebuilds() -> None:
    root = Path(__file__).resolve().parents[1]
    attributes = (root / ".gitattributes").read_text(encoding="utf-8").splitlines()

    assert attributes == ["* text=auto eol=lf", "", "*.png binary"]


def test_ci_has_cross_platform_tests_and_a_pinned_candidate_gate() -> None:
    import yaml

    root = Path(__file__).resolve().parents[1]
    workflow = yaml.safe_load((root / ".github" / "workflows" / "ci.yml").read_text())
    jobs = workflow["jobs"]
    matrix = jobs["python"]["strategy"]["matrix"]["include"]

    assert {item["os"] for item in matrix} == {
        "ubuntu-latest",
        "windows-latest",
        "macos-latest",
    }
    assert {item["python-version"] for item in matrix} == {"3.11", "3.12", "3.13", "3.14"}
    coverage_entry = next(item for item in matrix if item["coverage"])
    assert coverage_entry["extras"] == "dev,api,langgraph"
    uses = [
        step["uses"]
        for job in jobs.values()
        for step in job["steps"]
        if "uses" in step
    ]
    assert all(len(reference.rsplit("@", 1)[1].split()[0]) == 40 for reference in uses)
    checkout_steps = [
        step
        for job in jobs.values()
        for step in job["steps"]
        if str(step.get("uses", "")).startswith("actions/checkout@")
    ]
    assert checkout_steps
    assert all(step.get("with", {}).get("persist-credentials") is False for step in checkout_steps)
    package_commands = "\n".join(
        str(step.get("run", "")) for step in jobs["package-candidate"]["steps"]
    )
    for required in (
        "python -m build",
        "python -m twine check",
        "generate_release_candidate.py",
        "verify_release_candidate.py",
        "validate_sbom.py",
        "validate_bandit.py",
        "validate_licenses.py",
        "python -m pip_audit",
        "python -m piplicenses",
        "python -m bandit",
        "python -m pip freeze --all --exclude agent-physics",
        "python -m venv",
    ):
        assert required in package_commands
    python_commands = "\n".join(str(step.get("run", "")) for step in jobs["python"]["steps"])
    for required in (
        "--cov-branch",
        "--junitxml=artifacts/pytest-junit.xml",
        "--strict-markers",
        "xfail_strict=true",
        "validate_junit.py",
        "validate_coverage.py",
        "--statement-floor 90",
        "--branch-floor 80",
        "run_live_load.py --output artifacts/live-load",
        "run_live_load.py --verify-only artifacts/live-load",
        "actionlint_1.7.12_linux_amd64.tar.gz",
        "8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8",
    ):
        assert required in python_commands
    container_commands = "\n".join(
        str(step.get("run", "")) for step in jobs["container"]["steps"]
    )
    for required in (
        "aquasec/trivy@sha256:",
        "--scanners vuln,misconfig,secret,license",
        "--severity UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL",
        "--read-only",
        "--cap-drop ALL",
        "no-new-privileges:true",
        "--format cyclonedx",
        "git archive --format=tar HEAD",
    ):
        assert required in container_commands
    console_commands = "\n".join(
        str(step.get("run", "")) for step in jobs["console"]["steps"]
    )
    for required in (
        "--test-reporter=junit",
        "validate_node_junit.mjs",
        "npm audit --audit-level=low --json",
    ):
        assert required in console_commands
