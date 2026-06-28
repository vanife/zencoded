"""Core encoder: turn a file's bytes into a self-extracting Python script.

The generated script embeds the (optionally gzip-compressed) bytes as base64 and,
when executed, reconstructs the original file and verifies its SHA-256 checksum.
See :mod:`zencoded.template` for the script body.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from . import template

CompressMode = Literal["auto", "always", "never"]

#: Width of the base64 payload lines. Keeps diffs readable; decoders ignore newlines.
_WRAP_WIDTH = 76

#: Format version written into the data-file header.
DATAFILE_VERSION = 1
#: Line separating the JSON header from the base64 body in a data file. base64's
#: alphabet never contains '-', so this can never collide with a body line.
DATAFILE_SEPARATOR = "---"


@dataclass(frozen=True)
class EncodeResult:
    """Outcome of encoding a file into a self-extractor script."""

    filename: str
    script: str
    compressed: bool
    sha256: str
    original_size: int
    payload_size: int  # size of the bytes actually base64-encoded


@dataclass(frozen=True)
class DatafileResult:
    """Outcome of encoding a file into a header+base64 data file."""

    filename: str  # original file name
    content: str  # the full data-file text (JSON header + '---' + base64)
    compressed: bool
    sha256: str
    original_size: int
    payload_size: int


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


def _wrap_b64(encoded: str, prefix: str = "") -> str:
    """Wrap a base64 string into fixed-width lines, optionally prefixing each line.

    Fixed-width slicing rather than textwrap.wrap, which is prose-oriented and
    pathologically slow/memory-hungry on a multi-hundred-MB single token.
    """
    if not encoded:
        return ""
    return "\n".join(
        prefix + encoded[i : i + _WRAP_WIDTH]
        for i in range(0, len(encoded), _WRAP_WIDTH)
    )


def _compression_name(compressed: bool) -> str:
    return "gzip" if compressed else "none"


def render_script(
    *,
    filename: str,
    payload: bytes,
    compressed: bool,
    sha256: str,
    original_size: int,
    source: str | None,
) -> str:
    """Substitute the template tokens to produce the standalone script source.

    The base64 payload is emitted as a '#'-prefixed comment block so Python never
    compiles it as a string constant (see template).
    """
    body = template.TEMPLATE
    encoded = base64.b64encode(payload).decode("ascii")
    replacements = {
        template.TOKEN_FILENAME: filename,
        template.TOKEN_ENCODING: "base64",
        template.TOKEN_COMPRESSION: _compression_name(compressed),
        template.TOKEN_SHA256: sha256,
        template.TOKEN_SIZE: str(original_size),
        template.TOKEN_SOURCE: repr(source),  # None or a quoted string literal
        template.TOKEN_PAYLOAD: _wrap_b64(encoded, prefix="#"),
    }
    for token, value in replacements.items():
        body = body.replace(token, value)
    return body


def render_datafile(
    *,
    filename: str,
    payload: bytes,
    compressed: bool,
    sha256: str,
    original_size: int,
    source: str | None,
) -> str:
    """Build the data-file text: a pretty JSON header, '---', then base64 body."""
    header = {
        "zencoded": DATAFILE_VERSION,
        "encoding": "base64",
        "compression": _compression_name(compressed),
        "filename": filename,
        "size": original_size,
        "sha256": sha256,
    }
    if source is not None:
        header["source"] = source
    # indent=4 => human-readable, one property per line; sort_keys => stable diffs.
    header_text = json.dumps(header, indent=4, sort_keys=True)
    encoded = base64.b64encode(payload).decode("ascii")
    return f"{header_text}\n{DATAFILE_SEPARATOR}\n{_wrap_b64(encoded)}\n"


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


def encode_bytes_to_datafile(
    data: bytes,
    filename: str,
    *,
    compress: CompressMode = "auto",
    source: str | None = None,
) -> DatafileResult:
    """Encode in-memory bytes into a header+base64 data file (read by ``extract.py``)."""
    filename = Path(filename).name  # never embed a path, only the base name
    if not filename or filename in (".", ".."):
        raise ValueError(f"invalid output filename: {filename!r}")
    sha256 = hashlib.sha256(data).hexdigest()
    payload, compressed = _choose_payload(data, compress)
    content = render_datafile(
        filename=filename,
        payload=payload,
        compressed=compressed,
        sha256=sha256,
        original_size=len(data),
        source=source,
    )
    return DatafileResult(
        filename=filename,
        content=content,
        compressed=compressed,
        sha256=sha256,
        original_size=len(data),
        payload_size=len(payload),
    )


def encode_file_to_datafile(
    path: str | Path,
    *,
    compress: CompressMode = "auto",
    source: str | None = None,
) -> DatafileResult:
    """Encode a file on disk into a header+base64 data file."""
    path = Path(path)
    data = path.read_bytes()
    return encode_bytes_to_datafile(data, path.name, compress=compress, source=source)


def script_filename(original_filename: str) -> str:
    """Name of the generated self-extractor script for a given original filename."""
    return f"{Path(original_filename).name}.py"


def datafile_filename(original_filename: str) -> str:
    """Name of the generated data file for a given original filename."""
    return f"{Path(original_filename).name}.txt"
