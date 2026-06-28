"""Request/response schemas for the web API."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, field_validator


class JobRequest(BaseModel):
    url: str
    compress: Optional[Literal["auto", "always", "never"]] = None

    @field_validator("url")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("url must not be empty")
        return v


class JobCreated(BaseModel):
    job_id: str
    status: str


class JobView(BaseModel):
    id: str
    url: str
    compress: str
    actor: str
    status: str
    created_at: str
    updated_at: Optional[str] = None
    error: Optional[str] = None
    filename: Optional[str] = None
    script_path: Optional[str] = None
    sha256: Optional[str] = None
    original_size: Optional[int] = None
    compressed: Optional[bool] = None
    pushed: Optional[bool] = None
    published_via: Optional[str] = None
    download_url: Optional[str] = None


class UserView(BaseModel):
    login: str
