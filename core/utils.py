import os
import requests
import logging
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

SHORTENER_API = os.getenv("SHORTENER_API", "https://cdn.nzdworld.com/api/shorten/")
SHORTENER_ALLOWED_HOSTS = tuple(
    host.strip().lower()
    for host in os.getenv(
        "SHORTENER_ALLOWED_HOSTS",
        "cdn.nzdworld.com,cdn.nzdowlrd.com"
    ).split(",")
    if host.strip()
)
SHORTENER_LEGACY_HOSTS = tuple(
    host.strip().lower()
    for host in os.getenv("SHORTENER_LEGACY_HOSTS", "").split(",")
    if host.strip()
)


def is_valid_shortener_url(url):
    if not url:
        return False
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return False

    allowed_hosts = SHORTENER_ALLOWED_HOSTS + SHORTENER_LEGACY_HOSTS
    return any(host == allowed_host or host.endswith(f".{allowed_host}") for allowed_host in allowed_hosts)


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

