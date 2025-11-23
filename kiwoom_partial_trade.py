import time
from typing import Optional

# External library 'pykiwoom' provides a Python wrapper for Kiwoom OpenAPI
# It requires Windows and the official Kiwoom client installed.
try:
    from pykiwoom.kiwoom import Kiwoom
except ImportError:
    Kiwoom = None  # Placeholder if library is unavailable

class KiwoomTrader:
    def __init__(self):
        if Kiwoom is None:
            raise ImportError("pykiwoom library is required to run this script.")
        self.kiwoom = Kiwoom()

    def login(self) -> None:
        """Log in to Kiwoom OpenAPI."""
        self.kiwoom.CommConnect(block=True)

    def get_account(self) -> str:
        """Retrieve the default account number."""
        accounts = self.kiwoom.GetLoginInfo("ACCNO")
        if not accounts:
            raise RuntimeError("No accounts found. Check Kiwoom login status.")
        return accounts[0]

    def send_order(
        self,
        name: str,
        rqname: str,
        account: str,
        order_type: int,
        code: str,
        quantity: int,
        price: int,
        hoga: str = "00",
        order_no: str = "",
    ) -> None:
        """Wrapper around SendOrder for clarity."""
        self.kiwoom.SendOrder(name, rqname, account, order_type, code, quantity, price, hoga, order_no)
        time.sleep(1)  # simple rate limiting

    def partial_buy(
        self,
        code: str,
        total_quantity: int,
        splits: int,
        price: Optional[int] = 0,
    ) -> None:
        """Execute a buy order split into multiple smaller orders."""
        account = self.get_account()
        qty_per_order = total_quantity // splits
        remainder = total_quantity % splits

        for i in range(splits):
            qty = qty_per_order + (1 if i == splits - 1 and remainder else 0)
            self.send_order(
                name=f"buy{i}",
                rqname="buy",
                account=account,
                order_type=1,  # 1: new buy
                code=code,
                quantity=qty,
                price=price,
            )
            print(f"Placed buy order for {qty} shares of {code}")

    def partial_sell(
        self,
        code: str,
        total_quantity: int,
        splits: int,
        price: Optional[int] = 0,
    ) -> None:
        """Execute a sell order split into multiple smaller orders."""
        account = self.get_account()
        qty_per_order = total_quantity // splits
        remainder = total_quantity % splits

        for i in range(splits):
            qty = qty_per_order + (1 if i == splits - 1 and remainder else 0)
            self.send_order(
                name=f"sell{i}",
                rqname="sell",
                account=account,
                order_type=2,  # 2: new sell
                code=code,
                quantity=qty,
                price=price,
            )
            print(f"Placed sell order for {qty} shares of {code}")


def main():
    if Kiwoom is None:
        print("pykiwoom library is not installed. This script cannot run.")
        return

    trader = KiwoomTrader()
    trader.login()

    stock_code = "005930"  # example: Samsung Electronics
    total_qty = 100
    splits = 5

    # Example usage: buy and later sell in parts
    trader.partial_buy(stock_code, total_qty, splits, price=0)  # price=0 means market order
    time.sleep(5)  # wait before selling
    trader.partial_sell(stock_code, total_qty, splits, price=0)


if __name__ == "__main__":
    main()
