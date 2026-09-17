import hashlib
import os
import tempfile
from pathlib import Path
from typing import BinaryIO

ALLOWED_SUFFIXES = {".pdf", ".docx"}
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024


class StorageIntegrityError(Exception):
    pass


class StorageCapabilityError(Exception):
    pass


class UploadTooLargeError(ValueError):
    pass


def hash_upload(reader: BinaryIO) -> tuple[str, int]:
    """Validate and hash a seekable upload without allocating its whole body."""
    hasher = hashlib.sha256()
    size = 0
    reader.seek(0)
    try:
        while chunk := reader.read(CHUNK_BYTES):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                raise UploadTooLargeError("File exceeds 50 MiB limit")
            hasher.update(chunk)
    finally:
        reader.seek(0)
    if size == 0:
        raise ValueError("File is empty")
    return hasher.hexdigest(), size


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _resolved_path(path: Path) -> Path:
    resolved = str(path.resolve())
    # Windows can retain the extended namespace when a path appears/disappears
    # during resolve(). Normalize both operands before checking containment.
    if os.name == "nt":
        if resolved.startswith("\\\\?\\UNC\\"):
            resolved = "\\\\" + resolved[8:]
        elif resolved.startswith("\\\\?\\"):
            resolved = resolved[4:]
    return Path(resolved)


def safe_storage_path(root: Path, digest: str, original_name: str) -> Path:
    suffix = Path(original_name).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise ValueError("Unsupported document type")
    resolved_root = _resolved_path(root)
    target = _resolved_path(resolved_root / digest[:2] / f"{digest}{suffix}")
    if resolved_root not in target.parents:
        raise StorageIntegrityError("Storage path escapes configured root")
    return target


def store_content(
    root: Path,
    digest: str,
    original_name: str,
    reader: BinaryIO,
    size: int,
) -> tuple[Path, bool]:
    """Store content once and return its path plus whether this call created it."""
    target = safe_storage_path(root, digest, original_name)
    return ensure_content(root, target, digest, reader, size)


def ensure_content(
    root: Path, target: Path, digest: str, reader: BinaryIO, size: int
) -> tuple[Path, bool]:
    """Verify existing content or restore a missing file at this exact safe path.

    Published files are immutable. Database rollback must never remove one: a
    concurrent transaction may already reference it. Only temporary files are
    owned by this call; unreferenced published files need a separate collector.
    """
    try:
        resolved_root = _resolved_path(root)
        target = _resolved_path(target)
        if resolved_root not in target.parents:
            raise StorageIntegrityError("Storage path escapes configured root")
    except (OSError, ValueError) as error:
        raise StorageIntegrityError("Invalid stored path") from error
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        _verify_existing(target, digest, size)
        return target, False

    descriptor, temporary_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{digest}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as temporary_file:
            reader.seek(0)
            copied = 0
            hasher = hashlib.sha256()
            while chunk := reader.read(CHUNK_BYTES):
                copied += len(chunk)
                if copied > size:
                    raise StorageIntegrityError("Upload changed during storage")
                temporary_file.write(chunk)
                hasher.update(chunk)
            if copied != size or hasher.hexdigest() != digest:
                raise StorageIntegrityError("Upload changed during storage")
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        try:
            os.link(temporary_path, target)
            created = True
        except FileExistsError:
            _verify_existing(target, digest, size)
            created = False
        except (OSError, NotImplementedError) as error:
            raise StorageCapabilityError(
                "Atomic hard-link publication unavailable"
            ) from error
        return target, created
    finally:
        temporary_path.unlink(missing_ok=True)


def _verify_existing(path: Path, digest: str, expected_size: int) -> None:
    try:
        if path.stat().st_size != expected_size:
            raise StorageIntegrityError("Stored content does not match its size")
        hasher = hashlib.sha256()
        with path.open("rb") as stored_file:
            for chunk in iter(lambda: stored_file.read(CHUNK_BYTES), b""):
                hasher.update(chunk)
    except OSError as error:
        raise StorageIntegrityError("Stored content is unreadable") from error
    if hasher.hexdigest() != digest:
        raise StorageIntegrityError("Stored content does not match its digest")
