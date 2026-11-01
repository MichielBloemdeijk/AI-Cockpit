import html
import re
from urllib.parse import urlsplit


def handle(input, cockpit):
    """Fetch a page and store its text as a clip file; index_clip then makes its record."""
    url = str(input.get("url") or "")
    response = cockpit.fetch(url)
    if not response.ok:
        raise ValueError(f"The page answered {response.status}")
    body = re.sub(r"(?s)<(script|style)\b.*?</\1>", " ", response.text)
    text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", body)).split())
    parts = urlsplit(url)
    name = "clips/" + (re.sub(r"[^A-Za-z0-9_.-]+", "-", parts.netloc + parts.path).strip("-") or "page")[:120] + ".txt"
    cockpit.files.write(name, text, "text/plain")
    print(f"saved {len(text)} characters from {url}")
    return {"file": name, "characters": len(text)}
