"""Core encoder: turn a file's bytes into a self-extracting Python script.

The generated script embeds the (optionally gzip-compressed) bytes as base64 and,
when executed, reconstructs the original file and verifies its SHA-256 checksum.
See :mod:`zencoded.template` for the script body.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from . import template

CompressMode = Literal["auto", "always", "never"]

#: Width of the base64 payload lines embedded in generated scripts. Keeps diffs
#: readable; the extractor ignores the newlines when decoding.
_WRAP_WIDTH = 76


@dataclass(frozen=True)
class EncodeResult:
    """Outcome of encoding a file."""

    filename: str
    script: str
    compressed: bool
    sha256: str
    original_size: int
    payload_size: int  # size of the bytes actually base64-encoded


def _gzip_bytes(data: bytes) -> bytes:
    # mtime=0 so identical input yields byte-identical output (stable git diffs).
    return gzip.compress(data, compresslevel=9, mtime=0)


def _choose_payload(data: bytes, compress: CompressMode) -> tuple[bytes, bool]:
    """Return ``(payload_bytes, compressed)`` per the requested mode.

    ``auto`` compresses only when it actually shrinks the data, so already-compressed
    inputs (``.zip``, ``.exe``, …) are stored raw without wasted bytes.
    """
    if compress == "never":
        return data, False
    gzipped = _gzip_bytes(data)
    if compress == "always":
        return gzipped, True
    # auto
    if len(gzipped) < len(data):
        return gzipped, True
    return data, False


def _b64_block(payload: bytes) -> str:
    encoded = base64.b64encode(payload).decode("ascii")
    if not encoded:
        return ""
    # Each line is prefixed with '#': the payload is appended to the generated script
    # as a comment block (see template), so Python never compiles it as a string
    # constant. Fixed-width slicing rather than textwrap.wrap, which is prose-oriented
    # and pathologically slow/memory-hungry on a multi-hundred-MB single token.
    return "\n".join(
        "#" + encoded[i : i + _WRAP_WIDTH] for i in range(0, len(encoded), _WRAP_WIDTH)
    )


def render_script(
    *,
    filename: str,
    payload: bytes,
    compressed: bool,
    sha256: str,
    original_size: int,
    source: str | None,
) -> str:
    """Substitute the template tokens to produce the standalone script source."""
    body = template.TEMPLATE
    replacements = {
        template.TOKEN_FILENAME: filename,
        template.TOKEN_COMPRESSED: "True" if compressed else "False",
        template.TOKEN_SHA256: sha256,
        template.TOKEN_SIZE: str(original_size),
        template.TOKEN_SOURCE: repr(source),  # None or a quoted string literal
        template.TOKEN_PAYLOAD: _b64_block(payload),
    }
    for token, value in replacements.items():
        body = body.replace(token, value)
    return body


def encode_bytes(
    data: bytes,
    filename: str,
    *,
    compress: CompressMode = "auto",
    source: str | None = None,
) -> EncodeResult:
    """Encode in-memory bytes into a self-extractor script."""
    filename = Path(filename).name  # never embed a path, only the base name
    if not filename or filename in (".", ".."):
        raise ValueError(f"invalid output filename: {filename!r}")
    sha256 = hashlib.sha256(data).hexdigest()
    payload, compressed = _choose_payload(data, compress)
    script = render_script(
        filename=filename,
        payload=payload,
        compressed=compressed,
        sha256=sha256,
        original_size=len(data),
        source=source,
    )
    return EncodeResult(
        filename=filename,
        script=script,
        compressed=compressed,
        sha256=sha256,
        original_size=len(data),
        payload_size=len(payload),
    )


def encode_file(
    path: str | Path,
    *,
    compress: CompressMode = "auto",
    source: str | None = None,
) -> EncodeResult:
    """Encode a file on disk into a self-extractor script."""
    path = Path(path)
    data = path.read_bytes()
    return encode_bytes(data, path.name, compress=compress, source=source)


def script_filename(original_filename: str) -> str:
    """Name of the generated script for a given original filename."""
    return f"{Path(original_filename).name}.py"
