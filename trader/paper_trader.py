import csv
import json
from datetime import datetime
from pathlib import Path

from config.settings import (
    BUY_RATIO,
    FEE_RATE,
    INITIAL_CASH,
    MIN_ORDER_KRW,
    SLIPPAGE_RATE,
)


class PaperTrader:
    """USDT 모의계좌와 거래 내역을 관리합니다."""

    def __init__(
        self,
        account_file: str = "data/trades/paper_account.json",
        history_file: str = "data/trades/trade_history.csv",
    ):
        self.account_file = Path(account_file)
        self.history_file = Path(history_file)
        self.load_account()

    def load_account(self) -> None:
        """저장된 모의계좌를 불러옵니다."""

        account = {}

        if self.account_file.exists():
            account = json.loads(
                self.account_file.read_text(encoding="utf-8")
            )

        self.cash = float(account.get("cash", INITIAL_CASH))
        self.usdt_balance = float(
            account.get("usdt_balance", 0.0)
        )
        self.buy_price = float(
            account.get("buy_price", 0.0)
        )
        self.invested_krw = float(
            account.get("invested_krw", 0.0)
        )
        self.total_fee = float(
            account.get("total_fee", 0.0)
        )
        self.highest_price = float(
            account.get("highest_price", 0.0)
        )

        self.last_processed_candle_date = account.get(
            "last_processed_candle_date"
        )
        self.last_signal = account.get(
            "last_signal",
            "HOLD",
        )
        self.last_trade_action = account.get(
            "last_trade_action",
            "NONE",
        )

        self.has_position = self.usdt_balance > 0

        if not self.account_file.exists():
            self.save_account()

    def get_account(self) -> dict:
        """현재 계좌 정보를 반환합니다."""

        return {
            "cash": self.cash,
            "usdt_balance": self.usdt_balance,
            "has_position": self.has_position,
            "buy_price": self.buy_price,
            "invested_krw": self.invested_krw,
            "total_fee": self.total_fee,
            "highest_price": self.highest_price,
            "last_processed_candle_date": (
                self.last_processed_candle_date
            ),
            "last_signal": self.last_signal,
            "last_trade_action": self.last_trade_action,
        }

    def save_account(self) -> None:
        """현재 계좌 정보를 JSON 파일에 저장합니다."""

        self.account_file.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.account_file.write_text(
            json.dumps(
                self.get_account(),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def save_trade(
        self,
        action: str,
        price: float,
        amount: float,
        fee: float,
        profit: float = 0.0,
    ) -> None:
        """거래 내역을 CSV 파일에 저장합니다."""

        self.history_file.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        new_file = not self.history_file.exists()

        with self.history_file.open(
            "a",
            newline="",
            encoding="utf-8-sig",
        ) as file:
            writer = csv.writer(file)

            if new_file:
                writer.writerow([
                    "trade_time",
                    "trade_type",
                    "price",
                    "usdt_amount",
                    "fee",
                    "profit",
                ])

            writer.writerow([
                datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                action,
                round(price, 4),
                round(amount, 8),
                round(fee, 2),
                round(profit, 2),
            ])

    def update_highest_price(
        self,
        current_price: float,
    ) -> None:
        """보유 기간 중 최고 가격을 갱신합니다."""

        if (
            self.has_position
            and current_price > self.highest_price
        ):
            self.highest_price = current_price
            self.save_account()

    def is_candle_processed(
        self,
        candle_date: str,
    ) -> bool:
        """이미 처리한 일봉인지 확인합니다."""

        return (
            candle_date
            == self.last_processed_candle_date
        )

    def mark_candle_processed(
        self,
        candle_date: str,
        signal: str,
        action: str,
    ) -> None:
        """처리한 일봉과 신호를 저장합니다."""

        self.last_processed_candle_date = candle_date
        self.last_signal = signal
        self.last_trade_action = action
        self.save_account()

    def buy(self, current_price: float) -> dict:
        """현재 현금의 BUY_RATIO만큼 USDT를 매수합니다."""

        if self.has_position:
            return self._failed(
                "BUY",
                "이미 USDT를 보유 중입니다.",
            )

        if current_price <= 0:
            return self._failed(
                "BUY",
                "현재 가격이 올바르지 않습니다.",
            )

        # 백테스트와 동일하게 매수 원금과 수수료를 계산합니다.
        target_order = self.cash * BUY_RATIO
        maximum_order = self.cash / (1 + FEE_RATE)

        order_amount = min(
            target_order,
            maximum_order,
        )

        if order_amount < MIN_ORDER_KRW:
            return self._failed(
                "BUY",
                (
                    f"주문금액이 최소 주문금액 "
                    f"{MIN_ORDER_KRW:,.0f}원보다 작습니다."
                ),
            )

        execution_price = (
            current_price
            * (1 + SLIPPAGE_RATE)
        )
        fee = order_amount * FEE_RATE
        total_cost = order_amount + fee
        usdt_amount = order_amount / execution_price

        if total_cost > self.cash:
            return self._failed(
                "BUY",
                "주문 가능한 현금이 부족합니다.",
            )

        self.cash -= total_cost
        self.usdt_balance = usdt_amount
        self.has_position = True
        self.buy_price = execution_price
        self.invested_krw = total_cost
        self.total_fee += fee
        self.highest_price = execution_price
        self.last_trade_action = "BUY"

        self.save_account()
        self.save_trade(
            action="BUY",
            price=execution_price,
            amount=usdt_amount,
            fee=fee,
        )

        return {
            "success": True,
            "action": "BUY",
            "price": execution_price,
            "amount": usdt_amount,
            "fee": fee,
        }

    def sell(
        self,
        current_price: float,
        action: str = "SELL",
    ) -> dict:
        """보유한 USDT를 전량 매도합니다."""

        if not self.has_position:
            return self._failed(
                action,
                "보유 USDT가 없습니다.",
            )

        if current_price <= 0:
            return self._failed(
                action,
                "현재 가격이 올바르지 않습니다.",
            )

        amount = self.usdt_balance

        execution_price = (
            current_price
            * (1 - SLIPPAGE_RATE)
        )

        gross_amount = amount * execution_price
        fee = gross_amount * FEE_RATE
        net_amount = gross_amount - fee

        profit = (
            net_amount
            - self.invested_krw
        )

        profit_rate = (
            profit / self.invested_krw
            if self.invested_krw > 0
            else 0.0
        )

        self.cash += net_amount
        self.total_fee += fee

        # 매도 후 포지션 정보를 초기화합니다.
        self.usdt_balance = 0.0
        self.has_position = False
        self.buy_price = 0.0
        self.invested_krw = 0.0
        self.highest_price = 0.0
        self.last_trade_action = action

        self.save_account()
        self.save_trade(
            action=action,
            price=execution_price,
            amount=amount,
            fee=fee,
            profit=profit,
        )

        return {
            "success": True,
            "action": action,
            "price": execution_price,
            "amount": amount,
            "fee": fee,
            "profit": profit,
            "profit_rate": profit_rate,
        }

    def execute_action(
        self,
        action: str,
        current_price: float,
        candle_date: str | None = None,
    ) -> dict:
        """BUY, SELL 또는 HOLD를 실행합니다."""

        action = action.upper()

        if action == "BUY":
            result = self.buy(current_price)

        elif action.startswith("SELL"):
            result = self.sell(
                current_price,
                action,
            )

        else:
            result = {
                "success": True,
                "action": "HOLD",
            }

        if candle_date and result.get("success"):
            self.last_processed_candle_date = candle_date
            self.last_trade_action = result["action"]
            self.save_account()

        return result

    def show_account(
        self,
        current_price: float,
    ) -> None:
        """현재 모의계좌 상태를 출력합니다."""

        asset_value = 0.0
        evaluation_profit = 0.0

        if self.has_position:
            expected_sell_price = (
                current_price
                * (1 - SLIPPAGE_RATE)
            )

            asset_value = (
                self.usdt_balance
                * expected_sell_price
                * (1 - FEE_RATE)
            )

            evaluation_profit = (
                asset_value
                - self.invested_krw
            )

        total_asset = self.cash + asset_value

        print("\n====================")
        print("USDT 모의 계좌")
        print("====================")
        print(f"현금: {self.cash:,.2f}원")
        print(f"USDT: {self.usdt_balance:.8f}")
        print(f"매수가: {self.buy_price:,.4f}원")
        print(f"평가손익: {evaluation_profit:,.2f}원")
        print(f"평가금액: {total_asset:,.2f}원")
        print(f"누적수수료: {self.total_fee:,.2f}원")
        print("====================")

    @staticmethod
    def _failed(
        action: str,
        reason: str,
    ) -> dict:
        """실패 결과의 공통 형식을 반환합니다."""

        return {
            "success": False,
            "action": action,
            "reason": reason,
        }