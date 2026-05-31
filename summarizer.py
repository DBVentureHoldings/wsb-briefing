"""Claude summarization layer.

Takes pre-ranked tickers from apewisdom and post text from Reddit's RSS,
asks Claude Haiku for: overall market mood, dominant themes, per-ticker
take + industry, and a 2-3 sentence summary of each top post. Strict-JSON
output validated on the way in.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

import anthropic

from apewisdom_client import TickerData
from reddit_client import Post


MODEL = "claude-haiku-4-5-20251001"

POST_BODY_TRIM = 1200
TOP_N_TICKERS = 20
TOP_N_TICKER_NOTES = 10
TOP_N_POSTS_TO_SUMMARIZE = 10
MAX_POSTS_IN_CONTEXT = 25

SYSTEM_PROMPT = """You are a markets analyst summarizing overnight chatter from r/wallstreetbets for a serious retail trader and his finance-industry friend. They want a quick, no-fluff read on what retail is talking about — not investment advice.

Output strict JSON with this shape (no prose outside the JSON):

{
  "market_mood": "<one sentence: overall risk-on / risk-off / mixed tone of the sub today, with the why>",
  "themes": [
    "<3 to 5 bullets, each one sentence, describing dominant narratives — e.g. earnings reactions, sector rotation, specific catalysts>"
  ],
  "ticker_notes": [
    {"symbol": "NVDA", "industry": "Semiconductors", "sentiment": "bullish|bearish|mixed|neutral", "note": "<one short sentence on what WSB is saying about it and why; empty string if outside the top 10 by mention count>"}
  ],
  "post_summaries": [
    {"id": "<the POST_ID I tagged each post with>", "summary": "<2-3 sentences capturing what the post actually argues/shows, including any numbers, tickers, or stakes mentioned. If it's pure shitpost or meme, say so concisely.>"}
  ]
}

Rules:
- ticker_notes covers EVERY ticker I provide, in the same order. Always include `industry` (specific GICS sub-industry: e.g. "Semiconductors", "Oil & Gas Exploration & Production", "Biotechnology", "Regional Banks") and `sentiment`. For tickers ranked outside the top 10, set `note` to an empty string.
- For the top 10 tickers, `note` is one sentence on what WSB is saying and why. Use the rank-change context I give you (e.g. a ticker jumping from rank 28 to rank 1 is the day's story).
- post_summaries covers EVERY post I provide that has a POST_ID tag, in the same order.
- Be concrete. Reference the catalyst (earnings, news, options flow, FOMC, etc.) when it's in the source text.
- If chatter is shitposting with no thesis, say so plainly. Don't manufacture signal.
- No disclaimers, no "not financial advice" boilerplate. The reader knows.
- If you genuinely don't recognize a ticker, set industry to "Unknown" — never guess.
"""


@dataclass
class Briefing:
    market_mood: str
    themes: list[str]
    ticker_notes: list[dict]  # [{symbol, industry, sentiment, note}, ...]
    post_summaries: dict[str, str]  # post_id -> summary


def _format_ticker_for_prompt(t: TickerData) -> str:
    rc = t.rank_change
    rc_str = (
        f"unchanged" if rc == 0 or rc is None
        else (f"climbed {rc} (was #{t.rank_24h_ago})" if rc > 0
              else f"fell {-rc} (was #{t.rank_24h_ago})")
    )
    return f"#{t.rank} {t.symbol} ({t.name}) — {t.mentions} mentions, {t.upvotes} upvotes, rank {rc_str}"


def _build_corpus(posts: list[Post], must_include_ids: set[str]) -> str:
    chunks: list[str] = []
    for p in posts[:MAX_POSTS_IN_CONTEXT]:
        body = (p.selftext or "").strip().replace("\n", " ")[:POST_BODY_TRIM]
        tag = f"POST_ID={p.id}" if p.id in must_include_ids else "POST"
        header = f"{tag} [{p.title}]"
        chunk = header if not body else f"{header}\n  {body}"
        chunks.append(chunk)
    return "\n\n".join(chunks)


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def summarize(posts: list[Post], tickers: list[TickerData]) -> Briefing:
    if not tickers:
        return Briefing(
            market_mood="No ticker chatter detected.",
            themes=["No actionable signal in last 24h."],
            ticker_notes=[],
            post_summaries={},
        )

    top = tickers[:TOP_N_TICKERS]
    top_posts = posts[:TOP_N_POSTS_TO_SUMMARIZE]
    must_include_ids = {p.id for p in top_posts}

    corpus = _build_corpus(posts, must_include_ids)
    ticker_block = "\n".join(_format_ticker_for_prompt(t) for t in top)

    user_msg_parts = [
        f"Top {len(top)} tickers on r/wallstreetbets (pre-ranked by mentions, "
        f"with rank-change vs 24h ago). Cover ALL of them in ticker_notes in "
        f"this order — first {TOP_N_TICKER_NOTES} get a `note`, the rest need "
        f"only industry + sentiment:\n\n"
        f"{ticker_block}",
    ]
    if corpus:
        user_msg_parts.append(
            "Summarize each post tagged POST_ID=... in post_summaries (same id). "
            f"Raw posts (titles + body text — no comments available):\n\n{corpus}"
        )
    else:
        user_msg_parts.append(
            "No post bodies are available today (Reddit RSS unreachable). "
            "Return post_summaries as an empty array, but still cover all "
            "tickers in ticker_notes."
        )

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    resp = client.messages.create(
        model=MODEL,
        max_tokens=5000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": "\n\n".join(user_msg_parts)}],
    )

    raw = resp.content[0].text
    data = _parse_json(raw)
    post_summaries = {
        entry.get("id", ""): entry.get("summary", "")
        for entry in data.get("post_summaries", [])
        if entry.get("id")
    }
    return Briefing(
        market_mood=data.get("market_mood", ""),
        themes=data.get("themes", []),
        ticker_notes=data.get("ticker_notes", []),
        post_summaries=post_summaries,
    )
