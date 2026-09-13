
# config/settings.py

import os

from dotenv import load_dotenv


load_dotenv()


def env(name: str, default: str = "") -> str:
    """환경변수를 문자열로 읽습니다."""
    return os.getenv(name, default).strip()


def env_bool(name: str, default: str = "false") -> bool:
    """환경변수를 bool 값으로 변환합니다."""
    return env(name, default).lower() in {
        "1",
        "true",
        "yes",
        "y",
        "on",
    }


def env_optional_float(name: str) -> float | None:
    """값이 없으면 None, 있으면 float로 반환합니다."""
    value = env(name)

    if value.upper() in {"", "NONE", "NULL"}:
        return None

    return float(value)


# ============================================================
# 빗썸 API
# ============================================================

BITHUMB_API_KEY = env("BITHUMB_API_KEY")
BITHUMB_SECRET_KEY = env("BITHUMB_SECRET_KEY")

BITHUMB_PUBLIC_API_URL = (
    "https://api.bithumb.com/public"
)


# ============================================================
# 거래 환경
# ============================================================

# PAPER: 모의매매, REAL: 실거래
TRADING_MODE = env(
    "TRADING_MODE",
    "PAPER",
).upper()

REAL_TRADING_ENABLED = env_bool(
    "REAL_TRADING_ENABLED"
)

MARKET = env(
    "MARKET",
    "USDT_KRW",
).upper()

TRADING_TIMEZONE = env(
    "TRADING_TIMEZONE",
    "Asia/Seoul",
)


# ============================================================
# 전략 설정
# ============================================================

# 기존 이동평균 코드 호환용 설정입니다.
SHORT_MA_PERIOD = int(
    env("SHORT_MA_PERIOD", "5")
)

LONG_MA_PERIOD = int(
    env("LONG_MA_PERIOD", "20")
)

# Z-score 계산 기간입니다.
MEAN_PERIOD = int(
    env("MEAN_PERIOD", "20")
)

# 평균보다 충분히 하락했을 때 매수합니다.
ENTRY_Z_SCORE = float(
    env("ENTRY_Z_SCORE", "-1.8")
)

# 평균 위로 조금 회복하면 매도합니다.
EXIT_Z_SCORE = float(
    env("EXIT_Z_SCORE", "0.2")
)

# 일정 기간 회복하지 않으면 청산합니다.
MAX_HOLD_DAYS = int(
    env("MAX_HOLD_DAYS", "20")
)

FORCE_EXIT_DAYS = int(
    env("FORCE_EXIT_DAYS", "30")
)

TIME_EXIT_Z_SCORE = float(
    env("TIME_EXIT_Z_SCORE", "-0.5")
)

# 거래비용을 제외한 최소 예상수익률입니다.
# 0.002는 0.2%입니다.
MIN_PROFIT_BUFFER = float(
    env("MIN_PROFIT_BUFFER", "0.002")
)

# 장기 하락 추세를 확인합니다.
TREND_PERIOD = int(
    env("TREND_PERIOD", "60")
)

TREND_LOOKBACK = int(
    env("TREND_LOOKBACK", "5")
)

# 장기 이동평균의 0.2% 하락까지 허용합니다.
TREND_MAX_DROP = float(
    env("TREND_MAX_DROP", "0.002")
)

# 실시간 전략에서 조회할 완료 일봉 수입니다.
CANDLE_COUNT = int(
    env("CANDLE_COUNT", "80")
)


# ============================================================
# 자금 및 주문
# ============================================================

INITIAL_CASH = float(
    env("INITIAL_CASH", "50000")
)

# 기존 모의매매 코드 호환용 고정 주문 금액입니다.
ORDER_AMOUNT_KRW = float(
    env("ORDER_AMOUNT_KRW", "5000")
)

MIN_ORDER_KRW = float(
    env("MIN_ORDER_KRW", "5000")
)

# 현금의 40%만 매수하여 위험을 제한합니다.
BUY_RATIO = float(
    env("BUY_RATIO", "0.40")
)

# 매도 시 보유 수량 전체를 매도합니다.
SELL_RATIO = float(
    env("SELL_RATIO", "1.0")
)


# ============================================================
# 위험 관리
# ============================================================

# 매수 비용 대비 2.5% 손실이면 청산합니다.
MAX_LOSS_RATE = float(
    env("MAX_LOSS_RATE", "0.025")
)

# 비어 있으면 고정 익절을 사용하지 않습니다.
TAKE_PROFIT_RATE = env_optional_float(
    "TAKE_PROFIT_RATE"
)

# 비어 있으면 트레일링 스톱을 사용하지 않습니다.
TRAILING_STOP_RATE = env_optional_float(
    "TRAILING_STOP_RATE"
)


# ============================================================
# 거래 비용
# ============================================================

FEE_RATE = float(
    env("FEE_RATE", "0.0005")
)

SLIPPAGE_RATE = float(
    env("SLIPPAGE_RATE", "0.0002")
)


# ============================================================
# 설정값 검증
# ============================================================

def validate_rate(
    name: str,
    value: float | None,
    allow_none: bool = False,
) -> None:
    """비율이 0 이상 1 미만인지 확인합니다."""

    if value is None:
        if allow_none:
            return

        raise ValueError(
            f"{name} 값이 필요합니다."
        )

    if not 0 <= value < 1:
        raise ValueError(
            f"{name}은 0 이상 1 미만이어야 합니다."
        )


if TRADING_MODE not in {"PAPER", "REAL"}:
    raise ValueError(
        "TRADING_MODE는 PAPER 또는 REAL이어야 합니다."
    )

if MARKET != "USDT_KRW":
    raise ValueError(
        "MARKET은 USDT_KRW여야 합니다."
    )

if TRADING_MODE == "REAL":
    if not REAL_TRADING_ENABLED:
        raise ValueError(
            "실거래를 사용하려면 "
            "REAL_TRADING_ENABLED=true가 필요합니다."
        )

    if not BITHUMB_API_KEY or not BITHUMB_SECRET_KEY:
        raise ValueError(
            "REAL 모드에서는 빗썸 API Key가 필요합니다."
        )

if not 0 < SHORT_MA_PERIOD < LONG_MA_PERIOD:
    raise ValueError(
        "이동평균 기간은 0 < 단기 < 장기여야 합니다."
    )

if MEAN_PERIOD < 2:
    raise ValueError(
        "MEAN_PERIOD는 2 이상이어야 합니다."
    )

if TREND_PERIOD < 2:
    raise ValueError(
        "TREND_PERIOD는 2 이상이어야 합니다."
    )

if TREND_LOOKBACK < 1:
    raise ValueError(
        "TREND_LOOKBACK은 1 이상이어야 합니다."
    )

required_candles = max(
    LONG_MA_PERIOD,
    MEAN_PERIOD,
    TREND_PERIOD + TREND_LOOKBACK,
) + 1

if CANDLE_COUNT < required_candles:
    raise ValueError(
        f"CANDLE_COUNT는 최소 "
        f"{required_candles}개가 필요합니다."
    )

if ENTRY_Z_SCORE >= EXIT_Z_SCORE:
    raise ValueError(
        "ENTRY_Z_SCORE는 "
        "EXIT_Z_SCORE보다 작아야 합니다."
    )

if not 0 < MAX_HOLD_DAYS <= FORCE_EXIT_DAYS:
    raise ValueError(
        "보유기간은 0 < MAX_HOLD_DAYS "
        "<= FORCE_EXIT_DAYS여야 합니다."
    )

if INITIAL_CASH <= 0:
    raise ValueError(
        "INITIAL_CASH는 0보다 커야 합니다."
    )

if ORDER_AMOUNT_KRW <= 0:
    raise ValueError(
        "ORDER_AMOUNT_KRW는 0보다 커야 합니다."
    )

if MIN_ORDER_KRW <= 0:
    raise ValueError(
        "MIN_ORDER_KRW는 0보다 커야 합니다."
    )

if not 0 < BUY_RATIO <= 1:
    raise ValueError(
        "BUY_RATIO는 0보다 크고 1 이하여야 합니다."
    )

if not 0 < SELL_RATIO <= 1:
    raise ValueError(
        "SELL_RATIO는 0보다 크고 1 이하여야 합니다."
    )

for name, value in {
    "MAX_LOSS_RATE": MAX_LOSS_RATE,
    "MIN_PROFIT_BUFFER": MIN_PROFIT_BUFFER,
    "TREND_MAX_DROP": TREND_MAX_DROP,
    "FEE_RATE": FEE_RATE,
    "SLIPPAGE_RATE": SLIPPAGE_RATE,
}.items():
    validate_rate(name, value)

validate_rate(
    "TAKE_PROFIT_RATE",
    TAKE_PROFIT_RATE,
    allow_none=True,
)

validate_rate(
    "TRAILING_STOP_RATE",
    TRAILING_STOP_RATE,
    allow_none=True,
)

