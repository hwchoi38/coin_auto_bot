# backtest.py

import os
import time

import pandas as pd
import requests

from config.settings import (
    BUY_RATIO,
    FEE_RATE,
    FORCE_EXIT_DAYS,
    INITIAL_CASH,
    LONG_MA_PERIOD,
    MAX_HOLD_DAYS,
    MAX_LOSS_RATE,
    MEAN_PERIOD,
    MIN_ORDER_KRW,
    SLIPPAGE_RATE,
    TIME_EXIT_Z_SCORE,
    TRADING_TIMEZONE,
    TREND_LOOKBACK,
    TREND_PERIOD,
)
from strategy.moving_average import MovingAverageStrategy


INITIAL_ASSET = float(
    os.getenv("BACKTEST_INITIAL_CASH", str(INITIAL_CASH))
)

BACKTEST_YEARS = int(
    os.getenv("BACKTEST_YEARS", "3")
)

# 빗썸 일봉 API 주소입니다.
BITHUMB_DAILY_CANDLE_URL = (
    "https://api.bithumb.com/v1/candles/days"
)

# 조회할 빗썸 마켓입니다.
MARKET = "KRW-USDT"

# 빗썸 API는 한 번에 최대 200개까지 반환합니다.
CANDLE_REQUEST_LIMIT = 200

# 전략 지표 계산에 필요한 과거 일봉 수입니다.
WARMUP_DAYS = max(
    MEAN_PERIOD,
    LONG_MA_PERIOD,
    TREND_PERIOD + TREND_LOOKBACK,
) + 1


def fetch_daily_candles(
    target_date: pd.Timestamp,
) -> pd.DataFrame:
    """목표 날짜까지 빗썸 일봉을 200개씩 반복 조회합니다."""

    all_candles: list[dict] = []
    to_value: str | None = None

    while True:
        params: dict[str, str | int] = {
            "market": MARKET,
            "count": CANDLE_REQUEST_LIMIT,
        }

        # 첫 요청 이후에는 가장 오래된 캔들 이전부터 조회합니다.
        if to_value is not None:
            params["to"] = to_value

        try:
            response = requests.get(
                BITHUMB_DAILY_CANDLE_URL,
                params=params,
                timeout=15,
            )

            response.raise_for_status()
            candles = response.json()

        except (requests.RequestException, ValueError) as error:
            raise RuntimeError(
                f"빗썸 일봉 조회에 실패했습니다: {error}"
            ) from error

        if not isinstance(candles, list):
            raise RuntimeError(
                f"예상하지 못한 API 응답입니다: {candles}"
            )

        if not candles:
            break

        all_candles.extend(candles)

        # API 응답은 최신 날짜부터 과거 날짜 순서입니다.
        oldest_date = pd.Timestamp(
            candles[-1]["candle_date_time_kst"]
        )

        # 필요한 과거 날짜까지 수집했다면 종료합니다.
        if oldest_date <= target_date:
            break

        next_to_value = oldest_date.strftime(
            "%Y-%m-%dT%H:%M:%S"
        )

        # 동일한 날짜가 반복되면 무한 반복을 방지합니다.
        if next_to_value == to_value:
            break

        to_value = next_to_value

        # 연속 요청 사이에 짧은 간격을 둡니다.
        time.sleep(0.15)

    if not all_candles:
        raise RuntimeError(
            "빗썸에서 일봉 데이터를 가져오지 못했습니다."
        )

    df = pd.DataFrame(all_candles)

    # 빗썸 API 필드명을 기존 백테스트 형식으로 변경합니다.
    df = df.rename(
        columns={
            "candle_date_time_kst": "date",
            "opening_price": "open",
            "high_price": "high",
            "low_price": "low",
            "trade_price": "close",
            "candle_acc_trade_volume": "volume",
        }
    )

    df["date"] = pd.to_datetime(df["date"])

    df = (
        df.set_index("date")
        [["open", "high", "low", "close", "volume"]]
        .drop_duplicates()
        .sort_index()
    )

    return df


def load_price_data() -> pd.DataFrame:
    """지표 계산용 데이터와 실제 테스트 데이터를 반환합니다."""

    today = (
        pd.Timestamp.now(tz=TRADING_TIMEZONE)
        .normalize()
        .tz_localize(None)
    )

    start_date = (
        today
        - pd.DateOffset(years=BACKTEST_YEARS)
    )

    # 백테스트 시작 전 지표 계산용 데이터도 함께 조회합니다.
    collection_start_date = (
        start_date
        - pd.Timedelta(days=WARMUP_DAYS + 7)
    )

    df = fetch_daily_candles(
        target_date=collection_start_date,
    )

    df = (
        df.dropna(subset=["close"])
        .sort_index()
        .copy()
    )

    # 오늘 진행 중인 일봉은 제외합니다.
    df = df[df.index < today].copy()

    print(
        f"수집된 일봉     : {len(df):,}개"
    )
    print(
        f"전체 데이터 기간: "
        f"{df.index.min().date()} "
        f"~ {df.index.max().date()}"
    )

    start_index = df.index.searchsorted(start_date)

    if start_index >= len(df):
        raise RuntimeError(
            "백테스트 기간 데이터가 없습니다."
        )

    # 시작일 이전 데이터는 지표 계산에만 사용합니다.
    warmup_index = max(
        0,
        start_index - WARMUP_DAYS,
    )

    calculation_df = df.iloc[warmup_index:].copy()
    test_df = df.iloc[start_index:].copy()

    return calculation_df, test_df


def calculate_sell_value(
    amount: float,
    price: float,
) -> float:
    """수수료와 슬리피지를 반영한 매도금액입니다."""

    sell_price = price * (1 - SLIPPAGE_RATE)

    return (
        amount
        * sell_price
        * (1 - FEE_RATE)
    )


def get_exit_reason(
    signal: str,
    profit_rate: float,
    z_score: float,
    holding_days: int,
) -> str | None:
    """청산 조건을 우선순위대로 확인합니다."""

    if profit_rate <= -MAX_LOSS_RATE:
        return "손절"

    if signal == "SELL":
        return "Z-score 청산"

    if (
        holding_days >= MAX_HOLD_DAYS
        and z_score >= TIME_EXIT_Z_SCORE
    ):
        return "보유기간 청산"

    if holding_days >= FORCE_EXIT_DAYS:
        return "강제청산"

    return None


def calculate_buy_hold_return(
    df: pd.DataFrame,
) -> float:
    """첫날 전액 매수 후 마지막 날 매도한 수익률입니다."""

    first_price = float(
        df["close"].iloc[0]
    )

    last_price = float(
        df["close"].iloc[-1]
    )

    buy_price = (
        first_price
        * (1 + SLIPPAGE_RATE)
    )

    # 매수 수수료를 포함해 전액 사용합니다.
    order_amount = (
        INITIAL_ASSET
        / (1 + FEE_RATE)
    )

    amount = order_amount / buy_price

    final_value = calculate_sell_value(
        amount,
        last_price,
    )

    return (
        final_value / INITIAL_ASSET - 1
    ) * 100


def run_backtest(
    calculation_df: pd.DataFrame,
    test_df: pd.DataFrame,
) -> dict:
    """실시간 전략과 같은 조건으로 백테스트합니다."""

    strategy = MovingAverageStrategy()

    prices: list[float] = []

    cash = INITIAL_ASSET
    position = None

    trades: list[dict] = []
    asset_history = [cash]

    test_start_date = test_df.index[0]

    for current_date, row in calculation_df.iterrows():
        current_price = float(row["close"])
        prices.append(current_price)

        result = strategy.generate_signal(prices)

        # 시작일 이전 데이터는 지표 계산에만 사용합니다.
        if current_date < test_start_date:
            continue

        z_score = result["z_score"]

        # ====================================================
        # 매도
        # ====================================================
        if position is not None and z_score is not None:
            sell_value = calculate_sell_value(
                position["amount"],
                current_price,
            )

            profit = (
                sell_value
                - position["cost"]
            )

            profit_rate = (
                profit
                / position["cost"]
            )

            holding_days = (
                current_date
                - position["buy_date"]
            ).days

            exit_reason = get_exit_reason(
                signal=result["signal"],
                profit_rate=profit_rate,
                z_score=z_score,
                holding_days=holding_days,
            )

            if exit_reason:
                trades.append(
                    {
                        "buy_date": position["buy_date"],
                        "sell_date": current_date,
                        "holding_days": holding_days,
                        "entry_z": position["entry_z"],
                        "exit_z": z_score,
                        "profit": profit,
                        "return_pct": profit_rate * 100,
                        "exit_reason": exit_reason,
                    }
                )

                cash += sell_value
                position = None

        # ====================================================
        # 매수
        # ====================================================
        elif result["signal"] == "BUY":
            order_amount = min(
                cash * BUY_RATIO,
                cash / (1 + FEE_RATE),
            )

            if order_amount >= MIN_ORDER_KRW:
                buy_price = (
                    current_price
                    * (1 + SLIPPAGE_RATE)
                )

                total_cost = (
                    order_amount
                    * (1 + FEE_RATE)
                )

                position = {
                    "amount": order_amount / buy_price,
                    "cost": total_cost,
                    "buy_date": current_date,
                    "entry_z": z_score,
                }

                cash -= total_cost

        # 현재 평가자산을 기록합니다.
        current_asset = cash

        if position is not None:
            current_asset += calculate_sell_value(
                position["amount"],
                current_price,
            )

        asset_history.append(current_asset)

    return summarize_result(
        test_df=test_df,
        trades=trades,
        asset_history=asset_history,
        cash=cash,
        position=position,
    )


def summarize_result(
    test_df: pd.DataFrame,
    trades: list[dict],
    asset_history: list[float],
    cash: float,
    position: dict | None,
) -> dict:
    """최종 자산과 성과 지표를 계산합니다."""

    final_price = float(
        test_df["close"].iloc[-1]
    )

    final_asset = cash
    open_position = None

    # 미청산 포지션도 마지막 가격으로 평가합니다.
    if position is not None:
        sell_value = calculate_sell_value(
            position["amount"],
            final_price,
        )

        profit = (
            sell_value
            - position["cost"]
        )

        final_asset += sell_value

        open_position = {
            "buy_date": position["buy_date"],
            "holding_days": (
                test_df.index[-1]
                - position["buy_date"]
            ).days,
            "profit": profit,
            "return_pct": (
                profit
                / position["cost"]
                * 100
            ),
        }

    trade_df = pd.DataFrame(trades)

    profits = (
        trade_df["profit"]
        if not trade_df.empty
        else pd.Series(dtype="float64")
    )

    wins = profits[profits > 0]
    losses = profits[profits < 0]

    total_win = float(wins.sum())
    total_loss = abs(float(losses.sum()))

    if total_loss > 0:
        profit_factor = (
            total_win / total_loss
        )
    elif total_win > 0:
        profit_factor = float("inf")
    else:
        profit_factor = 0.0

    # 최대 낙폭을 계산합니다.
    assets = pd.Series(
        asset_history,
        dtype="float64",
    )

    drawdown = (
        assets
        / assets.cummax()
        - 1
    )

    total_profit = (
        final_asset
        - INITIAL_ASSET
    )

    profit_rate = (
        total_profit
        / INITIAL_ASSET
        * 100
    )

    test_days = max(
        (
            test_df.index[-1]
            - test_df.index[0]
        ).days,
        1,
    )

    annual_return = (
        (
            final_asset
            / INITIAL_ASSET
        )
        ** (365.25 / test_days)
        - 1
    ) * 100

    trade_count = len(trade_df)

    return {
        "start_date": test_df.index[0],
        "end_date": test_df.index[-1],
        "final_asset": final_asset,
        "profit": total_profit,
        "profit_rate": profit_rate,
        "annual_return": annual_return,
        "mdd": float(drawdown.min()) * 100,
        "buy_hold_return": calculate_buy_hold_return(test_df),
        "trade_count": trade_count,
        "win_rate": (
            len(wins) / trade_count * 100
            if trade_count
            else 0.0
        ),
        "profit_factor": profit_factor,
        "average_profit": (
            float(profits.mean())
            if trade_count
            else 0.0
        ),
        "average_win": (
            float(wins.mean())
            if not wins.empty
            else 0.0
        ),
        "average_loss": (
            float(losses.mean())
            if not losses.empty
            else 0.0
        ),
        "best_trade": (
            float(profits.max())
            if trade_count
            else 0.0
        ),
        "worst_trade": (
            float(profits.min())
            if trade_count
            else 0.0
        ),
        "average_holding_days": (
            float(
                trade_df["holding_days"].mean()
            )
            if trade_count
            else 0.0
        ),
        "exit_reasons": (
            trade_df["exit_reason"]
            .value_counts()
            .to_dict()
            if trade_count
            else {}
        ),
        "open_position": open_position,
        "trades": trade_df,
    }


def print_trade_details(
    trades: pd.DataFrame,
) -> None:
    """완료 거래내역을 표로 출력합니다."""

    if trades.empty:
        print("\n완료된 거래가 없습니다.")
        return

    detail = pd.DataFrame(
        {
            "매수일": pd.to_datetime(
                trades["buy_date"]
            ).dt.strftime("%Y-%m-%d"),

            "매도일": pd.to_datetime(
                trades["sell_date"]
            ).dt.strftime("%Y-%m-%d"),

            "보유일": trades["holding_days"],

            "진입Z": trades["entry_z"].map(
                lambda value: f"{value:.2f}"
            ),

            "청산Z": trades["exit_z"].map(
                lambda value: f"{value:.2f}"
            ),

            "손익": trades["profit"].map(
                lambda value: f"{value:,.2f}원"
            ),

            "수익률": trades["return_pct"].map(
                lambda value: f"{value:.3f}%"
            ),

            "청산사유": trades["exit_reason"],
        }
    )

    print("\n[거래별 상세내역]")
    print(detail.to_string(index=False))


def print_result(result: dict) -> None:
    """백테스트 결과를 출력합니다."""

    profit_factor = result["profit_factor"]

    profit_factor_text = (
        "∞"
        if profit_factor == float("inf")
        else f"{profit_factor:.2f}"
    )

    print("\n================================")
    print("USDT Z-score 평균 회귀 백테스트")
    print("================================")

    print(
        f"기간          : "
        f"{result['start_date'].date()} "
        f"~ {result['end_date'].date()}"
    )

    print(
        f"초기자산      : "
        f"{INITIAL_ASSET:,.2f}원"
    )

    print(
        f"최종자산      : "
        f"{result['final_asset']:,.2f}원"
    )

    print(
        f"총손익        : "
        f"{result['profit']:,.2f}원"
    )

    print(
        f"총수익률      : "
        f"{result['profit_rate']:.2f}%"
    )

    print(
        f"연환산 수익률 : "
        f"{result['annual_return']:.2f}%"
    )

    print(
        f"최대낙폭 MDD  : "
        f"{result['mdd']:.2f}%"
    )

    print(
        f"단순보유 수익률: "
        f"{result['buy_hold_return']:.2f}%"
    )

    print("\n[거래 분석]")

    print(
        f"완료 거래     : "
        f"{result['trade_count']}회"
    )

    print(
        f"승률          : "
        f"{result['win_rate']:.2f}%"
    )

    print(
        f"Profit Factor : "
        f"{profit_factor_text}"
    )

    print(
        f"평균 거래손익 : "
        f"{result['average_profit']:,.2f}원"
    )

    print(
        f"평균 수익     : "
        f"{result['average_win']:,.2f}원"
    )

    print(
        f"평균 손실     : "
        f"{result['average_loss']:,.2f}원"
    )

    print(
        f"최대 수익     : "
        f"{result['best_trade']:,.2f}원"
    )

    print(
        f"최대 손실     : "
        f"{result['worst_trade']:,.2f}원"
    )

    print(
        f"평균 보유기간 : "
        f"{result['average_holding_days']:.1f}일"
    )

    if result["exit_reasons"]:
        reasons = ", ".join(
            f"{reason} {count}회"
            for reason, count
            in result["exit_reasons"].items()
        )

        print(f"청산 사유     : {reasons}")

    position = result["open_position"]

    print(
        "미청산 포지션 : "
        f"{'있음' if position else '없음'}"
    )

    if position:
        print(
            f"  매수일      : "
            f"{position['buy_date'].date()}"
        )

        print(
            f"  보유기간    : "
            f"{position['holding_days']}일"
        )

        print(
            f"  평가손익    : "
            f"{position['profit']:,.2f}원"
        )

        print(
            f"  평가수익률  : "
            f"{position['return_pct']:.3f}%"
        )

    print_trade_details(result["trades"])
    print("================================")


def main() -> None:
    """백테스트를 실행합니다."""

    calculation_df, test_df = load_price_data()

    result = run_backtest(
        calculation_df,
        test_df,
    )

    print_result(result)


if __name__ == "__main__":
    main()