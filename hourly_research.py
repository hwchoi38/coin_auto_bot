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

# 일봉 연구와 같은 약 3년 범위를 확보합니다.
# 시간봉 규칙은 아직 바꾸지 않습니다.
RESEARCH_DAYS = 365 * 3

OUTPUT_FILE = "data/hourly_usdt_krw.csv"

DEVELOPMENT_RATIO = 0.70
VALIDATION_RATIO = 0.15


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

    indicators = df.asfreq("h").copy()

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

        if future_rows[["close", "low"]].isna().any().any():
            continue

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
            "observation_end_datetime": indicators.index[
                i + horizon_hours
            ],
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
    # 시간봉 사이의 누락 시각을 확인합니다.
    missing_hours = expected_index.difference(df.index)
    missing_count = len(missing_hours)

    print(f"누락 시간봉   : {missing_count:,}개")

    # 누락이 있으면 정확한 시각을 별도 CSV로 저장합니다.
    # 이후 분석에서 데이터 공백이 특정 기간에 몰렸는지 확인합니다.
    if missing_count > 0:
        missing_df = pd.DataFrame(
            {"missing_datetime": missing_hours}
        )

        missing_df.to_csv(
            "data/hourly_missing_candles.csv",
            index=False,
            encoding="utf-8-sig",
        )

        print(
            "누락 목록     : "
            "data/hourly_missing_candles.csv"
        )
    # 전체 시간 순서 기준으로 개발 70%, 검증 15%, 최종 시험 15%를 나눕니다.
    total_count = len(df)
    development_end_position = int(total_count * DEVELOPMENT_RATIO)
    validation_end_position = int(
        total_count * (DEVELOPMENT_RATIO + VALIDATION_RATIO)
    )

    # 개발 구간이 끝나는 시각입니다.
    validation_start_datetime = df.index[
        development_end_position
    ]

    # 최종 시험 구간 시작 시각입니다.
    # 이 시각 이후 데이터는 아직 사건 분석에 사용하지 않습니다.
    test_start_datetime = df.index[
        validation_end_position
    ]

    # 개발 + 검증(앞 85%)만 분석합니다.
    # 최종 시험 15%는 아직 보지 않는다는 원칙을 지킵니다.
    development_validation_df = df.iloc[
        :validation_end_position
    ]

    events_df = analyze_hourly_reversion(
        development_validation_df
    )

    print("\n[시간봉 평균회귀 개발·검증 관찰]")
    print(
        "개발 구간     : "
        f"{df.index.min():%Y-%m-%d %H:%M} ~ "
        f"{validation_start_datetime:%Y-%m-%d %H:%M}"
    )
    print(
        "검증 구간     : "
        f"{validation_start_datetime:%Y-%m-%d %H:%M} ~ "
        f"{test_start_datetime:%Y-%m-%d %H:%M}"
    )
    print("최종 시험 구간: 아직 확인하지 않음")

    if events_df.empty:
        print("24시간 뒤까지 확인 가능한 사건이 없습니다.")
        return

    # 개발 구간 종료 전까지 24시간 관찰이 끝난 사건만 사용합니다.
    development_events = events_df[
        events_df["observation_end_datetime"]
        < validation_start_datetime
    ]

        # 검증 구간에서 시작하고 검증 구간 안에서 24시간 관찰까지 끝난 사건만 사용합니다.
    validation_events = events_df[
        (events_df["event_datetime"] >= validation_start_datetime)
        & (
            events_df["observation_end_datetime"]
            < test_start_datetime
        )
    ]

    # 개발·검증 통계를 같은 기준으로 출력합니다.
    for segment_name, segment_events in [
        ("개발", development_events),
        ("검증", validation_events),
    ]:
        print(f"\n[{segment_name} 구간]")

        if segment_events.empty:
            print("관찰 가능한 사건이 없습니다.")
            continue

        print(f"사건 수              : {len(segment_events)}")

        for hours in [1, 3, 6, 12, 24]:
            median_return = segment_events[
                f"return_{hours}h_pct"
            ].median()

            print(
                f"{hours:>2}시간 수익 중앙값    : "
                f"{median_return:.3f}%"
            )

        print(
            "24시간 낙폭 중앙값    : "
            f"{segment_events['max_drawdown_24h_pct'].median():.3f}%"
        )
        print(
            "최악 낙폭             : "
            f"{segment_events['max_drawdown_24h_pct'].min():.3f}%"
        )
        print(
            "-3% 초과 낙폭 건수    : "
            f"{(segment_events['max_drawdown_24h_pct'] < -3.0).sum()}건"
        )
        print(
            "24시간 내 회복률      : "
            f"{segment_events['recovered_within_24h'].mean() * 100:.2f}%"
        )

    print(
        "\n사건 CSV       : "
        "data/hourly_reversion_events.csv"
    )
        
    


if __name__ == "__main__":
    main()