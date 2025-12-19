import requests
import logging

logger = logging.getLogger(__name__)

SHORTENER_API = "https://cdn.nzdworld.com/api/shorten/"


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

        data = res.json()
        if res.status_code == 200 and "short_url" in data:
            return data["short_url"]

    except Exception as e:
        logger.error(f"Shortener error: {e}")

    return long_url

