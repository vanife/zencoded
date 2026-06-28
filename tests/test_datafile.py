import gzip
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from zencoded import encoder
from zencoded.decoder import DecodeError, extract_datafile

EXTRACT_PY = Path(__file__).resolve().parents[1] / "extract.py"

SAMPLES = {
    "hello.txt": b"Hello from zencoded!\n" * 200,
    "empty.bin": b"",
    "already.gz": gzip.compress(b"y" * 5000),
    "random.bin": os.urandom(4096),
}


def _write_datafile(tmp_path, name, data, mode):
    res = encoder.encode_bytes_to_datafile(data, name, compress=mode, source="https://x/" + name)
    df = tmp_path / encoder.datafile_filename(name)
    df.write_text(res.content)
    return df, res


def _extract_with_script(datafile, out_dir, *extra):
    return subprocess.run(
        [sys.executable, str(EXTRACT_PY), str(datafile), "-o", str(out_dir), *extra],
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("mode", ["auto", "always", "never"])
@pytest.mark.parametrize("name", list(SAMPLES))
def test_round_trip_via_extract_script(tmp_path, mode, name):
    data = SAMPLES[name]
    df, _ = _write_datafile(tmp_path, name, data, mode)
    out = tmp_path / "out"
    proc = _extract_with_script(df, out)
    assert proc.returncode == 0, proc.stderr
    assert (out / name).read_bytes() == data


@pytest.mark.parametrize("mode", ["auto", "always", "never"])
def test_round_trip_via_package_decoder(tmp_path, mode):
    data = SAMPLES["random.bin"]
    df, _ = _write_datafile(tmp_path, "random.bin", data, mode)
    target = extract_datafile(df, tmp_path / "out")
    assert target.read_bytes() == data


def test_header_is_pretty_json_with_expected_fields(tmp_path):
    df, res = _write_datafile(tmp_path, "tool.zip", b"x" * 100, "never")
    text = df.read_text()
    header_text, sep, _ = text.partition("\n---\n")
    assert sep == "\n---\n"
    # one property per line, 4-space indent
    assert header_text.splitlines()[0] == "{"
    assert '    "encoding": "base64"' in header_text
    header = json.loads(header_text)
    assert header["encoding"] == "base64"
    assert header["compression"] == "none"
    assert header["filename"] == "tool.zip"
    assert header["size"] == 100
    assert header["sha256"] == res.sha256
    assert header["zencoded"] == 1


def test_body_is_plain_base64_no_comment_prefix(tmp_path):
    df, _ = _write_datafile(tmp_path, "b.bin", os.urandom(500), "never")
    body = df.read_text().split("\n---\n", 1)[1]
    assert body.strip()
    assert "#" not in body  # data file body is plain base64, unlike the .py comment block


def test_unknown_encoding_is_rejected(tmp_path):
    df, _ = _write_datafile(tmp_path, "x.bin", b"abc", "never")
    tampered = df.read_text().replace('"encoding": "base64"', '"encoding": "base32"')
    df.write_text(tampered)
    proc = _extract_with_script(df, tmp_path / "out")
    assert proc.returncode != 0 and "unsupported encoding" in proc.stderr
    with pytest.raises(DecodeError, match="unsupported encoding"):
        extract_datafile(df, tmp_path / "out2")


def test_checksum_mismatch_detected_and_file_removed(tmp_path):
    df, res = _write_datafile(tmp_path, "x.bin", b"abc", "never")
    df.write_text(df.read_text().replace(res.sha256, "0" * 64))
    out = tmp_path / "out"
    proc = _extract_with_script(df, out)
    assert proc.returncode != 0 and "mismatch" in proc.stderr
    assert not (out / "x.bin").exists()  # corrupt output must not be left behind


def test_refuses_overwrite_without_force(tmp_path):
    df, _ = _write_datafile(tmp_path, "x.bin", b"data", "never")
    out = tmp_path / "out"
    assert _extract_with_script(df, out).returncode == 0
    again = _extract_with_script(df, out)
    assert again.returncode != 0 and "refusing" in again.stderr
    assert _extract_with_script(df, out, "--force").returncode == 0


def test_stdout(tmp_path):
    data = b"stream me to stdout"
    df, _ = _write_datafile(tmp_path, "s.bin", data, "always")
    proc = subprocess.run(
        [sys.executable, str(EXTRACT_PY), str(df), "--stdout"],
        capture_output=True,
    )
    assert proc.returncode == 0
    assert proc.stdout == data


def test_missing_separator_is_error(tmp_path):
    bad = tmp_path / "bad.txt"
    bad.write_text('{"encoding": "base64"}\n')  # no --- separator
    with pytest.raises(DecodeError, match="separator"):
        extract_datafile(bad, tmp_path / "out")
