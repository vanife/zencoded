"""Job orchestration: download -> encode -> publish, with in-process status tracking.

Jobs run as FastAPI background tasks. Status lives in an in-memory registry keyed by
job id (suitable for the single-instance, low-volume deployment this service targets;
swap for a shared store if scaled out).
"""

from __future__ import annotations

import shutil
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from . import encoder, publisher
from .config import Settings
from .downloader import DownloadError, download
from .encoder import CompressMode
from .publisher import PublishError, publish
from .releaser import ReleaseError, upload_asset


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


@dataclass
class Job:
    id: str
    url: str
    compress: CompressMode
    actor: str
    status: JobStatus = JobStatus.QUEUED
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: Optional[str] = None
    error: Optional[str] = None
    # Result fields (populated on success).
    filename: Optional[str] = None
    script_path: Optional[str] = None
    sha256: Optional[str] = None
    original_size: Optional[int] = None
    compressed: Optional[bool] = None
    pushed: Optional[bool] = None
    published_via: Optional[str] = None  # "git" or "release"
    download_url: Optional[str] = None  # release asset URL, when published via release

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d


class JobRegistry:
    """Thread-safe in-memory store of jobs."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def create(self, url: str, compress: CompressMode, actor: str) -> Job:
        job = Job(id=uuid.uuid4().hex, url=url, compress=compress, actor=actor)
        with self._lock:
            self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def update(self, job_id: str, **changes) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            for key, value in changes.items():
                setattr(job, key, value)
            job.updated_at = datetime.now(timezone.utc).isoformat()


# Module-level registry shared by the web app.
registry = JobRegistry()


async def run_job(settings: Settings, job_id: str) -> None:
    """Execute a job end-to-end, recording status transitions in the registry."""
    import asyncio

    job = registry.get(job_id)
    if job is None:
        return

    registry.update(job_id, status=JobStatus.RUNNING)
    work_dir = settings.temp_dir / job_id
    try:
        result = await download(
            job.url,
            work_dir,
            max_bytes=settings.max_download_bytes,
            timeout=settings.download_timeout,
            max_redirects=settings.max_redirects,
        )

        # Encoding can be CPU/memory heavy for large files -> run off the event loop
        # so a big job does not block other requests.
        encoded = await asyncio.to_thread(
            encoder.encode_file, result.path, compress=job.compress, source=result.final_url
        )
        script_name = encoder.script_filename(encoded.filename)

        # Route by size: large scripts can't be git-pushed (>100 MiB), so upload them
        # as a Release asset instead. Threshold/mode come from settings.
        script_size = len(encoded.script.encode("utf-8"))
        mode = settings.resolve_publish_mode(script_size)

        # The file is always written to data/ first; both paths need it on disk.
        target = await asyncio.to_thread(
            publisher.write_script, settings, script_name, encoded.script
        )

        result_fields: dict = {
            "filename": encoded.filename,
            "script_path": str(target),
            "sha256": encoded.sha256,
            "original_size": encoded.original_size,
            "compressed": encoded.compressed,
            "published_via": mode,
        }

        if mode == "release":
            released = await asyncio.to_thread(upload_asset, settings, path=target)
            result_fields["download_url"] = released.download_url
            result_fields["pushed"] = released.uploaded
        else:  # git
            published = await asyncio.to_thread(
                publish,
                settings,
                filename=script_name,
                script=encoded.script,
                source_url=result.final_url,
                actor=job.actor,
                job_id=job_id,
            )
            result_fields["pushed"] = published.pushed

        registry.update(job_id, status=JobStatus.SUCCESS, **result_fields)
    except (DownloadError, PublishError, ReleaseError, ValueError, OSError) as exc:
        registry.update(job_id, status=JobStatus.FAILED, error=str(exc))
    except Exception as exc:  # noqa: BLE001 - record unexpected failures too
        registry.update(job_id, status=JobStatus.FAILED, error=f"unexpected error: {exc}")
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
