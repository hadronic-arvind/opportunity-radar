"""Change-only local and optional webhook notifications."""

import json
import os
import platform
import subprocess
import urllib.parse
import urllib.request
from typing import Any, Dict, List

from . import __version__


def summarize(items: List[Dict[str, Any]]) -> str:
    if not items:
        return "No new high-fit opportunities."
    first = items[0]
    suffix = "" if len(items) == 1 else " and {} more".format(len(items) - 1)
    return "{} at {}{}".format(first["title"], first["organization"], suffix)


NOTIFICATION_SCRIPT = """on run argv
display notification (item 2 of argv) with title (item 1 of argv)
end run"""

MAX_NOTIFICATION_TITLE_CHARS = 120
MAX_NOTIFICATION_BODY_CHARS = 600


def notify_macos(title: str, body: str) -> bool:
    if platform.system() != "Darwin" or not os.path.isfile("/usr/bin/osascript"):
        return False
    safe_title = str(title)[:MAX_NOTIFICATION_TITLE_CHARS]
    safe_body = str(body)[:MAX_NOTIFICATION_BODY_CHARS]
    subprocess.run(
        ["/usr/bin/osascript", "-e", NOTIFICATION_SCRIPT, safe_title, safe_body],
        check=False,
        timeout=10,
    )
    return True


def notify_webhook(payload: Dict[str, Any]) -> bool:
    url = os.environ.get("OPPORTUNITY_MONITOR_WEBHOOK_URL", "").strip()
    if not url:
        return False
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("Webhook URL must be absolute HTTPS")
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "OpportunityRadar/{}".format(__version__),
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20):
        return True
