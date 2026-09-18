"""Public wire contracts for the signed-in hardware-daily reader."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class HardwareDailyArticle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    content_id: str = Field(min_length=1)
    content_text: str = Field(min_length=1)
    url: HttpUrl
    comment_count: int = Field(ge=0)
    vote_up_count: int = Field(ge=0)
    author_name: str = Field(min_length=1)
    author_profile_url: HttpUrl
    author_badge_text: str | None = None
    edit_time: datetime
    authority_level: str | None = None
    thumbnail_url: HttpUrl | None = None
    content_is_excerpt: bool = True


class HardwareDailyCacheState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["fresh", "stale"]
    fetched_at: datetime
    expires_at: datetime


class HardwareDailyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    articles: list[HardwareDailyArticle] = Field(min_length=1)
    cache: HardwareDailyCacheState
