from config.settings import (
    CANDLE_COUNT,
    LONG_MA_PERIOD,
    MEAN_PERIOD,
    SHORT_MA_PERIOD,
    TRADING_MODE,
)
from exchange.bithumb_client import BithumbClient
from risk.risk_manager import RiskManager
from strategy.moving_average import MovingAverageStrategy
from trader.paper_trader import PaperTrader


def main() -> None:
    """USDT Z-score 평균 회귀 모의매매를 실행합니다."""

    if TRADING_MODE != "PAPER":
        raise ValueError(
            "현재 main.py는 PAPER 모드만 지원합니다."
        )

    client = BithumbClient()
    strategy = MovingAverageStrategy()
    trader = PaperTrader()
    risk_manager = RiskManager()

    # 전략 계산에 충분한 완료 일봉을 조회합니다.
    candle_count = max(
        CANDLE_COUNT,
        LONG_MA_PERIOD,
        MEAN_PERIOD,
    )

    current_price = client.get_current_price()
    candles = client.get_closed_daily_candles(
        candle_count
    )

    if not candles:
        raise RuntimeError(
            "완료 일봉 데이터를 가져오지 못했습니다."
        )

    prices = [
        float(candle["close"])
        for candle in candles
    ]
    candle_date = str(candles[-1]["date"])

    result = strategy.generate_signal(prices)
    original_signal = result["signal"]

    # 보유 중인 경우 최고 가격을 갱신합니다.
    if trader.has_position:
        trader.update_highest_price(current_price)

    duplicate_candle = trader.is_candle_processed(
        candle_date
    )

    # 같은 일봉의 신규 매수·전략 매도를 방지합니다.
    strategy_signal = (
        "HOLD"
        if duplicate_candle
        else original_signal
    )

    # 손절과 트레일링 스톱은 중복 일봉에서도 검사합니다.
    decision = risk_manager.decide_action(
        signal=strategy_signal,
        has_position=trader.has_position,
        buy_price=trader.buy_price,
        current_price=current_price,
        highest_price=trader.highest_price,
        usdt_balance=trader.usdt_balance,
        invested_krw=trader.invested_krw,
    )

    print("\n==============================")
    print("빗썸 USDT Z-score 평균 회귀 전략")
    print("==============================")
    print(f"현재가: {current_price:,.4f}원")
    print(f"완료 일봉: {candle_date}")

    if result.get("short_ma") is not None:
        print(
            f"이동평균: "
            f"{SHORT_MA_PERIOD}일 "
            f"{result['short_ma']:,.4f} / "
            f"{LONG_MA_PERIOD}일 "
            f"{result['long_ma']:,.4f}"
        )

    if result.get("mean_price") is not None:
        print(
            f"기준 평균: "
            f"{result['mean_price']:,.4f}원"
        )

    if result.get("z_score") is not None:
        print(
            f"Z-score: "
            f"{result['z_score']:.4f}"
        )

    if result.get("target_price") is not None:
        print(
            f"청산 목표가: "
            f"{result['target_price']:,.4f}원"
        )

    if result.get("expected_return") is not None:
        print(
            f"예상 순수익률: "
            f"{result['expected_return'] * 100:.3f}%"
        )

    print(f"전략 신호: {original_signal}")

    if duplicate_candle:
        print("일봉 상태: 이미 처리된 일봉")

    print(f"최종 행동: {decision['action']}")
    print(f"판단: {decision['reason']}")

    execution = trader.execute_action(
        action=decision["action"],
        current_price=current_price,
    )

    if execution.get("success"):
        # 신규 일봉일 때만 처리 상태를 저장합니다.
        if not duplicate_candle:
            trader.mark_candle_processed(
                candle_date=candle_date,
                signal=original_signal,
                action=execution["action"],
            )

        if execution["action"] == "BUY":
            print(
                f"매수 수량: "
                f"{execution.get('amount', 0):.8f} USDT"
            )

        elif execution["action"].startswith("SELL"):
            print(
                f"거래 손익: "
                f"{execution.get('profit', 0):,.2f}원"
            )
    else:
        print(
            f"거래 실패: "
            f"{execution.get('reason', '알 수 없는 오류')}"
        )

    trader.show_account(current_price)


if __name__ == "__main__":
    main()