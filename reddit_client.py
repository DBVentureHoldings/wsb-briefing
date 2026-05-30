"""Reddit fetcher using public JSON endpoints (no credentials required).

Reddit's legacy Data API now requires a moderation-use-case registration, which
is closed off for personal scrapers. Their public JSON endpoints (add `.json`
to any URL) still work fine for low-volume use as long as you send a unique
User-Agent and stay under ~60 requests per minute. We make ~101 requests once
a day, well under the limit.
"""

from __future__ import annotations

import json as json_lib
import os
import subprocess
import time
from dataclasses import dataclass, field

# Reddit blocks generic/non-browser User-Agents on the public JSON endpoints.
# A Mozilla-prefixed UA with a project tag at the end is what works in practice.
USER_AGENT = os.environ.get(
    "REDDIT_USER_AGENT",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) wsb-briefing/0.1",
)

BASE = "https://old.reddit.com"


@dataclass
class Comment:
    body: str
    score: int


@dataclass
class Post:
    id: str
    title: str
    selftext: str
    score: int
    num_comments: int
    url: str
    created_utc: float
    flair: str | None
    comments: list[Comment] = field(default_factory=list)

    @property
    def combined_text(self) -> str:
        parts = [self.title, self.selftext, *(c.body for c in self.comments)]
        return "\n".join(p for p in parts if p)


def _get_json(path: str, params: dict | None = None, retries: int = 3):
    """GET a Reddit JSON endpoint via curl.

    Python's stdlib HTTP client gets blocked by Reddit's TLS fingerprinting
    even with browser headers. curl uses a different TLS stack that passes.
    """
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{BASE}{path}?{qs}"
    else:
        url = f"{BASE}{path}"

    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            result = subprocess.run(
                [
                    "curl",
                    "--silent",
                    "--show-error",
                    "--fail",
                    "--max-time", "20",
                    "-A", USER_AGENT,
                    "-H", "Accept: application/json",
                    url,
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            return json_lib.loads(result.stdout)
        except subprocess.CalledProcessError as e:
            last_err = e
            # curl exit 22 = HTTP >= 400. Back off on transient codes.
            time.sleep(2 ** attempt)
        except json_lib.JSONDecodeError as e:
            last_err = e
            time.sleep(2 ** attempt)
    raise RuntimeError(f"reddit fetch failed after {retries} attempts: {last_err}")


def _parse_comments(listing: dict, limit: int) -> list[Comment]:
    out: list[Comment] = []
    for child in listing.get("data", {}).get("children", []):
        if child.get("kind") != "t1":
            continue
        d = child.get("data", {})
        body = d.get("body") or ""
        if not body or body == "[deleted]" or body == "[removed]":
            continue
        out.append(Comment(body=body, score=d.get("score", 0) or 0))
    out.sort(key=lambda c: c.score, reverse=True)
    return out[:limit]


def fetch_recent_posts(
    subreddit: str = "wallstreetbets",
    limit: int = 100,
    hours: int = 24,
    comments_per_post: int = 15,
    request_delay: float = 1.1,
) -> list[Post]:
    """Pull top-of-day posts plus their top comments.

    Reddit caps `limit` at 100 per request — that's fine for our needs.
    `request_delay` is a courtesy sleep between comment fetches to stay
    well under the unauthenticated rate ceiling.
    """
    listing = _get_json(
        f"/r/{subreddit}/top.json",
        params={"t": "day", "limit": str(min(limit, 100))},
    )

    cutoff = time.time() - hours * 3600
    posts: list[Post] = []
    children = listing.get("data", {}).get("children", [])

    for i, child in enumerate(children):
        d = child.get("data", {})
        created = d.get("created_utc", 0)
        if created < cutoff:
            continue

        post_id = d.get("id", "")
        title = d.get("title", "") or ""
        selftext = d.get("selftext", "") or ""

        # Fetch top comments for this post.
        comments: list[Comment] = []
        try:
            comment_data = _get_json(
                f"/r/{subreddit}/comments/{post_id}.json",
                params={"sort": "top", "limit": str(comments_per_post * 2)},
            )
            # Response is [post_listing, comment_listing]
            if isinstance(comment_data, list) and len(comment_data) >= 2:
                comments = _parse_comments(comment_data[1], comments_per_post)
        except Exception as e:
            print(f"  warn: comment fetch failed for {post_id}: {e}")

        posts.append(
            Post(
                id=post_id,
                title=title,
                selftext=selftext,
                score=d.get("score", 0) or 0,
                num_comments=d.get("num_comments", 0) or 0,
                url=f"https://reddit.com{d.get('permalink', '')}",
                created_utc=created,
                flair=d.get("link_flair_text"),
                comments=comments,
            )
        )

        # Be a polite citizen — don't burst.
        if i < len(children) - 1:
            time.sleep(request_delay)

    return posts
