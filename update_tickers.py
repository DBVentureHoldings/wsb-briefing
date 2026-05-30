"""Download the US-listed ticker universe (NASDAQ + NYSE/AMEX) into tickers.txt.

Run on install and weekly thereafter. Source: nasdaqtrader.com SymDir,
which is the authoritative free feed used by most tooling.
"""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

NASDAQ_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"

HERE = Path(__file__).parent
OUT = HERE / "tickers.txt"


def _parse(text: str, symbol_col: int) -> set[str]:
    symbols: set[str] = set()
    lines = text.splitlines()
    if not lines:
        return symbols
    for line in lines[1:]:
        if line.startswith("File Creation Time"):
            continue
        parts = line.split("|")
        if len(parts) <= symbol_col:
            continue
        sym = parts[symbol_col].strip().upper()
        if not sym or not sym.isalpha():
            continue
        if len(sym) > 5:
            continue
        symbols.add(sym)
    return symbols


def fetch() -> set[str]:
    out: set[str] = set()
    for url, col in [(NASDAQ_URL, 0), (OTHER_URL, 0)]:
        with urllib.request.urlopen(url, timeout=30) as resp:
            text = resp.read().decode("utf-8", errors="ignore")
        out |= _parse(text, col)
    return out


def main() -> int:
    symbols = fetch()
    if len(symbols) < 1000:
        print(f"refusing to write: only got {len(symbols)} symbols", file=sys.stderr)
        return 1
    OUT.write_text("\n".join(sorted(symbols)) + "\n")
    print(f"wrote {len(symbols)} symbols to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
