import gzip
import os
import subprocess
import sys
from pathlib import Path

import pytest

from zencoded import encoder

SAMPLES = {
    "hello.txt": b"Hello from zencoded!\n" * 200,
    "empty.bin": b"",
    "already.gz": gzip.compress(b"x" * 5000),
    "random.bin": os.urandom(4096),
}


def _extract(script: str, out_dir: Path, name: str) -> bytes:
    script_path = out_dir / "extractor.py"
    script_path.write_text(script)
    subprocess.run(
        [sys.executable, str(script_path), "-o", str(out_dir)],
        check=True,
        capture_output=True,
    )
    return (out_dir / name).read_bytes()


@pytest.mark.parametrize("mode", ["auto", "always", "never"])
@pytest.mark.parametrize("name", list(SAMPLES))
def test_round_trip(tmp_path, mode, name):
    data = SAMPLES[name]
    result = encoder.encode_bytes(data, name, compress=mode, source="https://x/" + name)
    assert _extract(result.script, tmp_path, name) == data
    assert result.sha256 == __import__("hashlib").sha256(data).hexdigest()
    if mode == "never":
        assert result.compressed is False
    if mode == "always":
        assert result.compressed is True


def test_payload_is_trailing_comment_block_not_literal():
    # The payload must NOT be embedded as a compiled string literal (that costs
    # several times its size in RAM to compile). It lives as a trailing comment block
    # streamed from __file__. Guard against regressing to the literal format.
    result = encoder.encode_bytes(b"x" * 5000, "x.bin", compress="never")
    assert 'PAYLOAD = """' not in result.script
    assert "ZENCODED-PAYLOAD-DO-NOT-EDIT-BELOW" in result.script
    payload_lines = [
        ln for ln in result.script.splitlines()
        if ln and ln[0] == "#" and "ZENCODED" not in ln and "!/usr" not in ln
    ]
    assert payload_lines, "expected #-prefixed payload comment lines"


def test_round_trip_highly_compressible_compressed(tmp_path):
    # Exercises the bounded streaming-decompress path (small input expands a lot).
    data = b"zencoded " * 200_000  # ~1.8 MB, very compressible
    result = encoder.encode_bytes(data, "blob.bin", compress="always")
    assert result.compressed is True
    assert _extract(result.script, tmp_path, "blob.bin") == data


def test_auto_does_not_enlarge_compressed_input():
    data = SAMPLES["already.gz"]
    result = encoder.encode_bytes(data, "already.gz", compress="auto")
    # auto must store raw rather than inflate an already-compressed payload
    assert result.compressed is False
    assert result.payload_size == len(data)


def test_auto_compresses_compressible_input():
    result = encoder.encode_bytes(b"a" * 10000, "a.txt", compress="auto")
    assert result.compressed is True
    assert result.payload_size < 10000


def test_checksum_mismatch_is_detected(tmp_path):
    result = encoder.encode_bytes(b"abc", "a.txt")
    tampered = result.script.replace(result.sha256, "0" * 64)
    path = tmp_path / "x.py"
    path.write_text(tampered)
    proc = subprocess.run(
        [sys.executable, str(path), "-o", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    assert "mismatch" in proc.stderr


def test_refuses_overwrite_without_force(tmp_path):
    result = encoder.encode_bytes(b"data", "f.txt")
    path = tmp_path / "x.py"
    path.write_text(result.script)
    subprocess.run([sys.executable, str(path), "-o", str(tmp_path)], check=True, capture_output=True)
    proc = subprocess.run(
        [sys.executable, str(path), "-o", str(tmp_path)], capture_output=True, text=True
    )
    assert proc.returncode != 0 and "refusing" in proc.stderr
    subprocess.run(
        [sys.executable, str(path), "-o", str(tmp_path), "--force"],
        check=True,
        capture_output=True,
    )


def test_filename_is_basename_only():
    result = encoder.encode_bytes(b"x", "../../etc/passwd")
    assert result.filename == "passwd"


def test_invalid_filename_rejected():
    with pytest.raises(ValueError):
        encoder.encode_bytes(b"x", "..")


def test_script_filename():
    assert encoder.script_filename("foo.zip") == "foo.zip.py"


def test_datafile_filename():
    assert encoder.datafile_filename("foo.zip") == "foo.zip.txt"


def test_self_extractor_declares_encoding_and_compression():
    plain = encoder.encode_bytes(b"x" * 100, "x.bin", compress="never")
    assert 'ENCODING = "base64"' in plain.script
    assert 'COMPRESSION = "none"' in plain.script
    zipped = encoder.encode_bytes(b"a" * 5000, "a.bin", compress="always")
    assert 'COMPRESSION = "gzip"' in zipped.script


def test_datafile_header_valid_json_and_body_plain():
    res = encoder.encode_bytes_to_datafile(b"a" * 5000, "a.bin", compress="always")
    header_text, sep, body = res.content.partition("\n---\n")
    assert sep
    header = __import__("json").loads(header_text)
    assert header == {
        "zencoded": 1,
        "encoding": "base64",
        "compression": "gzip",
        "filename": "a.bin",
        "size": 5000,
        "sha256": res.sha256,
    }
    assert body.strip() and "#" not in body  # plain base64, no comment prefixes


def test_datafile_omits_source_when_absent():
    res = encoder.encode_bytes_to_datafile(b"x", "x.bin")
    header = __import__("json").loads(res.content.split("\n---\n")[0])
    assert "source" not in header
