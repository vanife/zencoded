import subprocess
from pathlib import Path

import pytest

from zencoded import publisher
from zencoded.config import Settings
from zencoded.publisher import PublishError


def _run(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    """A working repo with a bare 'origin' remote, branch 'main'."""
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(bare)], check=True, capture_output=True)
    work = tmp_path / "work"
    work.mkdir()
    _run(work, "init", "-b", "main")
    _run(work, "config", "user.email", "t@t")
    _run(work, "config", "user.name", "t")
    (work / "data").mkdir()
    (work / "README.md").write_text("seed\n")
    _run(work, "add", "-A")
    _run(work, "commit", "-m", "seed")
    _run(work, "remote", "add", "origin", str(bare))
    _run(work, "push", "-u", "origin", "main")
    return work, bare


def _settings(work: Path) -> Settings:
    return Settings(
        repo_dir=work,
        data_dir=work / "data",
        git_branch="main",
        git_remote="origin",
        deploy_key_path=None,  # local file remote, no SSH
        publish_enabled=True,
        secure_cookies=False,
    )


def test_path_traversal_rejected(tmp_path):
    s = _settings(tmp_path)
    (tmp_path / "data").mkdir()
    with pytest.raises(PublishError):
        publisher.write_script(s, "../escape.py", "print('x')")
    with pytest.raises(PublishError):
        publisher.write_script(s, "sub/dir.py", "print('x')")


def test_publish_commits_and_pushes(repo):
    work, bare = repo
    s = _settings(work)
    result = publisher.publish(
        s,
        filename="tool.zip.py",
        script="print('hello')\n",
        source_url="https://example.com/tool.zip",
        actor="alice",
        job_id="job123",
    )
    assert result.committed and result.pushed
    assert (work / "data" / "tool.zip.py").exists()
    # File made it to the remote.
    listing = subprocess.run(
        ["git", "-C", str(bare), "ls-tree", "-r", "--name-only", "main"],
        check=True, capture_output=True, text=True,
    ).stdout
    assert "data/tool.zip.py" in listing
    # Commit message carries provenance.
    msg = subprocess.run(
        ["git", "-C", str(work), "log", "-1", "--pretty=%B"],
        check=True, capture_output=True, text=True,
    ).stdout
    assert "https://example.com/tool.zip" in msg and "alice" in msg and "job123" in msg


def test_publish_disabled_writes_without_commit(repo):
    work, _ = repo
    s = _settings(work)
    s.publish_enabled = False
    result = publisher.publish(
        s, filename="x.py", script="print(1)\n",
        source_url="https://e/x", actor="a", job_id="j",
    )
    assert not result.committed and not result.pushed
    assert (work / "data" / "x.py").exists()


def test_publish_idempotent_when_unchanged(repo):
    work, _ = repo
    s = _settings(work)
    kwargs = dict(
        filename="d.py", script="same\n", source_url="https://e/d",
        actor="a", job_id="j",
    )
    first = publisher.publish(s, **kwargs)
    assert first.committed
    second = publisher.publish(s, **kwargs)  # identical content -> nothing to commit
    assert not second.committed and not second.pushed
