"""WSB morning briefing — orchestrator.

Usage:
  python scraper.py              # full run: fetch, summarize, email, save
  python scraper.py --dry-run    # fetch only, print to stdout, no Claude/email
  python scraper.py --no-email   # everything except the email send
"""

from __future__ import annotations

import argparse
import sys
import traceback
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).parent
# override=True so an empty shell var doesn't shadow .env values.
load_dotenv(HERE / ".env", override=True)

from apewisdom_client import fetch_top_tickers  # noqa: E402
from reddit_client import fetch_recent_posts  # noqa: E402
from render import ReportData, render_html, render_markdown  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="no Claude, no email")
    ap.add_argument("--no-email", action="store_true", help="skip email send")
    ap.add_argument("--top-n", type=int, default=20, help="tickers to show")
    ap.add_argument("--posts", type=int, default=25, help="RSS posts to pull")
    args = ap.parse_args()

    print("Fetching apewisdom ticker rankings...", file=sys.stderr)
    tickers = fetch_top_tickers(limit=args.top_n)
    print(f"  {len(tickers)} tickers; top: " + ", ".join(
        f"{t.symbol}({t.mentions})" for t in tickers[:10]
    ), file=sys.stderr)

    print("Fetching Reddit RSS posts...", file=sys.stderr)
    posts = fetch_recent_posts(limit=args.posts)
    print(f"  {len(posts)} posts", file=sys.stderr)

    if args.dry_run:
        print("\n--- DRY RUN ticker table ---")
        for t in tickers[: args.top_n]:
            rc = t.rank_change
            rc_str = (
                f"new" if rc is None
                else (f"↑{rc}" if rc > 0 else (f"↓{-rc}" if rc < 0 else "—"))
            )
            print(
                f"#{t.rank:>2} {rc_str:>4}  {t.symbol:<6} {t.name[:32]:<32} "
                f"mentions={t.mentions:<4} upvotes={t.upvotes}"
            )
        return 0

    print("Summarizing with Claude...", file=sys.stderr)
    from summarizer import summarize  # lazy import
    briefing = summarize(posts, tickers)

    report = ReportData(
        on_date=date.today(),
        posts_scanned=len(posts),
        tickers=tickers,
        briefing=briefing,
        top_posts=posts,
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
    top_ticker = tickers[0].symbol if tickers else "—"
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
