"""Kalshi API client with RSA-PSS signature authentication."""
import base64
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from backend.config import settings

logger = logging.getLogger("trading_bot")

BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"


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

    async def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> dict:
        url = f"{BASE_URL}{path}"
        headers = self._sign_request("GET", path)
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url, headers=headers, params=params)
            response.raise_for_status()
            return response.json()

    async def post(self, path: str, body: dict) -> dict:
        url = f"{BASE_URL}{path}"
        headers = self._sign_request("POST", path)
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(url, headers=headers, json=body)
            response.raise_for_status()
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
        side: str,          # "yes" or "no"
        count: int,         # number of contracts (each = $1 max payout)
        price_cents: int,   # limit price in cents (1–99)
        client_order_id: Optional[str] = None,
    ) -> dict:
        """
        Place a limit order on Kalshi.

        Args:
            ticker: Market ticker e.g. "KXHIGHNY-26APR28-B72"
            side: "yes" or "no"
            count: Number of contracts. Each contract costs price_cents/100 dollars
                   and pays $1 if you win. So `count` ≈ size_usd / (price_cents/100).
            price_cents: Limit price in cents (1–99). Use yes_ask * 100 for yes side.
            client_order_id: Optional idempotency key.

        Returns:
            Kalshi order response dict (contains "order" key with order_id, status, etc.)
        """
        body: dict = {
            "ticker": ticker,
            "action": "buy",
            "side": side,
            "count": count,
            "type": "limit",
        }
        if side == "yes":
            body["yes_price"] = price_cents
        else:
            body["no_price"] = price_cents

        if client_order_id:
            body["client_order_id"] = client_order_id

        logger.info(
            f"Placing Kalshi order: {ticker} {side.upper()} x{count} @ {price_cents}c"
        )
        return await self.post("/portfolio/orders", body)

    async def cancel_order(self, order_id: str) -> dict:
        return await self.post(f"/portfolio/orders/{order_id}/decrease", {"reduce_by": 999999})

    async def get_order(self, order_id: str) -> dict:
        return await self.get(f"/portfolio/orders/{order_id}")


def kalshi_credentials_present() -> bool:
    return bool(settings.KALSHI_API_KEY_ID and settings.KALSHI_PRIVATE_KEY_PATH)
