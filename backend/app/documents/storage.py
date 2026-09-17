import hashlib
import os
import tempfile
from pathlib import Path

ALLOWED_SUFFIXES = {".pdf", ".docx"}
MAX_UPLOAD_BYTES = 50 * 1024 * 1024


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def safe_storage_path(root: Path, digest: str, original_name: str) -> Path:
    suffix = Path(original_name).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise ValueError("Unsupported document type")
    resolved_root = root.resolve()
    target = (resolved_root / digest[:2] / f"{digest}{suffix}").resolve()
    if resolved_root not in target.parents:
        raise ValueError("Storage path escapes configured root")
    return target


def store_content(
    root: Path,
    digest: str,
    original_name: str,
    content: bytes,
) -> tuple[Path, bool]:
    """Store content once and return its path plus whether this call created it."""
    target = safe_storage_path(root, digest, original_name)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        _verify_existing(target, digest, len(content))
        return target, False

    descriptor, temporary_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{digest}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as temporary_file:
            temporary_file.write(content)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        try:
            os.link(temporary_path, target)
            created = True
        except FileExistsError:
            _verify_existing(target, digest, len(content))
            created = False
        return target, created
    finally:
        temporary_path.unlink(missing_ok=True)


def _verify_existing(path: Path, digest: str, expected_size: int) -> None:
    if path.stat().st_size != expected_size:
        raise ValueError("Stored content does not match its digest")
    hasher = hashlib.sha256()
    with path.open("rb") as stored_file:
        for chunk in iter(lambda: stored_file.read(1024 * 1024), b""):
            hasher.update(chunk)
    if hasher.hexdigest() != digest:
        raise ValueError("Stored content does not match its digest")
