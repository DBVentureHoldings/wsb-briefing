"""Reddit post fetcher via the public RSS feed.

We use RSS because Reddit's JSON endpoints block cloud datacenter IPs (Azure
in particular). RSS is served by a different layer of Reddit's infrastructure
and historically permits broader access. No comments are available via RSS,
only post titles and selftext bodies.

If RSS *also* gets blocked on a particular runner, fetch_recent_posts() returns
an empty list and the briefing still runs with apewisdom ticker data alone.
"""

from __future__ import annotations

import html as html_lib
import os
import re
import subprocess
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET


USER_AGENT = os.environ.get(
    "REDDIT_USER_AGENT",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) wsb-briefing/0.2",
)


@dataclass
class Comment:
    """Kept for backward-compat with render code — always empty under RSS."""
    body: str
    score: int


@dataclass
class Post:
    id: str
    title: str
    selftext: str
    score: int  # always 0 under RSS (no upvote count in feed)
    num_comments: int  # always 0 under RSS
    url: str
    created_utc: float
    flair: str | None
    comments: list[Comment] = field(default_factory=list)

    @property
    def combined_text(self) -> str:
        parts = [self.title, self.selftext]
        return "\n".join(p for p in parts if p)


_ATOM_NS = {"a": "http://www.w3.org/2005/Atom"}


def _strip_html(s: str) -> str:
    """Best-effort: decode entities, drop tags, collapse whitespace."""
    if not s:
        return ""
    decoded = html_lib.unescape(s)
    decoded = html_lib.unescape(decoded)  # double-encoded in some entries
    # Extract just the selftext paragraph(s). Reddit's RSS wraps everything in
    # a table with header/footer chrome. The actual body lives inside <div class="md">.
    m = re.search(r'<div class="md">(.*?)</div>', decoded, re.DOTALL)
    body = m.group(1) if m else decoded
    body = re.sub(r"<[^>]+>", " ", body)
    body = re.sub(r"\s+", " ", body).strip()
    return body


def fetch_recent_posts(limit: int = 25, **_) -> list[Post]:
    """Pull the day's top posts from Reddit's RSS feed.

    Extra **_ kwargs swallow `hours`, `comments_per_post`, etc. that the old
    JSON-based signature accepted but we no longer use.
    """
    url = "https://www.reddit.com/r/wallstreetbets/top/.rss?t=day"
    try:
        result = subprocess.run(
            [
                "curl", "--silent", "--show-error", "--fail", "--max-time", "30",
                "-A", USER_AGENT,
                "-H", "Accept: application/atom+xml,application/xml,text/xml",
                url,
            ],
            capture_output=True, text=True, check=True,
        )
    except subprocess.CalledProcessError as e:
        print(f"  warn: Reddit RSS fetch failed ({e.returncode}); continuing without post data")
        return []

    try:
        root = ET.fromstring(result.stdout)
    except ET.ParseError as e:
        print(f"  warn: Reddit RSS XML parse failed: {e}")
        return []

    posts: list[Post] = []
    for entry in root.findall("a:entry", _ATOM_NS)[:limit]:
        post_id = (entry.findtext("a:id", "", _ATOM_NS) or "").replace("t3_", "")
        title = (entry.findtext("a:title", "", _ATOM_NS) or "").strip()
        content = entry.findtext("a:content", "", _ATOM_NS) or ""
        link_el = entry.find("a:link[@rel='alternate']", _ATOM_NS)
        link = link_el.get("href") if link_el is not None else ""

        selftext = _strip_html(content)
        # Strip "[link] [comments]" suffix that RSS appends to every entry.
        selftext = re.sub(r"\[link\]\s*\[comments\]\s*$", "", selftext).strip()

        posts.append(
            Post(
                id=post_id,
                title=title,
                selftext=selftext,
                score=0,
                num_comments=0,
                url=link or f"https://reddit.com/r/wallstreetbets/comments/{post_id}",
                created_utc=0.0,
                flair=None,
                comments=[],
            )
        )
    return posts
