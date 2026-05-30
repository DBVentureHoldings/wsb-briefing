"""Extract ticker mentions from WSB post + comment text and rank them.

The signal we care about:
- mention_count: how many distinct posts/comments name this ticker
- weighted_score: sum of post/comment upvotes (engagement-weighted)
- sentiment: net bullish minus bearish lexicon hits in surrounding text
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from reddit_client import Post

HERE = Path(__file__).parent

# `$TSLA` or `$tsla` — explicit cashtag, 1–5 letters
CASHTAG_RE = re.compile(r"\$([A-Za-z]{1,5})\b")
# Bare uppercase tokens 2–5 chars. Must be word-bounded and NOT preceded by $.
BARE_RE = re.compile(r"(?<![\$A-Za-z0-9])([A-Z]{2,5})(?![A-Za-z0-9])")

BULLISH = {
    "moon", "moonshot", "rocket", "rockets", "calls", "long", "buy", "buying",
    "bought", "bullish", "rip", "ripping", "ripped", "squeeze", "squeezing",
    "breakout", "tendies", "diamond", "hold", "holding", "pump", "pumping",
    "ath", "rally", "rallying", "send", "sending", "yolo", "lfg",
}
BEARISH = {
    "puts", "short", "shorting", "shorted", "sell", "selling", "sold", "dump",
    "dumping", "dumped", "bearish", "crash", "crashing", "crashed", "tank",
    "tanking", "tanked", "bag", "bags", "bagholder", "loss", "losses", "rug",
    "rugged", "rugpull", "drilling", "drill", "down", "red", "drop", "dropping",
    "rekt", "fucked",
}


@dataclass
class TickerTally:
    symbol: str
    mention_count: int = 0
    weighted_score: int = 0  # sum of post/comment upvotes
    bullish_hits: int = 0
    bearish_hits: int = 0
    sample_quotes: list[str] = None

    def __post_init__(self) -> None:
        if self.sample_quotes is None:
            self.sample_quotes = []

    @property
    def sentiment(self) -> str:
        net = self.bullish_hits - self.bearish_hits
        total = self.bullish_hits + self.bearish_hits
        if total == 0:
            return "neutral"
        ratio = net / total
        if ratio > 0.3:
            return "bullish"
        if ratio < -0.3:
            return "bearish"
        return "mixed"

    @property
    def sentiment_score(self) -> int:
        return self.bullish_hits - self.bearish_hits


def load_universe() -> set[str]:
    path = HERE / "tickers.txt"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} missing — run `python update_tickers.py` to fetch it"
        )
    return {line.strip().upper() for line in path.read_text().splitlines() if line.strip()}


def load_blacklist() -> set[str]:
    path = HERE / "jargon_blacklist.txt"
    out: set[str] = set()
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.add(line.upper())
    return out


def extract_tickers(
    text: str, universe: set[str], blacklist: set[str]
) -> set[str]:
    """Return the set of distinct tickers mentioned in `text`.

    Cashtag form ($XYZ) bypasses the blacklist — if someone writes $DD, they
    mean the stock, not "due diligence". Bare form must be uppercase AND in
    the universe AND not in the blacklist AND at least 2 chars.
    """
    found: set[str] = set()

    for m in CASHTAG_RE.findall(text):
        sym = m.upper()
        if sym in universe and len(sym) >= 1:
            found.add(sym)

    for sym in BARE_RE.findall(text):
        if sym in blacklist:
            continue
        if sym not in universe:
            continue
        found.add(sym)

    return found


def _count_lexicon(text: str, lex: set[str]) -> int:
    lower = text.lower()
    hits = 0
    for word in lex:
        # word-boundary match; cheap because lexicons are small
        if re.search(rf"\b{re.escape(word)}\b", lower):
            hits += 1
    return hits


def tally(posts: list[Post]) -> list[TickerTally]:
    universe = load_universe()
    blacklist = load_blacklist()
    by_symbol: dict[str, TickerTally] = defaultdict(lambda: TickerTally(symbol=""))

    for post in posts:
        post_text = f"{post.title}\n{post.selftext}"
        post_tickers = extract_tickers(post_text, universe, blacklist)
        post_bull = _count_lexicon(post_text, BULLISH)
        post_bear = _count_lexicon(post_text, BEARISH)

        for sym in post_tickers:
            t = by_symbol[sym]
            t.symbol = sym
            t.mention_count += 1
            t.weighted_score += post.score
            t.bullish_hits += post_bull
            t.bearish_hits += post_bear
            if len(t.sample_quotes) < 3 and post.title:
                t.sample_quotes.append(post.title[:140])

        for comment in post.comments:
            ct = extract_tickers(comment.body, universe, blacklist)
            if not ct:
                continue
            c_bull = _count_lexicon(comment.body, BULLISH)
            c_bear = _count_lexicon(comment.body, BEARISH)
            for sym in ct:
                t = by_symbol[sym]
                t.symbol = sym
                t.mention_count += 1
                t.weighted_score += max(comment.score, 0)
                t.bullish_hits += c_bull
                t.bearish_hits += c_bear

    return sorted(
        by_symbol.values(),
        key=lambda t: (t.mention_count, t.weighted_score),
        reverse=True,
    )
