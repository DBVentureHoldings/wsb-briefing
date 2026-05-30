"""WSB morning briefing — orchestrator.

Usage:
  python scraper.py              # full run: fetch, summarize, email, save report
  python scraper.py --dry-run    # fetch + extract only, print to stdout, no API calls, no email
  python scraper.py --no-email   # skip email but do everything else (saves report)
"""

from __future__ import annotations

import argparse
import sys
import traceback
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).parent
# override=True so an empty shell var (e.g. from ~/.zshrc) doesn't shadow .env.
load_dotenv(HERE / ".env", override=True)

from reddit_client import fetch_recent_posts  # noqa: E402
from ticker_extractor import tally  # noqa: E402
from render import ReportData, render_markdown, render_html  # noqa: E402


def _ensure_tickers_file() -> None:
    if not (HERE / "tickers.txt").exists():
        print("tickers.txt missing — fetching ticker universe...", file=sys.stderr)
        import update_tickers
        if update_tickers.main() != 0:
            raise SystemExit("failed to fetch ticker universe")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="no Claude, no email")
    ap.add_argument("--no-email", action="store_true", help="skip email send")
    ap.add_argument("--limit", type=int, default=100, help="max posts to pull")
    ap.add_argument("--top-n", type=int, default=20, help="tickers to show in report")
    args = ap.parse_args()

    _ensure_tickers_file()

    print("Fetching r/wallstreetbets...", file=sys.stderr)
    posts = fetch_recent_posts(limit=args.limit)
    comments_total = sum(len(p.comments) for p in posts)
    print(f"  {len(posts)} posts, {comments_total} comments", file=sys.stderr)

    print("Extracting tickers...", file=sys.stderr)
    ranked = tally(posts)
    print(
        f"  {len(ranked)} distinct tickers; top: "
        + ", ".join(f"{t.symbol}({t.mention_count})" for t in ranked[:10]),
        file=sys.stderr,
    )

    if args.dry_run:
        print("\n--- DRY RUN ticker table ---")
        for i, t in enumerate(ranked[: args.top_n], start=1):
            print(
                f"{i:>2}. {t.symbol:<6} mentions={t.mention_count:<3} "
                f"weighted={t.weighted_score:<6} sentiment={t.sentiment}"
            )
        return 0

    print("Summarizing with Claude...", file=sys.stderr)
    from summarizer import summarize  # lazy import so dry-run doesn't need anthropic
    briefing = summarize(posts, ranked)

    top_posts = sorted(posts, key=lambda p: p.score, reverse=True)
    report = ReportData(
        on_date=date.today(),
        posts_scanned=len(posts),
        comments_scanned=comments_total,
        tickers=ranked,
        briefing=briefing,
        top_posts=top_posts,
    )

    md = render_markdown(report, top_n=args.top_n)
    html = render_html(report, top_n=args.top_n)

    reports_dir = HERE / "reports"
    reports_dir.mkdir(exist_ok=True)
    md_path = reports_dir / f"wsb-briefing-{report.on_date.isoformat()}.md"
    md_path.write_text(md)
    print(f"Wrote {md_path}", file=sys.stderr)

    if args.no_email:
        return 0

    from emailer import send  # lazy import
    top_ticker = ranked[0].symbol if ranked else "—"
    subject = f"WSB Briefing {report.on_date.isoformat()} — top: {top_ticker}"
    try:
        send(subject, html, md)
        print("Email sent.", file=sys.stderr)
    except Exception:
        print("Email send FAILED:", file=sys.stderr)
        traceback.print_exc()
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
