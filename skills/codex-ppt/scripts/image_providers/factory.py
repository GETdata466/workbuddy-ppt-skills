from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse

from .atlascloud import AtlasCloudImageProvider
from .base import ImageProvider
from .codex_oauth import (
    create_codex_oauth_provider_from_env,
    is_codex_oauth_base_url,
)
from .openai_compatible import OpenAICompatibleImageProvider


def create_image_provider(
    *,
    api_key: Optional[str],
    base_url: Optional[str],
    allow_fallback: bool = True,
) -> ImageProvider:
    if is_codex_oauth_base_url(base_url):
        return create_codex_oauth_provider_from_env(allow_fallback=allow_fallback)
    if _is_atlascloud_base_url(base_url):
        return AtlasCloudImageProvider(api_key=api_key, base_url=base_url)
    return OpenAICompatibleImageProvider(api_key=api_key, base_url=base_url)


def _is_atlascloud_base_url(base_url: Optional[str]) -> bool:
    if not base_url:
        return False
    hostname = urlparse(base_url).hostname or ""
    return "atlascloud.ai" in hostname.lower()
