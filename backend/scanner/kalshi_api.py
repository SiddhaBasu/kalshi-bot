"""
Thin, read-only REST wrapper around Kalshi's market-data endpoints.

Separate from backend.data.kalshi_client.KalshiClient's trading methods (orders,
balance) — this module only covers the endpoints a scanner needs: markets,
trades, events, and orderbooks. Reuses KalshiClient's RSA-PSS signing since
Kalshi's elections.kalshi.com host requires a signed request for every route,
public or not.
"""
import logging
import re
from typing import Any, Dict, List, Optional

from backend.data.kalshi_client import KalshiClient

logger = logging.getLogger("trading_bot")


def slugify(title: str) -> str:
    """Turn a market title into the URL slug Kalshi uses, e.g. 'More tech layoffs in 2026' -> 'more-tech-layoffs-in-2026'."""
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug


def build_market_link(series_ticker: str, title: str, event_ticker: str) -> str:
    """
    Build a clickable kalshi.com link: kalshi.com/markets/{series}/{title-slug}/{event}
    All path segments are lowercased to match Kalshi's URL scheme.
    """
    return f"https://kalshi.com/markets/{series_ticker.lower()}/{slugify(title)}/{event_ticker.lower()}"


def derive_series_ticker(ticker: str) -> str:
    """Fallback when a market payload omits series_ticker: it's the prefix before the first '-'."""
    return ticker.split("-")[0]


def derive_event_ticker(ticker: str) -> str:
    """Fallback when a market payload omits event_ticker: the ticker minus its final '-<strike>' segment."""
    parts = ticker.split("-")
    return "-".join(parts[:-1]) if len(parts) > 1 else ticker


class KalshiScannerAPI:
    """Read-only Kalshi REST endpoints used for market/trade/event/orderbook scanning."""

    def __init__(self):
        self._client = KalshiClient()
        self._market_cache: Dict[str, dict] = {}

    async def get_markets(
        self,
        *,
        series_ticker: Optional[str] = None,
        event_ticker: Optional[str] = None,
        status: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: int = 200,
    ) -> dict:
        """GET /markets — list markets, optionally filtered by series/event/status."""
        params: Dict[str, Any] = {"limit": limit}
        if series_ticker:
            params["series_ticker"] = series_ticker
        if event_ticker:
            params["event_ticker"] = event_ticker
        if status:
            params["status"] = status
        if cursor:
            params["cursor"] = cursor
        return await self._client.get("/markets", params=params)

    async def get_market(self, ticker: str, *, use_cache: bool = True) -> Optional[dict]:
        """GET /markets/{ticker} — single market's metadata (title, series_ticker, event_ticker, prices)."""
        if use_cache and ticker in self._market_cache:
            return self._market_cache[ticker]
        try:
            data = await self._client.get(f"/markets/{ticker}")
            market = data.get("market", data)
            self._market_cache[ticker] = market
            return market
        except Exception as e:
            logger.debug(f"get_market failed for {ticker}: {e}")
            return None

    async def get_trades(
        self,
        *,
        ticker: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: int = 1000,
        min_ts: Optional[int] = None,
        max_ts: Optional[int] = None,
    ) -> dict:
        """GET /markets/trades — recent fills. Without `ticker`, returns trades across all markets."""
        params: Dict[str, Any] = {"limit": limit}
        if ticker:
            params["ticker"] = ticker
        if cursor:
            params["cursor"] = cursor
        if min_ts is not None:
            params["min_ts"] = min_ts
        if max_ts is not None:
            params["max_ts"] = max_ts
        return await self._client.get("/markets/trades", params=params)

    async def get_all_trades(
        self,
        *,
        min_ts: Optional[int] = None,
        max_ts: Optional[int] = None,
        max_pages: int = 20,
    ) -> List[dict]:
        """Paginate through /markets/trades until the cursor is exhausted or max_pages is hit."""
        trades: List[dict] = []
        cursor: Optional[str] = None
        for _ in range(max_pages):
            data = await self.get_trades(cursor=cursor, min_ts=min_ts, max_ts=max_ts)
            page = data.get("trades", [])
            trades.extend(page)
            cursor = data.get("cursor")
            if not cursor or not page:
                break
        return trades

    async def get_events(
        self,
        *,
        series_ticker: Optional[str] = None,
        status: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: int = 200,
        with_nested_markets: bool = False,
    ) -> dict:
        """GET /events — list events, optionally filtered by series/status."""
        params: Dict[str, Any] = {"limit": limit, "with_nested_markets": with_nested_markets}
        if series_ticker:
            params["series_ticker"] = series_ticker
        if status:
            params["status"] = status
        if cursor:
            params["cursor"] = cursor
        return await self._client.get("/events", params=params)

    async def get_event(self, event_ticker: str, *, with_nested_markets: bool = True) -> dict:
        """GET /events/{event_ticker} — single event plus its markets."""
        params = {"with_nested_markets": with_nested_markets}
        return await self._client.get(f"/events/{event_ticker}", params=params)

    async def get_orderbook(self, ticker: str, *, depth: Optional[int] = None) -> dict:
        """GET /markets/{ticker}/orderbook — current yes/no book."""
        params = {"depth": depth} if depth else None
        return await self._client.get(f"/markets/{ticker}/orderbook", params=params)
