"""Kalshi API client with RSA-PSS signature authentication."""
import asyncio
import base64
import logging
import random
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from backend.config import settings

logger = logging.getLogger("trading_bot")

BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"

# Several jobs poll Kalshi every 1s (btc_poll_job, btc_ticker_poll_job) --
# a 429 with no retry/backoff would surface as a generic exception indistinguishable
# from a real failure, and would just be dropped on the next 1s tick with no
# visibility into how often it's happening.
_MAX_RATE_LIMIT_RETRIES = 3
_BASE_BACKOFF_SECONDS = 0.5


class KalshiClient:
    """Async Kalshi API client using RSA-PSS signature auth."""

    def __init__(self):
        self._private_key = None

    def _load_private_key(self):
        if self._private_key is not None:
            return self._private_key
        key_path = settings.KALSHI_PRIVATE_KEY_PATH
        if not key_path:
            raise ValueError("KALSHI_PRIVATE_KEY_PATH not configured")
        pem_data = Path(key_path).expanduser().read_bytes()
        self._private_key = serialization.load_pem_private_key(pem_data, password=None)
        return self._private_key

    def _sign_request(self, method: str, path: str) -> Dict[str, str]:
        """
        RSA-PSS sign: timestamp_ms + METHOD + /trade-api/v2<path>
        Query params are NOT included in the signed string.
        """
        timestamp_ms = str(int(time.time() * 1000))
        full_path = f"/trade-api/v2{path}"
        message = f"{timestamp_ms}{method.upper()}{full_path}"

        private_key = self._load_private_key()
        signature = private_key.sign(
            message.encode("utf-8"),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH,
            ),
            hashes.SHA256(),
        )

        return {
            "KALSHI-ACCESS-KEY": settings.KALSHI_API_KEY_ID,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode("utf-8"),
            "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
            "Content-Type": "application/json",
        }

    async def _request_with_retry(self, method: str, path: str, **kwargs) -> httpx.Response:
        """Shared request path for get/post/delete: retries 429s with backoff,
        respecting Retry-After when Kalshi sends it, and logs every retry so rate
        limiting is visible instead of surfacing as an opaque exception."""
        url = f"{BASE_URL}{path}"
        for attempt in range(_MAX_RATE_LIMIT_RETRIES + 1):
            headers = self._sign_request(method, path)
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.request(method, url, headers=headers, **kwargs)
            if response.status_code != 429:
                response.raise_for_status()
                return response
            if attempt == _MAX_RATE_LIMIT_RETRIES:
                logger.warning(
                    "Kalshi 429 rate limit on %s %s -- exhausted %d retries",
                    method, path, _MAX_RATE_LIMIT_RETRIES,
                )
                response.raise_for_status()
            retry_after = response.headers.get("Retry-After")
            if retry_after is not None:
                try:
                    delay = float(retry_after)
                except ValueError:
                    delay = _BASE_BACKOFF_SECONDS * (2 ** attempt)
            else:
                delay = _BASE_BACKOFF_SECONDS * (2 ** attempt) + random.uniform(0, 0.25)
            logger.warning(
                "Kalshi 429 rate limit on %s %s -- retry %d/%d after %.2fs",
                method, path, attempt + 1, _MAX_RATE_LIMIT_RETRIES, delay,
            )
            await asyncio.sleep(delay)
        raise RuntimeError("unreachable")  # loop always returns or raises above

    async def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> dict:
        response = await self._request_with_retry("GET", path, params=params)
        return response.json()

    async def post(self, path: str, body: dict) -> dict:
        response = await self._request_with_retry("POST", path, json=body)
        return response.json()

    async def delete(self, path: str, params: Optional[Dict[str, Any]] = None) -> dict:
        response = await self._request_with_retry("DELETE", path, params=params)
        return response.json()

    async def get_markets(self, params: Optional[Dict[str, Any]] = None) -> dict:
        return await self.get("/markets", params=params)

    async def get_market(self, ticker: str) -> dict:
        return await self.get(f"/markets/{ticker}")

    async def get_balance(self) -> dict:
        return await self.get("/portfolio/balance")

    async def place_order(
        self,
        ticker: str,
        side: str,          # "yes" or "no" -- the outcome YOU want to buy
        count: int,         # number of contracts (each = $1 max payout)
        price_cents: int,   # limit price in cents (1-99), denominated in `side`'s own price
        client_order_id: Optional[str] = None,
    ) -> dict:
        """
        Place a limit order on Kalshi.

        Verified against https://docs.kalshi.com/api-reference/orders/create-order-v2
        and the official kalshi-python SDK (2026-09-10) -- the request body
        below replaces an earlier version of this method that used a
        yes_price/no_price-in-cents + action/type schema that doesn't match
        Kalshi's current API at all and would have failed (or behaved
        unpredictably) on a real order.

        Kalshi's V2 order API expresses every order in YES-contract terms --
        there's no separate yes/no order type, just a book side:
          side="bid" -> buying YES at `price`
          side="ask" -> selling YES at `price`, economically identical to
                        buying NO at (1 - price)
        So to buy NO, this submits side="ask" at price = 1 - (NO price requested).

        Args:
            ticker: Market ticker e.g. "KXHIGHNY-26APR28-B72"
            side: "yes" or "no" -- translated below into Kalshi's bid/ask book
                  side + YES-denominated price; callers keep thinking in
                  yes/no, not bid/ask.
            count: Number of contracts. Each contract costs price_cents/100 dollars
                   and pays $1 if you win.
            price_cents: Limit price in cents (1-99), in `side`'s own price
                         (e.g. price_cents=70, side="no" = willing to pay 70c for NO).
            client_order_id: Idempotency key -- REQUIRED by Kalshi's V2 API
                (generates a random one if not given, since None is not accepted).

        Returns:
            Kalshi order response dict (contains "order" key with order_id, status, etc.)
        """
        if side == "yes":
            book_side = "bid"
            yes_price = price_cents / 100.0
        else:
            book_side = "ask"
            yes_price = 1.0 - (price_cents / 100.0)

        body: dict = {
            "ticker": ticker,
            "side": book_side,
            "count": str(count),
            "price": f"{yes_price:.4f}",
            "time_in_force": "good_till_canceled",  # we manage our own expiry/cancellation, not Kalshi's
            "self_trade_prevention_type": "taker_at_cross",
            "client_order_id": client_order_id or str(uuid.uuid4()),
        }

        logger.info(
            f"Placing Kalshi order: {ticker} {side.upper()} x{count} @ {price_cents}c "
            f"(book_side={book_side}, yes_price={yes_price:.4f})"
        )
        return await self.post("/portfolio/orders", body)

    async def cancel_order(self, order_id: str) -> dict:
        """
        Verified against the official kalshi-python SDK (2026-09-10): cancel
        is DELETE /portfolio/events/orders/{order_id}, not the previous
        "decrease by a huge number" hack against a /decrease path that isn't
        actually cancel's real endpoint. A 404 here means the order already
        filled or was already cancelled -- callers should treat exceptions
        from this method as "couldn't confirm the cancel," not necessarily
        as a hard failure (see backend/core/btc_live_trading.py's usage).
        """
        return await self.delete(f"/portfolio/events/orders/{order_id}")

    async def get_order(self, order_id: str) -> dict:
        return await self.get(f"/portfolio/orders/{order_id}")

    async def get_orders(self, *, ticker: Optional[str] = None, status: Optional[str] = None) -> dict:
        """
        List orders, optionally filtered by ticker/status. This is the source
        of truth for crash-safe idempotency: after a restart, we don't
        necessarily still know an order_id we submitted right before a crash,
        so recovery has to ask Kalshi directly "do I already have an order on
        this ticker" rather than trusting local DB state alone.
        """
        params: Dict[str, Any] = {}
        if ticker:
            params["ticker"] = ticker
        if status:
            params["status"] = status
        return await self.get("/portfolio/orders", params=params)


def kalshi_credentials_present() -> bool:
    return bool(settings.KALSHI_API_KEY_ID and settings.KALSHI_PRIVATE_KEY_PATH)
