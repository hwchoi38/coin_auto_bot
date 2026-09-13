# strategy/moving_average.py

from math import isfinite, sqrt

from config.settings import (
    ENTRY_Z_SCORE,
    EXIT_Z_SCORE,
    FEE_RATE,
    LONG_MA_PERIOD,
    MEAN_PERIOD,
    MIN_PROFIT_BUFFER,
    SHORT_MA_PERIOD,
    SLIPPAGE_RATE,
    TREND_LOOKBACK,
    TREND_MAX_DROP,
    TREND_PERIOD,
)


class MovingAverageStrategy:
    """USDT Z-score 평균 회귀 전략입니다."""

    def __init__(
        self,
        mean_window: int = MEAN_PERIOD,
        short_window: int = SHORT_MA_PERIOD,
        long_window: int = LONG_MA_PERIOD,
        trend_window: int = TREND_PERIOD,
        trend_lookback: int = TREND_LOOKBACK,
        entry_z_score: float = ENTRY_Z_SCORE,
        exit_z_score: float = EXIT_Z_SCORE,
    ):
        self.mean_window = mean_window
        self.short_window = short_window
        self.long_window = long_window
        self.trend_window = trend_window
        self.trend_lookback = trend_lookback
        self.entry_z_score = entry_z_score
        self.exit_z_score = exit_z_score

        self._validate_settings()

    def _validate_settings(self) -> None:
        """전략 설정값을 검사합니다."""

        if self.mean_window < 2 or self.trend_window < 2:
            raise ValueError(
                "평균과 추세 계산 기간은 2 이상이어야 합니다."
            )

        if not 0 < self.short_window < self.long_window:
            raise ValueError(
                "이동평균 기간은 0 < 단기 < 장기여야 합니다."
            )

        if self.trend_lookback < 1:
            raise ValueError(
                "추세 비교 기간은 1 이상이어야 합니다."
            )

        if self.entry_z_score >= self.exit_z_score:
            raise ValueError(
                "진입 Z-score는 청산 Z-score보다 작아야 합니다."
            )

    def generate_signal(self, prices: list[float]) -> dict:
        """가격과 추세를 분석하여 BUY, SELL, HOLD를 반환합니다."""

        required = max(
            self.mean_window,
            self.long_window,
            self.trend_window + self.trend_lookback,
        )

        if len(prices) < required:
            return self._result(
                reason=f"가격 데이터 부족: 최소 {required}개 필요"
            )

        prices = self._validate_prices(prices)

        current_price = prices[-1]

        mean_prices = prices[-self.mean_window:]
        mean_price = self._mean(mean_prices)
        standard_deviation = self._sample_std(
            mean_prices,
            mean_price,
        )

        short_ma = self._mean(
            prices[-self.short_window:]
        )
        long_ma = self._mean(
            prices[-self.long_window:]
        )

        # 현재와 과거의 장기 추세 이동평균을 비교합니다.
        current_trend_ma = self._mean(
            prices[-self.trend_window:]
        )

        past_end = -self.trend_lookback
        past_start = past_end - self.trend_window

        previous_trend_ma = self._mean(
            prices[past_start:past_end]
        )

        trend_change = (
            current_trend_ma / previous_trend_ma - 1
        )

        deviation_rate = (
            current_price / mean_price - 1
        )

        if standard_deviation == 0:
            return self._result(
                short_ma=short_ma,
                long_ma=long_ma,
                mean_price=mean_price,
                deviation_rate=deviation_rate,
                trend_ma=current_trend_ma,
                trend_change=trend_change,
                standard_deviation=0.0,
                reason="가격 변동이 없어 거래하지 않습니다.",
            )

        z_score = (
            current_price - mean_price
        ) / standard_deviation

        target_price = (
            mean_price
            + self.exit_z_score * standard_deviation
        )

        expected_return = self._expected_return(
            current_price,
            target_price,
        )

        trend_allowed = trend_change >= -TREND_MAX_DROP

        signal, reason = self._decide_signal(
            z_score=z_score,
            expected_return=expected_return,
            trend_allowed=trend_allowed,
            trend_change=trend_change,
        )

        return self._result(
            signal=signal,
            short_ma=short_ma,
            long_ma=long_ma,
            mean_price=mean_price,
            deviation_rate=deviation_rate,
            z_score=z_score,
            standard_deviation=standard_deviation,
            target_price=target_price,
            expected_return=expected_return,
            trend_ma=current_trend_ma,
            trend_change=trend_change,
            trend_allowed=trend_allowed,
            reason=reason,
        )

    def _decide_signal(
        self,
        z_score: float,
        expected_return: float,
        trend_allowed: bool,
        trend_change: float,
    ) -> tuple[str, str]:
        """진입, 청산 및 추세 조건을 판단합니다."""

        if z_score >= self.exit_z_score:
            return (
                "SELL",
                f"Z-score {z_score:.3f}이 "
                f"청산 기준 {self.exit_z_score:.3f} 이상입니다.",
            )

        if z_score > self.entry_z_score:
            return (
                "HOLD",
                f"Z-score {z_score:.3f}이 "
                "진입 기준에 도달하지 않았습니다.",
            )

        if not trend_allowed:
            return (
                "HOLD",
                f"장기 추세가 {trend_change * 100:.3f}% "
                "하락하여 매수를 제한합니다.",
            )

        if expected_return < MIN_PROFIT_BUFFER:
            return (
                "HOLD",
                f"예상 순수익률 {expected_return * 100:.3f}%가 "
                f"최소 기준 {MIN_PROFIT_BUFFER * 100:.3f}%보다 낮습니다.",
            )

        return (
            "BUY",
            f"Z-score {z_score:.3f}, "
            f"예상 순수익률 {expected_return * 100:.3f}%로 "
            "매수 조건을 충족했습니다.",
        )

    @staticmethod
    def _validate_prices(
        prices: list[float],
    ) -> list[float]:
        """가격 데이터를 실수로 변환하고 검사합니다."""

        try:
            result = [float(price) for price in prices]
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "가격 데이터는 숫자여야 합니다."
            ) from exc

        if not all(
            isfinite(price) and price > 0
            for price in result
        ):
            raise ValueError(
                "가격 데이터에 잘못된 값이 있습니다."
            )

        return result

    @staticmethod
    def _mean(values: list[float]) -> float:
        """산술평균을 계산합니다."""
        return sum(values) / len(values)

    @staticmethod
    def _sample_std(
        values: list[float],
        mean: float,
    ) -> float:
        """pandas rolling.std()와 같은 표본 표준편차입니다."""

        variance = sum(
            (value - mean) ** 2
            for value in values
        ) / (len(values) - 1)

        return sqrt(variance)

    @staticmethod
    def _expected_return(
        current_price: float,
        target_price: float,
    ) -> float:
        """수수료와 슬리피지를 반영한 예상 순수익률입니다."""

        buy_cost = (
            current_price
            * (1 + SLIPPAGE_RATE)
            * (1 + FEE_RATE)
        )

        sell_value = (
            target_price
            * (1 - SLIPPAGE_RATE)
            * (1 - FEE_RATE)
        )

        return sell_value / buy_cost - 1

    @staticmethod
    def _result(
        signal: str = "HOLD",
        short_ma=None,
        long_ma=None,
        mean_price=None,
        deviation_rate=None,
        z_score=None,
        standard_deviation=None,
        target_price=None,
        expected_return=None,
        trend_ma=None,
        trend_change=None,
        trend_allowed=None,
        reason: str = "",
    ) -> dict:
        """거래 판단 결과를 공통 형식으로 반환합니다."""

        return {
            "signal": signal,
            "previous_short_ma": None,
            "previous_long_ma": None,
            "short_ma": short_ma,
            "long_ma": long_ma,
            "mean_price": mean_price,
            "deviation_rate": deviation_rate,
            "z_score": z_score,
            "standard_deviation": standard_deviation,
            "target_price": target_price,
            "expected_return": expected_return,
            "trend_ma": trend_ma,
            "trend_change": trend_change,
            "trend_allowed": trend_allowed,
            "reason": reason,
        }