"""apewisdom.io WSB ticker rankings — pre-scraped, cloud-friendly, no auth.

apewisdom curates r/wallstreetbets mentions hourly and exposes a free public
JSON API. We pull rank, name, mention count, upvote count, and 24h-ago rank
(for rank-change deltas). This replaces our hand-rolled scraping + extraction
because Reddit IP-blocks cloud datacenters.
"""

from __future__ import annotations

import html as html_lib
import json as json_lib
import subprocess
from dataclasses import dataclass


API_URL = "https://apewisdom.io/api/v1.0/filter/wallstreetbets/page/1"


@dataclass
class TickerData:
    rank: int
    symbol: str
    name: str
    mentions: int
    upvotes: int
    rank_24h_ago: int | None
    mentions_24h_ago: int | None

    @property
    def rank_change(self) -> int | None:
        """Positive = climbed up the rankings. None if no prior rank."""
        if self.rank_24h_ago is None:
            return None
        return self.rank_24h_ago - self.rank

    @property
    def mentions_change_pct(self) -> float | None:
        if not self.mentions_24h_ago:
            return None
        return (self.mentions - self.mentions_24h_ago) / self.mentions_24h_ago * 100


def fetch_top_tickers(limit: int = 25) -> list[TickerData]:
    result = subprocess.run(
        ["curl", "--silent", "--show-error", "--fail", "--max-time", "20", API_URL],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json_lib.loads(result.stdout)
    out: list[TickerData] = []
    for entry in data.get("results", [])[:limit]:
        out.append(
            TickerData(
                rank=entry.get("rank", 0),
                symbol=entry.get("ticker", ""),
                name=html_lib.unescape(entry.get("name", "")),
                mentions=entry.get("mentions", 0),
                upvotes=entry.get("upvotes", 0),
                rank_24h_ago=entry.get("rank_24h_ago"),
                mentions_24h_ago=entry.get("mentions_24h_ago"),
            )
        )
    return out
