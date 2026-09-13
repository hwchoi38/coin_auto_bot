from config.settings import (
    FEE_RATE,
    MAX_LOSS_RATE,
    SLIPPAGE_RATE,
    TAKE_PROFIT_RATE,
    TRAILING_STOP_RATE,
)


class RiskManager:
    """USDT 포지션의 최종 행동을 결정합니다."""

    def __init__(self):
        self.stop_loss_rate = MAX_LOSS_RATE
        self.take_profit_rate = TAKE_PROFIT_RATE
        self.trailing_stop_rate = TRAILING_STOP_RATE

    @staticmethod
    def _result(
        action: str,
        reason: str,
        profit_rate: float = 0.0,
        trailing_rate: float = 0.0,
    ) -> dict:
        """판단 결과 형식을 통일합니다."""

        return {
            "action": action,
            "reason": reason,
            "profit_rate": profit_rate * 100,
            "trailing_drop_rate": trailing_rate * 100,
        }

    @staticmethod
    def calculate_profit_rate(
        current_price: float,
        usdt_balance: float,
        invested_krw: float,
    ) -> float:
        """수수료와 슬리피지를 반영한 예상 수익률입니다."""

        if current_price <= 0 or usdt_balance <= 0 or invested_krw <= 0:
            return 0.0

        sell_price = current_price * (1 - SLIPPAGE_RATE)
        sell_amount = usdt_balance * sell_price * (1 - FEE_RATE)

        return (sell_amount - invested_krw) / invested_krw

    def decide_action(
        self,
        signal: str,
        has_position: bool,
        buy_price: float = 0.0,
        current_price: float = 0.0,
        highest_price: float = 0.0,
        usdt_balance: float = 0.0,
        invested_krw: float = 0.0,
    ) -> dict:
        """신호와 수익률을 이용해 행동을 결정합니다."""

        signal = signal.upper()

        if not has_position:
            if signal == "BUY":
                return self._result("BUY", "골든크로스로 매수합니다.")
            return self._result("HOLD", "매수 조건이 아닙니다.")

        profit_rate = self.calculate_profit_rate(
            current_price,
            usdt_balance,
            invested_krw,
        )

        highest_price = max(highest_price, current_price)
        trailing_rate = (
            (current_price - highest_price) / highest_price
            if highest_price > 0
            else 0.0
        )

        if profit_rate <= -self.stop_loss_rate:
            return self._result(
                "SELL_STOP_LOSS",
                "손절 기준에 도달했습니다.",
                profit_rate,
                trailing_rate,
            )

        if (
            self.trailing_stop_rate is not None
            and highest_price > buy_price
            and trailing_rate <= -self.trailing_stop_rate
        ):
            return self._result(
                "SELL_TRAILING_STOP",
                "고점 대비 하락하여 매도합니다.",
                profit_rate,
                trailing_rate,
            )

        if (
            self.take_profit_rate is not None
            and profit_rate >= self.take_profit_rate
        ):
            return self._result(
                "SELL_TAKE_PROFIT",
                "목표 수익률에 도달했습니다.",
                profit_rate,
                trailing_rate,
            )

        if signal == "SELL":
            return self._result(
                "SELL_TREND",
                "데드크로스로 매도합니다.",
                profit_rate,
                trailing_rate,
            )

        return self._result(
            "HOLD",
            "USDT를 계속 보유합니다.",
            profit_rate,
            trailing_rate,
        )
