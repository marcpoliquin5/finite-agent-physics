"""Deterministic, fail-closed release-candidate evidence utilities.

This module validates built Python distributions and emits a small evidence bundle.  The
bundle is deliberately classified as a release candidate: it is not a signature, a trusted
attestation, proof of a clean Git checkout, or authorization to publish.
"""

from __future__ import annotations

import base64
import csv
import gzip
import hashlib
import io
import json
import re
import shutil
import stat
import tarfile
import tomllib
import zipfile
from datetime import UTC, datetime
from email.parser import BytesParser
from email.policy import default
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping


SCHEMA_VERSION = "finite.release-candidate/v1"
SBOM_FILENAME = "release-candidate.sbom.cdx.json"
VALIDATION_FILENAME = "release-candidate.validation.json"
PROVENANCE_FILENAME = "release-candidate.provenance.intoto.json"
CHECKSUM_FILENAME = "SHA256SUMS"
EXACT_LIMITATIONS = (
    "Checksums and provenance are unsigned local evidence, not an identity-bound attestation.",
    "The Git revision, source state, and SOURCE_DATE_EPOCH are caller assertions.",
    "The SBOM records declared wheel requirements, not a resolved transitive deployment inventory.",
    "SLSA resolvedDependencies is empty because the project has no transitive dependency lock.",
    "Same-checkout repeat builds do not establish reproducibility across independent builders.",
    "Live advisory results are time-varying and intentionally outside the deterministic bundle.",
    "The Python sdist is not a complete judge-source archive and may omit repository assets.",
    "The bundle proves no IBM Bob, watsonx, eligibility, deployment, signing, or publication event.",
)

_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_REQUIREMENT_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
_EXACT_VERSION_RE = re.compile(r"(?:^|[^<>=!~])==\s*([A-Za-z0-9][A-Za-z0-9.!+_-]*)")
_CHECKSUM_RE = re.compile(r"^([0-9a-f]{64})  ([A-Za-z0-9][A-Za-z0-9._+-]*)$")
_WINDOWS_DEVICE_NAMES = frozenset(
    {"aux", "con", "nul", "prn", *(f"com{index}" for index in range(1, 10)), *(f"lpt{index}" for index in range(1, 10))}
)
_FORBIDDEN_PARTS = frozenset(
    {
        ".finite",
        ".git",
        "__pycache__",
        "artifacts",
        "node_modules",
    }
)
_FORBIDDEN_SUFFIXES = frozenset(
    {".dll", ".dylib", ".key", ".p12", ".pem", ".pfx", ".pyc", ".pyd", ".so", ".sqlite3"}
)


class ReleaseCandidateError(ValueError):
    """Raised when candidate contents or evidence are incomplete or ambiguous."""


def canonical_json_bytes(value: object) -> bytes:
    """Return canonical UTF-8 JSON with a single trailing newline."""

    try:
        text = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ReleaseCandidateError(f"value is not canonical JSON: {exc}") from exc
    return (text + "\n").encode("utf-8")


def sha256_file(path: Path) -> str:
    """Hash one regular file without loading it all into memory."""

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalize_sdist(
    source: Path,
    destination: Path,
    *,
    source_date_epoch: int,
) -> dict[str, Any]:
    """Rewrite an sdist with deterministic tar metadata and a deterministic gzip header."""

    if type(source_date_epoch) is not int or not 0 <= source_date_epoch <= 0xFFFFFFFF:
        raise ReleaseCandidateError("source_date_epoch must fit the unsigned gzip timestamp field")
    source_path = source.resolve()
    destination_path = destination.resolve()
    if source_path == destination_path:
        raise ReleaseCandidateError("normalized sdist destination must differ from its source")
    if source_path.name != destination_path.name or not source_path.name.endswith(".tar.gz"):
        raise ReleaseCandidateError("normalized sdist must preserve the .tar.gz filename")
    if not source_path.is_file() or _is_linklike(source_path):
        raise ReleaseCandidateError("source sdist must be a regular file")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    if destination_path.exists() or _is_linklike(destination_path):
        raise ReleaseCandidateError("normalized sdist destination must not already exist")

    try:
        with tarfile.open(source_path, mode="r:gz") as archive:
            members = archive.getmembers()
            _validate_archive_names(
                [member.name.removesuffix("/") for member in members], archive_kind="sdist"
            )
            unsafe = [member.name for member in members if not (member.isfile() or member.isdir())]
            if unsafe:
                raise ReleaseCandidateError(
                    f"sdist contains links or special files: {sorted(unsafe)!r}"
                )
            payloads: dict[str, bytes] = {}
            for member in members:
                if not member.isfile():
                    continue
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise ReleaseCandidateError(f"sdist member cannot be read: {member.name}")
                payloads[member.name] = extracted.read()
    except (OSError, tarfile.TarError) as exc:
        raise ReleaseCandidateError(f"cannot read source sdist: {exc}") from exc

    temporary = destination_path.with_name(f".{destination_path.name}.finite-tmp")
    if temporary.exists() or _is_linklike(temporary):
        raise ReleaseCandidateError("normalized sdist temporary path already exists")
    try:
        with temporary.open("xb") as raw_stream:
            with gzip.GzipFile(
                filename="",
                mode="wb",
                compresslevel=9,
                fileobj=raw_stream,
                mtime=source_date_epoch,
            ) as gzip_stream:
                with tarfile.open(
                    fileobj=gzip_stream,
                    mode="w",
                    format=tarfile.PAX_FORMAT,
                ) as output:
                    for member in sorted(members, key=lambda item: item.name):
                        normalized = tarfile.TarInfo(member.name)
                        normalized.uid = 0
                        normalized.gid = 0
                        normalized.uname = ""
                        normalized.gname = ""
                        normalized.mtime = source_date_epoch
                        normalized.mode = (
                            0o755 if member.isdir() or member.mode & 0o111 else 0o644
                        )
                        if member.isdir():
                            normalized.type = tarfile.DIRTYPE
                            normalized.size = 0
                            output.addfile(normalized)
                        else:
                            payload = payloads[member.name]
                            normalized.type = tarfile.REGTYPE
                            normalized.size = len(payload)
                            output.addfile(normalized, io.BytesIO(payload))
        temporary.replace(destination_path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    return {
        "schema_version": "finite.normalized-sdist/v1",
        "filename": destination_path.name,
        "source_date_epoch": source_date_epoch,
        "sha256": sha256_file(destination_path),
        "member_count": len(members),
    }


def validate_distributions(dist_dir: Path, project_root: Path) -> dict[str, Any]:
    """Validate exactly one wheel and one source distribution against the source tree."""

    root = project_root.resolve()
    directory = dist_dir.resolve()
    project = _load_project_metadata(root)
    expected_modules = _expected_python_modules(root, project["name"])
    wheel, sdist = _discover_distributions(directory)
    _validate_distribution_filenames(wheel, sdist, project)

    wheel_result = _validate_wheel(wheel, project, expected_modules)
    sdist_result = _validate_sdist(sdist, project, expected_modules)
    if wheel_result["requires_dist"] != sdist_result["requires_dist"]:
        raise ReleaseCandidateError("wheel and sdist dependency metadata differ")

    return {
        "schema_version": SCHEMA_VERSION,
        "classification": "release-candidate",
        "release_ready": False,
        "limitations": list(EXACT_LIMITATIONS),
        "project": {"name": project["name"], "version": project["version"]},
        "requires_dist": wheel_result.pop("requires_dist"),
        "source_module_count": len(expected_modules),
        "distributions": sorted(
            [wheel_result, sdist_result], key=lambda item: str(item["filename"])
        ),
    }


def generate_release_candidate(
    *,
    dist_dir: Path,
    output_dir: Path,
    project_root: Path,
    source_revision: str,
    source_date_epoch: int,
    source_state: str,
) -> dict[str, Any]:
    """Generate and immediately verify a deterministic candidate evidence directory."""

    if not _COMMIT_RE.fullmatch(source_revision):
        raise ReleaseCandidateError("source_revision must be a 40-character lowercase Git SHA")
    if type(source_date_epoch) is not int or source_date_epoch < 0:
        raise ReleaseCandidateError("source_date_epoch must be a non-negative integer")
    if source_state not in {"clean", "dirty"}:
        raise ReleaseCandidateError("source_state must be 'clean' or 'dirty'")
    try:
        timestamp = datetime.fromtimestamp(source_date_epoch, tz=UTC).isoformat().replace(
            "+00:00", "Z"
        )
    except (OSError, OverflowError, ValueError) as exc:
        raise ReleaseCandidateError("source_date_epoch is outside the supported range") from exc

    if _is_linklike(output_dir):
        raise ReleaseCandidateError("output_dir must not be a symbolic link or junction")
    directory = output_dir.resolve()
    source_directory = dist_dir.resolve()
    if directory == source_directory:
        raise ReleaseCandidateError("output_dir must be separate from dist_dir")
    directory.mkdir(parents=True, exist_ok=True)
    if any(directory.iterdir()):
        raise ReleaseCandidateError("output_dir must be empty to prevent stale candidate files")

    validation = validate_distributions(source_directory, project_root)
    validation["source"] = {
        "revision": source_revision,
        "source_date_epoch": source_date_epoch,
        "state": source_state,
        "state_is_caller_asserted": True,
    }

    copied_distributions: list[Path] = []
    for record in validation["distributions"]:
        source = source_directory / str(record["filename"])
        destination = directory / source.name
        shutil.copyfile(source, destination)
        copied_distributions.append(destination)

    validation_path = directory / VALIDATION_FILENAME
    validation_path.write_bytes(canonical_json_bytes(validation))

    sbom = _build_sbom(validation, timestamp)
    sbom_path = directory / SBOM_FILENAME
    sbom_path.write_bytes(canonical_json_bytes(sbom))

    provenance_subjects = [*copied_distributions, validation_path, sbom_path]
    provenance = _build_provenance(
        validation=validation,
        source_revision=source_revision,
        source_date_epoch=source_date_epoch,
        source_state=source_state,
        subjects=provenance_subjects,
    )
    provenance_path = directory / PROVENANCE_FILENAME
    provenance_path.write_bytes(canonical_json_bytes(provenance))

    checksummed = [*provenance_subjects, provenance_path]
    checksum_lines = [f"{sha256_file(path)}  {path.name}" for path in sorted(checksummed)]
    (directory / CHECKSUM_FILENAME).write_text(
        "\n".join(checksum_lines) + "\n", encoding="ascii", newline="\n"
    )

    verified = verify_release_candidate(directory)
    return {
        "schema_version": SCHEMA_VERSION,
        "classification": "release-candidate",
        "release_ready": False,
        "output_dir": str(directory),
        "files": verified["files"],
    }


def verify_release_candidate(output_dir: Path) -> dict[str, Any]:
    """Verify checksums, canonical documents, subjects, and non-release classification."""

    directory = output_dir.resolve()
    wheel, sdist = _discover_distributions(directory)
    fixed = {
        VALIDATION_FILENAME,
        SBOM_FILENAME,
        PROVENANCE_FILENAME,
        CHECKSUM_FILENAME,
    }
    expected_names = {wheel.name, sdist.name, *fixed}
    actual_names = {path.name for path in directory.iterdir() if path.is_file()}
    linklike = [path.name for path in directory.iterdir() if _is_linklike(path)]
    if linklike:
        raise ReleaseCandidateError(f"candidate files must not be links or junctions: {linklike!r}")
    non_files = [path.name for path in directory.iterdir() if not path.is_file()]
    if non_files or actual_names != expected_names:
        raise ReleaseCandidateError(
            "candidate directory must contain exactly the two distributions and four evidence "
            f"files; missing={sorted(expected_names - actual_names)!r}, "
            f"unknown={sorted(actual_names - expected_names | set(non_files))!r}"
        )

    checksums = _read_checksums(directory / CHECKSUM_FILENAME)
    expected_checksummed = expected_names - {CHECKSUM_FILENAME}
    if set(checksums) != expected_checksummed:
        raise ReleaseCandidateError("SHA256SUMS does not cover the exact candidate file set")
    for filename, declared_digest in checksums.items():
        actual_digest = sha256_file(directory / filename)
        if actual_digest != declared_digest:
            raise ReleaseCandidateError(f"checksum mismatch for {filename}")

    validation = _read_canonical_json(directory / VALIDATION_FILENAME)
    sbom = _read_canonical_json(directory / SBOM_FILENAME)
    provenance = _read_canonical_json(directory / PROVENANCE_FILENAME)
    _verify_nonrelease_classification(validation, sbom, provenance)

    distribution_records = validation.get("distributions")
    if type(distribution_records) is not list:
        raise ReleaseCandidateError("validation report distributions must be an array")
    declared_distributions = {
        str(record.get("filename")): str(record.get("sha256"))
        for record in distribution_records
        if type(record) is dict
    }
    expected_distributions = {wheel.name, sdist.name}
    if set(declared_distributions) != expected_distributions:
        raise ReleaseCandidateError("validation report does not name the exact distributions")
    for filename, digest in declared_distributions.items():
        if digest != checksums[filename]:
            raise ReleaseCandidateError(f"validation digest mismatch for {filename}")

    expected_subjects = expected_names - {CHECKSUM_FILENAME, PROVENANCE_FILENAME}
    _verify_provenance_subjects(provenance, directory, expected_subjects)
    return {
        "schema_version": SCHEMA_VERSION,
        "classification": "release-candidate",
        "release_ready": False,
        "files": sorted(expected_names),
    }


def _load_project_metadata(root: Path) -> dict[str, str]:
    pyproject = root / "pyproject.toml"
    try:
        document = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        project = document["project"]
        name = project["name"]
        version = project["version"]
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError) as exc:
        raise ReleaseCandidateError(f"cannot read project metadata from {pyproject}: {exc}") from exc
    if type(name) is not str or not name or type(version) is not str or not version:
        raise ReleaseCandidateError("project name and version must be non-empty strings")
    return {"name": name, "version": version, "root": str(root)}


def _expected_python_modules(root: Path, project_name: str) -> dict[str, bytes]:
    package = _normalize_name(project_name).replace("-", "_")
    source = root / "src" / package
    modules = {
        f"{package}/{path.relative_to(source).as_posix()}": path.read_bytes()
        for path in source.rglob("*.py")
    }
    if not modules or f"{package}/__init__.py" not in modules:
        raise ReleaseCandidateError(f"source package is missing or empty: {source}")
    return modules


def _discover_distributions(directory: Path) -> tuple[Path, Path]:
    if not directory.is_dir():
        raise ReleaseCandidateError(f"distribution directory does not exist: {directory}")
    wheels = sorted(directory.glob("*.whl"))
    sdists = sorted(directory.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise ReleaseCandidateError(
            "expected exactly one wheel and one .tar.gz source distribution; "
            f"found wheels={len(wheels)}, sdists={len(sdists)}"
        )
    if (
        not wheels[0].is_file()
        or not sdists[0].is_file()
        or _is_linklike(wheels[0])
        or _is_linklike(sdists[0])
    ):
        raise ReleaseCandidateError("distribution paths must be regular files")
    return wheels[0], sdists[0]


def _validate_wheel(
    path: Path,
    project: Mapping[str, str],
    expected_modules: Mapping[str, bytes],
) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(path) as archive:
            if archive.testzip() is not None:
                raise ReleaseCandidateError(f"wheel CRC validation failed: {path.name}")
            infos = archive.infolist()
            names = [info.filename for info in infos if not info.is_dir()]
            _validate_archive_names(names, archive_kind="wheel")
            for info in infos:
                mode = info.external_attr >> 16
                if mode and stat.S_ISLNK(mode):
                    raise ReleaseCandidateError(f"wheel contains a symbolic link: {info.filename}")

            metadata_name = _single_suffix(names, ".dist-info/METADATA", "wheel METADATA")
            dist_info_prefix = metadata_name.removesuffix("METADATA")
            expected_dist_info = (
                f"{_normalize_name(project['name']).replace('-', '_')}-"
                f"{project['version']}.dist-info/"
            )
            if dist_info_prefix != expected_dist_info:
                raise ReleaseCandidateError("wheel dist-info directory does not match name/version")
            wheel_name = _single_suffix(names, ".dist-info/WHEEL", "wheel WHEEL")
            record_name = _single_suffix(names, ".dist-info/RECORD", "wheel RECORD")
            _single_suffix(names, ".dist-info/entry_points.txt", "wheel entry points")
            _single_suffix(names, ".dist-info/licenses/LICENSE", "wheel license")
            metadata = BytesParser(policy=default).parsebytes(archive.read(metadata_name))
            _verify_core_metadata(metadata, project, path.name)
            wheel_metadata = BytesParser(policy=default).parsebytes(archive.read(wheel_name))
            if wheel_metadata.get("Root-Is-Purelib") != "true":
                raise ReleaseCandidateError("wheel must declare Root-Is-Purelib: true")
            _verify_wheel_record(archive, record_name, names)
            actual_modules = {
                name
                for name in names
                if name.startswith(_normalize_name(project["name"]).replace("-", "_") + "/")
                and name.endswith(".py")
            }
            if actual_modules != set(expected_modules):
                raise ReleaseCandidateError(
                    "wheel Python modules do not match src; "
                    f"missing={sorted(set(expected_modules) - actual_modules)!r}, "
                    f"unexpected={sorted(actual_modules - set(expected_modules))!r}"
                )
            unexpected_payloads = [
                name
                for name in names
                if name not in expected_modules and not name.startswith(dist_info_prefix)
            ]
            if unexpected_payloads:
                raise ReleaseCandidateError(
                    f"wheel contains undeclared package payloads: {sorted(unexpected_payloads)!r}"
                )
            mismatched_modules = [
                name for name, payload in expected_modules.items() if archive.read(name) != payload
            ]
            if mismatched_modules:
                raise ReleaseCandidateError(
                    f"wheel module bytes differ from src: {sorted(mismatched_modules)!r}"
                )
            requires_dist = sorted(metadata.get_all("Requires-Dist", []), key=str.casefold)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ReleaseCandidateError(f"cannot inspect wheel {path.name}: {exc}") from exc

    return {
        "kind": "wheel",
        "filename": path.name,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "member_count": len(names),
        "record_verified": True,
        "requires_dist": requires_dist,
    }


def _validate_sdist(
    path: Path,
    project: Mapping[str, str],
    expected_modules: Mapping[str, bytes],
) -> dict[str, Any]:
    try:
        with tarfile.open(path, mode="r:gz") as archive:
            members = archive.getmembers()
            _validate_archive_names(
                [member.name.removesuffix("/") for member in members], archive_kind="sdist"
            )
            names = [member.name for member in members if member.isfile()]
            unsafe = [member.name for member in members if not (member.isfile() or member.isdir())]
            if unsafe:
                raise ReleaseCandidateError(
                    f"sdist contains links or special files: {sorted(unsafe)!r}"
                )
            roots = {PurePosixPath(name).parts[0] for name in names}
            if len(roots) != 1:
                raise ReleaseCandidateError("sdist must have exactly one top-level directory")
            prefix = next(iter(roots))
            expected_prefix = path.name.removesuffix(".tar.gz")
            if prefix != expected_prefix:
                raise ReleaseCandidateError("sdist root directory does not match its filename")
            required = {
                f"{prefix}/LICENSE",
                f"{prefix}/README.md",
                f"{prefix}/pyproject.toml",
                f"{prefix}/PKG-INFO",
                *(f"{prefix}/src/{module}" for module in expected_modules),
            }
            missing = required - set(names)
            if missing:
                raise ReleaseCandidateError(f"sdist is missing required files: {sorted(missing)!r}")
            package = _normalize_name(project["name"]).replace("-", "_")
            source_prefix = f"{prefix}/src/"
            allowed_source_prefixes = (
                f"{source_prefix}{package}/",
                f"{source_prefix}{package}.egg-info/",
            )
            unexpected_source_payloads = [
                name
                for name in names
                if name.startswith(source_prefix)
                and not name.startswith(allowed_source_prefixes)
            ]
            unexpected_package_payloads = [
                name
                for name in names
                if name.startswith(f"{source_prefix}{package}/")
                and name.removeprefix(source_prefix) not in expected_modules
            ]
            if unexpected_source_payloads or unexpected_package_payloads:
                raise ReleaseCandidateError(
                    "sdist contains undeclared source payloads: "
                    f"{sorted(unexpected_source_payloads + unexpected_package_payloads)!r}"
                )
            mismatched_modules: list[str] = []
            for name, payload in expected_modules.items():
                member = archive.extractfile(f"{source_prefix}{name}")
                if member is None or member.read() != payload:
                    mismatched_modules.append(name)
            if mismatched_modules:
                raise ReleaseCandidateError(
                    f"sdist module bytes differ from src: {sorted(mismatched_modules)!r}"
                )
            for filename in ("LICENSE", "README.md", "pyproject.toml"):
                member = archive.extractfile(f"{prefix}/{filename}")
                if member is None or member.read() != (Path(project["root"]) / filename).read_bytes():
                    raise ReleaseCandidateError(f"sdist {filename} differs from the source tree")
            pkg_info = archive.extractfile(f"{prefix}/PKG-INFO")
            if pkg_info is None:
                raise ReleaseCandidateError("sdist PKG-INFO cannot be read")
            metadata = BytesParser(policy=default).parsebytes(pkg_info.read())
            _verify_core_metadata(metadata, project, path.name)
            requires_dist = sorted(metadata.get_all("Requires-Dist", []), key=str.casefold)
    except (OSError, tarfile.TarError) as exc:
        raise ReleaseCandidateError(f"cannot inspect sdist {path.name}: {exc}") from exc

    return {
        "kind": "sdist",
        "filename": path.name,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "member_count": len(names),
        "links_and_special_files": 0,
        "requires_dist": requires_dist,
    }


def _validate_archive_names(names: Iterable[str], *, archive_kind: str) -> None:
    exact: set[str] = set()
    folded: set[str] = set()
    for name in names:
        if not name or any(ord(character) < 32 or ord(character) == 127 for character in name):
            raise ReleaseCandidateError(f"{archive_kind} member contains control characters")
        if "\\" in name:
            raise ReleaseCandidateError(f"{archive_kind} member uses a backslash: {name!r}")
        pure = PurePosixPath(name)
        parts = pure.parts
        if (
            not parts
            or pure.is_absolute()
            or ".." in parts
            or "." in parts
            or pure.as_posix() != name
        ):
            raise ReleaseCandidateError(f"{archive_kind} member path is unsafe: {name!r}")
        if ":" in parts[0]:
            raise ReleaseCandidateError(f"{archive_kind} member path is drive-qualified: {name!r}")
        if any(part.casefold() in _FORBIDDEN_PARTS for part in parts):
            raise ReleaseCandidateError(f"{archive_kind} contains forbidden path: {name!r}")
        for part in parts:
            if part != part.strip() or part.endswith((".", " ")):
                raise ReleaseCandidateError(
                    f"{archive_kind} member path is not cross-platform normalized: {name!r}"
                )
            device_stem = part.split(".", 1)[0].casefold()
            if device_stem in _WINDOWS_DEVICE_NAMES:
                raise ReleaseCandidateError(
                    f"{archive_kind} member uses a reserved device name: {name!r}"
                )
        leaf = parts[-1].casefold()
        if leaf == ".env" or leaf.startswith(".env."):
            raise ReleaseCandidateError(f"{archive_kind} contains an environment file: {name!r}")
        if PurePosixPath(leaf).suffix in _FORBIDDEN_SUFFIXES:
            raise ReleaseCandidateError(f"{archive_kind} contains a forbidden file type: {name!r}")
        if name in exact or name.casefold() in folded:
            raise ReleaseCandidateError(f"{archive_kind} contains a duplicate path: {name!r}")
        exact.add(name)
        folded.add(name.casefold())


def _verify_core_metadata(metadata: Any, project: Mapping[str, str], filename: str) -> None:
    if _normalize_name(str(metadata.get("Name", ""))) != _normalize_name(project["name"]):
        raise ReleaseCandidateError(f"{filename} project name does not match pyproject.toml")
    if metadata.get("Version") != project["version"]:
        raise ReleaseCandidateError(f"{filename} version does not match pyproject.toml")


def _verify_wheel_record(
    archive: zipfile.ZipFile,
    record_name: str,
    archive_names: list[str],
) -> None:
    try:
        rows = list(csv.reader(io.StringIO(archive.read(record_name).decode("utf-8"))))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise ReleaseCandidateError(f"wheel RECORD is invalid: {exc}") from exc
    seen: set[str] = set()
    for row in rows:
        if len(row) != 3:
            raise ReleaseCandidateError("wheel RECORD rows must have exactly three fields")
        name, hash_field, size_field = row
        if name in seen:
            raise ReleaseCandidateError(f"wheel RECORD duplicates {name!r}")
        seen.add(name)
        if name == record_name:
            if hash_field or size_field:
                raise ReleaseCandidateError("wheel RECORD self-entry must omit digest and size")
            continue
        if name not in archive_names:
            raise ReleaseCandidateError(f"wheel RECORD references a missing member: {name!r}")
        if not hash_field.startswith("sha256="):
            raise ReleaseCandidateError(f"wheel RECORD entry is not SHA-256 bound: {name!r}")
        payload = archive.read(name)
        encoded = hash_field.removeprefix("sha256=")
        padding = "=" * (-len(encoded) % 4)
        try:
            declared = base64.urlsafe_b64decode(encoded + padding)
        except (ValueError, base64.binascii.Error) as exc:
            raise ReleaseCandidateError(f"wheel RECORD digest is malformed: {name!r}") from exc
        if declared != hashlib.sha256(payload).digest():
            raise ReleaseCandidateError(f"wheel RECORD digest mismatch: {name!r}")
        if not size_field.isdecimal() or int(size_field) != len(payload):
            raise ReleaseCandidateError(f"wheel RECORD size mismatch: {name!r}")
    if seen != set(archive_names):
        raise ReleaseCandidateError("wheel RECORD does not cover the exact archive member set")


def _single_suffix(names: Iterable[str], suffix: str, label: str) -> str:
    matches = [name for name in names if name.endswith(suffix)]
    if len(matches) != 1:
        raise ReleaseCandidateError(f"expected exactly one {label}; found {len(matches)}")
    return matches[0]


def _normalize_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _is_linklike(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction is not None and is_junction())


def _validate_distribution_filenames(
    wheel: Path, sdist: Path, project: Mapping[str, str]
) -> None:
    try:
        from packaging.utils import (
            InvalidSdistFilename,
            InvalidWheelFilename,
            canonicalize_name,
            parse_sdist_filename,
            parse_wheel_filename,
        )

        wheel_name, wheel_version, _build, wheel_tags = parse_wheel_filename(wheel.name)
        sdist_name, sdist_version = parse_sdist_filename(sdist.name)
    except ImportError as exc:
        raise ReleaseCandidateError(
            "distribution filename validation requires the pinned release tooling"
        ) from exc
    except (InvalidWheelFilename, InvalidSdistFilename) as exc:
        raise ReleaseCandidateError(f"invalid distribution filename: {exc}") from exc

    expected_name = canonicalize_name(project["name"])
    expected_version = project["version"]
    if wheel_name != expected_name or sdist_name != expected_name:
        raise ReleaseCandidateError("distribution filenames do not match the project name")
    if str(wheel_version) != expected_version or str(sdist_version) != expected_version:
        raise ReleaseCandidateError("distribution filenames do not match the project version")
    if {str(tag) for tag in wheel_tags} != {"py3-none-any"}:
        raise ReleaseCandidateError("wheel must use the single pure-Python tag py3-none-any")


def _build_sbom(validation: Mapping[str, Any], timestamp: str) -> dict[str, Any]:
    project = validation["project"]
    project_name = str(project["name"])
    project_version = str(project["version"])
    root_ref = f"pkg:pypi/{_normalize_name(project_name)}@{project_version}"
    components: list[dict[str, Any]] = []
    dependency_refs: list[str] = []
    for requirement in validation["requires_dist"]:
        raw = str(requirement)
        match = _REQUIREMENT_NAME_RE.match(raw)
        if match is None:
            raise ReleaseCandidateError(f"cannot parse Requires-Dist entry: {raw!r}")
        name = _normalize_name(match.group(1))
        requirement_digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        bom_ref = f"urn:finite:dependency:{name}:{requirement_digest[:16]}"
        version_match = _EXACT_VERSION_RE.search(raw)
        component: dict[str, Any] = {
            "bom-ref": bom_ref,
            "type": "library",
            "name": name,
            "version": version_match.group(1) if version_match else "unspecified",
            "scope": "optional" if "extra ==" in raw else "required",
            "properties": [{"name": "finite:pep508-requirement", "value": raw}],
        }
        components.append(component)
        dependency_refs.append(bom_ref)

    return {
        "$schema": "https://cyclonedx.org/schema/bom-1.6.schema.json",
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "version": 1,
        "metadata": {
            "timestamp": timestamp,
            "component": {
                "bom-ref": root_ref,
                "type": "application",
                "name": project_name,
                "version": project_version,
                "purl": root_ref,
                "properties": [
                    {"name": "finite:classification", "value": "release-candidate"},
                    {"name": "finite:release-ready", "value": "false"},
                    {
                        "name": "finite:sbom-scope",
                        "value": "declared-wheel-requirements-not-resolved-environment",
                    },
                ],
            },
        },
        "components": components,
        "dependencies": [{"ref": root_ref, "dependsOn": dependency_refs}],
    }


def _build_provenance(
    *,
    validation: Mapping[str, Any],
    source_revision: str,
    source_date_epoch: int,
    source_state: str,
    subjects: Iterable[Path],
) -> dict[str, Any]:
    project = validation["project"]
    subject_records = [
        {"name": path.name, "digest": {"sha256": sha256_file(path)}}
        for path in sorted(subjects)
    ]
    return {
        "_type": "https://in-toto.io/Statement/v1",
        "subject": subject_records,
        "predicateType": "https://slsa.dev/provenance/v1",
        "predicate": {
            "buildDefinition": {
                "buildType": "urn:finite:build-type:python-release-candidate:v1",
                "externalParameters": {
                    "classification": "release-candidate",
                    "releaseReady": False,
                    "projectName": project["name"],
                    "projectVersion": project["version"],
                    "sourceRevision": source_revision,
                    "sourceDateEpoch": source_date_epoch,
                    "sourceState": source_state,
                    "sourceStateIsCallerAsserted": True,
                    "limitations": list(EXACT_LIMITATIONS),
                },
                "internalParameters": {},
                "resolvedDependencies": [],
            },
            "runDetails": {
                "builder": {"id": "urn:finite:builder:release-candidate:v1"},
            },
        },
    }


def _read_checksums(path: Path) -> dict[str, str]:
    try:
        text = path.read_bytes().decode("ascii")
    except (OSError, UnicodeDecodeError) as exc:
        raise ReleaseCandidateError(f"cannot read SHA256SUMS: {exc}") from exc
    if not text.endswith("\n") or "\r" in text:
        raise ReleaseCandidateError("SHA256SUMS must use LF and end with one newline")
    lines = text.splitlines()
    result: dict[str, str] = {}
    for line in lines:
        match = _CHECKSUM_RE.fullmatch(line)
        if match is None:
            raise ReleaseCandidateError(f"malformed SHA256SUMS line: {line!r}")
        digest, filename = match.groups()
        if filename in result:
            raise ReleaseCandidateError(f"duplicate SHA256SUMS filename: {filename}")
        result[filename] = digest
    if list(result) != sorted(result):
        raise ReleaseCandidateError("SHA256SUMS entries must be sorted by filename")
    return result


def _read_canonical_json(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseCandidateError(f"cannot read {path.name}: {exc}") from exc
    if type(value) is not dict:
        raise ReleaseCandidateError(f"{path.name} must contain a JSON object")
    if raw != canonical_json_bytes(value):
        raise ReleaseCandidateError(f"{path.name} is not canonical JSON")
    return value


def _verify_nonrelease_classification(
    validation: Mapping[str, Any],
    sbom: Mapping[str, Any],
    provenance: Mapping[str, Any],
) -> None:
    if validation.get("classification") != "release-candidate":
        raise ReleaseCandidateError("validation report classification is not release-candidate")
    if validation.get("release_ready") is not False:
        raise ReleaseCandidateError("validation report must explicitly set release_ready=false")
    try:
        properties = sbom["metadata"]["component"]["properties"]
        parameters = provenance["predicate"]["buildDefinition"]["externalParameters"]
    except (KeyError, TypeError) as exc:
        raise ReleaseCandidateError("SBOM or provenance structure is incomplete") from exc
    property_map = {
        item.get("name"): item.get("value") for item in properties if type(item) is dict
    }
    if property_map.get("finite:classification") != "release-candidate":
        raise ReleaseCandidateError("SBOM classification is not release-candidate")
    if property_map.get("finite:release-ready") != "false":
        raise ReleaseCandidateError("SBOM must explicitly set release-ready false")
    if parameters.get("classification") != "release-candidate":
        raise ReleaseCandidateError("provenance classification is not release-candidate")
    if parameters.get("releaseReady") is not False:
        raise ReleaseCandidateError("provenance must explicitly set releaseReady=false")


def _verify_provenance_subjects(
    provenance: Mapping[str, Any], directory: Path, expected_names: set[str]
) -> None:
    subjects = provenance.get("subject")
    if type(subjects) is not list:
        raise ReleaseCandidateError("provenance subject must be an array")
    declared: dict[str, str] = {}
    for subject in subjects:
        if type(subject) is not dict or type(subject.get("digest")) is not dict:
            raise ReleaseCandidateError("provenance subject is malformed")
        name = subject.get("name")
        digest = subject["digest"].get("sha256")
        if type(name) is not str or type(digest) is not str or name in declared:
            raise ReleaseCandidateError("provenance subject name or digest is malformed")
        declared[name] = digest
    if set(declared) != expected_names:
        raise ReleaseCandidateError("provenance does not bind the exact expected subject set")
    for name, digest in declared.items():
        if digest != sha256_file(directory / name):
            raise ReleaseCandidateError(f"provenance subject digest mismatch for {name}")
