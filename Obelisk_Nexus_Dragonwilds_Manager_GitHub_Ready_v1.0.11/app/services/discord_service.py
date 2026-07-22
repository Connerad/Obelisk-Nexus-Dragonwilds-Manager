from __future__ import annotations

import json
import urllib.error
import urllib.request


class DiscordError(RuntimeError):
    pass


def send_webhook(url: str, title: str, description: str, fields: list[dict] | None = None) -> None:
    if not url.strip():
        return
    payload = {
        "username": "Dragonwilds Server Manager",
        "embeds": [{
            "title": title[:256],
            "description": description[:4096],
            "fields": (fields or [])[:25],
            "footer": {"text": "Unofficial community server manager"},
        }],
    }
    request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json", "User-Agent": "DragonwildsServerManagerRebuild/1.0"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            response.read(1024)
    except urllib.error.HTTPError as exc:
        raise DiscordError(f"Discord returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise DiscordError(f"Could not reach Discord: {exc.reason}") from exc
