"""Artifact identity producer: the plugin hashes real bytes.

Before v4 an artifact SHA only ever existed as a *claim*: a run's
``metadata.artifact_sha256`` written by a model, with no producer anywhere in
the plugin. On a real board that meant every PASS ended in
``held/artifact_unbound`` and ``ready_to_deliver`` was unreachable.

This module is the missing producer. It is deliberately tiny and depends only
on the standard library and a local path: read the file once, refuse anything
that is not a bounded regular file, and return the identity triple the store
records (sha256, real path, byte length). It performs no network I/O, no
subprocess call and no board write.

The gate then requires *this* record for the current revision; a submission
that never went through it stays ``artifact_unbound``.
"""
from __future__ import annotations

import hashlib
import os
import stat

__all__ = ["ArtifactError", "MAX_ARTIFACT_BYTES", "hash_artifact"]

#: Largest artifact the producer will hash, in bytes. A bigger file is refused
#: rather than streamed for an unbounded time.
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024

_CHUNK = 1 << 20


class ArtifactError(Exception):
    """A local artifact could not be turned into a verifiable identity."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(code)


def hash_artifact(path: str) -> tuple[str, str, int]:
    """Return ``(sha256_hex, real_path, size_bytes)`` for one regular file.

    The file is opened once and measured through its descriptor, so a path swap
    between ``stat`` and read cannot change what was hashed. Symlinks resolve
    to their target exactly once (``realpath``) and the resolved path is what
    gets recorded and re-opened.
    """
    if not isinstance(path, str) or not path.strip():
        raise ArtifactError("artifact_path_invalid", "path must be a non-empty string")
    if not os.path.isabs(path):
        raise ArtifactError("artifact_path_invalid",
                            "path must be absolute so the identity is auditable")
    try:
        real = os.path.realpath(path)
        info = os.stat(real)
    except FileNotFoundError:
        raise ArtifactError("artifact_missing", f"no such file: {real}") from None
    except OSError as exc:
        raise ArtifactError("artifact_unreadable", f"{type(exc).__name__}: {exc}") from None
    if not stat.S_ISREG(info.st_mode):
        raise ArtifactError("artifact_not_regular", f"not a regular file: {real}")
    if info.st_size > MAX_ARTIFACT_BYTES:
        raise ArtifactError("artifact_too_large",
                            f"{info.st_size} bytes exceeds {MAX_ARTIFACT_BYTES}")

    hasher = hashlib.sha256()
    size = 0
    try:
        with open(real, "rb") as handle:
            fd = handle.fileno()
            actual = os.fstat(fd)
            if (actual.st_dev, actual.st_ino) != (info.st_dev, info.st_ino):
                raise ArtifactError("artifact_changed_while_reading",
                                    "path was replaced between stat and open")
            while True:
                chunk = handle.read(_CHUNK)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_ARTIFACT_BYTES:
                    raise ArtifactError("artifact_too_large",
                                        f"grew past {MAX_ARTIFACT_BYTES} bytes while reading")
                hasher.update(chunk)
    except ArtifactError:
        raise
    except OSError as exc:
        raise ArtifactError("artifact_unreadable", f"{type(exc).__name__}: {exc}") from None
    if size != info.st_size:
        raise ArtifactError("artifact_changed_while_reading",
                            f"read {size} bytes but stat said {info.st_size}")
    return hasher.hexdigest(), real, size
