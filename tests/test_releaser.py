import httpx
import pytest

from zencoded import releaser
from zencoded.config import Settings
from zencoded.releaser import ReleaseError, upload_asset


def _settings(tmp_path, **overrides) -> Settings:
    base = dict(
        repo_dir=tmp_path,
        data_dir=tmp_path / "data",
        publish_enabled=True,
        publish_mode="release",
        github_token="t0ken",
        github_owner="octocat",
        github_repo="zencoded",
        release_tag="encoded",
        secure_cookies=False,
    )
    base.update(overrides)
    return Settings(**base)


def _script(tmp_path, name="tool.zip.py", content=b"print('hi')\n"):
    p = tmp_path / name
    p.write_bytes(content)
    return p


def test_requires_token_owner_repo(tmp_path):
    s = _settings(tmp_path, github_token="")
    with pytest.raises(ReleaseError, match="github_token"):
        upload_asset(s, path=_script(tmp_path), client=_client(lambda r: httpx.Response(200)))


def test_disabled_skips_upload(tmp_path):
    s = _settings(tmp_path, publish_enabled=False)
    res = upload_asset(s, path=_script(tmp_path), client=_client(lambda r: httpx.Response(500)))
    assert res.uploaded is False and res.download_url is None


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_creates_release_when_missing_and_uploads(tmp_path):
    s = _settings(tmp_path)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "GET" and request.url.path.endswith("/releases/tags/encoded"):
            return httpx.Response(404)
        if request.method == "POST" and request.url.path.endswith("/releases"):
            return httpx.Response(
                201,
                json={
                    "id": 1,
                    "assets": [],
                    "upload_url": "https://uploads.github.test/repos/octocat/zencoded/releases/1/assets{?name,label}",
                },
            )
        if request.method == "POST" and "uploads.github.test" in str(request.url):
            assert request.url.params["name"] == "tool.zip.py"
            return httpx.Response(
                201,
                json={"browser_download_url": "https://github.test/dl/tool.zip.py"},
            )
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    res = upload_asset(s, path=_script(tmp_path), client=_client(handler))
    assert res.uploaded and res.download_url == "https://github.test/dl/tool.zip.py"
    assert ("POST", "/repos/octocat/zencoded/releases") in calls  # created release


def test_overwrites_existing_asset(tmp_path):
    s = _settings(tmp_path)
    deleted = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/releases/tags/encoded"):
            return httpx.Response(
                200,
                json={
                    "id": 7,
                    "assets": [{"id": 99, "name": "tool.zip.py"}],
                    "upload_url": "https://uploads.github.test/repos/octocat/zencoded/releases/7/assets{?name,label}",
                },
            )
        if request.method == "DELETE":
            deleted.append(request.url.path)
            return httpx.Response(204)
        if request.method == "POST" and "uploads.github.test" in str(request.url):
            return httpx.Response(201, json={"browser_download_url": "https://github.test/dl/x"})
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    res = upload_asset(s, path=_script(tmp_path), client=_client(handler))
    assert res.uploaded
    assert deleted == ["/repos/octocat/zencoded/releases/assets/99"]


def test_upload_failure_raises(tmp_path):
    s = _settings(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(404)
        if request.method == "POST" and request.url.path.endswith("/releases"):
            return httpx.Response(
                201,
                json={"id": 1, "assets": [], "upload_url": "https://uploads.github.test/u{?name,label}"},
            )
        return httpx.Response(422, text="bad asset")

    with pytest.raises(ReleaseError, match="asset upload failed"):
        upload_asset(s, path=_script(tmp_path), client=_client(handler))


def test_resolve_publish_mode(tmp_path):
    s = _settings(tmp_path, publish_mode="auto", release_size_threshold=100)
    assert s.resolve_publish_mode(50) == "git"
    assert s.resolve_publish_mode(150) == "release"
    s2 = _settings(tmp_path, publish_mode="git")
    assert s2.resolve_publish_mode(10**9) == "git"
