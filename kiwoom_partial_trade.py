"""Utilities for partial buy/sell execution via the Kiwoom OpenAPI.

This module offers two backends:
- A real Kiwoom backend that uses ``pykiwoom`` (requires Windows + Kiwoom client).
- A dry-run backend that simulates requests for local testing without the API.

Run ``python kiwoom_partial_trade.py --help`` for CLI usage.
"""

from __future__ import annotations

import argparse
import dataclasses
import importlib
import importlib.util
import logging
import time
from typing import Protocol


LOGGER = logging.getLogger(__name__)


# Avoid try/except around imports: probe availability before importing.
_KIWOOM_SPEC = importlib.util.find_spec("pykiwoom")
if _KIWOOM_SPEC:
    Kiwoom = importlib.import_module("pykiwoom.kiwoom").Kiwoom  # type: ignore[attr-defined]
else:
    Kiwoom = None


class KiwoomAPI(Protocol):
    """Minimal Kiwoom API surface used by this script."""

    def CommConnect(self, block: bool = True) -> None:  # noqa: N802 - Kiwoom naming
        ...

    def GetLoginInfo(self, tag: str) -> list[str]:  # noqa: N802 - Kiwoom naming
        ...

    def SendOrder(
        self,
        rqname: str,
        screen_no: str,
        account: str,
        order_type: int,
        code: str,
        quantity: int,
        price: int,
        hoga: str,
        order_no: str,
    ) -> None:  # noqa: N802 - Kiwoom naming
        ...


@dataclasses.dataclass(slots=True)
class KiwoomConfig:
    """Configuration for partial order execution."""

    code: str
    total_quantity: int
    splits: int
    price: int = 0
    sleep_between_orders: float = 1.0
    action: str = "both"  # buy, sell, or both

    def validate(self) -> None:
        if self.total_quantity <= 0:
            raise ValueError("total_quantity must be positive")
        if self.splits <= 0:
            raise ValueError("splits must be positive")
        if self.splits > self.total_quantity:
            raise ValueError("splits cannot exceed total_quantity")
        if self.action not in {"buy", "sell", "both"}:
            raise ValueError("action must be one of: buy, sell, both")


class RealKiwoomClient:
    """Concrete Kiwoom API wrapper that mirrors the required methods."""

    def __init__(self) -> None:
        if Kiwoom is None:
            raise ImportError("pykiwoom is required for real trading but is not installed.")
        self._client: KiwoomAPI = Kiwoom()

    def login(self) -> None:
        self._client.CommConnect(block=True)
        LOGGER.info("Logged into Kiwoom OpenAPI")

    def account(self) -> str:
        accounts = self._client.GetLoginInfo("ACCNO")
        if not accounts:
            raise RuntimeError("No accounts found. Confirm Kiwoom login status.")
        return accounts[0]

    def send_order(
        self,
        rqname: str,
        screen_no: str,
        account: str,
        order_type: int,
        code: str,
        quantity: int,
        price: int,
        hoga: str,
        order_no: str,
    ) -> None:
        self._client.SendOrder(rqname, screen_no, account, order_type, code, quantity, price, hoga, order_no)


class DryRunKiwoomClient:
    """Simulated Kiwoom client for local testing without external connectivity."""

    def __init__(self) -> None:
        self._logged_in = False

    def login(self) -> None:
        self._logged_in = True
        LOGGER.info("[DRY-RUN] Login simulated")

    def account(self) -> str:
        if not self._logged_in:
            raise RuntimeError("Call login() before requesting account in dry-run mode.")
        return "0000-DRY-RUN"

    def send_order(
        self,
        rqname: str,
        screen_no: str,
        account: str,
        order_type: int,
        code: str,
        quantity: int,
        price: int,
        hoga: str,
        order_no: str,
    ) -> None:
        LOGGER.info(
            "[DRY-RUN] rqname=%s screen=%s account=%s type=%s code=%s qty=%s price=%s hoga=%s order_no=%s",
            rqname,
            screen_no,
            account,
            order_type,
            code,
            quantity,
            price,
            hoga,
            order_no,
        )


class PartialOrderExecutor:
    """Executes partial buy/sell orders using a Kiwoom-compatible client."""

    def __init__(self, client: RealKiwoomClient | DryRunKiwoomClient, config: KiwoomConfig):
        self.client = client
        self.config = config

    def _split_quantities(self) -> list[int]:
        base = self.config.total_quantity // self.config.splits
        remainder = self.config.total_quantity % self.config.splits
        quantities = [base for _ in range(self.config.splits)]
        if remainder:
            quantities[-1] += remainder
        LOGGER.debug("Split quantities: %s", quantities)
        return quantities

    def _execute_orders(self, order_type: int, tag: str) -> None:
        account = self.client.account()
        for idx, qty in enumerate(self._split_quantities()):
            rqname = f"{tag}{idx}"
            self.client.send_order(
                rqname=rqname,
                screen_no=tag,
                account=account,
                order_type=order_type,
                code=self.config.code,
                quantity=qty,
                price=self.config.price,
                hoga="00",  # 00: market order
                order_no="",
            )
            LOGGER.info("Placed %s order %s for %s shares of %s", tag, idx + 1, qty, self.config.code)
            time.sleep(self.config.sleep_between_orders)

    def run(self) -> None:
        self.config.validate()
        self.client.login()

        if self.config.action in {"buy", "both"}:
            self._execute_orders(order_type=1, tag="buy")  # 1: new buy
        if self.config.action in {"sell", "both"}:
            self._execute_orders(order_type=2, tag="sell")  # 2: new sell


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Partial buy/sell automation for Kiwoom OpenAPI")
    parser.add_argument("code", help="Stock code (e.g., 005930 for Samsung Electronics)")
    parser.add_argument("total_quantity", type=int, help="Total quantity to trade")
    parser.add_argument("splits", type=int, help="Number of partial orders")
    parser.add_argument("--price", type=int, default=0, help="Order price (0 for market order)")
    parser.add_argument(
        "--sleep",
        type=float,
        default=1.0,
        help="Seconds to wait between orders (helps avoid rate limits)",
    )
    parser.add_argument(
        "--action",
        choices=["buy", "sell", "both"],
        default="both",
        help="Whether to place buy orders, sell orders, or both",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Use a simulated backend instead of the real Kiwoom API",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging verbosity",
    )
    return parser.parse_args()


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def build_client(use_dry_run: bool) -> RealKiwoomClient | DryRunKiwoomClient:
    if use_dry_run:
        return DryRunKiwoomClient()
    return RealKiwoomClient()


def main() -> None:
    args = parse_args()
    configure_logging(args.log_level)

    client = build_client(args.dry_run)
    config = KiwoomConfig(
        code=args.code,
        total_quantity=args.total_quantity,
        splits=args.splits,
        price=args.price,
        sleep_between_orders=args.sleep,
        action=args.action,
    )

    executor = PartialOrderExecutor(client, config)
    executor.run()


if __name__ == "__main__":
    main()
