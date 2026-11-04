"""Change-only local notifications."""

import os
import platform
import subprocess
from typing import Any, Dict, List


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
