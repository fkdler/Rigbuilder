"""Fetch and safely cache the one Zhihu hardware-daily article we display."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import re
import time
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.hardware_daily_cache import HardwareDailyCache
from app.schemas.hardware_daily import (
    HardwareDailyArticle,
    HardwareDailyCacheState,
    HardwareDailyResponse,
)

logger = logging.getLogger(__name__)

_CACHE_KEY = "zhihu:gpu-daily:v1"
_SEARCH_URL = "https://developer.zhihu.com/api/v1/content/zhihu_search"
_CACHE_SCHEMA_VERSION = 2
_LEADING_PROMO = "日报有用记得三连哦，你的鼓励真的很重要～"
_TRAILING_PROMO_PREFIX = "今天的日报就到这里，每晚11点准时更新"
_VIDEO_PROMO_PREFIX = "视频版本同步更新"


class HardwareDailyUnavailableError(Exception):
    """No safe cached issue is available to render."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    """SQLite test stores lose tzinfo; PostgreSQL values retain it."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _as_nonnegative_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _canonical_article_url(value: Any) -> str | None:
    """Accept only real Zhihu article URLs and drop provider tracking tags."""
    if not isinstance(value, str):
        return None
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.hostname != "zhuanlan.zhihu.com":
        return None
    if not parsed.path.startswith("/p/") or not parsed.path[3:].isdigit():
        return None
    query = [(key, item) for key, item in parse_qsl(parsed.query, keep_blank_values=True)
             if key.lower() not in {"utm_medium", "utm_source"}]
    return urlunparse(parsed._replace(query=urlencode(query)))


def _canonical_thumbnail_url(value: Any) -> str | None:
    """Accept only HTTPS images hosted by Zhihu's image CDN."""
    if not isinstance(value, str):
        return None
    parsed = urlparse(value.strip())
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (hostname == "zhimg.com" or hostname.endswith(".zhimg.com")):
        return None
    return value.strip()


def _normalize_title(value: str) -> str:
    title = re.sub(r"\s*-\s*知乎\s*$", "", value.strip())
    return re.sub(r"\s*[|｜]\s*", "｜", title)


def _clean_content(value: str) -> str:
    """Remove the author's repeated promotion lines without rewriting the article."""
    kept: list[str] = []
    for raw_line in value.splitlines():
        line = raw_line.strip()
        plain = line.strip("* ")
        link_label = re.sub(r"^\[([^]]+)](?:\([^)]*\))?$", r"\1", plain).strip()
        if plain == _LEADING_PROMO:
            continue
        if plain.startswith(_TRAILING_PROMO_PREFIX):
            continue
        if link_label.startswith(_VIDEO_PROMO_PREFIX):
            continue
        kept.append(raw_line.rstrip())
    return "\n".join(kept).strip()


class HardwareDailyService:
    """A small isolated service; it is intentionally not used by Agent code."""

    def __init__(self, settings: Settings):
        self.settings = settings

    async def latest(self, session: Session) -> HardwareDailyResponse:
        now = _utc_now()
        cached = session.get(HardwareDailyCache, _CACHE_KEY)
        if cached is not None and _as_utc(cached.expires_at) > now and self._cache_is_current(cached.payload):
            return self._response(cached, "fresh")

        # PostgreSQL transaction advisory locks make the cache refresh a global
        # single-flight.  The SQLite branch exists only for isolated unit tests;
        # production migration and deployment remain PostgreSQL-only.
        if not self._try_refresh_lock(session):
            if cached is not None and self._cache_is_current(cached.payload):
                return self._response(cached, "stale")
            raise HardwareDailyUnavailableError("日报正在由另一位读者刷新，请稍后重试。")

        try:
            articles = await self._fetch_articles()
            refreshed_at = _utc_now()
            expires_at = refreshed_at + timedelta(seconds=self.settings.zhihu_daily_cache_ttl_seconds)
            payload = {
                "schema_version": _CACHE_SCHEMA_VERSION,
                "articles": [article.model_dump(mode="json") for article in articles],
            }
            if cached is None:
                cached = HardwareDailyCache(
                    cache_key=_CACHE_KEY,
                    payload=payload,
                    fetched_at=refreshed_at,
                    expires_at=expires_at,
                    last_refresh_error=None,
                )
                session.add(cached)
            else:
                cached.payload = payload
                cached.fetched_at = refreshed_at
                cached.expires_at = expires_at
                cached.last_refresh_error = None
            # Commit releases pg_try_advisory_xact_lock.  It is intentionally after
            # the upstream call so no simultaneous stale request spends quota.
            session.commit()
            session.refresh(cached)
            return self._response(cached, "fresh")
        except HardwareDailyUnavailableError as exc:
            session.rollback()
            if cached is not None and self._cache_is_current(cached.payload):
                # Preserve the last known good payload while retaining an operator
                # diagnostic.  A separate brief transaction avoids persisting a
                # partial refresh.
                cached = session.get(HardwareDailyCache, _CACHE_KEY)
                if cached is not None:
                    cached.last_refresh_error = str(exc)[:2_000]
                    session.commit()
                    return self._response(cached, "stale")
            raise
        except Exception as exc:  # noqa: BLE001 - upstream errors must degrade safely
            logger.warning("hardware daily refresh failed: %s", type(exc).__name__)
            session.rollback()
            if cached is not None and self._cache_is_current(cached.payload):
                cached = session.get(HardwareDailyCache, _CACHE_KEY)
                if cached is not None:
                    cached.last_refresh_error = f"{type(exc).__name__}: {exc}"[:2_000]
                    session.commit()
                    return self._response(cached, "stale")
            raise HardwareDailyUnavailableError("暂时无法同步知乎硬件日报，请稍后重试。") from exc

    def _try_refresh_lock(self, session: Session) -> bool:
        bind = session.get_bind()
        if bind.dialect.name != "postgresql":
            return True
        return bool(session.execute(text(
            "SELECT pg_try_advisory_xact_lock(hashtext(:cache_key))"
        ), {"cache_key": _CACHE_KEY}).scalar())

    async def _fetch_articles(self) -> list[HardwareDailyArticle]:
        secret = self.settings.zhihu_access_secret
        if not secret:
            raise HardwareDailyUnavailableError("服务端尚未配置知乎日报凭据。")
        headers = {
            "Authorization": f"Bearer {secret}",
            "X-Request-Timestamp": str(int(time.time())),
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(
                timeout=self.settings.zhihu_daily_timeout_seconds,
                follow_redirects=False,
            ) as client:
                response = await client.get(
                    _SEARCH_URL,
                    params={"Query": self.settings.zhihu_daily_query, "Count": 10},
                    headers=headers,
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise HardwareDailyUnavailableError("知乎日报请求失败。") from exc

        if not isinstance(payload, dict) or payload.get("Code") != 0:
            raise HardwareDailyUnavailableError("知乎日报返回了无效结果。")
        data = payload.get("Data")
        items = data.get("Items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise HardwareDailyUnavailableError("知乎日报未返回可用文章。")

        candidates: list[HardwareDailyArticle] = []
        for item in items:
            article = self._normalize_candidate(item)
            if article is not None:
                candidates.append(article)
        if not candidates:
            raise HardwareDailyUnavailableError("未找到 Wallace 的显卡日报。")
        return sorted(candidates, key=lambda article: article.edit_time, reverse=True)

    def _normalize_candidate(self, item: Any) -> HardwareDailyArticle | None:
        if not isinstance(item, dict):
            return None
        if item.get("ContentType") != "Article" or item.get("AuthorName") != self.settings.zhihu_daily_author_name:
            return None
        title = item.get("Title")
        content_id = item.get("ContentID")
        content_text = item.get("ContentText")
        url = _canonical_article_url(item.get("Url"))
        if not all(isinstance(value, str) and value.strip() for value in (title, content_id, content_text, url)):
            return None
        if "显卡日报" not in title:
            return None
        edit_time_raw = item.get("EditTime")
        try:
            edit_time = datetime.fromtimestamp(int(edit_time_raw), tz=timezone.utc)
        except (TypeError, ValueError, OSError, OverflowError):
            return None
        authority = item.get("AuthorityLevel")
        cleaned_content = _clean_content(content_text)
        if not cleaned_content:
            return None
        thumbnail = None
        for key in ("ThumbnailUrl", "ThumbnailURL", "Thumbnail", "CoverUrl", "CoverURL", "Cover", "ImageUrl", "ImageURL", "Image"):
            thumbnail = _canonical_thumbnail_url(item.get(key))
            if thumbnail:
                break
        return HardwareDailyArticle(
            title=_normalize_title(title),
            content_id=content_id.strip(),
            content_text=cleaned_content,
            url=url,
            comment_count=_as_nonnegative_int(item.get("CommentCount")),
            vote_up_count=_as_nonnegative_int(item.get("VoteUpCount")),
            author_name=self.settings.zhihu_daily_author_name,
            author_profile_url=self.settings.zhihu_daily_author_profile_url,
            author_badge_text=None,
            edit_time=edit_time,
            authority_level=str(authority) if authority is not None else None,
            thumbnail_url=thumbnail,
            content_is_excerpt=True,
        )

    @staticmethod
    def _cache_is_current(payload: Any) -> bool:
        return (
            isinstance(payload, dict)
            and payload.get("schema_version") == _CACHE_SCHEMA_VERSION
            and isinstance(payload.get("articles"), list)
            and bool(payload["articles"])
        )

    @staticmethod
    def _response(cache: HardwareDailyCache, status: str) -> HardwareDailyResponse:
        return HardwareDailyResponse(
            articles=[HardwareDailyArticle.model_validate(item) for item in cache.payload["articles"]],
            cache=HardwareDailyCacheState(
                status=status, fetched_at=cache.fetched_at, expires_at=cache.expires_at,
            ),
        )
