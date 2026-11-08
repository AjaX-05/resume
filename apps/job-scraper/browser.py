"""Launch a real installed Chrome/Edge for Jobright (Cloudflare-backed SPA)."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def launch_chromium(playwright: Any, *, headless: bool = True, **extra: Any) -> Any:
    """Prefer installed Chrome, then Edge, then Playwright's Chromium."""
    args = ["--disable-blink-features=AutomationControlled"]
    last: Exception | None = None
    for channel in ("chrome", "msedge", None):
        try:
            kwargs: dict[str, Any] = {"headless": headless, "args": args, **extra}
            if channel:
                kwargs["channel"] = channel
            browser = playwright.chromium.launch(**kwargs)
            logger.info("Launched browser channel=%s headless=%s", channel or "chromium", headless)
            return browser
        except Exception as e:
            last = e
            logger.debug("Browser launch failed channel=%s: %s", channel, e)
    raise RuntimeError(f"Could not launch Chrome, Edge, or Chromium: {last}")
