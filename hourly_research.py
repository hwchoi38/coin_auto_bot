import os
import time

import pandas as pd
import requests


# 기존 backtest.py는 수정하지 않습니다.
# 시간봉 데이터 적합성만 관찰하는 별도 연구 파일입니다.

BITHUMB_HOURLY_CANDLE_URL = (
    "https://api.bithumb.com/v1/candles/minutes/60"
)

MARKET = "KRW-USDT"
HOUR_UNIT = 60

# 첫 수집은 최근 90일만 확인합니다.
# 데이터 형식·누락·중복이 정상인 것을 확인한 뒤 기간을 늘립니다.
RESEARCH_DAYS = 90

OUTPUT_FILE = "data/hourly_usdt_krw.csv"


def fetch_hourly_candles() -> pd.DataFrame:
    """빗썸 KRW-USDT 완료 시간봉을 최근 RESEARCH_DAYS만 수집합니다."""
    target_count = RESEARCH_DAYS * 24
    rows = []
    to_time = None

    while len(rows) < target_count:
        params = {
            "market": MARKET,
            "count": 200,
        }

        # 이전 묶음보다 과거 데이터를 계속 요청합니다.
        if to_time is not None:
            params["to"] = to_time

        response = requests.get(
            BITHUMB_HOURLY_CANDLE_URL,
            params=params,
            timeout=10,
        )
        response.raise_for_status()

        batch = response.json()

        if not batch:
            break

        rows.extend(batch)

        # API 응답은 최신 → 과거 순서입니다.
        # 가장 오래된 시각을 다음 요청의 기준 시각으로 사용합니다.
        to_time = batch[-1]["candle_date_time_kst"]

        # 요청 제한을 피하기 위한 짧은 대기입니다.
        time.sleep(0.2)

    df = pd.DataFrame(rows)

    if df.empty:
        raise RuntimeError("시간봉 데이터를 받지 못했습니다.")

    df = df.rename(
        columns={
            "candle_date_time_kst": "datetime",
            "opening_price": "open",
            "high_price": "high",
            "low_price": "low",
            "trade_price": "close",
            "candle_acc_trade_volume": "volume",
        }
    )

    df["datetime"] = pd.to_datetime(df["datetime"])

    # 같은 시간봉이 겹칠 수 있으므로 중복을 제거하고 시간순으로 정렬합니다.
    df = (
        df.drop_duplicates(subset=["datetime"])
        .sort_values("datetime")
        .set_index("datetime")
    )

    # 진행 중인 현재 시간봉은 미래 정보가 될 수 있으므로 제거합니다.
    current_hour = (
        pd.Timestamp.now(tz="Asia/Seoul")
        .floor("h")
        .tz_localize(None)
    )
    df = df[df.index < current_hour]

    return df.tail(target_count)

def analyze_hourly_reversion(df: pd.DataFrame) -> pd.DataFrame:
    """
    24시간 기준 Z-score가 낮아진 사건 뒤,
    1·3·6·12·24시간의 가격 변화와 낙폭을 관찰합니다.
    """
    mean_period = 24
    entry_z_score = -1.5
    horizon_hours = 24

    indicators = df.copy()

    # 최근 24시간 가격을 기준으로 평균과 표준편차를 계산합니다.
    indicators["mean"] = (
        indicators["close"]
        .rolling(mean_period)
        .mean()
    )
    indicators["std"] = (
        indicators["close"]
        .rolling(mean_period)
        .std(ddof=0)
    )
    indicators["z_score"] = (
        (indicators["close"] - indicators["mean"])
        / indicators["std"]
    )

    events = []
    waiting_for_recovery = False

    # 24시간 뒤까지 확인할 수 있는 사건만 사용합니다.
    for i in range(len(indicators) - horizon_hours):
        row = indicators.iloc[i]

        if pd.isna(row["z_score"]):
            continue

        # 같은 하락 구간을 중복 집계하지 않습니다.
        if waiting_for_recovery:
            if row["z_score"] >= 0:
                waiting_for_recovery = False
            continue

        # 최근 24시간 기준으로 충분히 낮은 가격만 기록합니다.
        if row["z_score"] > entry_z_score:
            continue

        future_rows = indicators.iloc[
            i + 1:i + 1 + horizon_hours
        ]

        recovered_rows = future_rows[
            future_rows["z_score"] >= 0
        ]

        if recovered_rows.empty:
            recovery_hours = None
            recovered_within_24h = False
        else:
            recovery_hours = (
                indicators.index.get_loc(recovered_rows.index[0]) - i
            )
            recovered_within_24h = True

        event = {
            "event_datetime": indicators.index[i],
            "entry_price": row["close"],
            "entry_z_score": row["z_score"],
            "recovery_hours": recovery_hours,
            "recovered_within_24h": recovered_within_24h,

            # 사건 이후 24시간 중 추가 하락폭입니다.
            "max_drawdown_24h_pct": (
                (future_rows["low"].min() / row["close"]) - 1
            ) * 100,
        }

        for hours in [1, 3, 6, 12, 24]:
            future_close = future_rows.iloc[hours - 1]["close"]

            event[f"return_{hours}h_pct"] = (
                (future_close / row["close"]) - 1
            ) * 100

        events.append(event)
        waiting_for_recovery = True

    events_df = pd.DataFrame(events)

    events_df.to_csv(
        "data/hourly_reversion_events.csv",
        index=False,
        encoding="utf-8-sig",
    )

    return events_df

def main() -> None:
    df = fetch_hourly_candles()

    os.makedirs("data", exist_ok=True)
    df.to_csv(OUTPUT_FILE, encoding="utf-8-sig")

    print(f"수집된 시간봉 : {len(df):,}개")
    print(
        "전체 기간     : "
        f"{df.index.min():%Y-%m-%d %H:%M} ~ "
        f"{df.index.max():%Y-%m-%d %H:%M}"
    )
    print(f"저장 파일     : {OUTPUT_FILE}")

    # 시간 간격 누락 여부를 확인합니다.
    expected_index = pd.date_range(
        start=df.index.min(),
        end=df.index.max(),
        freq="h",
    )
    missing_count = len(expected_index.difference(df.index))

    print(f"누락 시간봉   : {missing_count:,}개")

        # 시간봉 평균회귀 사건을 관찰용으로만 분석합니다.
    events_df = analyze_hourly_reversion(df)

    print("\n[시간봉 평균회귀 관찰]")

    if events_df.empty:
        print("24시간 뒤까지 확인 가능한 사건이 없습니다.")
        return

    print(f"사건 수        : {len(events_df)}")
    print(
        "3시간 수익 중앙값: "
        f"{events_df['return_3h_pct'].median():.3f}%"
    )
    print(
        "24시간 수익 중앙값: "
        f"{events_df['return_24h_pct'].median():.3f}%"
    )
    print(
        "최저 낙폭 중앙값: "
        f"{events_df['max_drawdown_24h_pct'].median():.3f}%"
    )
    print(
        "최악 낙폭      : "
        f"{events_df['max_drawdown_24h_pct'].min():.3f}%"
    )
    print(
        "24시간 내 회복률: "
        f"{events_df['recovered_within_24h'].mean() * 100:.2f}%"
    )
    print(
        "사건 CSV       : "
        "data/hourly_reversion_events.csv"
    )


if __name__ == "__main__":
    main()