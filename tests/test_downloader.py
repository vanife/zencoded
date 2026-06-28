import ipaddress

import pytest

from zencoded import downloader
from zencoded.downloader import DownloadError, validate_url


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/x",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/x",
        "http://192.168.1.1/x",
        "http://localhost/x",
        "http://[::1]/x",
        "http://0.0.0.0/x",
    ],
)
def test_blocks_private_and_metadata(url):
    with pytest.raises(DownloadError):
        validate_url(url)


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/x", "gopher://x/"])
def test_blocks_disallowed_schemes(url):
    with pytest.raises(DownloadError):
        validate_url(url)


def test_blocks_when_dns_resolves_to_private(monkeypatch):
    # Even a public-looking hostname is rejected if it resolves to a private IP.
    def fake_getaddrinfo(host, port, *a, **k):
        return [(2, 1, 6, "", ("10.1.2.3", port))]

    monkeypatch.setattr(downloader.socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(DownloadError):
        validate_url("https://sneaky.example.com/x")


def test_allows_public(monkeypatch):
    def fake_getaddrinfo(host, port, *a, **k):
        return [(2, 1, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(downloader.socket, "getaddrinfo", fake_getaddrinfo)
    url = validate_url("https://example.com/file.bin")
    assert str(url).startswith("https://example.com")


def test_is_blocked_ip_classification():
    assert downloader._is_blocked_ip(ipaddress.ip_address("169.254.169.254"))
    assert downloader._is_blocked_ip(ipaddress.ip_address("127.0.0.1"))
    assert downloader._is_blocked_ip(ipaddress.ip_address("fe80::1"))
    assert not downloader._is_blocked_ip(ipaddress.ip_address("93.184.216.34"))


def test_filename_sanitization():
    assert downloader._sanitize_filename("../../etc/passwd") == "passwd"
    assert downloader._sanitize_filename("a/b/c.zip") == "c.zip"
    assert downloader._sanitize_filename("") == "download.bin"
    assert downloader._sanitize_filename("%2e%2e%2ffoo.txt") == "foo.txt"
