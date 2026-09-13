from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from config.settings import (
    BITHUMB_PUBLIC_API_URL,
    CANDLE_COUNT,
    MARKET,
    TRADING_TIMEZONE,
)


class BithumbClient:
    """빗썸 USDT/KRW 시세를 조회합니다."""

    def __init__(self):
        self.market = MARKET
        self.timezone = ZoneInfo(TRADING_TIMEZONE)

    def _get(self, endpoint: str, params: dict | None = None):
        """빗썸 공개 API의 data 값을 반환합니다."""

        try:
            response = requests.get(
                f"{BITHUMB_PUBLIC_API_URL}/{endpoint}",
                params=params,
                timeout=10,
            )
            response.raise_for_status()
            result = response.json()
        except requests.RequestException as exc:
            raise RuntimeError(f"빗썸 API 요청 실패: {exc}") from exc
        except ValueError as exc:
            raise RuntimeError("빗썸 API 응답이 JSON이 아닙니다.") from exc

        if str(result.get("status")) != "0000":
            raise RuntimeError(f"빗썸 API 처리 오류: {result}")

        return result["data"]

    def get_current_price(self) -> float:
        """USDT 현재가를 반환합니다."""
        return float(self._get(f"ticker/{self.market}")["closing_price"])

    def get_candlestick(
        self,
        interval: str = "24h",
        count: int = CANDLE_COUNT,
    ) -> list:
        """USDT 캔들 데이터를 반환합니다."""

        if not 1 <= count <= 200:
            raise ValueError("캔들 개수는 1~200이어야 합니다.")

        candles = self._get(
            f"candlestick/{self.market}/{interval}",
            {"count": count},
        )

        if not isinstance(candles, list) or not candles:
            raise RuntimeError("캔들 데이터가 없습니다.")

        return candles

    def _parse_candle(self, candle: list) -> dict:
        """캔들 배열을 딕셔너리로 변환합니다."""

        candle_time = datetime.fromtimestamp(
            float(candle[0]) / 1000,
            tz=self.timezone,
        )

        return {
            "timestamp": int(float(candle[0])),
            "date": candle_time.strftime("%Y-%m-%d"),
            "_datetime": candle_time,
            "open": float(candle[1]),
            "close": float(candle[2]),
            "high": float(candle[3]),
            "low": float(candle[4]),
            "volume": float(candle[5]),
        }

    def get_closed_daily_candles(
        self,
        count: int = CANDLE_COUNT,
    ) -> list[dict]:
        """오늘 진행 중인 일봉을 제외하여 반환합니다."""

        candles = [
            self._parse_candle(candle)
            for candle in self.get_candlestick("24h", count + 2)
        ]
        candles.sort(key=lambda item: item["timestamp"])

        today = datetime.now(self.timezone).date()
        closed = [
            candle
            for candle in candles
            if candle["_datetime"].date() < today
        ]

        if len(closed) < count:
            raise RuntimeError(
                f"완료 일봉 부족: 필요 {count}, 조회 {len(closed)}"
            )

        result = []

        for candle in closed[-count:]:
            candle = candle.copy()
            candle.pop("_datetime")
            result.append(candle)

        return result
