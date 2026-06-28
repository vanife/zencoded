#!/usr/bin/env python3
"""Reconstruct the original file from a zencoded data file.

A zencoded data file is plain text: a JSON header, a line containing only ``---``, then
the (optionally gzip-compressed) payload as wrapped base64. This reader is standard
library only and streams the body, so peak memory stays small regardless of file size:

    python extract.py tool.zip.txt              # write ./tool.zip
    python extract.py tool.zip.txt -o out/      # write into out/
    python extract.py tool.zip.txt --force      # overwrite an existing target
    python extract.py tool.zip.txt --stdout     # write raw bytes to stdout

The SHA-256 recorded in the header is verified; a mismatch is a hard error.
"""
import argparse
import binascii
import hashlib
import json
import sys
import zlib
from pathlib import Path

SEPARATOR = "---"
_BLOCK_CHARS = 1 << 24  # read/decode ~16 MiB at a time


def read_header(fh):
    """Read JSON header lines up to the '---' separator and return the parsed dict.

    Leaves the file positioned at the first body line.
    """
    lines = []
    for line in fh:
        if line.rstrip("\n") == SEPARATOR:
            break
        lines.append(line)
    else:
        raise SystemExit("invalid data file: missing '---' separator")
    try:
        return json.loads("".join(lines))
    except json.JSONDecodeError as exc:
        raise SystemExit("invalid data file: header is not valid JSON: %s" % exc)


def _body_blocks(fh):
    """Yield ~16 MiB blocks of the base64 body, each ending on a line boundary."""
    while True:
        block = fh.read(_BLOCK_CHARS)
        if not block:
            return
        if not block.endswith("\n"):
            block += fh.readline()  # finish the trailing partial line
        yield block


def original_chunks(fh, compression):
    """Yield the original file's bytes, decoding base64 (and gunzipping) lazily."""
    decompressor = (
        zlib.decompressobj(16 + zlib.MAX_WBITS) if compression == "gzip" else None
    )
    for block in _body_blocks(fh):
        raw = binascii.a2b_base64(block)  # newlines are ignored
        if decompressor is None:
            if raw:
                yield raw
            continue
        # Bound decompressed output per call so a tiny but highly compressible block
        # can't expand to gigabytes at once; keep draining until nothing is left.
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("datafile", help="path to the zencoded data file")
    parser.add_argument(
        "-o", "--output-dir", default=".",
        help="directory to write the extracted file into (default: current dir)",
    )
    parser.add_argument(
        "-f", "--force", action="store_true",
        help="overwrite the target file if it already exists",
    )
    parser.add_argument(
        "--stdout", action="store_true",
        help="write the raw bytes to stdout instead of a file",
    )
    args = parser.parse_args(argv)

    with open(args.datafile, "r", encoding="ascii") as fh:
        header = read_header(fh)

        encoding = header.get("encoding")
        if encoding != "base64":
            raise SystemExit("unsupported encoding: %r" % (encoding,))
        compression = header.get("compression", "none")
        if compression not in ("none", "gzip"):
            raise SystemExit("unsupported compression: %r" % (compression,))
        expected_sha = header.get("sha256")
        filename = Path(header.get("filename", "")).name
        if not filename and not args.stdout:
            raise SystemExit("invalid data file: missing filename")

        digest = hashlib.sha256()

        if args.stdout:
            out = sys.stdout.buffer
            for chunk in original_chunks(fh, compression):
                digest.update(chunk)
                out.write(chunk)
            out.flush()
            if expected_sha and digest.hexdigest() != expected_sha:
                raise SystemExit("checksum mismatch: extracted data is corrupt")
            return 0

        target = Path(args.output_dir) / filename
        if target.exists() and not args.force:
            raise SystemExit(
                "refusing to overwrite existing file: %s (use --force)" % target
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        with target.open("wb") as out:
            for chunk in original_chunks(fh, compression):
                digest.update(chunk)
                out.write(chunk)
                written += len(chunk)

    if expected_sha and digest.hexdigest() != expected_sha:
        target.unlink()  # don't leave a corrupt file behind
        raise SystemExit(
            "checksum mismatch: expected %s, got %s" % (expected_sha, digest.hexdigest())
        )
    print("wrote %s (%d bytes)" % (target, written))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
