# backtest.py

import os
import time
from io import StringIO

import pandas as pd
import requests

from config.settings import (
    BUY_RATIO,
    ENTRY_Z_SCORE,
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

# 평균회귀 특성 분석 결과를 저장할 경로입니다.
# 이 CSV는 매매 판단에는 사용하지 않는 관찰용 결과입니다.
MEAN_REVERSION_EVENT_FILE = (
    "data/mean_reversion_events.csv"
)

# ECB 기준환율 API입니다. EUR 대비 USD·KRW 환율을 이용해
# 원·달러(KRW/USD) 기준환율을 계산합니다.
ECB_EXCHANGE_RATE_URL = (
    "https://data-api.ecb.europa.eu/service/data/"
    "EXR/D.{currency}.EUR.SP00.A"
)


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


def fetch_usd_krw_rates(
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
) -> pd.DataFrame:
    """ECB 기준환율로 일별 원·달러 환율을 계산합니다."""

    exchange_rates: dict[str, pd.Series] = {}

    for currency in ("USD", "KRW"):
        try:
            response = requests.get(
                ECB_EXCHANGE_RATE_URL.format(
                    currency=currency,
                ),
                params={
                    "startPeriod": start_date.strftime(
                        "%Y-%m-%d"
                    ),
                    "endPeriod": end_date.strftime(
                        "%Y-%m-%d"
                    ),
                    "format": "csvdata",
                },
                timeout=20,
            )

            response.raise_for_status()
            rate_df = pd.read_csv(
                StringIO(response.text)
            )

        except (
            requests.RequestException,
            ValueError,
        ) as error:
            raise RuntimeError(
                f"ECB {currency} 기준환율 조회에 실패했습니다: "
                f"{error}"
            ) from error

        if rate_df.empty or {
            "TIME_PERIOD",
            "OBS_VALUE",
        }.difference(rate_df.columns):
            raise RuntimeError(
                f"ECB {currency} 기준환율 데이터가 비어 있습니다."
            )

        rate_df["date"] = pd.to_datetime(
            rate_df["TIME_PERIOD"]
        )

        exchange_rates[currency] = pd.Series(
            rate_df["OBS_VALUE"].astype(float).to_numpy(),
            index=rate_df["date"],
            name=currency,
        )

    rate_df = pd.concat(
        [
            exchange_rates["USD"].rename("usd_per_eur"),
            exchange_rates["KRW"].rename("krw_per_eur"),
        ],
        axis=1,
    ).dropna()

    # ECB는 EUR당 통화 단위를 제공하므로 나누면 KRW/USD가 됩니다.
    rate_df["usd_krw_rate"] = (
        rate_df["krw_per_eur"]
        / rate_df["usd_per_eur"]
    )

    return rate_df[["usd_krw_rate"]]


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

    # 기준환율은 발표 시점 차이로 미래 정보가 섞이지 않게
    # 직전 이용 가능 영업일 값을 사용합니다.
    usd_krw_df = fetch_usd_krw_rates(
        start_date=df.index.min() - pd.Timedelta(days=7),
        end_date=df.index.max(),
    )

    # ECB 기준환율은 발표일 다음 날부터 사용할 수 있다고 가정합니다.
    # 발표 시점 차이로 미래 정보가 섞이지 않도록 날짜를 하루 뒤로 이동합니다.
    usd_krw_df = usd_krw_df.copy()
    usd_krw_df.index = (
        usd_krw_df.index + pd.Timedelta(days=1)
    )

    df = df.join(usd_krw_df, how="left")

    # 주말·휴일에는 가장 최근 영업일의 기준환율을 유지합니다.
    df["usd_krw_rate"] = df["usd_krw_rate"].ffill()

    # 환율 데이터가 정말 없으면 기간을 조용히 바꾸지 않고 오류를 냅니다.
    if df["usd_krw_rate"].isna().any():
        raise RuntimeError(
            "원·달러 기준환율을 연결하지 못한 일봉이 있습니다."
        )

    # 빗썸 USDT/KRW가 기준환율 대비 얼마나 비싼지·싼지 계산합니다.
    df["premium_pct"] = (
        df["close"] / df["usd_krw_rate"] - 1
    ) * 100

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


def analyze_mean_reversion_events(
    calculation_df: pd.DataFrame,
    test_df: pd.DataFrame,
) -> pd.DataFrame:
    """낮은 Z-score 사건의 이후 회복 특성을 관찰합니다."""

    strategy = MovingAverageStrategy()
    prices: list[float] = []
    premium_values: list[float] = []
    indicators: list[dict] = []

    # 백테스트와 같은 순서로 지표를 계산해 미래 정보를 쓰지 않습니다.
    for current_date, row in calculation_df.iterrows():
        prices.append(float(row["close"]))
        premium_values.append(float(row["premium_pct"]))
        signal_result = strategy.generate_signal(prices)

        # 기존 가격 Z-score와 별도로, 환율 대비 괴리율의 Z-score를 계산합니다.
        # 이 값은 아직 매매 신호에 사용하지 않는 관찰용 지표입니다.
        premium_z_score = None

        if len(premium_values) >= MEAN_PERIOD:
            premium_window = premium_values[-MEAN_PERIOD:]
            premium_mean = (
                sum(premium_window) / MEAN_PERIOD
            )
            premium_variance = sum(
                (value - premium_mean) ** 2
                for value in premium_window
            ) / MEAN_PERIOD
            premium_std = premium_variance ** 0.5

            if premium_std != 0:
                premium_z_score = (
                    premium_values[-1] - premium_mean
                ) / premium_std

        trend_pct = None

        if len(prices) >= TREND_PERIOD + TREND_LOOKBACK:
            current_ma = sum(prices[-TREND_PERIOD:]) / TREND_PERIOD
            previous_ma = (
                sum(
                    prices[
                        -(TREND_PERIOD + TREND_LOOKBACK):
                        -TREND_LOOKBACK
                    ]
                )
                / TREND_PERIOD
            )

            if previous_ma != 0:
                trend_pct = (current_ma / previous_ma - 1) * 100

                # 현재 종가와 3거래일 전 종가를 비교합니다.
        # 저점 진입 직전에도 하락이 이어졌는지 확인하는 관찰용 값입니다.
        recent_3d_return_pct = None

        if len(prices) >= 4:
            recent_3d_return_pct = (
                prices[-1] / prices[-4] - 1
            ) * 100

        # 현재 종가와 7거래일 전 종가를 비교합니다.
        # 단기 하락 추세가 지속 중인지 확인하는 관찰용 값입니다.
        recent_7d_return_pct = None

        if len(prices) >= 8:
            recent_7d_return_pct = (
                prices[-1] / prices[-8] - 1
            ) * 100

        # 최근 7일 종가의 최고·최저 차이입니다.
        # 값이 클수록 최근 가격 변동이 큰 불안정 구간으로 볼 수 있습니다.
        recent_7d_range_pct = None

        if len(prices) >= 7:
            recent_prices = prices[-7:]
            lowest_recent_price = min(recent_prices)

            if lowest_recent_price != 0:
                recent_7d_range_pct = (
                    max(recent_prices)
                    / lowest_recent_price
                    - 1
                ) * 100

        indicators.append(
            {
                "date": current_date,
                "close": float(row["close"]),
                "low": float(row["low"]),
                "z_score": signal_result["z_score"],
                "trend_pct": trend_pct,

                # 원·달러 기준환율 대비 빗썸 USDT/KRW 괴리율 관찰값입니다.
                "usd_krw_rate": float(row["usd_krw_rate"]),
                "premium_pct": float(row["premium_pct"]),
                "premium_z_score": premium_z_score,

                # 아래 세 값은 매매 판단에는 사용하지 않는 관찰용 데이터입니다.
                "recent_3d_return_pct": recent_3d_return_pct,
                "recent_7d_return_pct": recent_7d_return_pct,
                "recent_7d_range_pct": recent_7d_range_pct,
            }
        )

    indicator_df = pd.DataFrame(indicators).set_index("date")
    test_start_date = test_df.index[0]
    test_end_date = test_df.index[-1]
    event_rows: list[dict] = []

    # 저점 사건이 발생한 뒤 평균(Z-score 0 이상)으로 회복할 때까지
    # 같은 하락 국면에서 새 사건을 만들지 않기 위한 상태값입니다.
    waiting_for_recovery = False

    for index, (current_date, row) in enumerate(
        indicator_df.iterrows()
    ):
        z_score = row["z_score"]

        # 워밍업 구간은 지표 계산에만 사용하고, 사건 분석에는 포함하지 않습니다.
        if current_date < test_start_date:
            continue

        # Z-score가 0 이상이면 이전 저점 사건이 평균으로 회복한 것으로 봅니다.
        # 이후 다시 Z-score가 낮아지면 새로운 독립 사건으로 기록할 수 있습니다.
        if z_score is not None and z_score >= 0:
            waiting_for_recovery = False

        is_low = (
            z_score is not None
            and z_score <= ENTRY_Z_SCORE
        )

        # 저점이 아니거나, 이전 저점이 아직 회복되지 않았다면 새 사건을 기록하지 않습니다.
        if not is_low or waiting_for_recovery:
            continue

        # 이번 저점을 새로운 사건으로 기록한 뒤,
        # 평균으로 회복할 때까지 다음 저점 신호를 무시합니다.
        waiting_for_recovery = True

        # 30일 뒤까지 확인 가능한 사건만 분석합니다.
        if current_date + pd.Timedelta(days=30) > test_end_date:
            continue

        future_rows = indicator_df.iloc[index + 1:index + 31]

        if len(future_rows) < 30:
            continue

        # 진입 종가 이후 30일 동안의 최저 일봉 저가입니다.
        lowest_price = future_rows["low"].min()

        trend_pct = row["trend_pct"]

        if trend_pct is None or pd.isna(trend_pct):
            trend_group = "추세 계산 불가"
        elif trend_pct > 0:
            trend_group = "상승·횡보"
        else:
            trend_group = "하락"

        # 회복은 Z-score가 0 이상으로 돌아온 첫 날로 정의합니다.
        recovered = future_rows[
            future_rows["z_score"] >= 0
        ]

        recovery_days = (
            (recovered.index[0] - current_date).days
            if not recovered.empty
            else None
        )

        event = {
            "event_date": current_date,
            "entry_price": row["close"],
            "entry_z": z_score,
            "entry_trend_pct": trend_pct,
            "trend_group": trend_group,
            "usd_krw_rate": row["usd_krw_rate"],
            "premium_pct": row["premium_pct"],
            "premium_z_score": row["premium_z_score"],
            "recovery_days": recovery_days,
            "recovered_within_30d": not recovered.empty,
            "max_drawdown_30d_pct": (
                lowest_price / row["close"] - 1
            ) * 100,

            # 진입 전에 확인 가능한 단기 가격 상태를 CSV에 저장합니다.
            "recent_3d_return_pct": row["recent_3d_return_pct"],
            "recent_7d_return_pct": row["recent_7d_return_pct"],
            "recent_7d_range_pct": row["recent_7d_range_pct"],
        }

        for days in (3, 7, 14, 30):
            future_price = future_rows.iloc[days - 1]["close"]
            event[f"return_{days}d_pct"] = (
                future_price / row["close"] - 1
            ) * 100

        event_rows.append(event)

    return pd.DataFrame(event_rows)


def analyze_premium_events(calculation_df, analysis_df) -> pd.DataFrame:
    """
    환율 대비 프리미엄의 평균회귀 성질만 관찰합니다.
    기존 매매 규칙이나 가격 Z-score 이벤트 분석은 변경하지 않습니다.
    """
    premium_entry_z_score = -1.5  # 관찰용 사전 기준값
    horizon_days = 30

    indicators = calculation_df.copy()

    # 프리미엄 자체의 최근 평균과 표준편차를 계산합니다.
    indicators["premium_mean"] = (
        indicators["premium_pct"]
        .rolling(MEAN_PERIOD)
        .mean()
    )
    indicators["premium_std"] = (
        indicators["premium_pct"]
        .rolling(MEAN_PERIOD)
        .std(ddof=0)
    )

    indicators["premium_z_score"] = (
        (indicators["premium_pct"] - indicators["premium_mean"])
        / indicators["premium_std"]
    )

    # 분석 구간에 필요한 지표를 붙입니다.
    analysis_df = analysis_df.copy()
    analysis_df = analysis_df.join(
        indicators[["premium_z_score"]],
        how="left",
    )

    events = []
    waiting_for_recovery = False

    # 미래 30일 성과를 계산할 수 있는 사건만 사용합니다.
    for i in range(len(analysis_df) - horizon_days):
        row = analysis_df.iloc[i]

        if pd.isna(row["premium_z_score"]):
            continue

        # 한 번 사건이 발생하면 프리미엄 Z-score가 0 이상이 될 때까지
        # 같은 하락 구간을 중복 집계하지 않습니다.
        if waiting_for_recovery:
            if row["premium_z_score"] >= 0:
                waiting_for_recovery = False
            continue

        # 프리미엄이 평소보다 충분히 낮은 날을 사건으로 기록합니다.
        if row["premium_z_score"] > premium_entry_z_score:
            continue

        future_rows = analysis_df.iloc[i + 1:i + 1 + horizon_days]

        # 30일 안에 프리미엄이 평균 수준(Z-score 0 이상)으로
        # 회복한 첫 시점을 찾습니다.
        recovered_rows = future_rows[
            future_rows["premium_z_score"] >= 0
        ]

        if recovered_rows.empty:
            recovery_days = None
            recovered_within_30d = False
        else:
            first_recovery_index = recovered_rows.index[0]
            recovery_days = (
                analysis_df.index.get_loc(first_recovery_index) - i
            )
            recovered_within_30d = True

        event = {
            "event_date": analysis_df.index[i].date(),
            "entry_price": row["close"],
            "entry_premium_pct": row["premium_pct"],
            "entry_premium_z_score": row["premium_z_score"],
            "recovery_days": recovery_days,
            "recovered_within_30d": recovered_within_30d,

            # 사건 뒤 USDT/KRW 가격이 얼마나 더 내려갔는지 확인합니다.
            "max_price_drawdown_30d_pct": (
                (future_rows["low"].min() / row["close"]) - 1
            ) * 100,
        }

        # 프리미엄 변화는 수익률이 아니라 %p(퍼센트포인트)입니다.
        for days in [3, 7, 14, 30]:
            future_premium = future_rows.iloc[days - 1]["premium_pct"]

            event[f"premium_change_{days}d_pct_point"] = (
                future_premium - row["premium_pct"]
            )

            # 프리미엄 회복 중 실제 USDT 가격이 어떻게 움직였는지도 기록합니다.
            future_close = future_rows.iloc[days - 1]["close"]

            event[f"price_return_{days}d_pct"] = (
                (future_close / row["close"]) - 1
            ) * 100

        events.append(event)

        # 이번 사건이 평균으로 회복될 때까지 다음 사건을 막습니다.
        waiting_for_recovery = True

    events_df = pd.DataFrame(events)

    os.makedirs("data", exist_ok=True)
    events_df.to_csv(
        "data/premium_reversion_events_development.csv",
        index=False,
        encoding="utf-8-sig",
    )

    return events_df

def print_premium_report(events_df: pd.DataFrame) -> None:
    """개발 구간의 프리미엄 평균회귀 관찰 결과를 출력합니다."""
    print("\n================================")
    print("[프리미엄 평균회귀 관찰 - 개발 구간]")
    print("================================")

    if events_df.empty:
        print("관찰 사건이 없습니다.")
        return

    recovery_rate = (
        events_df["recovered_within_30d"].mean() * 100
    )

    summary = pd.DataFrame(
        [{
            "사건수": len(events_df),
            "3일후 중앙값(%p)": (
                events_df["premium_change_3d_pct_point"].median()
            ),
            "7일후 중앙값(%p)": (
                events_df["premium_change_7d_pct_point"].median()
            ),
            "14일후 중앙값(%p)": (
                events_df["premium_change_14d_pct_point"].median()
            ),
            "30일후 중앙값(%p)": (
                events_df["premium_change_30d_pct_point"].median()
            ),
            "최저 가격낙폭 중앙값(%)": (
                events_df["max_price_drawdown_30d_pct"].median()
            ),
            "최악 가격낙폭(%)": (
                events_df["max_price_drawdown_30d_pct"].min()
            ),
            "평균 회복일": (
                events_df["recovery_days"].mean()
            ),
            "30일내 회복률(%)": recovery_rate,
            "30일내 미회복": (
                (~events_df["recovered_within_30d"]).sum()
            ),
        }]
    )

    print(summary.to_string(index=False))
    print(
        "\n프리미엄 사건 CSV: "
        "data/premium_reversion_events_development.csv"
    )

def print_mean_reversion_report(
    events: pd.DataFrame,
) -> None:
    """추세별 평균회귀 사건의 수익률과 회복 기간을 출력합니다."""

    print("\n[평균회귀 특성 관찰]")

    if events.empty:
        print("30일 뒤까지 확인 가능한 저점 사건이 없습니다.")
        return

    report = (
        events.groupby("trend_group")
        .agg(
            사건수=("event_date", "size"),
            **{
                "3일후 평균": ("return_3d_pct", "mean"),
                "7일후 평균": ("return_7d_pct", "mean"),
                "14일후 평균": ("return_14d_pct", "mean"),
                "30일후 평균": ("return_30d_pct", "mean"),

                # 평균보다 극단적인 수익 사건의 영향을 덜 받는 기준입니다.
                "30일후 중앙값": ("return_30d_pct", "median"),

                # 각 사건에서 평균적으로 얼마나 더 하락했는지 확인합니다.
                "최저 낙폭 중앙값": (
                    "max_drawdown_30d_pct",
                    "median",
                ),

                # 저위험 전략에서 특히 중요한 최악의 추가 하락입니다.
                "최악 낙폭": (
                    "max_drawdown_30d_pct",
                    "min",
                ),

                "평균 회복일": ("recovery_days", "mean"),
                "30일내 회복률": (
                    "recovered_within_30d",
                    "mean",
                ),

                # True는 회복, False는 미회복이므로 False 건수를 셉니다.
                "30일내 미회복": (
                    "recovered_within_30d",
                    lambda values: int((~values).sum()),
                ),
            },
        )
        .reset_index()
    )

    report["30일내 회복률"] *= 100

    for column in (
        "3일후 평균",
        "7일후 평균",
        "14일후 평균",
        "30일후 평균",
        "30일후 중앙값",
        "최저 낙폭 중앙값",
        "최악 낙폭",
        "평균 회복일",
        "30일내 회복률",
     ):
        report[column] = report[column].map(
            lambda value: f"{value:.2f}"
            if pd.notna(value)
            else "-"
        )

    print(report.to_string(index=False))


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

    # ====================================================
    # 시간 순서 70% / 15% / 15% 구간 검증
    # ====================================================
    # 전략 설정값은 바꾸지 않고, 같은 규칙이 각 시기에도 유지되는지 확인합니다.
    total_count = len(test_df)

    development_end = int(total_count * 0.70)
    validation_end = int(total_count * 0.85)

    development_df = test_df.iloc[:development_end].copy()
    validation_df = test_df.iloc[
        development_end:validation_end
    ].copy()
    final_test_df = test_df.iloc[validation_end:].copy()

    test_segments = [
        ("개발 구간 70%", development_df),
        ("검증 구간 15%", validation_df),
        ("최종 시험 구간 15%", final_test_df),
    ]

    for segment_name, segment_test_df in test_segments:
        # 해당 구간 종료일 이후의 데이터는 제거합니다.
        # 미래 데이터를 보지 않도록 지표 계산용 데이터도 여기까지 제한합니다.
        segment_calculation_df = calculation_df[
            calculation_df.index <= segment_test_df.index[-1]
        ].copy()

        segment_result = run_backtest(
            segment_calculation_df,
            segment_test_df,
        )

        print("\n================================")
        print(f"[{segment_name}]")
        print("================================")

        print_result(segment_result)

    # 실제 매수 여부와 무관한 저점 사건을 별도로 분석합니다.
    # 따라서 아래 분석은 기존 백테스트 매매 결과에 영향을 주지 않습니다.
    events = analyze_mean_reversion_events(
        calculation_df,
        test_df,
    )

    os.makedirs(
        os.path.dirname(MEAN_REVERSION_EVENT_FILE),
        exist_ok=True,
    )

    events.to_csv(
        MEAN_REVERSION_EVENT_FILE,
        index=False,
        encoding="utf-8-sig",
        date_format="%Y-%m-%d",
    )

    print_mean_reversion_report(events)
    print(
        f"\n평균회귀 사건 CSV: "
        f"{MEAN_REVERSION_EVENT_FILE}"
    )

    # 프리미엄 가설은 개발 구간 70%에서만 먼저 관찰합니다.
    # 검증·최종 시험 구간을 미리 보지 않아 과최적화를 막습니다.
    premium_events = analyze_premium_events(
        development_df,
        development_df,
    )

    print_premium_report(premium_events)


if __name__ == "__main__":
    main()
