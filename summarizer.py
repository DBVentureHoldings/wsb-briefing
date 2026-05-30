"""Send the day's WSB corpus to Claude Haiku and get a structured briefing back."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

import anthropic

from reddit_client import Post
from ticker_extractor import TickerTally

MODEL = "claude-haiku-4-5-20251001"

POST_BODY_TRIM = 800
COMMENT_TRIM = 300
TOP_N_TICKERS = 20  # tickers for which Claude returns name + industry
TOP_N_TICKER_NOTES = 10  # tickers that also get a rich WSB-take note
TOP_N_POSTS_TO_SUMMARIZE = 10
MAX_POSTS_IN_CONTEXT = 40

SYSTEM_PROMPT = """You are a markets analyst summarizing overnight chatter from r/wallstreetbets for a serious retail trader and his finance-industry friend. They want a quick, no-fluff read on what retail is talking about — not investment advice.

Output strict JSON with this shape (no prose outside the JSON):

{
  "market_mood": "<one sentence: overall risk-on / risk-off / mixed tone of the sub today, with the why>",
  "themes": [
    "<3 to 5 bullets, each one sentence, describing dominant narratives — e.g. earnings reactions, sector rotation, specific catalysts>"
  ],
  "ticker_notes": [
    {"symbol": "NVDA", "name": "NVIDIA Corporation", "industry": "Semiconductors", "note": "<one short sentence on what WSB is saying about it and why; empty string if outside the top 10 by mention count>"}
  ],
  "post_summaries": [
    {"id": "<the POST_ID I tagged each post with>", "summary": "<2-3 sentences capturing what the post actually argues/shows, including any numbers, tickers, or stakes mentioned in the body or top comments. If it's pure shitpost or meme, say so concisely.>"}
  ]
}

Rules:
- ticker_notes covers EVERY ticker I provide, in the same order. Always include `name` (the company's full legal name) and `industry` (specific GICS sub-industry: e.g. "Semiconductors", "Oil & Gas Exploration & Production", "Biotechnology", "Regional Banks"). For tickers ranked outside the top 10 by mention count, set `note` to an empty string.
- For the top 10 tickers, `note` is one sentence on what WSB is saying and why.
- post_summaries covers EVERY post I provide that has a POST_ID tag, in the same order.
- Be concrete. Reference the catalyst (earnings, news, options flow, FOMC, etc.) when it's in the source text.
- If the chatter is shitposting with no thesis, say so plainly. Don't manufacture signal.
- No disclaimers, no "not financial advice" boilerplate. The reader knows.
- If you genuinely don't recognize a ticker, set name to "Unknown" and industry to "Unknown" — never guess.
"""


@dataclass
class Briefing:
    market_mood: str
    themes: list[str]
    ticker_notes: list[dict]  # [{symbol, note}, ...]
    post_summaries: dict[str, str]  # post_id -> 2-3 sentence summary


def _build_corpus(
    posts: list[Post],
    top_tickers: list[TickerTally],
    must_include_ids: set[str],
) -> str:
    """Build the prompt corpus.

    Posts that must be summarized (top-by-score) are always included AND
    tagged with POST_ID so Claude can reference them in the response.
    Other posts are picked by ticker-relevance and shown without IDs.
    """
    top_symbols = {t.symbol for t in top_tickers[:TOP_N_TICKERS]}

    def relevance(p: Post) -> int:
        text_upper = p.combined_text.upper()
        return sum(1 for s in top_symbols if s in text_upper) * 1000 + p.score

    ranked = sorted(posts, key=relevance, reverse=True)
    selected: list[Post] = []
    seen_ids: set[str] = set()
    for p in ranked:
        if p.id in seen_ids:
            continue
        if len(selected) < MAX_POSTS_IN_CONTEXT or p.id in must_include_ids:
            selected.append(p)
            seen_ids.add(p.id)
    # Make sure every must-include post is present even if it lost on relevance.
    for p in posts:
        if p.id in must_include_ids and p.id not in seen_ids:
            selected.append(p)
            seen_ids.add(p.id)

    chunks: list[str] = []
    for p in selected:
        body = (p.selftext or "").strip().replace("\n", " ")[:POST_BODY_TRIM]
        tag = f"POST_ID={p.id}" if p.id in must_include_ids else "POST"
        header = f"{tag} [{p.score}↑ · {p.num_comments}💬] [{p.flair or '-'}] {p.title}"
        chunk = header
        if body:
            chunk += f"\n  {body}"
        for c in p.comments[:8]:
            cbody = (c.body or "").strip().replace("\n", " ")[:COMMENT_TRIM]
            if cbody:
                chunk += f"\n  > [{c.score}↑] {cbody}"
        chunks.append(chunk)

    return "\n\n".join(chunks)


def _parse_json(text: str) -> dict:
    # Be forgiving: strip code fences if Claude wrapped the JSON.
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def summarize(posts: list[Post], tickers: list[TickerTally]) -> Briefing:
    if not tickers:
        return Briefing(
            market_mood="No ticker chatter detected.",
            themes=["No actionable signal in last 24h."],
            ticker_notes=[],
            post_summaries={},
        )

    top = tickers[:TOP_N_TICKERS]
    top_by_score = sorted(posts, key=lambda p: p.score, reverse=True)[:TOP_N_POSTS_TO_SUMMARIZE]
    must_include_ids = {p.id for p in top_by_score}

    corpus = _build_corpus(posts, top, must_include_ids)
    symbol_list = ", ".join(
        f"{t.symbol} ({t.mention_count} mentions, {t.sentiment})" for t in top
    )

    user_msg = (
        f"All {len(top)} tickers to cover in ticker_notes (in this order — first "
        f"{TOP_N_TICKER_NOTES} get a `note`, the rest just need name + industry):\n"
        f"{symbol_list}\n\n"
        f"Summarize each post tagged POST_ID=... in post_summaries (use the same id).\n\n"
        f"Raw posts and top comments (truncated):\n\n{corpus}"
    )

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    resp = client.messages.create(
        model=MODEL,
        max_tokens=3000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
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
