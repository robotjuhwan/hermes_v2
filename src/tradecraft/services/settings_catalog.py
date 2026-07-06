from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from tradecraft.config import AppSettings


@dataclass(frozen=True)
class SettingMeta:
    label: str = ""
    description: str = ""
    category: str = ""
    input_type: str = ""
    choices: tuple[str, ...] = ()
    min_value: float | None = None
    max_value: float | None = None
    step: float | None = None
    risk: str = "normal"
    editable: bool | None = None


CATEGORY_LABELS: dict[str, str] = {
    "ai": "AI/LLM",
    "trading": "쥬 블록 트레이딩",
    "market": "장중 판단/시장",
    "memory": "메모리/성장 루프",
    "research": "리서치/RAG/리포트",
    "signals": "시그널/밸류/ETF",
    "kis": "KIS/거래소 연결",
    "ops": "운영/보안/스토리지",
    "legacy": "레거시/포트폴리오",
    "advanced": "고급/기타",
}

SECRET_KEYWORDS = (
    "secret",
    "token",
    "access_key",
    "api_key",
    "app_key",
    "account_no",
    "chat_id",
)
SECRET_EXCEPTIONS = {
    "llm_usage_db_path",
    "kis_rate_limit_db_path",
}
LOCKED_FIELDS = {
    "admin_token",
    "admin_tokens",
    "telegram_bot_token",
    "telegram_chat_id",
    "telegram_webhook_secret",
    "upbit_access_key",
    "upbit_secret_key",
    "bithumb_access_key",
    "bithumb_secret_key",
    "binance_spot_api_key",
    "binance_spot_api_secret",
    "binance_futures_api_key",
    "binance_futures_api_secret",
    "kis_primary_app_key",
    "kis_primary_app_secret",
    "kis_primary_account_no",
    "kis_secondary_app_key",
    "kis_secondary_app_secret",
    "kis_secondary_account_no",
    "reports_api_token",
    "reports_api_tokens",
}
ONE_SHOT_FIELDS = {
    "intelligence_once",
    "binance_block_trader_once",
    "crypto_alpha_once",
    "crypto_market_research_once",
    "kis_block_trader_once",
    "investment_memory_once",
    "market_judge_once",
    "market_pulse_once",
    "strategy_insight_once",
    "watchdog_once",
}
HIGH_RISK_FIELDS = {
    "binance_block_trader_execute_spot_orders",
    "binance_block_trader_execute_futures_orders",
    "binance_block_trader_execute_upbit_orders",
    "kis_block_trader_execute_orders",
    "reports_ui_trust_proxy",
    "rag_allow_legacy_pickle_migration",
}
RETIRED_PREFIXES = ("kis_trader_",)
WARNING_FIELDS = {
    "allow_origins",
    "kis_base_url",
    "upbit_base_url",
    "bithumb_base_url",
    "binance_spot_base_url",
    "binance_futures_base_url",
    "reports_ui_allowed_cidrs",
}

META: dict[str, SettingMeta] = {
    "codex_runtime_mode_preference": SettingMeta(
        "Codex native runtime 모드",
        "HERMES 판단 LLM은 Codex native SDK를 기본 경로로 사용합니다. command/url 값은 레거시 호환용입니다.",
        "ai",
        choices=("sdk", "none"),
    ),
    "llm_model": SettingMeta(
        "LLM 모델",
        "쥬/리서치/메모리 판단에 쓰는 기본 모델입니다.",
        "ai",
        choices=("gpt-5.5", "gpt-5.4", "gpt-5.4-mini", "gpt-5.2"),
    ),
    "llm_reasoning_effort": SettingMeta(
        "추론 강도",
        "gpt 계열 판단의 reasoning effort입니다. 쥬 기본값은 xhigh입니다.",
        "ai",
        choices=("low", "medium", "high", "xhigh"),
    ),
    "codex_runtime_timeout_ms": SettingMeta(
        "LLM 타임아웃(ms)",
        "Codex native runtime 응답을 기다리는 최대 시간입니다. gpt-5.5 xhigh 장중 판단은 길게 기다립니다.",
        "ai",
        min_value=1000,
        max_value=600000,
        step=1000,
    ),
    "codex_runtime_sdk_codex_bin": SettingMeta(
        "Codex SDK 런타임",
        "openai-codex SDK가 사용할 Codex app-server 바이너리 경로입니다. 비워두면 macOS Codex.app 런타임을 우선 사용합니다.",
        "ai",
    ),
    "codex_native_thread_mode": SettingMeta(
        "Codex native thread 모드",
        "쥬 판단 호출이 fresh thread를 쓸지, 일별/지속 thread를 재사용할지 정합니다.",
        "ai",
        choices=("ephemeral", "daily", "persistent"),
    ),
    "codex_native_compact_after_turns": SettingMeta(
        "Codex thread 압축 기준",
        "native thread에 이 횟수만큼 turn이 누적되면 compact를 시도합니다.",
        "ai",
        min_value=1,
        max_value=100,
        step=1,
    ),
    "codex_native_read_turns": SettingMeta(
        "Codex thread read 기록",
        "디버깅을 위해 thread.read 스냅샷을 turn 메타데이터에 남깁니다.",
        "ai",
        input_type="toggle",
    ),
    "codex_native_account_check_interval_sec": SettingMeta(
        "Codex 계정 점검 간격",
        "native readiness 계정 확인 결과를 갱신하는 최소 간격입니다.",
        "ai",
        min_value=60,
        max_value=86400,
        step=60,
    ),
    "codex_native_model_check_interval_sec": SettingMeta(
        "Codex 모델 점검 간격",
        "native readiness 모델 목록 확인 결과를 갱신하는 최소 간격입니다.",
        "ai",
        min_value=60,
        max_value=86400,
        step=60,
    ),
    "codex_native_developer_instructions_enabled": SettingMeta(
        "Codex developer instructions",
        "Jue 페르소나, workflow 권한, 안전 게이트를 native developer_instructions로 주입합니다.",
        "ai",
        input_type="toggle",
    ),
    "llm_usage_enabled": SettingMeta(
        "LLM 사용량 기록",
        "쥬/리서치/메모리 LLM 호출 통계를 DB에 남깁니다.",
        "ai",
    ),
    "llm_usage_db_path": SettingMeta(
        "LLM 사용량 DB",
        "LLM 호출 통계 저장 경로입니다.",
        "ai",
    ),
    "kis_block_trader_enabled": SettingMeta(
        "블록 트레이더 활성화",
        "쥬 블록 매매 루프를 켤지 결정합니다.",
        "trading",
    ),
    "kis_block_trader_execute_orders": SettingMeta(
        "KIS 실주문 실행",
        "켜면 안전 게이트를 통과한 블록 주문이 실제 KIS 주문으로 나갑니다.",
        "trading",
        risk="danger",
    ),
    "kis_block_trader_rule_interval_sec": SettingMeta(
        "룰 엔진 주기(초)",
        "오픈/대기 블록의 목표가·손절가·매수대기 조건을 확인하는 주기입니다.",
        "trading",
        min_value=3,
        max_value=300,
        step=1,
    ),
    "kis_block_trader_manager_interval_sec": SettingMeta(
        "쥬 판단 주기(초)",
        "장중 LLM 블록 매니저가 새 판단을 수행하는 간격입니다.",
        "trading",
        min_value=300,
        max_value=14400,
        step=60,
    ),
    "kis_block_trader_manager_error_retry_sec": SettingMeta(
        "KIS 판단 실패 재시도(초)",
        "쥬 판단이 스키마·타임아웃·프롬프트 예산 문제로 실패했을 때 정규 판단 주기 전에 다시 시도하는 최소 쿨다운입니다.",
        "ops",
        min_value=60,
        max_value=1800,
        step=60,
        risk="medium",
    ),
    "kis_block_trader_retention_interval_sec": SettingMeta(
        "KIS DB 정리 주기(초)",
        "KIS 블록 트레이더 quote/판단 로그 보존 정리를 실행할 최소 간격입니다.",
        "ops",
        min_value=60,
        max_value=86400,
        step=60,
        risk="low",
    ),
    "kis_block_trader_aggressive_limit_bps": SettingMeta(
        "공격적 지정가 bps",
        "매수/매도 주문가를 현재가에서 얼마나 공격적으로 보낼지 정합니다.",
        "trading",
        min_value=0,
        max_value=300,
        step=1,
    ),
    "kis_block_trader_max_manager_symbols": SettingMeta(
        "판단 대상 종목 수",
        "한 번의 쥬 판단에 넣는 최대 종목 수입니다.",
        "trading",
        min_value=1,
        max_value=80,
        step=1,
    ),
    "kis_block_trader_prompt_target_chars": SettingMeta(
        "KIS 판단 입력 정상 목표(chars)",
        "쥬 KIS 매니저 입력이 평소 이 크기 안에 들어오도록 압축합니다.",
        "ai",
        min_value=20_000,
        max_value=300_000,
        step=5_000,
    ),
    "kis_block_trader_prompt_warn_chars": SettingMeta(
        "KIS 판단 입력 경고선(chars)",
        "쥬 KIS 매니저 입력이 이 값을 넘으면 과대 입력으로 기록합니다.",
        "ai",
        min_value=20_000,
        max_value=400_000,
        step=5_000,
    ),
    "kis_block_trader_prompt_max_chars": SettingMeta(
        "KIS 판단 입력 상한(chars)",
        "쥬 KIS 매니저 입력의 하드 상한입니다. 넘지 않도록 더 강하게 압축합니다.",
        "ai",
        min_value=30_000,
        max_value=500_000,
        step=5_000,
    ),
    "kis_block_trader_manager_query": SettingMeta(
        "쥬 매니저 지시문",
        "블록 매니저 호출 시 기본 목적 문장입니다.",
        "trading",
        input_type="textarea",
    ),
    "kis_block_trader_quote_retention_days": SettingMeta(
        "KIS 시세 보존일",
        "KIS 블록 트레이더의 고빈도 quote snapshot을 보존할 일수입니다.",
        "ops",
        min_value=1,
        max_value=365,
        step=1,
    ),
    "kis_block_trader_reconciliation_retention_days": SettingMeta(
        "KIS 잔고 대조 보존일",
        "KIS 블록 원장과 실제 잔고 reconciliation 실행 로그를 보존할 일수입니다.",
        "ops",
        min_value=1,
        max_value=365,
        step=1,
    ),
    "kis_block_trader_manager_run_retention_days": SettingMeta(
        "KIS 판단 로그 보존일",
        "쥬 KIS 매니저 prompt/response 실행 로그를 보존할 일수입니다.",
        "ops",
        min_value=1,
        max_value=365,
        step=1,
    ),
    "kis_block_trader_archive_retention_days": SettingMeta(
        "KIS archive 최종 보존일",
        "KIS quote/manager/reconciliation archive 테이블의 최종 보존 기간입니다. 블록과 주문 원장은 삭제하지 않습니다.",
        "ops",
        min_value=1,
        max_value=365,
        step=1,
    ),
    "block_horizon_targets": SettingMeta(
        "현금/단기/중기/장기/ETF 목표",
        "쥬가 블록 색깔별 균형을 볼 때 쓰는 목표 비중입니다.",
        "trading",
        input_type="textarea",
    ),
    "kis_block_trader_etf_universe": SettingMeta(
        "블록 ETF 유니버스",
        "쥬가 블록 후보로 고려하는 ETF 코드:이름 목록입니다.",
        "trading",
        input_type="textarea",
    ),
    "binance_block_trader_enabled": SettingMeta(
        "바이낸스 쥬 브랜치",
        "바이낸스 현물/선물 블록 트레이딩 루프를 켭니다.",
        "trading",
    ),
    "binance_block_trader_once": SettingMeta(
        "바이낸스 1회 실행",
        "바이낸스 블록 트레이더를 한 사이클만 실행하고 종료합니다.",
        "trading",
    ),
    "binance_block_trader_execute_spot_orders": SettingMeta(
        "바이낸스 현물 실주문",
        "켜면 현물 블록 주문이 Binance spot 주문으로 나갑니다.",
        "trading",
        risk="danger",
    ),
    "binance_block_trader_execute_futures_orders": SettingMeta(
        "바이낸스 선물 실주문",
        "켜면 선물 블록 주문이 Binance USD-M futures 주문으로 나갑니다.",
        "trading",
        risk="danger",
    ),
    "binance_block_trader_execute_upbit_orders": SettingMeta(
        "업비트 현물 실주문",
        "켜면 바이낸스 쥬가 업비트 KRW 현물 블록 주문도 실행합니다.",
        "trading",
        risk="danger",
    ),
    "binance_block_trader_db_path": SettingMeta(
        "바이낸스 블록 DB",
        "바이낸스 블록 원장 저장 경로입니다.",
        "trading",
    ),
    "binance_block_trader_state_path": SettingMeta(
        "바이낸스 러너 상태",
        "바이낸스 블록 트레이더 러너 상태 파일 경로입니다.",
        "trading",
    ),
    "binance_block_trader_quote_interval_sec": SettingMeta(
        "바이낸스 시세 주기(초)",
        "바이낸스 블록 후보/포지션 시세를 갱신하는 주기입니다.",
        "trading",
        min_value=5,
        max_value=300,
        step=1,
    ),
    "binance_block_trader_rule_interval_sec": SettingMeta(
        "바이낸스 룰 주기(초)",
        "바이낸스 블록 목표가·손절가·트레일링 조건을 확인하는 주기입니다.",
        "trading",
        min_value=5,
        max_value=300,
        step=1,
    ),
    "binance_block_trader_manager_interval_sec": SettingMeta(
        "바이낸스 판단 주기(초)",
        "24시간 바이낸스 매니저가 새 판단을 수행하는 간격입니다.",
        "trading",
        min_value=300,
        max_value=14400,
        step=60,
    ),
    "binance_block_trader_manager_error_retry_sec": SettingMeta(
        "바이낸스 판단 실패 재시도(초)",
        "매니저가 스키마·타임아웃 등으로 실패했을 때 정규 판단 주기를 기다리지 않고 다시 시도하는 최소 쿨다운입니다.",
        "ops",
        min_value=60,
        max_value=1800,
        step=60,
        risk="medium",
    ),
    "binance_block_trader_performance_feedback_interval_sec": SettingMeta(
        "바이낸스 성과 피드백 주기(초)",
        "닫힌 블록 반성/성과 테이블을 다시 계산하는 최소 간격입니다. idle 틱마다 전체 블록을 훑지 않도록 제한합니다.",
        "ops",
        min_value=60,
        max_value=3600,
        step=60,
        risk="low",
    ),
    "binance_block_trader_retention_interval_sec": SettingMeta(
        "바이낸스 DB 정리 주기(초)",
        "바이낸스 블록 트레이더 quote/판단 로그 보존 정리를 실행할 최소 간격입니다.",
        "ops",
        min_value=60,
        max_value=86400,
        step=60,
        risk="low",
    ),
    "binance_block_trader_telegram_reports_enabled": SettingMeta(
        "바이낸스 정기 텔레그램 보고",
        "바이낸스 쥬가 아침/정오/밤 운영 요약을 텔레그램으로 보냅니다.",
        "trading",
    ),
    "binance_block_trader_telegram_report_slots": SettingMeta(
        "바이낸스 보고 시간",
        "KST 기준 name:HH:MM 형식의 보고 슬롯입니다. 예: morning:06:00,noon:12:00,night:20:00",
        "trading",
        input_type="textarea",
    ),
    "binance_block_trader_llm_model": SettingMeta(
        "바이낸스 판단 모델",
        "24시간 바이낸스 매니저 판단에 쓰는 모델입니다.",
        "ai",
        choices=("gpt-5.5", "gpt-5.4", "gpt-5.4-mini"),
    ),
    "binance_block_trader_llm_reasoning_effort": SettingMeta(
        "바이낸스 추론 강도",
        "바이낸스 매니저 호출의 reasoning effort입니다.",
        "ai",
        choices=("low", "medium", "high", "xhigh"),
    ),
    "binance_block_trader_max_manager_symbols": SettingMeta(
        "바이낸스 판단 심볼 수",
        "상위 300 관찰층에서 압축해 한 번의 바이낸스 판단에 넣는 최대 심볼 수입니다.",
        "trading",
        min_value=1,
        max_value=120,
        step=1,
    ),
    "binance_block_trader_volatile_attack_enabled": SettingMeta(
        "초변동 공격 Lane",
        "고변동 알트를 작은 수량·넓은 손절·큰 목표가·대기진입으로 별도 관리합니다.",
        "trading",
    ),
    "binance_block_trader_volatile_attack_candidate_limit": SettingMeta(
        "초변동 후보 수",
        "판단 입력에 포함할 초변동 후보 압축 상한입니다.",
        "trading",
        min_value=1,
        max_value=40,
        step=1,
    ),
    "binance_block_trader_volatile_attack_budget_multiplier": SettingMeta(
        "초변동 예산 배율",
        "일반 현물/선물 산출 예산에 곱하는 초변동 lane 소액 시작 배율입니다.",
        "trading",
        min_value=0.05,
        max_value=1.0,
        step=0.05,
    ),
    "binance_block_trader_volatile_attack_min_change_pct": SettingMeta(
        "초변동 최소 변동률",
        "초변동 lane으로 분류할 24h 변동률 기준입니다.",
        "signals",
        min_value=1.0,
        max_value=50.0,
        step=0.5,
    ),
    "binance_block_trader_volatile_attack_min_volume_expansion": SettingMeta(
        "초변동 거래량 팽창",
        "초변동 lane으로 분류할 거래량 팽창 배율 기준입니다.",
        "signals",
        min_value=1.0,
        max_value=10.0,
        step=0.1,
    ),
    "binance_block_trader_volatile_attack_min_reward_risk": SettingMeta(
        "초변동 최소 R/R",
        "초변동 lane 후보가 유지해야 하는 최소 보상/위험 비율입니다.",
        "trading",
        min_value=1.0,
        max_value=5.0,
        step=0.1,
    ),
    "binance_block_trader_volatile_attack_stop_multiplier": SettingMeta(
        "초변동 손절 폭 배율",
        "초변동 lane의 기본 손절 폭을 넓히는 배율입니다.",
        "trading",
        min_value=1.0,
        max_value=3.0,
        step=0.05,
    ),
    "binance_block_trader_prompt_target_chars": SettingMeta(
        "바이낸스 판단 입력 정상 목표(chars)",
        "바이낸스 쥬 매니저 입력이 평소 이 크기 안에 들어오도록 압축합니다.",
        "ai",
        min_value=20_000,
        max_value=300_000,
        step=5_000,
    ),
    "binance_block_trader_prompt_warn_chars": SettingMeta(
        "바이낸스 판단 입력 경고선(chars)",
        "바이낸스 쥬 매니저 입력이 이 값을 넘으면 과대 입력으로 기록합니다.",
        "ai",
        min_value=20_000,
        max_value=400_000,
        step=5_000,
    ),
    "binance_block_trader_prompt_max_chars": SettingMeta(
        "바이낸스 판단 입력 상한(chars)",
        "바이낸스 쥬 매니저 입력의 하드 상한입니다. 넘지 않도록 더 강하게 압축합니다.",
        "ai",
        min_value=30_000,
        max_value=500_000,
        step=5_000,
    ),
    "binance_block_trader_spot_universe": SettingMeta(
        "바이낸스 현물 유니버스",
        "쥬가 현물 블록 후보로 보는 심볼 목록입니다.",
        "trading",
        input_type="textarea",
    ),
    "binance_block_trader_futures_universe": SettingMeta(
        "바이낸스 선물 유니버스",
        "쥬가 선물 블록 후보로 보는 심볼 목록입니다.",
        "trading",
        input_type="textarea",
    ),
    "binance_block_trader_upbit_universe": SettingMeta(
        "업비트 현물 유니버스",
        "쥬가 업비트 KRW 현물 블록 후보로 보는 마켓 목록입니다. 예: KRW-BTC,KRW-ETH",
        "trading",
        input_type="textarea",
    ),
    "binance_block_trader_max_futures_leverage": SettingMeta(
        "바이낸스 최대 선물 레버리지",
        "격리 마진 선물 블록에 허용하는 최대 레버리지입니다.",
        "trading",
        min_value=1,
        max_value=10,
        step=1,
    ),
    "binance_block_trader_min_liquidation_distance_pct": SettingMeta(
        "최소 청산 거리(%)",
        "선물 블록 진입 전 요구하는 최소 청산가 거리입니다.",
        "trading",
        min_value=1,
        max_value=100,
        step=0.5,
    ),
    "binance_block_trader_aggressive_limit_bps": SettingMeta(
        "바이낸스 공격적 지정가 bps",
        "바이낸스 주문가를 현재가에서 얼마나 공격적으로 보낼지 정합니다.",
        "trading",
        min_value=0,
        max_value=300,
        step=1,
    ),
    "binance_block_trader_failed_exit_retry_cooldown_sec": SettingMeta(
        "바이낸스 청산 재시도 쿨다운(초)",
        "청산 주문 실패 후 같은 블록을 다시 주문하기까지 기다리는 시간입니다.",
        "trading",
        min_value=0,
        max_value=600,
        step=5,
    ),
    "binance_block_trader_min_entry_confidence": SettingMeta(
        "바이낸스 신규진입 최소 신뢰도",
        "쥬가 watch/hold 성격 후보를 실제 블록으로 승격할 때 요구하는 기본 신뢰도입니다.",
        "trading",
        min_value=0,
        max_value=1,
        step=0.01,
    ),
    "binance_block_trader_min_entry_expected_r": SettingMeta(
        "바이낸스 신규진입 최소 기대 R",
        "퀀트 신호의 기대 R이 이 값보다 낮으면 기본적으로 신규 블록을 보류합니다.",
        "trading",
        min_value=0,
        max_value=2,
        step=0.01,
    ),
    "binance_block_trader_min_entry_directional_score": SettingMeta(
        "바이낸스 신규진입 방향 점수",
        "퀀트 방향 점수가 이 값보다 낮으면 방향 우위가 약한 것으로 봅니다.",
        "trading",
        min_value=0,
        max_value=100,
        step=1,
    ),
    "binance_block_trader_min_candidate_stop_pct": SettingMeta(
        "바이낸스 후보 최소 손절폭(%)",
        "신규 후보 가격 설계에서 잡음에 너무 쉽게 털리지 않도록 쓰는 최소 손절폭입니다.",
        "trading",
        min_value=0.1,
        max_value=10,
        step=0.1,
    ),
    "binance_block_trader_profit_lock_trigger_r": SettingMeta(
        "바이낸스 수익보호 발동 R",
        "블록이 이 R만큼 유리하게 움직이면 룰엔진이 손절선을 이익 보호 위치로 옮깁니다.",
        "trading",
        min_value=0,
        max_value=5,
        step=0.05,
    ),
    "binance_block_trader_profit_lock_stop_r": SettingMeta(
        "바이낸스 수익보호 잠금 R",
        "수익보호 발동 후 손절선을 진입가 기준 몇 R 이익 위치에 둘지 정합니다.",
        "trading",
        min_value=0,
        max_value=2,
        step=0.01,
    ),
    "binance_block_trader_spot_quote_budget_pct": SettingMeta(
        "바이낸스 현물 예산 비율(%)",
        "현물 후보 블록의 기본 주문 예산을 현물 현금 대비 몇 %로 잡을지 정합니다.",
        "trading",
        min_value=0,
        max_value=100,
        step=0.5,
    ),
    "binance_block_trader_spot_min_quote_budget_usdt": SettingMeta(
        "바이낸스 현물 최소 예산(USDT)",
        "현물 후보 블록 1개가 가질 최소 quote 예산입니다.",
        "trading",
        min_value=0,
        max_value=10_000,
        step=1,
    ),
    "binance_block_trader_spot_max_quote_budget_usdt": SettingMeta(
        "바이낸스 현물 최대 예산(USDT)",
        "현물 후보 블록 1개가 가질 최대 quote 예산입니다.",
        "trading",
        min_value=0,
        max_value=100_000,
        step=10,
    ),
    "binance_block_trader_upbit_quote_budget_pct": SettingMeta(
        "업비트 현물 예산 비율(%)",
        "업비트 현물 후보 블록의 기본 주문 예산을 업비트 KRW 현금 대비 몇 %로 잡을지 정합니다.",
        "trading",
        min_value=0,
        max_value=100,
        step=0.5,
    ),
    "binance_block_trader_upbit_min_quote_budget_krw": SettingMeta(
        "업비트 현물 최소 예산(KRW)",
        "업비트 현물 후보 블록 1개가 가질 최소 원화 예산입니다.",
        "trading",
        min_value=0,
        max_value=10_000_000,
        step=1_000,
    ),
    "binance_block_trader_upbit_max_quote_budget_krw": SettingMeta(
        "업비트 현물 최대 예산(KRW)",
        "업비트 현물 후보 블록 1개가 가질 최대 원화 예산입니다.",
        "trading",
        min_value=0,
        max_value=100_000_000,
        step=10_000,
    ),
    "binance_block_trader_futures_quote_budget_pct": SettingMeta(
        "바이낸스 선물 예산 비율(%)",
        "선물 후보 블록의 기본 주문 예산을 선물 지갑 현금 대비 몇 %로 잡을지 정합니다.",
        "trading",
        min_value=0,
        max_value=100,
        step=0.5,
    ),
    "binance_block_trader_futures_min_quote_budget_usdt": SettingMeta(
        "바이낸스 선물 최소 예산(USDT)",
        "선물 후보 블록 1개가 가질 최소 quote 예산입니다.",
        "trading",
        min_value=0,
        max_value=10_000,
        step=1,
    ),
    "binance_block_trader_futures_max_quote_budget_usdt": SettingMeta(
        "바이낸스 선물 최대 예산(USDT)",
        "선물 후보 블록 1개가 가질 최대 quote 예산입니다.",
        "trading",
        min_value=0,
        max_value=100_000,
        step=10,
    ),
    "binance_block_trader_budget_performance_scale_enabled": SettingMeta(
        "바이낸스 성과 기반 예산 확대",
        "최근 블록 성과가 충분히 좋으면 후보 블록 예산을 단계적으로 키웁니다.",
        "trading",
    ),
    "binance_block_trader_budget_performance_scale_min_samples": SettingMeta(
        "바이낸스 예산 확대 최소 표본",
        "성과 기반 예산 확대를 적용하기 전 필요한 최소 닫힌 블록 표본 수입니다.",
        "trading",
        min_value=1,
        max_value=200,
        step=1,
    ),
    "binance_block_trader_budget_performance_scale_win_rate_pct": SettingMeta(
        "바이낸스 예산 확대 승률 기준(%)",
        "최근 성과 승률이 이 값 이상이고 실현손익/평균 R이 양수일 때 예산 확대를 허용합니다.",
        "trading",
        min_value=0,
        max_value=100,
        step=1,
    ),
    "binance_block_trader_budget_performance_scale_multiplier": SettingMeta(
        "바이낸스 예산 확대 배수",
        "성과 조건 충족 시 기본 후보 예산에 곱하는 배수입니다.",
        "trading",
        min_value=1,
        max_value=3,
        step=0.1,
    ),
    "binance_block_trader_account_risk_pct": SettingMeta(
        "바이낸스 블록당 계좌 리스크(%)",
        "손절가까지의 거리 기준으로 한 블록이 감수할 계좌 리스크 예산입니다.",
        "trading",
        min_value=0.01,
        max_value=10,
        step=0.01,
    ),
    "binance_block_trader_max_total_exposure_usdt": SettingMeta(
        "바이낸스 총 노출 한도(USDT)",
        "0이면 총 노출 한도를 계좌/심볼 리스크 게이트에 맡깁니다.",
        "trading",
        min_value=0,
        max_value=1_000_000,
        step=10,
    ),
    "binance_block_trader_max_symbol_exposure_pct": SettingMeta(
        "바이낸스 심볼별 노출 한도(%)",
        "한 심볼에 몰릴 수 있는 최대 계좌 노출 비율입니다.",
        "trading",
        min_value=0,
        max_value=100,
        step=0.5,
    ),
    "binance_block_trader_min_reward_risk": SettingMeta(
        "바이낸스 최소 보상/위험",
        "목표가와 손절가 기준 R/R이 이 값보다 낮으면 신규 블록을 거절합니다.",
        "trading",
        min_value=0,
        max_value=10,
        step=0.1,
    ),
    "binance_block_trader_quote_retention_days": SettingMeta(
        "바이낸스 시세 보존일",
        "고빈도 quote snapshot을 보존할 일수입니다. 블록 원장과 주문 기록은 별도 보존합니다.",
        "ops",
        min_value=1,
        max_value=365,
        step=1,
    ),
    "binance_block_trader_manager_run_retention_days": SettingMeta(
        "바이낸스 판단 로그 보존일",
        "쥬 매니저 prompt/response 실행 로그를 보존할 일수입니다.",
        "ops",
        min_value=1,
        max_value=365,
        step=1,
    ),
    "binance_block_trader_archive_retention_days": SettingMeta(
        "바이낸스 archive 최종 보존일",
        "바이낸스 quote/manager archive 테이블의 최종 보존 기간입니다. 블록과 주문 원장은 삭제하지 않습니다.",
        "ops",
        min_value=1,
        max_value=365,
        step=1,
    ),
    "crypto_market_research_enabled": SettingMeta(
        "크립토 시장 리서치",
        "바이낸스 쥬가 참고하는 시장구조/파생지표 리서치 루프를 켭니다.",
        "signals",
    ),
    "crypto_market_research_once": SettingMeta(
        "크립토 리서치 1회 실행",
        "크립토 시장 리서치를 한 사이클만 실행하고 종료합니다.",
        "signals",
    ),
    "crypto_market_research_db_path": SettingMeta(
        "크립토 리서치 DB",
        "크립토 시장구조, 후보, AI 판단을 저장하는 DB 경로입니다.",
        "signals",
    ),
    "crypto_market_research_state_path": SettingMeta(
        "크립토 리서치 러너 상태",
        "크립토 시장 리서치 러너 상태 파일 경로입니다.",
        "signals",
    ),
    "crypto_market_research_universe": SettingMeta(
        "크립토 리서치 유니버스",
        "시장구조와 AI 리서치를 수행할 Binance 심볼 목록입니다.",
        "signals",
        input_type="textarea",
    ),
    "crypto_market_research_max_symbols": SettingMeta(
        "크립토 리서치 심볼 수",
        "한 사이클에서 수집/분석하는 최대 심볼 수입니다.",
        "signals",
        min_value=1,
        max_value=500,
        step=1,
    ),
    "crypto_market_research_auto_universe_enabled": SettingMeta(
        "크립토 자동 유니버스",
        "Binance 24h 거래대금 상위 심볼을 리서치 유니버스에 자동으로 보탭니다.",
        "signals",
    ),
    "crypto_market_research_auto_universe_limit": SettingMeta(
        "크립토 자동 유니버스 수",
        "거래대금 기준으로 추가할 동적 심볼 후보 수입니다.",
        "signals",
        min_value=0,
        max_value=500,
        step=1,
    ),
    "crypto_market_research_research_universe_limit": SettingMeta(
        "크립토 깊은 수집 수",
        "상위 관찰 유니버스에서 실제 OHLCV/오더북/파생 데이터를 깊게 수집할 심볼 수입니다.",
        "signals",
        min_value=10,
        max_value=200,
        step=5,
    ),
    "crypto_market_research_llm_top_symbols": SettingMeta(
        "크립토 LLM 집중 심볼 수",
        "넓게 수집한 뒤 쥬 판단에 넣을 상위 압축 심볼 수입니다.",
        "ai",
        min_value=1,
        max_value=50,
        step=1,
    ),
    "crypto_market_research_min_quote_volume_usdt": SettingMeta(
        "크립토 최소 거래대금(USDT)",
        "자동 유니버스 편입에 필요한 24시간 quote volume 하한입니다.",
        "signals",
        min_value=0,
        max_value=1_000_000_000,
        step=100_000,
    ),
    "crypto_market_research_kline_intervals": SettingMeta(
        "크립토 멀티타임프레임",
        "interval:limit 형식의 Binance kline 수집 폭입니다. 예: 1m:120,5m:96,15m:96,1h:168,4h:180",
        "signals",
        input_type="textarea",
    ),
    "crypto_market_research_kline_hot_window_rows": SettingMeta(
        "크립토 캔들 hot window",
        "심볼·마켓·인터벌별 active DB에 유지할 최신 캔들 수입니다. 패턴랩 lookback보다 크게 유지하세요.",
        "signals",
        min_value=120,
        max_value=5000,
        step=60,
    ),
    "crypto_market_research_market_hot_window_rows": SettingMeta(
        "크립토 시장 스냅샷 hot window",
        "심볼별 ticker/orderbook/funding/OI active 원본을 최신 몇 개까지 유지할지 정합니다.",
        "signals",
        min_value=120,
        max_value=5000,
        step=60,
    ),
    "crypto_market_research_regime_enabled": SettingMeta(
        "크립토 시장 레짐",
        "BTC 주도 추세, 알트 강약, 위험 회피/회전장 같은 시장 레짐 분류를 켭니다.",
        "signals",
    ),
    "crypto_market_research_squeeze_guard_enabled": SettingMeta(
        "크립토 스퀴즈 가드",
        "펀딩·베이시스·OI 기반 crowded long/short 위험 피처를 켭니다.",
        "signals",
    ),
    "crypto_market_research_collect_symbol_timeout_sec": SettingMeta(
        "크립토 심볼별 수집 제한(초)",
        "한 심볼의 OHLCV/오더북/파생지표 수집이 오래 걸릴 때 끊어내는 제한 시간입니다.",
        "signals",
        min_value=3,
        max_value=120,
        step=1,
    ),
    "crypto_market_research_collect_cycle_timeout_sec": SettingMeta(
        "크립토 수집 사이클 제한(초)",
        "한 사이클의 상세 수집 전체가 오래 걸릴 때 끊어내는 제한 시간입니다.",
        "signals",
        min_value=30,
        max_value=900,
        step=30,
    ),
    "crypto_market_research_collect_concurrency": SettingMeta(
        "크립토 수집 동시성",
        "상세 수집 대상 심볼을 동시에 몇 개까지 처리할지 정합니다. 크게 올리면 Binance 호출량도 같이 늘어납니다.",
        "signals",
        min_value=1,
        max_value=12,
        step=1,
    ),
    "crypto_market_research_feature_interval_sec": SettingMeta(
        "크립토 구조 수집 주기(초)",
        "가격/거래량/파생지표 feature를 갱신하는 간격입니다.",
        "signals",
        min_value=60,
        max_value=3600,
        step=60,
    ),
    "crypto_market_research_llm_interval_sec": SettingMeta(
        "크립토 LLM 리서치 주기(초)",
        "크립토 압축 리서치 판단을 갱신하는 간격입니다.",
        "ai",
        min_value=300,
        max_value=21600,
        step=300,
    ),
    "crypto_market_research_llm_model": SettingMeta(
        "크립토 리서치 모델",
        "크립토 시장구조 리서치에 쓰는 모델입니다.",
        "ai",
        choices=("gpt-5.5", "gpt-5.4", "gpt-5.4-mini"),
    ),
    "crypto_market_research_llm_reasoning_effort": SettingMeta(
        "크립토 리서치 추론 강도",
        "크립토 리서치 native 모델 호출의 reasoning effort입니다.",
        "ai",
        choices=("low", "medium", "high", "xhigh"),
    ),
    "crypto_market_research_external_enabled": SettingMeta(
        "크립토 외부 컨텍스트",
        "CoinGecko/DefiLlama/Fear & Greed류 외부 컨텍스트 저장을 허용합니다.",
        "signals",
    ),
    "crypto_market_research_external_sources": SettingMeta(
        "크립토 외부 소스",
        "활성화할 외부 컨텍스트 소스 목록입니다.",
        "signals",
        input_type="textarea",
    ),
    "crypto_market_research_retention_days": SettingMeta(
        "크립토 리서치 시계열 보존일",
        "핫 테이블의 market snapshots, klines, derivatives, regime snapshots 보존일입니다.",
        "ops",
        min_value=1,
        max_value=365,
        step=1,
    ),
    "crypto_market_research_archive_retention_days": SettingMeta(
        "크립토 리서치 archive 보존일",
        "압축 archive 테이블의 크립토 리서치 시계열을 보관할 최대 일수입니다.",
        "ops",
        min_value=7,
        max_value=730,
        step=1,
    ),
    "crypto_quant_enabled": SettingMeta(
        "바이낸스 퀀트 신호",
        "ATR, RSI, EMA, 거래량, 스프레드, 펀딩 기반 롱/숏/관망 패킷을 생성합니다.",
        "signals",
    ),
    "crypto_quant_db_path": SettingMeta(
        "바이낸스 퀀트 DB",
        "최신 퀀트 신호와 시계열 signal history를 저장하는 DB 경로입니다.",
        "signals",
    ),
    "crypto_quant_context_limit": SettingMeta(
        "쥬 판단용 퀀트 신호 수",
        "바이낸스 쥬 프롬프트에 넣는 최신 정량 신호 최대 개수입니다.",
        "signals",
        min_value=4,
        max_value=50,
        step=1,
    ),
    "crypto_quant_hot_window_rows": SettingMeta(
        "크립토 퀀트 hot window",
        "심볼·호라이즌별 active signal history를 최신 몇 개까지 유지할지 정합니다.",
        "signals",
        min_value=120,
        max_value=5000,
        step=60,
    ),
    "crypto_quant_archive_window_rows": SettingMeta(
        "크립토 퀀트 archive window",
        "심볼·호라이즌별 압축 archive signal history를 최신 몇 개까지 유지할지 정합니다.",
        "ops",
        min_value=120,
        max_value=5000,
        step=60,
    ),
    "crypto_quant_retention_days": SettingMeta(
        "크립토 퀀트 히스토리 보존일",
        "핫 테이블의 정량 signal history와 outcome 라벨 보존일입니다.",
        "ops",
        min_value=1,
        max_value=365,
        step=1,
    ),
    "crypto_quant_archive_retention_days": SettingMeta(
        "크립토 퀀트 archive 보존일",
        "압축 archive 테이블의 정량 signal history와 outcome 라벨을 보관할 최대 일수입니다.",
        "ops",
        min_value=7,
        max_value=730,
        step=1,
    ),
    "crypto_pattern_lab_enabled": SettingMeta(
        "Freqtrade 패턴 랩",
        "Freqtrade 계열 전략 아이디어를 정적 분석하고 HERMES 시계열로 재검증합니다.",
        "signals",
    ),
    "crypto_pattern_lab_db_path": SettingMeta(
        "패턴 랩 DB",
        "전략 패턴과 백테스트 scorecard를 저장하는 DB 경로입니다.",
        "signals",
    ),
    "kr_equity_pattern_lab_db_path": SettingMeta(
        "국장 패턴 랩 DB",
        "KIS 쥬의 국장 전용 walk-forward/OOS 검증 세트를 저장하는 DB 경로입니다.",
        "signals",
    ),
    "kr_equity_pattern_lab_enabled": SettingMeta(
        "국장 패턴 랩 자동 갱신",
        "live evaluator가 KIS 실거래 성과에서 국장 전용 검증 세트를 갱신합니다.",
        "signals",
    ),
    "kr_equity_pattern_lab_min_samples": SettingMeta(
        "국장 패턴 랩 최소 표본",
        "한 종목/패턴 그룹을 optimized set으로 만들기 위한 최소 KIS live alpha 표본입니다.",
        "signals",
        min_value=2,
        max_value=100,
        step=1,
    ),
    "crypto_pattern_lab_strategy_paths": SettingMeta(
        "Freqtrade 전략 경로",
        "정적 분석할 Freqtrade 전략 파일/폴더 목록입니다. 쉼표로 구분합니다.",
        "signals",
        input_type="textarea",
    ),
    "crypto_pattern_lab_intervals": SettingMeta(
        "패턴 백테스트 타임프레임",
        "패턴을 검증할 타임프레임 목록입니다. 예: 5m,15m,1h",
        "signals",
    ),
    "crypto_pattern_lab_context_limit": SettingMeta(
        "쥬 판단용 패턴 수",
        "바이낸스 쥬 프롬프트에 넣는 상위 패턴 scorecard 수입니다.",
        "signals",
        min_value=3,
        max_value=50,
        step=1,
    ),
    "crypto_pattern_lab_backtests_per_tuple_retention": SettingMeta(
        "패턴 백테스트 보존 개수",
        "패턴/심볼/타임프레임 조합별 최신 백테스트 행을 몇 개까지 보존할지 정합니다.",
        "ops",
        min_value=3,
        max_value=50,
        step=1,
    ),
    "crypto_pattern_lab_optimizer_runs_per_tuple_retention": SettingMeta(
        "패턴 최적화 run 보존 개수",
        "패턴/심볼/타임프레임/objective 조합별 최신 최적화 run을 몇 개까지 보존할지 정합니다.",
        "ops",
        min_value=2,
        max_value=50,
        step=1,
    ),
    "crypto_pattern_lab_optimizer_trials_per_run_retention": SettingMeta(
        "패턴 최적화 trial 보존 개수",
        "최적화 run별 상위 trial을 몇 개까지 보존할지 정합니다.",
        "ops",
        min_value=3,
        max_value=100,
        step=1,
    ),
    "crypto_pattern_lab_max_backtest_rows": SettingMeta(
        "패턴 백테스트 총 보존 행",
        "crypto_pattern_lab.db의 pattern_backtests 전체 행 상한입니다. 오래된 반복 검증 행이 계속 쌓이는 것을 막습니다.",
        "ops",
        min_value=10_000,
        max_value=500_000,
        step=5_000,
    ),
    "crypto_pattern_lab_max_optimizer_runs": SettingMeta(
        "패턴 최적화 run 총 보존 행",
        "optimization_runs 전체 행 상한입니다. optimized set에 연결된 run은 별도로 보존합니다.",
        "ops",
        min_value=500,
        max_value=50_000,
        step=500,
    ),
    "crypto_pattern_lab_max_optimizer_trials": SettingMeta(
        "패턴 최적화 trial 총 보존 행",
        "optimization_trials 전체 행 상한입니다. best/optimized set에 연결된 trial은 별도로 보존합니다.",
        "ops",
        min_value=2_000,
        max_value=250_000,
        step=1_000,
    ),
    "crypto_alpha_enabled": SettingMeta(
        "크립토 알파 DB",
        "바이낸스 쥬가 읽는 무료 공개 촉매/결과 라벨/패턴 점수 DB를 켭니다.",
        "signals",
    ),
    "crypto_alpha_once": SettingMeta(
        "크립토 알파 1회 실행",
        "크립토 알파 수집과 결과 라벨링을 한 사이클만 실행하고 종료합니다.",
        "signals",
    ),
    "crypto_alpha_db_path": SettingMeta(
        "크립토 알파 DB 경로",
        "무료 공개 촉매, 심볼 연결, 결과 라벨, 패턴 점수 저장 경로입니다.",
        "signals",
    ),
    "crypto_alpha_state_path": SettingMeta(
        "크립토 알파 러너 상태",
        "크립토 알파 러너 상태 파일 경로입니다.",
        "signals",
    ),
    "crypto_alpha_source_ids": SettingMeta(
        "크립토 알파 소스",
        "활성화할 공개 크롤링 소스 ID 목록입니다.",
        "signals",
        input_type="textarea",
    ),
    "crypto_alpha_crawl_interval_sec": SettingMeta(
        "크립토 알파 수집 주기(초)",
        "허용된 공개 소스에서 촉매 근거를 수집하는 주기입니다.",
        "signals",
        min_value=300,
        max_value=21600,
        step=300,
    ),
    "crypto_alpha_outcome_interval_sec": SettingMeta(
        "크립토 알파 라벨링 주기(초)",
        "이벤트 이후 가격 결과를 라벨링하는 주기입니다.",
        "signals",
        min_value=300,
        max_value=21600,
        step=300,
    ),
    "crypto_alpha_rate_limit_sec": SettingMeta(
        "크립토 알파 소스 대기(초)",
        "공개 소스 요청 사이 최소 대기 시간입니다.",
        "signals",
        min_value=0,
        max_value=60,
        step=0.5,
    ),
    "crypto_alpha_context_limit": SettingMeta(
        "크립토 알파 판단팩 수",
        "바이낸스 쥬 판단에 넣을 최대 알파 이벤트 수입니다.",
        "signals",
        min_value=1,
        max_value=50,
        step=1,
    ),
    "crypto_alpha_llm_model": SettingMeta(
        "크립토 알파 모델",
        "크립토 알파 요약/압축이 LLM을 사용할 때의 모델입니다.",
        "ai",
        choices=("gpt-5.5", "gpt-5.4", "gpt-5.4-mini"),
    ),
    "crypto_alpha_llm_reasoning_effort": SettingMeta(
        "크립토 알파 추론 강도",
        "크립토 알파 요약/압축 호출의 reasoning effort입니다.",
        "ai",
        choices=("low", "medium", "high", "xhigh"),
    ),
    "market_judge_enabled": SettingMeta(
        "장중 판단 루프",
        "계좌/시세/전략 지식 기반 장중 LLM 판단 루프를 켭니다.",
        "market",
    ),
    "market_quote_interval_sec": SettingMeta(
        "시세 수집 주기(초)",
        "장중 quote-only 수집 간격입니다.",
        "market",
        min_value=10,
        max_value=600,
        step=5,
    ),
    "market_judge_interval_sec": SettingMeta(
        "LLM 장중 판단 주기(초)",
        "장중 LLM 판단 cadence입니다. 기본은 30분입니다.",
        "market",
        min_value=600,
        max_value=14400,
        step=60,
    ),
    "market_judge_quote_retention_days": SettingMeta(
        "장중 시세 hot 보존일",
        "market_judgment DB의 quote_snapshots 활성 보존 기간입니다. 오래된 시세는 archive로 이동합니다.",
        "market",
        min_value=1,
        max_value=60,
        step=1,
    ),
    "market_judge_quote_archive_retention_days": SettingMeta(
        "장중 시세 archive 보존일",
        "market_judgment DB의 quote_snapshots_archive 최종 보존 기간입니다.",
        "market",
        min_value=1,
        max_value=180,
        step=1,
    ),
    "market_judge_account_retention_days": SettingMeta(
        "국장 계좌 스냅샷 보존일",
        "market_judgment DB의 account_snapshots 보존 기간입니다.",
        "market",
        min_value=1,
        max_value=180,
        step=1,
    ),
    "market_judge_judgment_retention_days": SettingMeta(
        "장중 판단 보존일",
        "market_judgment DB의 judgment_runs/symbol_judgments 보존 기간입니다.",
        "market",
        min_value=1,
        max_value=365,
        step=1,
    ),
    "market_judge_compact_recent_run_count": SettingMeta(
        "장중 판단 원본 보존 run 수",
        "market_judgment DB에서 최근 몇 개 판단 run의 prompt/response 원본을 유지할지 정합니다. 그 이전 큰 payload는 marker로 축약됩니다.",
        "market",
        min_value=1,
        max_value=240,
        step=1,
    ),
    "market_judge_compact_min_chars": SettingMeta(
        "장중 판단 run 축약 기준 문자 수",
        "prompt/response/source_snapshot 합산 길이가 이 값보다 큰 오래된 judgment run을 축약합니다.",
        "market",
        min_value=1000,
        max_value=300000,
        step=1000,
    ),
    "market_judge_compact_symbol_min_chars": SettingMeta(
        "종목별 판단 payload 축약 기준 문자 수",
        "quote/position/strategy payload 합산 길이가 이 값보다 큰 오래된 symbol judgment를 축약합니다.",
        "market",
        min_value=500,
        max_value=50000,
        step=500,
    ),
    "market_judge_max_symbols": SettingMeta(
        "시세 대상 종목 수",
        "시장 판단 루프가 quote를 모으는 최대 종목 수입니다.",
        "market",
        min_value=1,
        max_value=300,
        step=1,
    ),
    "market_judge_llm_max_symbols": SettingMeta(
        "LLM 집중 판단 종목 수",
        "LLM 입력에 깊게 넣는 종목 수입니다.",
        "market",
        min_value=1,
        max_value=60,
        step=1,
    ),
    "market_judge_use_naver_fallback": SettingMeta(
        "네이버 시세 보조 사용",
        "KIS 현재가 실패 시 네이버 보조 조회를 허용합니다. 기본값은 실패를 그대로 드러내도록 꺼져 있습니다.",
        "market",
    ),
    "market_judge_query": SettingMeta(
        "장중 판단 지시문",
        "시장 판단 LLM 호출의 기본 지시문입니다.",
        "market",
        input_type="textarea",
    ),
    "market_pulse_enabled": SettingMeta(
        "시장 펄스 수집",
        "지수, 수급, 프로그램 매매 등 거시/장중 펄스 수집을 켭니다.",
        "market",
    ),
    "market_pulse_interval_sec": SettingMeta(
        "시장 펄스 주기(초)",
        "시장 펄스 수집 주기입니다.",
        "market",
        min_value=30,
        max_value=1800,
        step=10,
    ),
    "market_pulse_retention_days": SettingMeta(
        "시장 펄스 hot 보존일",
        "market_pulse DB의 활성 스냅샷 보존 기간입니다. 오래된 펄스는 archive로 이동합니다.",
        "market",
        min_value=1,
        max_value=60,
        step=1,
    ),
    "market_pulse_archive_retention_days": SettingMeta(
        "시장 펄스 archive 보존일",
        "market_pulse DB의 archive 스냅샷 최종 보존 기간입니다.",
        "market",
        min_value=1,
        max_value=180,
        step=1,
    ),
    "market_pulse_index_codes": SettingMeta(
        "시장 지수 코드",
        "수집할 지수/선물 코드 목록입니다.",
        "market",
    ),
    "investment_memory_enabled": SettingMeta(
        "쥬 메모리 루프",
        "장전/장중/마감 저널과 블록 반성 루프를 켭니다.",
        "memory",
    ),
    "investment_memory_poll_interval_sec": SettingMeta(
        "메모리 polling 주기(초)",
        "반성/저널/정책 업데이트 대기 이벤트 확인 주기입니다.",
        "memory",
        min_value=10,
        max_value=1800,
        step=5,
    ),
    "investment_memory_send_telegram": SettingMeta(
        "메모리 텔레그램 발송",
        "장전 마음가짐, 장중 점검, 마감 리뷰 발송을 허용합니다.",
        "memory",
    ),
    "investment_memory_run_daily_discovery": SettingMeta(
        "메모리 러너 일일 발굴 실행",
        "메모리 러너가 daily discovery를 직접 실행할지 여부입니다. 기본은 꺼서 RAG/리서치 엔진 중복 초기화를 피하고, KIS 러너가 만든 결과를 읽게 합니다.",
        "memory",
        risk="medium",
    ),
    "investment_memory_policy_mode": SettingMeta(
        "정책 반영 모드",
        "반성 결과를 정책 후보/주의/선호로 반영하는 방식입니다.",
        "memory",
        choices=("soft_auto", "manual", "off"),
    ),
    "investment_memory_persona_tone": SettingMeta(
        "페르소나 톤",
        "쥬가 UI/텔레그램/프롬프트에서 유지하는 말투 톤입니다.",
        "memory",
        choices=("friendly_partner", "strict_operator", "calm_coach"),
    ),
    "investment_memory_context_max_chars": SettingMeta(
        "메모리 컨텍스트 한도",
        "블록 매니저 프롬프트에 넣는 메모리 최대 문자 수입니다.",
        "memory",
        min_value=1000,
        max_value=50000,
        step=500,
    ),
    "investment_memory_ops_summary_cache_ttl_sec": SettingMeta(
        "진단 복구 요약 캐시",
        "블록/운영 화면의 19검증 복구 요약을 짧게 재사용해 M1에서 반복 DB 계산을 줄입니다.",
        "memory",
        min_value=0,
        max_value=60,
        step=1,
    ),
    "investment_memory_compaction_interval_sec": SettingMeta(
        "메모리 저장소 압축 주기(초)",
        "retired 정책 룰 파일/DB 행을 정리하는 주기입니다. 메모리 DB가 커지는 것을 막습니다.",
        "memory",
        min_value=300,
        max_value=86400,
        step=300,
    ),
    "investment_memory_policy_retired_keep": SettingMeta(
        "정책별 retired 룰 보관 수",
        "각 정책 ID마다 남겨둘 retired 버전 수입니다. 활성 룰은 삭제하지 않습니다.",
        "memory",
        min_value=0,
        max_value=20,
        step=1,
    ),
    "investment_memory_validation_event_retained_rows_per_venue": SettingMeta(
        "검증 이벤트 원문 보존 수",
        "KIS/Binance별 최신 processed 19검증 이벤트 원문을 몇 개까지 남길지 정합니다. 정책 scorecard와 insight는 별도로 유지됩니다.",
        "memory",
        min_value=24,
        max_value=5000,
        step=24,
    ),
    "investment_memory_run_recent_rows_per_group": SettingMeta(
        "메모리 run 상세 보존 수",
        "kind/slot/status 그룹별 최신 memory run 입력·출력 원문을 몇 개까지 상세 보존할지 정합니다.",
        "memory",
        min_value=3,
        max_value=288,
        step=3,
    ),
    "investment_memory_symbol_analysis_recent_rows_per_symbol": SettingMeta(
        "종목 분석 상세 보존 수",
        "종목별 최신 즉석 분석 prompt/snapshot/raw response를 몇 개까지 상세 보존할지 정합니다.",
        "memory",
        min_value=1,
        max_value=20,
        step=1,
    ),
    "daily_discovery_enabled": SettingMeta(
        "랜덤 디스커버리",
        "장전 코스피/코스닥 랜덤 종목 스터디 루프를 켭니다.",
        "memory",
    ),
    "daily_discovery_kospi_count": SettingMeta(
        "코스피 랜덤 수",
        "장전 랜덤 스터디 대상 코스피 종목 수입니다.",
        "memory",
        min_value=0,
        max_value=50,
        step=1,
    ),
    "daily_discovery_kosdaq_count": SettingMeta(
        "코스닥 랜덤 수",
        "장전 랜덤 스터디 대상 코스닥 종목 수입니다.",
        "memory",
        min_value=0,
        max_value=50,
        step=1,
    ),
    "daily_discovery_exclude_recent_days": SettingMeta(
        "최근 제외 일수",
        "이미 스터디한 종목을 다시 뽑지 않는 최소 일수입니다.",
        "memory",
        min_value=0,
        max_value=365,
        step=1,
    ),
    "research_enabled": SettingMeta(
        "리서치 러너",
        "전략 리서치 스냅샷/지식 업데이트 루프를 켭니다.",
        "research",
    ),
    "research_runner_collect_reports": SettingMeta(
        "리서치 러너 리포트 수집",
        "전용 네이버 리포트 러너와 별도로 legacy research runner에서도 리포트 수집을 수행합니다. 보통은 꺼둬 중복 수집을 피합니다.",
        "research",
    ),
    "research_run_interval_sec": SettingMeta(
        "리서치 주기(초)",
        "리서치 러너 갱신 간격입니다.",
        "research",
        min_value=300,
        max_value=86400,
        step=60,
    ),
    "research_max_items": SettingMeta(
        "리서치 최대 항목",
        "한 번에 정리할 리서치 항목 수입니다.",
        "research",
        min_value=1,
        max_value=200,
        step=1,
    ),
    "research_knowledge_max_chars": SettingMeta(
        "리서치 지식 한도",
        "리서치 프롬프트/요약에 넣는 최대 문자 수입니다.",
        "research",
        min_value=1000,
        max_value=200000,
        step=1000,
    ),
    "research_db_reference_top_k": SettingMeta(
        "리포트 DB 참조 수",
        "리서치/답변에서 끌어오는 리포트 DB top-k입니다.",
        "research",
        min_value=1,
        max_value=100,
        step=1,
    ),
    "research_codex_query": SettingMeta(
        "외부 리서치 질의",
        "리서치 러너가 외부/코덱스 리서치에 던지는 기본 질의입니다.",
        "research",
        input_type="textarea",
    ),
    "naver_reports_enabled": SettingMeta(
        "네이버 리포트 수집",
        "네이버 증권 리포트 수집 루프를 켭니다.",
        "research",
    ),
    "naver_reports_interval_sec": SettingMeta(
        "리포트 수집 주기(초)",
        "네이버 리포트 수집 간격입니다.",
        "research",
        min_value=300,
        max_value=86400,
        step=60,
    ),
    "naver_reports_cycle_timeout_sec": SettingMeta(
        "리포트 수집 timeout(초)",
        "네이버 리포트 1회 수집이 이 시간을 넘기면 timeout으로 기록하고 다음 주기로 넘어갑니다.",
        "research",
        min_value=300,
        max_value=21600,
        step=60,
    ),
    "naver_reports_max_pages": SettingMeta(
        "리포트 수집 페이지",
        "각 리포트 목록에서 읽을 최대 페이지 수입니다.",
        "research",
        min_value=1,
        max_value=100,
        step=1,
    ),
    "naver_reports_seed_urls": SettingMeta(
        "리포트 seed URL",
        "수집 대상 네이버 리포트 목록 URL입니다.",
        "research",
        input_type="textarea",
    ),
    "naver_reports_llm_facts_enabled": SettingMeta(
        "리포트 LLM facts",
        "리포트 fact 추출에 LLM을 사용합니다.",
        "research",
    ),
    "rag_enabled": SettingMeta(
        "RAG 벡터 저장소",
        "리포트 벡터 검색 저장소를 사용합니다.",
        "research",
    ),
    "rag_query_top_k": SettingMeta(
        "RAG 검색 top-k",
        "질문/전략 판단에 가져오는 RAG 문단 수입니다.",
        "research",
        min_value=1,
        max_value=100,
        step=1,
    ),
    "rag_sync_batch_size": SettingMeta(
        "RAG sync batch",
        "RAG 동기화 batch size입니다.",
        "research",
        min_value=1,
        max_value=5000,
        step=1,
    ),
    "rag_allow_legacy_pickle_migration": SettingMeta(
        "legacy pickle migration",
        "옛 pickle 기반 RAG 저장소 마이그레이션을 허용합니다.",
        "research",
        risk="danger",
    ),
    "strategy_insight_collect_interval_sec": SettingMeta(
        "Whale/세시반 수집 주기",
        "전략 인사이트 공개 데이터 수집 간격입니다.",
        "signals",
        min_value=300,
        max_value=86400,
        step=60,
    ),
    "strategy_insight_retention_days": SettingMeta(
        "Whale/세시반 신호 보존일",
        "전략 후보에 쓰는 외부 시그널 DB의 hot history 보존 기간입니다. 최근 흐름은 남기고 오래된 반복 시그널은 정리합니다.",
        "signals",
        min_value=7,
        max_value=365,
        step=1,
    ),
    "strategy_insight_signal_row_cap_per_symbol": SettingMeta(
        "Whale/세시반 종목별 신호 상한",
        "같은 source/type/symbol 조합에서 보존할 최근 신호 row 수입니다. 세시반처럼 반복 수집되는 신호가 DB를 다시 키우지 않게 막습니다.",
        "signals",
        min_value=12,
        max_value=2000,
        step=12,
    ),
    "strategy_insight_sidecar_max_lines": SettingMeta(
        "Whale/세시반 JSONL sidecar 상한",
        "SQLite가 기준 저장소일 때 legacy JSONL sidecar를 source별 소형 캐시로 유지할 최대 행 수입니다.",
        "signals",
        min_value=0,
        max_value=5000,
        step=50,
    ),
    "strategy_insight_migrate_legacy_jsonl": SettingMeta(
        "Whale/세시반 legacy JSONL 이관",
        "기존 JSONL 전체를 SQLite로 자동 이관할지 정합니다. 운영 중에는 DB 재팽창 방지를 위해 꺼두는 것이 기본입니다.",
        "signals",
    ),
    "valuation_watchlist": SettingMeta(
        "밸류 관심종목",
        "정기적으로 네이버/WiseReport 밸류를 수집할 종목 코드 목록입니다.",
        "signals",
        input_type="textarea",
    ),
    "valuation_min_refresh_hours": SettingMeta(
        "밸류 재수집 간격(시간)",
        "같은 종목을 다시 수집하기 전 최소 대기 시간입니다.",
        "signals",
        min_value=1,
        max_value=168,
        step=1,
    ),
    "valuation_max_symbols_per_collect": SettingMeta(
        "밸류 1회 최대 종목",
        "한 번의 밸류 수집에서 처리할 최대 종목 수입니다.",
        "signals",
        min_value=1,
        max_value=300,
        step=1,
    ),
    "valuation_auto_collect": SettingMeta(
        "밸류 자동 보강",
        "KIS 블록 트레이더 루프가 관심종목과 활성 블록의 오래된 네이버/WiseReport 밸류를 소량씩 보강합니다.",
        "signals",
    ),
    "valuation_auto_min_interval_sec": SettingMeta(
        "밸류 자동 보강 최소 간격(초)",
        "KIS 블록 트레이더가 다음 밸류 자동 보강을 시도하기 전 기다리는 최소 시간입니다.",
        "signals",
        min_value=300,
        max_value=21600,
        step=300,
    ),
    "valuation_auto_max_symbols": SettingMeta(
        "밸류 자동 보강 1회 종목 수",
        "룰 틱을 방해하지 않도록 KIS 블록 트레이더 루프에서 한 번에 보강할 최대 종목 수입니다.",
        "signals",
        min_value=1,
        max_value=30,
        step=1,
    ),
    "etf_research_auto_collect": SettingMeta(
        "ETF 자동 리서치",
        "블록 트레이더 루프에서 ETF 리서치를 자동 보강합니다.",
        "signals",
    ),
    "etf_research_universe": SettingMeta(
        "ETF 리서치 유니버스",
        "ETF 리서치 대상으로 삼을 코드:이름 목록입니다.",
        "signals",
        input_type="textarea",
    ),
    "etf_research_max_symbols": SettingMeta(
        "ETF 최대 수집 종목",
        "한 번에 수집할 ETF 최대 종목 수입니다.",
        "signals",
        min_value=1,
        max_value=300,
        step=1,
    ),
    "etf_research_retention_days": SettingMeta(
        "ETF hot 보존일",
        "ETF 리서치 원본 스냅샷을 active 테이블에 보존할 기간입니다.",
        "signals",
        min_value=1,
        max_value=60,
        step=1,
    ),
    "etf_research_archive_retention_days": SettingMeta(
        "ETF archive 보존일",
        "압축 ETF 리서치 스냅샷 archive를 보존할 총 기간입니다.",
        "signals",
        min_value=1,
        max_value=180,
        step=1,
    ),
    "kis_rate_limit_enabled": SettingMeta(
        "KIS rate limiter",
        "KIS REST 호출 제한 보호를 켭니다.",
        "kis",
    ),
    "kis_rest_rate_limit_per_sec": SettingMeta(
        "KIS 초당 호출 제한",
        "KIS REST 호출을 초당 몇 회로 제한할지 정합니다.",
        "kis",
        min_value=0.1,
        max_value=20,
        step=0.1,
    ),
    "kis_account_min_interval_sec": SettingMeta(
        "KIS 계좌조회 최소 간격(초)",
        "잔고/계좌 조회 API만 별도 느린 버킷으로 보호하는 최소 간격입니다.",
        "kis",
        min_value=0.2,
        max_value=10,
        step=0.1,
    ),
    "kis_token_min_interval_sec": SettingMeta(
        "KIS 토큰 최소 재발급 간격",
        "토큰 재발급 요청 사이의 최소 간격입니다.",
        "kis",
        min_value=10,
        max_value=3600,
        step=5,
    ),
    "dashboard_kis_balance_cache_ttl_sec": SettingMeta(
        "KIS 대시보드 잔고 캐시(초)",
        "대시보드 새로고침 때 KIS 잔고 조회를 재사용하는 시간입니다. 호출 제한과 화면 신선도 사이의 균형값입니다.",
        "kis",
        min_value=0,
        max_value=600,
        step=5,
    ),
    "dashboard_crypto_balance_cache_ttl_sec": SettingMeta(
        "가상자산 대시보드 잔고 캐시(초)",
        "대시보드 새로고침 때 업비트/빗썸/바이낸스 잔고 조회를 재사용하는 시간입니다. 주문 판단용 틱이 아니라 화면 표시용 캐시입니다.",
        "binance",
        min_value=0,
        max_value=600,
        step=5,
    ),
    "dashboard_stale_balance_cache_ttl_sec": SettingMeta(
        "대시보드 stale 잔고 표시 캐시(초)",
        "fresh 캐시가 만료됐더라도 마지막 성공 잔고를 즉시 stale 상태로 보여주는 최대 시간입니다. 거래 판단에는 사용하지 않는 화면 표시용 완충값입니다.",
        "system",
        min_value=0,
        max_value=7200,
        step=60,
    ),
    "dashboard_kis_balance_error_cooldown_sec": SettingMeta(
        "KIS 대시보드 오류 쿨다운(초)",
        "KIS 잔고 조회가 실패한 직후 같은 실패 요청을 반복하지 않고 오류 상태를 유지하는 시간입니다.",
        "kis",
        min_value=0,
        max_value=600,
        step=5,
    ),
    "dashboard_balance_fetch_timeout_sec": SettingMeta(
        "대시보드 잔고 조회 타임아웃(초)",
        "대시보드 강제 새로고침 때 거래소별 잔고 조회가 화면 응답을 오래 붙잡지 않도록 제한하는 시간입니다.",
        "system",
        min_value=1,
        max_value=60,
        step=1,
    ),
    "dashboard_kis_us_balance_enabled": SettingMeta(
        "KIS 미장 대시보드 조회",
        "대시보드에서 KIS 1번 미장 잔고를 foreground로 조회할지 여부입니다. 국장 중심 운용이면 꺼두면 새로고침이 가벼워집니다.",
        "kis",
    ),
    "kis_primary_product_code": SettingMeta(
        "국장1 상품코드",
        "KIS 국장1 계좌 상품코드입니다. 계좌번호와 키는 UI에서 노출하지 않습니다.",
        "kis",
    ),
    "kis_secondary_product_code": SettingMeta(
        "국장2 상품코드",
        "KIS 국장2 계좌 상품코드입니다. 계좌번호와 키는 UI에서 노출하지 않습니다.",
        "kis",
    ),
    "runtime_max_age_sec": SettingMeta(
        "런타임 stale 기준(초)",
        "런타임 상태를 오래됨으로 볼 기준입니다.",
        "ops",
        min_value=10,
        max_value=3600,
        step=5,
    ),
    "runtime_write_interval_sec": SettingMeta(
        "런타임 기록 주기(초)",
        "runtime runner 상태 기록 주기입니다.",
        "ops",
        min_value=1,
        max_value=300,
        step=1,
    ),
    "runtime_storage_large_file_threshold_mb": SettingMeta(
        "대용량 파일 기준(MB)",
        "런타임 스토리지 정리에서 큰 파일로 판단할 기준입니다.",
        "ops",
        min_value=1,
        max_value=1024,
        step=1,
    ),
    "runtime_storage_prune_unreferenced_pdfs": SettingMeta(
        "미참조 PDF 정리",
        "DB에서 참조하지 않는 리포트 PDF 정리를 허용합니다.",
        "ops",
    ),
    "runtime_storage_prune_extracted_report_pdfs": SettingMeta(
        "추출완료 PDF 정리",
        "본문이 DB에 저장된 오래된 리포트 원본 PDF 정리를 허용합니다.",
        "ops",
        risk="high",
    ),
    "runtime_storage_extracted_report_pdf_retention_days": SettingMeta(
        "추출완료 PDF 보관일",
        "본문 추출이 끝난 리포트 원본 PDF를 보관할 최소 일수입니다.",
        "ops",
        min_value=1,
        max_value=365,
        step=1,
        risk="high",
    ),
    "runtime_storage_prune_rag_repair_artifacts": SettingMeta(
        "RAG 복구 산출물 정리",
        "오래된 RAG corrupt/legacy 백업 산출물을 런타임 정리 대상으로 포함합니다.",
        "ops",
        risk="medium",
    ),
    "runtime_storage_rag_repair_artifact_retention_days": SettingMeta(
        "RAG 복구 산출물 보관일",
        "RAG 복구 백업과 격리 디렉터리를 보관할 최소 일수입니다.",
        "ops",
        min_value=1,
        max_value=90,
        step=1,
        risk="medium",
    ),
    "runtime_storage_prune_old_runtime_logs": SettingMeta(
        "오래된 로그 정리",
        "보관일을 넘긴 .runtime 로그 파일을 스토리지 정리 대상으로 포함합니다.",
        "ops",
        risk="low",
    ),
    "runtime_storage_log_retention_days": SettingMeta(
        "로그 보관일",
        "런타임 로그 파일을 보관할 최소 일수입니다. 최근 로그는 유지합니다.",
        "ops",
        min_value=1,
        max_value=90,
        step=1,
        risk="low",
    ),
    "runtime_storage_rotate_large_active_logs": SettingMeta(
        "활성 로그 회전",
        "최근 활성 로그가 너무 커지면 압축 보관하고 최근 꼬리만 남깁니다.",
        "ops",
        risk="low",
    ),
    "runtime_storage_active_log_max_mb": SettingMeta(
        "활성 로그 최대 크기(MB)",
        "이 크기를 넘은 최근 .log 파일은 런타임 정리 시 압축 회전됩니다.",
        "ops",
        min_value=1,
        max_value=1024,
        step=1,
        risk="low",
    ),
    "runtime_storage_active_log_tail_kb": SettingMeta(
        "활성 로그 유지 꼬리(KB)",
        "압축 회전 후 현재 로그 파일에 남길 최근 로그 크기입니다.",
        "ops",
        min_value=64,
        max_value=65536,
        step=64,
        risk="low",
    ),
    "runtime_storage_prune_scratch_artifacts": SettingMeta(
        "임시 산출물 정리",
        "보관일을 넘긴 .runtime 루트의 _tmp_/tmp_/before-cleanup 파일을 정리 대상으로 포함합니다.",
        "ops",
        risk="low",
    ),
    "runtime_storage_scratch_artifact_retention_days": SettingMeta(
        "임시 산출물 보관일",
        "테스트·수동 점검으로 생긴 scratch 파일을 보관할 최소 일수입니다. 최근 파일은 유지합니다.",
        "ops",
        min_value=1,
        max_value=90,
        step=1,
        risk="low",
    ),
    "runtime_storage_prune_old_backtest_artifacts": SettingMeta(
        "오래된 백테스트 산출물 정리",
        "보관일을 넘긴 .runtime 루트의 backtest_*.json 파일을 정리 대상으로 포함합니다.",
        "ops",
        risk="low",
    ),
    "runtime_storage_backtest_artifact_retention_days": SettingMeta(
        "백테스트 산출물 보관일",
        "백테스트 JSON 산출물을 보관할 최소 일수입니다. 최근 결과는 유지합니다.",
        "ops",
        min_value=1,
        max_value=365,
        step=1,
        risk="low",
    ),
    "runtime_storage_prune_old_ui_check_artifacts": SettingMeta(
        "오래된 UI 검증 이미지 정리",
        ".runtime/ui-check의 오래된 화면 검증 이미지를 정리 대상으로 포함합니다.",
        "ops",
        risk="low",
    ),
    "runtime_storage_ui_check_artifact_retention_days": SettingMeta(
        "UI 검증 이미지 보관일",
        "화면 검증 스크린샷을 보관할 최소 일수입니다. 최근 결과는 유지합니다.",
        "ops",
        min_value=1,
        max_value=365,
        step=1,
        risk="low",
    ),
    "runtime_storage_prune_zero_byte_runtime_markers": SettingMeta(
        "0바이트 DB 표식 정리",
        "실제 DB 옆에 잘못 생성된 0바이트 테이블/컬럼 표식 파일을 정리 대상으로 포함합니다.",
        "ops",
        risk="low",
    ),
    "runtime_storage_zero_byte_marker_retention_days": SettingMeta(
        "0바이트 표식 보관일",
        "0바이트 DB 표식 파일을 보관할 최소 일수입니다. 최근 생성 표식은 유지합니다.",
        "ops",
        min_value=1,
        max_value=90,
        step=1,
        risk="low",
    ),
    "runtime_storage_database_compact_min_free_mb": SettingMeta(
        "DB compact 후보 최소 여유(MB)",
        "SQLite freelist 여유 공간이 이 크기 이상일 때 compact 후보로 표시합니다. 자동 compact는 수행하지 않습니다.",
        "ops",
        min_value=0,
        max_value=1024,
        step=1,
        risk="low",
    ),
    "runtime_storage_database_compact_min_free_ratio_pct": SettingMeta(
        "DB compact 후보 최소 비율(%)",
        "SQLite DB 전체 크기 대비 freelist 여유 공간 비율이 이 값 이상일 때 compact 후보로 표시합니다.",
        "ops",
        min_value=0,
        max_value=100,
        step=1,
        risk="low",
    ),
    "watchdog_enabled": SettingMeta(
        "워치독",
        "운영 runner가 멈추면 allowlist 기반으로 자동 재시작합니다.",
        "ops",
    ),
    "trading_validation_max_age_sec": SettingMeta(
        "트레이딩 검증 유효시간(초)",
        "19개 자동매매 검증 결과가 이 시간보다 오래되면 Live Authority에서 stale로 보고 신규 위험 확대를 막습니다.",
        "ops",
        min_value=300,
        max_value=86400,
        step=300,
        risk="high",
    ),
    "trading_validation_payload_compaction_enabled": SettingMeta(
        "검증 payload 압축",
        "오래된 19개 검증 상세 payload를 요약본으로 압축해 validation DB 성장을 억제합니다.",
        "ops",
        risk="low",
    ),
    "trading_validation_payload_recent_rows_per_group": SettingMeta(
        "검증 원문 보존 rows",
        "venue/scope/revision별로 원문 상세 payload를 유지할 최신 검증 row 수입니다.",
        "ops",
        min_value=6,
        max_value=288,
        step=6,
        risk="medium",
    ),
    "trading_validation_payload_max_rows_per_group": SettingMeta(
        "검증 이력 최대 rows",
        "venue/scope/revision별로 압축 후에도 유지할 최대 검증 이력 row 수입니다.",
        "ops",
        min_value=48,
        max_value=5000,
        step=24,
        risk="medium",
    ),
    "trading_validation_payload_compact_min_chars": SettingMeta(
        "검증 압축 최소 크기",
        "이 글자 수보다 큰 오래된 validation payload만 압축합니다.",
        "ops",
        min_value=1000,
        max_value=200000,
        step=1000,
        risk="low",
    ),
    "binance_validation_spot_fee_rate": SettingMeta(
        "Binance 현물 검증 수수료율",
        "실제 commission 기록이 없을 때 Binance 현물 성과 검증에 적용할 왕복 notional 기반 안전측 수수료율입니다.",
        "ops",
        min_value=0,
        max_value=0.01,
        step=0.0001,
        risk="high",
    ),
    "binance_validation_futures_fee_rate": SettingMeta(
        "Binance 선물 검증 수수료율",
        "실제 commission 기록이 없을 때 Binance 선물 성과 검증에 적용할 왕복 notional 기반 안전측 수수료율입니다.",
        "ops",
        min_value=0,
        max_value=0.01,
        step=0.0001,
        risk="high",
    ),
    "binance_validation_slippage_bps": SettingMeta(
        "Binance 검증 슬리피지(bps)",
        "실제 slippage 기록이 없을 때 성과 검증 비용에 추가할 왕복 notional 기반 슬리피지 bps입니다.",
        "ops",
        min_value=0,
        max_value=100,
        step=0.5,
        risk="high",
    ),
    "binance_validation_initial_equity_usdt": SettingMeta(
        "Binance 검증 기준 자본(USDT)",
        "Binance 쥬의 MDD, Calmar, 수익률, 파산확률 계산에 사용할 기준 자본입니다.",
        "ops",
        min_value=1,
        max_value=1_000_000,
        step=10,
        risk="high",
    ),
    "kis_validation_buy_fee_rate": SettingMeta(
        "KIS 매수 검증 수수료율",
        "KIS 국내주식 실제 수수료 기록이 없을 때 매수 notional에 적용할 검증용 수수료율입니다.",
        "ops",
        min_value=0,
        max_value=0.01,
        step=0.00001,
        risk="high",
    ),
    "kis_validation_sell_fee_rate": SettingMeta(
        "KIS 매도 검증 수수료율",
        "KIS 국내주식 실제 수수료 기록이 없을 때 매도 notional에 적용할 검증용 수수료율입니다.",
        "ops",
        min_value=0,
        max_value=0.01,
        step=0.00001,
        risk="high",
    ),
    "kis_validation_sell_tax_rate": SettingMeta(
        "KIS 매도 검증 거래세율",
        "KIS 국내주식 실제 세금 기록이 없을 때 매도 notional에 적용할 검증용 거래세율입니다.",
        "ops",
        min_value=0,
        max_value=0.01,
        step=0.0001,
        risk="high",
    ),
    "kis_validation_slippage_bps": SettingMeta(
        "KIS 검증 슬리피지(bps)",
        "실제 slippage 기록이 없을 때 KIS 성과 검증 비용에 추가할 왕복 notional 기반 슬리피지 bps입니다.",
        "ops",
        min_value=0,
        max_value=100,
        step=0.5,
        risk="high",
    ),
    "kis_validation_initial_equity_krw": SettingMeta(
        "KIS 검증 기준 자본(KRW)",
        "KIS 쥬의 MDD, Calmar, 수익률, 파산확률 계산에 사용할 기준 원화 자본입니다.",
        "ops",
        min_value=10_000,
        max_value=10_000_000_000,
        step=100_000,
        risk="high",
    ),
    "watchdog_interval_sec": SettingMeta(
        "워치독 점검 주기(초)",
        "기본값은 30분입니다. 너무 짧게 잡으면 불필요한 운영 churn이 생길 수 있습니다.",
        "ops",
        min_value=300,
        max_value=86400,
        step=300,
    ),
    "watchdog_cooldown_sec": SettingMeta(
        "워치독 재시작 cooldown(초)",
        "같은 runner를 다시 재시작하기 전 최소 대기 시간입니다.",
        "ops",
        min_value=60,
        max_value=3600,
        step=60,
    ),
    "watchdog_flap_window_sec": SettingMeta(
        "워치독 flapping window(초)",
        "반복 장애를 판정하는 시간 창입니다.",
        "ops",
        min_value=300,
        max_value=21600,
        step=300,
    ),
    "watchdog_max_restarts_per_window": SettingMeta(
        "워치독 최대 재시작 수",
        "시간 창 안에서 같은 runner를 자동 재시작할 수 있는 최대 횟수입니다.",
        "ops",
        min_value=1,
        max_value=20,
        step=1,
    ),
    "watchdog_runner_keys": SettingMeta(
        "워치독 대상 runner",
        "비워두면 기본 핵심 runner 전체를 감시합니다. 필요하면 쉼표로 제한할 수 있습니다.",
        "ops",
        input_type="textarea",
    ),
    "allow_origins": SettingMeta(
        "CORS 허용 origin",
        "UI/API 접근 origin 목록입니다. 실거래 앱에서는 코드/env 배포로만 변경합니다.",
        "ops",
        input_type="textarea",
        risk="danger",
        editable=False,
    ),
    "reports_ui_allowed_cidrs": SettingMeta(
        "Reports UI CIDR",
        "리포트 마이크로서비스 UI 접근 허용 CIDR입니다.",
        "ops",
        input_type="textarea",
        risk="warn",
    ),
    "reports_ui_trust_proxy": SettingMeta(
        "Reports trust proxy",
        "프록시 헤더를 신뢰합니다. 노출 환경에서는 신중히 켜야 합니다.",
        "ops",
        risk="danger",
    ),
    "portfolio_coach_enabled": SettingMeta(
        "포트폴리오 코치",
        "레거시 포트폴리오 코치 레이어를 켭니다.",
        "legacy",
    ),
}


def _field_aliases(field: Any) -> list[str]:
    out: list[str] = []
    alias = getattr(field, "alias", None)
    if isinstance(alias, str) and alias:
        out.append(alias)
    validation_alias = getattr(field, "validation_alias", None)
    choices = getattr(validation_alias, "choices", None)
    if choices:
        out.extend(str(item) for item in choices if str(item))
    elif isinstance(validation_alias, str) and validation_alias:
        out.append(validation_alias)
    return list(dict.fromkeys(out))


def _primary_env_name(key: str, field: Any) -> str:
    aliases = _field_aliases(field)
    for alias in aliases:
        if alias.startswith("TRADECRAFT_"):
            return alias
    return aliases[0] if aliases else key.upper()


def _is_secret_field(key: str) -> bool:
    if key in SECRET_EXCEPTIONS:
        return False
    if key in LOCKED_FIELDS:
        return True
    lower = key.lower()
    return any(part in lower for part in SECRET_KEYWORDS)


def _is_retired_field(key: str) -> bool:
    return any(key.startswith(prefix) for prefix in RETIRED_PREFIXES)


def _category_for_key(key: str) -> str:
    explicit = META.get(key)
    if explicit and explicit.category:
        return explicit.category
    if key.startswith(("llm_", "research_codex", "naver_reports_llm")):
        return "ai"
    if key.startswith(("kis_block_", "block_horizon")):
        return "trading"
    if key.startswith(("market_judge", "market_quote", "market_pulse")):
        return "market"
    if key.startswith(("investment_memory", "daily_discovery")):
        return "memory"
    if key.startswith(("research_", "naver_reports", "rag_")):
        return "research"
    if key.startswith(
        (
            "strategy_insight",
            "valuation_",
            "etf_research",
            "crypto_market_research",
            "crypto_pattern_lab",
            "crypto_alpha",
        )
    ):
        return "signals"
    if key.startswith(("kis_", "upbit_", "bithumb_", "binance_", "fx_", "usd_")):
        return "kis"
    if key.startswith(("runtime_", "reports_", "allow_", "host", "port", "admin_", "telegram_")):
        return "ops"
    if key.startswith("portfolio_coach"):
        return "legacy"
    return "advanced"


def _setting_label(key: str) -> str:
    meta = META.get(key)
    if meta and meta.label:
        return meta.label
    return key.replace("_", " ")


def _setting_description(key: str) -> str:
    meta = META.get(key)
    if meta and meta.description:
        return meta.description
    if key.endswith("_path"):
        return "로컬 런타임 파일/DB 경로입니다. 변경 후 재시작이 필요합니다."
    if key.endswith("_url") or key.endswith("_base_url"):
        return "외부 API/수집 대상 URL입니다. 변경 전 연결 대상을 확인하세요."
    if key.endswith("_enabled"):
        return "해당 기능 루프를 켜거나 끕니다."
    if key.endswith("_interval_sec"):
        return "해당 러너/수집기의 실행 간격입니다."
    return "AppSettings에서 감지한 운영 설정입니다."


def _annotation_name(annotation: Any) -> str:
    if annotation is bool:
        return "bool"
    if annotation is int:
        return "int"
    if annotation is float:
        return "float"
    return "str"


def _input_type(key: str, annotation: Any, secret: bool) -> str:
    meta = META.get(key)
    if secret:
        return "secret_status"
    if meta and meta.input_type:
        return meta.input_type
    if meta and meta.choices:
        return "select"
    if annotation is bool:
        return "toggle"
    if annotation in {int, float}:
        return "number"
    if key.endswith("_json") or key.endswith("_sources") or key.endswith("_urls"):
        return "textarea"
    if any(part in key for part in ("query", "persona", "universe", "targets", "origins", "cidrs")):
        return "textarea"
    return "text"


def _is_editable(key: str, secret: bool) -> bool:
    meta = META.get(key)
    if meta and meta.editable is not None:
        return meta.editable
    if secret or key in LOCKED_FIELDS or key in ONE_SHOT_FIELDS:
        return False
    return True


def _risk_for_key(key: str) -> str:
    meta = META.get(key)
    if meta and meta.risk != "normal":
        return meta.risk
    if key in HIGH_RISK_FIELDS:
        return "danger"
    if key in WARNING_FIELDS or key.endswith("_base_url"):
        return "warn"
    return "normal"


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        if text.startswith("export "):
            text = text[7:].strip()
        if "=" not in text:
            continue
        key, value = text.split("=", 1)
        key = key.strip()
        value = value.strip()
        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in {'"', "'"}
        ):
            value = value[1:-1]
        if key:
            out[key] = value
    return out


def _value_to_display(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    return value


def _stringify_env_value(value: Any, value_type: str) -> str:
    if value_type == "bool":
        return "true" if bool(value) else "false"
    if value_type == "int":
        return str(int(value))
    if value_type == "float":
        return str(float(value)).rstrip("0").rstrip(".")
    return str(value)


def _format_env_line(env_name: str, value: Any, value_type: str) -> str:
    text = _stringify_env_value(value, value_type)
    if text == "" or re.fullmatch(r"[A-Za-z0-9_./:@,+\-|]+", text):
        return f"{env_name}={text}"
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'{env_name}="{escaped}"'


def _coerce_value(key: str, raw: Any, value_type: str) -> Any:
    meta = META.get(key) or SettingMeta()
    if value_type == "bool":
        if isinstance(raw, bool):
            value = raw
        else:
            text = str(raw).strip().lower()
            if text in {"true", "1", "yes", "y", "on", "enabled"}:
                value = True
            elif text in {"false", "0", "no", "n", "off", "disabled"}:
                value = False
            else:
                raise HTTPException(status_code=400, detail=f"{key}: invalid boolean")
    elif value_type == "int":
        try:
            value = int(float(str(raw).replace(",", "").strip()))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"{key}: invalid integer") from exc
    elif value_type == "float":
        try:
            value = float(str(raw).replace(",", "").strip())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"{key}: invalid number") from exc
    else:
        value = str(raw)

    if value_type in {"int", "float"}:
        number = float(value)
        if meta.min_value is not None and number < meta.min_value:
            raise HTTPException(status_code=400, detail=f"{key}: below minimum")
        if meta.max_value is not None and number > meta.max_value:
            raise HTTPException(status_code=400, detail=f"{key}: above maximum")
    if meta.choices and str(value) not in set(meta.choices):
        raise HTTPException(status_code=400, detail=f"{key}: invalid choice")
    return value


def _write_env_updates(path: Path, updates: dict[str, tuple[Any, str]]) -> None:
    original_lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    remaining = dict(updates)
    handled: set[str] = set()
    new_lines: list[str] = []
    for line in original_lines:
        stripped = line.strip()
        prefix = "export " if stripped.startswith("export ") else ""
        candidate = stripped[7:].strip() if prefix else stripped
        if candidate and not candidate.startswith("#") and "=" in candidate:
            key = candidate.split("=", 1)[0].strip()
            if key in updates:
                if key in handled:
                    continue
                value, value_type = updates[key]
                remaining.pop(key, None)
                handled.add(key)
                new_lines.append(f"{prefix}{_format_env_line(key, value, value_type)}")
                continue
        new_lines.append(line)
    if remaining:
        if new_lines and new_lines[-1].strip():
            new_lines.append("")
        new_lines.append("# Updated by HERMES settings UI")
        for key, (value, value_type) in remaining.items():
            new_lines.append(_format_env_line(key, value, value_type))
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup_dir = Path(".runtime/config_backups")
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        shutil.copy2(path, backup_dir / f".env.{stamp}.bak")
    path.write_text("\n".join(new_lines).rstrip() + "\n", encoding="utf-8")


def build_settings_catalog(
    settings: AppSettings,
    *,
    env_path: Path | str = ".env",
) -> dict[str, Any]:
    env_file = Path(env_path)
    env_values = _read_env_file(env_file)
    items: list[dict[str, Any]] = []
    for key, field in settings.__class__.model_fields.items():
        if _is_retired_field(key):
            continue
        annotation = getattr(field, "annotation", str)
        value_type = _annotation_name(annotation)
        aliases = _field_aliases(field)
        env_name = _primary_env_name(key, field)
        secret = _is_secret_field(key)
        category = _category_for_key(key)
        meta = META.get(key) or SettingMeta()
        current_value = getattr(settings, key)
        env_raw = env_values.get(env_name)
        editable = _is_editable(key, secret)
        risk = _risk_for_key(key)
        item: dict[str, Any] = {
            "key": key,
            "label": _setting_label(key),
            "description": _setting_description(key),
            "category": category,
            "category_label": CATEGORY_LABELS.get(category, category),
            "env": env_name,
            "aliases": aliases,
            "type": value_type,
            "input_type": _input_type(key, annotation, secret),
            "editable": editable,
            "secret": secret,
            "risk": risk,
            "restart_required": True,
            "default": _value_to_display(getattr(field, "default", None)),
            "configured": bool(str(current_value or "").strip()) if secret else None,
            "configured_in_env": bool(env_raw) if secret else None,
            "value": None if secret else _value_to_display(current_value),
            "env_value": None if secret else env_raw,
            "pending_restart": False,
            "locked_reason": "",
            "choices": list(meta.choices),
            "min": meta.min_value,
            "max": meta.max_value,
            "step": meta.step,
        }
        if not editable:
            if secret:
                item["locked_reason"] = "secret_masked"
            elif key in ONE_SHOT_FIELDS:
                item["locked_reason"] = "one_shot_flag"
            else:
                item["locked_reason"] = "locked"
        if not secret and env_raw is not None:
            item["pending_restart"] = env_raw != _stringify_env_value(
                current_value,
                value_type,
            )
        items.append(item)

    categories = [
        {
            "key": key,
            "label": label,
            "count": sum(1 for item in items if item["category"] == key),
            "editable_count": sum(
                1 for item in items if item["category"] == key and item["editable"]
            ),
        }
        for key, label in CATEGORY_LABELS.items()
        if any(item["category"] == key for item in items)
    ]
    return {
        "env_path": str(env_file),
        "restart_required_on_change": True,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "categories": categories,
        "items": sorted(items, key=lambda row: (row["category"], row["key"])),
    }


def update_settings_env(
    settings: AppSettings,
    updates: dict[str, Any],
    *,
    env_path: Path | str = ".env",
    confirm_high_risk: bool = False,
) -> dict[str, Any]:
    if not isinstance(updates, dict) or not updates:
        raise HTTPException(status_code=400, detail="updates required")

    by_key = settings.__class__.model_fields
    env_updates: dict[str, tuple[Any, str]] = {}
    changed: list[dict[str, Any]] = []
    high_risk: list[str] = []
    for key, raw_value in updates.items():
        if _is_retired_field(key):
            raise HTTPException(status_code=400, detail=f"{key}: setting is retired")
        if key not in by_key:
            raise HTTPException(status_code=400, detail=f"{key}: unknown setting")
        field = by_key[key]
        secret = _is_secret_field(key)
        editable = _is_editable(key, secret)
        if not editable:
            raise HTTPException(status_code=400, detail=f"{key}: setting is locked")
        risk = _risk_for_key(key)
        if risk == "danger":
            high_risk.append(key)
        annotation = getattr(field, "annotation", str)
        value_type = _annotation_name(annotation)
        value = _coerce_value(key, raw_value, value_type)
        env_name = _primary_env_name(key, field)
        env_updates[env_name] = (value, value_type)
        changed.append(
            {
                "key": key,
                "env": env_name,
                "value": _value_to_display(value),
                "risk": risk,
            }
        )
    if high_risk and not confirm_high_risk:
        raise HTTPException(
            status_code=400,
            detail=f"high risk confirmation required: {', '.join(high_risk)}",
        )

    env_file = Path(env_path)
    _write_env_updates(env_file, env_updates)
    return {
        "status": "saved",
        "env_path": str(env_file),
        "changed": changed,
        "restart_required": True,
        "message": "설정은 .env에 저장되었습니다. 실행 중인 control/runner를 재시작해야 적용됩니다.",
        "catalog": build_settings_catalog(settings, env_path=env_file),
    }
