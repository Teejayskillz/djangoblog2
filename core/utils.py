import requests
import logging
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

SHORTENER_API = "https://cdn.nzdworld.com/api/shorten/"
SHORTENER_CANONICAL_HOSTS = ("cdn.nzdworld.com", "cdn.nzdowlrd.com")
SHORTENER_LEGACY_HOSTS = ("dl.jaraflix.com",)


def is_valid_shortener_url(url):
    if not url:
        return False
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return False

    return any(
        host == allowed_host or host.endswith(f".{allowed_host}")
        for allowed_host in SHORTENER_CANONICAL_HOSTS
    )


def shorten_url(long_url, title=None):
    try:
        res = requests.post(
            SHORTENER_API,
            json={
                "url": long_url,
                "title": title
            },
            timeout=10
        )

        logger.warning(f"Shortener status: {res.status_code}")
        logger.warning(f"Shortener response: {res.text}")

        try:
            data = res.json()
        except ValueError:
            logger.error("Shortener response was not valid JSON.")
            return long_url

        short_url = data.get("short_url") if isinstance(data, dict) else None
        if res.status_code == 200 and short_url and is_valid_shortener_url(short_url):
            return short_url

        logger.warning(
            "Shortener returned an unexpected host; rejecting it to avoid redirecting to the wrong CDN. "
            f"Got: {short_url}"
        )

    except Exception as e:
        logger.error(f"Shortener error: {e}")

    return long_url

