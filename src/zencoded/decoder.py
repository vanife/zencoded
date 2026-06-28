"""Decode zencoded data files (JSON header + '---' + base64 body).

This is the importable counterpart of the standalone, stdlib-only ``extract.py`` at the
repo root; the two intentionally mirror each other (extract.py must run without the
``zencoded`` package installed, so the logic is duplicated). Both stream the body so
peak memory stays small regardless of file size.
"""

from __future__ import annotations

import binascii
import hashlib
import json
import zlib
from pathlib import Path
from typing import IO, Iterator, Optional

from .encoder import DATAFILE_SEPARATOR

_BLOCK_CHARS = 1 << 24  # read/decode ~16 MiB at a time


class DecodeError(Exception):
    """Raised when a data file is malformed or fails verification."""


def read_header(fh: IO[str]) -> dict:
    """Read JSON header lines up to the separator; leave fh at the first body line."""
    lines: list[str] = []
    for line in fh:
        if line.rstrip("\n") == DATAFILE_SEPARATOR:
            break
        lines.append(line)
    else:
        raise DecodeError(f"missing {DATAFILE_SEPARATOR!r} separator")
    try:
        return json.loads("".join(lines))
    except json.JSONDecodeError as exc:
        raise DecodeError(f"header is not valid JSON: {exc}") from exc


def _body_blocks(fh: IO[str]) -> Iterator[str]:
    while True:
        block = fh.read(_BLOCK_CHARS)
        if not block:
            return
        if not block.endswith("\n"):
            block += fh.readline()
        yield block


def original_chunks(fh: IO[str], compression: str) -> Iterator[bytes]:
    """Yield the original bytes, decoding base64 (and gunzipping) lazily."""
    decompressor = (
        zlib.decompressobj(16 + zlib.MAX_WBITS) if compression == "gzip" else None
    )
    for block in _body_blocks(fh):
        raw = binascii.a2b_base64(block)  # newlines ignored
        if decompressor is None:
            if raw:
                yield raw
            continue
        while True:
            out = decompressor.decompress(raw, _BLOCK_CHARS)
            raw = decompressor.unconsumed_tail
            if out:
                yield out
            if not out and not raw:
                break
    if decompressor is not None:
        tail = decompressor.flush()
        if tail:
            yield tail


def extract_datafile(
    datafile: str | Path,
    output_dir: str | Path = ".",
    *,
    force: bool = False,
) -> Path:
    """Reconstruct the original file from ``datafile`` and return its path."""
    with open(datafile, "r", encoding="ascii") as fh:
        header = read_header(fh)
        if header.get("encoding") != "base64":
            raise DecodeError(f"unsupported encoding: {header.get('encoding')!r}")
        compression = header.get("compression", "none")
        if compression not in ("none", "gzip"):
            raise DecodeError(f"unsupported compression: {compression!r}")
        filename = Path(header.get("filename", "")).name
        if not filename:
            raise DecodeError("missing filename in header")
        expected_sha = header.get("sha256")

        target = Path(output_dir) / filename
        if target.exists() and not force:
            raise DecodeError(f"refusing to overwrite existing file: {target} (use force)")
        target.parent.mkdir(parents=True, exist_ok=True)

        digest = hashlib.sha256()
        with target.open("wb") as out:
            for chunk in original_chunks(fh, compression):
                digest.update(chunk)
                out.write(chunk)

    if expected_sha and digest.hexdigest() != expected_sha:
        target.unlink(missing_ok=True)
        raise DecodeError(
            f"checksum mismatch: expected {expected_sha}, got {digest.hexdigest()}"
        )
    return target
