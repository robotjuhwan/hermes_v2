# TradeCraft UI (Restart)

트레이딩 봇 재시작용 첫 단계 프로젝트입니다.

- 웹 UI 우선: 반응형(PC/모바일)
- 거래소/시장 탭: 전체, 업비트, 바이낸스, 국장, 미장 잔고 조회
- 전체 자산 총합 + 탭별 자산/현금/평가손익 요약
- 자산 상세: 계좌현금(KRW/USDT/USD)과 보유 포지션을 단일 테이블로 통합
- 봇 세션 패널: 단기 세션 + 중/장기 밸런스 세션 상태 모니터링
- 단기 세션 지표: 실현/미실현/수수료/순손익(실시간) 분리 표시
- 밸런스 세션 원칙: 드리프트, 턴오버, 벤치마크, 트래킹에러, 리밸런스 기준 표시
- Telegram API 브릿지: 상태 조회 + webhook 수신 + getUpdates 폴링
- Upbit API 연동(초기): 키 설정 시 대시보드 업비트 잔고 실조회
- Telegram CLI:
  - `/help`
  - `/status`
  - `/venues`
  - `/balance <venue>`
  - `/upbit` `/binance` `/krx` `/kr2` `/us`
  - `/sessions`
  - `/session <id>`
- Control Plane(UI + Telegram) / Bot Runtime 분리 구조
- 백엔드: FastAPI

Telegram 설정은 UI에서 변경하지 않고 `.env`로만 관리합니다.

## Run

```bash
cd /Users/juhwan/hermes_v2
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Control Plane(UI + Telegram):

```bash
tradecraft-control
```

Bot Runtime(매매 모듈, 별도 프로세스):

```bash
python -m tradecraft.runtime.runner
```

Backtest Runtime(가상시간 가속, 별도 프로세스):

```bash
tradecraft-backtest --cycles 1440 --speed 240
```

웹 UI에서 `백테스트 랩` 탭으로 시나리오 실행/진행률/PNL 곡선을 실시간으로 확인할 수 있습니다.

브라우저에서 [http://127.0.0.1:8000](http://127.0.0.1:8000) 접속.

## Environment

`.env.example` 참고:

- `TRADECRAFT_TELEGRAM_BOT_TOKEN`
- `TRADECRAFT_TELEGRAM_CHAT_ID`
- `UPBIT_ACCESS_KEY`
- `UPBIT_SECRET_KEY`
- `UPBIT_BASE_URL`
- `BITHUMB_ACCESS_KEY`
- `BITHUMB_SECRET_KEY`
- `BITHUMB_BASE_URL`
- `BINANCE_SPOT_API_KEY`
- `BINANCE_SPOT_API_SECRET`
- `BINANCE_SPOT_BASE_URL`
- `BINANCE_FUTURES_API_KEY`
- `BINANCE_FUTURES_API_SECRET`
- `BINANCE_FUTURES_BASE_URL`
- `BINANCE_USDT_KRW`
- `KIS_BASE_URL`
- `KIS_PRIMARY_APP_KEY`
- `KIS_PRIMARY_APP_SECRET`
- `KIS_PRIMARY_ACCOUNT_NO`
- `KIS_PRIMARY_PRODUCT_CODE`
- `KIS_SECONDARY_APP_KEY`
- `KIS_SECONDARY_APP_SECRET`
- `KIS_SECONDARY_ACCOUNT_NO`
- `KIS_SECONDARY_PRODUCT_CODE`
- `TRADECRAFT_RUNTIME_STATE_PATH`
- `TRADECRAFT_RUNTIME_SESSIONS_PATH`
- `TRADECRAFT_RUNTIME_MAX_AGE_SEC`
- `TRADECRAFT_RUNTIME_WRITE_INTERVAL_SEC`
- `TRADECRAFT_BACKTEST_RESULT_PATH`
- `TRADECRAFT_BACKTEST_CYCLES`
- `TRADECRAFT_BACKTEST_STEP_SEC`
- `TRADECRAFT_BACKTEST_SPEED`
- `TRADECRAFT_BACKTEST_INITIAL_PRICE`
- `TRADECRAFT_BACKTEST_VOLATILITY_BPS`
- `TRADECRAFT_BACKTEST_DRIFT_BPS`
- `TRADECRAFT_BACKTEST_FEE_RATE`
- `TRADECRAFT_BACKTEST_SLIPPAGE_BPS`
- `TRADECRAFT_BACKTEST_SEED`
- `TRADECRAFT_BACKTEST_LIVE_STATE_PATH`
- `TRADECRAFT_BACKTEST_LIVE_EMIT_INTERVAL`
- `TRADECRAFT_BACKTEST_LIVE_MAX_CURVE_POINTS`
- `TRADECRAFT_HOST`
- `TRADECRAFT_PORT`
- `TRADECRAFT_ALLOW_ORIGINS`

## Reference Docs

- Exchange/Broker official API links:
  - `/Users/juhwan/hermes_v2/docs/exchange_api_official_docs.md`

## API (current)

- `GET /api/health`
- `GET /api/dashboard`
- `GET /api/telegram/status`
- `POST /api/telegram/webhook`
- `GET /api/backtest/scenarios`
- `GET /api/backtest/data-status`
- `GET /api/backtest/status`
- `POST /api/backtest/start`
- `POST /api/backtest/stop`

웹훅 URL이 비어 있어도 서버가 `getUpdates` 폴링으로 명령을 처리합니다.

Upbit 키가 설정되면 `GET /api/dashboard`의 업비트 자산이 실계정 잔고로 대체됩니다.

`POST /api/telegram/webhook`로 들어온 텔레그램 명령어는 아래를 지원합니다.

- `/help`
- `/status`
- `/venues`
- `/balance <venue>`
- `/upbit` `/bithumb` `/binance` `/krx` `/kr2` `/us`
- `/sessions`
- `/session <session_id>`

## Test

```bash
pytest
```

## Runtime Skeleton

`tradecraft.runtime`는 UI/Telegram(Control Plane)과 분리된 별도 실행체 골격입니다.

- `src/tradecraft/runtime/runner.py`
  - 런타임 프로세스 진입점
  - 주기적으로 세션 상태 스냅샷을 `.runtime/state.json`에 기록
  - `TRADECRAFT_RUNTIME_SESSIONS_PATH` 설정 시 외부 JSON 세션 파일 로드
- `src/tradecraft/runtime/engine.py`
  - 세션 루프 오케스트레이션
  - `strategy -> risk -> broker` 파이프라인 순서로 tick 처리
- `src/tradecraft/runtime/contracts.py`
  - Strategy/Risk/Broker 인터페이스(Protocol)
- `src/tradecraft/runtime/session_loader.py`
  - 런타임 전용 세션 로더
  - 파일이 없거나 깨졌을 때 기본 세션으로 자동 fallback
- `src/tradecraft/runtime/strategies.py`
  - `NoopShortTermStrategy`, `NoopBalanceStrategy` (골격 전략)
  - 전략 레지스트리(`register_strategy`) 제공
- `src/tradecraft/runtime/brokers.py`
  - `NoopBroker` (실주문 비활성, 인터페이스만 제공)
- `src/tradecraft/runtime/risk.py`
  - `NoopRiskManager` (리스크 모듈 자리만 확보)

세션 파일 예시(JSON):

```json
{
  "sessions": [
    {
      "session_id": "upbit_scalper",
      "venue_id": "upbit",
      "mode": "short_term",
      "cycle_sec": 20,
      "trade_symbol": "BTC/KRW",
      "strategy_id": "noop_short_term"
    }
  ]
}
```

샘플 파일은 `docs/runtime_sessions.example.json`에 있습니다.

현재는 실행 골격 단계이며, 실제 시세/신호/주문 라우팅은 추후 모듈을 붙이는 형태로 확장합니다.

## Backtest Skeleton

`tradecraft.backtest`는 매매 런타임과 분리된 원샷 백테스트 프로세스입니다.

- `src/tradecraft/backtest/runner.py`
  - 실행 시 세션/가상시계/리플레이를 한 번 돌리고 결과 JSON 저장
- `src/tradecraft/backtest/clock.py`
  - `step_sec`, `speed` 기반 VirtualClock
- `src/tradecraft/backtest/replay.py`
  - 외부 시세 없이도 재현 가능한 synthetic 가격 흐름
- `src/tradecraft/backtest/sim_broker.py`
  - 수수료/슬리피지 반영한 모의 체결기
- `src/tradecraft/backtest/engine.py`
  - 세션별 signal/fill/trade/PNL 요약 생성
- `src/tradecraft/backtest/live_manager.py`
  - UI/API에서 실시간 진행률/곡선 상태를 `.runtime/backtest_live.json`에 저장
- `src/tradecraft/backtest/scenarios.py`
  - baseline/bull/bear/high_vol/fee_stress 시나리오 프리셋 제공
- `src/tradecraft/backtest/data_registry.py`
  - 세션에서 관측된 symbol을 누적 기록해 데이터 준비 상태를 관리

기본 결과 파일은 `TRADECRAFT_BACKTEST_RESULT_PATH`(기본 `.runtime/backtest_result.json`)에 저장됩니다.
