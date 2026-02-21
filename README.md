# TradeCraft Control Plane

Freqtrade 기반 자동매매를 모니터링/제어하기 위한 Control Plane 프로젝트입니다.

- 웹 UI 우선: 반응형(PC/모바일)
- 거래소/시장 탭: 전체, 업비트, 바이낸스, 국장, 미장 잔고 조회
- 전체 자산 총합 + 탭별 자산/현금/평가손익 요약
- 자산 상세: 계좌현금(KRW/USDT/USD)과 보유 포지션을 단일 테이블로 통합
- 봇 세션 패널: Freqtrade Spot/Futures 오픈 포지션 + 런타임 세션 상태 모니터링
- 단기 세션 지표: 실현/미실현/수수료/순손익(실시간) 분리 표시
- 밸런스 세션 원칙: 드리프트, 턴오버, 벤치마크, 트래킹에러, 리밸런스 기준 표시
- Telegram API 브릿지: 상태 조회 + webhook 수신 + getUpdates 폴링
- Freqtrade API 브릿지: Spot/Futures 오픈 포지션/PNL/세션 연동
- Upbit API 연동(초기): 키 설정 시 대시보드 업비트 잔고 실조회
- Telegram CLI:
  - `/help`
  - `/status`
  - `/venues`
  - `/balance <venue>`
  - `/upbit` `/binance` `/krx` `/kr2` `/us`
  - `/sessions`
  - `/session <id>`
- Tradecraft는 UI/제어 계층, 자동매매 실행은 외부 Freqtrade 인스턴스가 담당
- 백엔드: FastAPI

Telegram 설정은 UI에서 변경하지 않고 `.env`로만 관리합니다.

## Run

```bash
cd /Users/juhwan/hermes_v2
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Tradecraft Control Plane(UI + Telegram + Integrations):

```bash
tradecraft-control
```

KRX 리서치 스케줄러(Codex CLI + 리포트 URL 수집):

```bash
tradecraft-research
```

KIS LLM 트레이더 러너(리서치 기반 종목선정 + 주문 실행):

```bash
tradecraft-kis-trader
```

네이버 증권 리포트 크롤러(DB 저장):

```bash
tradecraft-naver-reports
```

Freqtrade Spot/Futures는 외부 프로세스로 실행되며, Tradecraft에서 `/api/freqtrade/strategies` 제어 API를 통해 전략 시작/중지/상태 확인이 가능합니다.

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
- `USD_KRW`
- `FX_CACHE_TTL_SEC`
- `FREQTRADE_SPOT_API_URL`
- `FREQTRADE_SPOT_USERNAME`
- `FREQTRADE_SPOT_PASSWORD`
- `FREQTRADE_SPOT_CONFIG_PATH`
- `FREQTRADE_FUTURES_API_URL`
- `FREQTRADE_FUTURES_USERNAME`
- `FREQTRADE_FUTURES_PASSWORD`
- `FREQTRADE_FUTURES_CONFIG_PATH`
- `FREQTRADE_BOT_API_URLS`
- `FREQTRADE_EXECUTABLE_PATH`
- `FREQTRADE_WORKDIR`
- `FREQTRADE_RUNTIME_DIR`
- `FREQTRADE_STOP_TIMEOUT_SEC`
- `FREQTRADE_TIMEOUT_SEC`
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
- `TRADECRAFT_RESEARCH_STATE_PATH`
- `TRADECRAFT_RESEARCH_MAX_AGE_SEC`
- `TRADECRAFT_RESEARCH_ENABLED`
- `TRADECRAFT_RESEARCH_RUN_INTERVAL_SEC`
- `TRADECRAFT_RESEARCH_MAX_ITEMS`
- `TRADECRAFT_RESEARCH_MARKET_SCOPE`
- `TRADECRAFT_RESEARCH_CODEX_COMMAND`
- `TRADECRAFT_RESEARCH_CODEX_QUERY`
- `TRADECRAFT_RESEARCH_CODEX_TIMEOUT_SEC`
- `TRADECRAFT_RESEARCH_REPORT_URLS`
- `TRADECRAFT_RESEARCH_STRATEGY_MD_PATH`
- `TRADECRAFT_KIS_TRADER_ENABLED`
- `TRADECRAFT_KIS_TRADER_STATE_PATH`
- `TRADECRAFT_KIS_TRADER_INTERVAL_SEC`
- `TRADECRAFT_KIS_TRADER_LLM_COMMAND`
- `TRADECRAFT_KIS_TRADER_PERSONA`
- `TRADECRAFT_KIS_TRADER_MAX_ORDERS_PER_CYCLE`
- `TRADECRAFT_KIS_TRADER_MAX_BUDGET_PER_ORDER_KRW`
- `TRADECRAFT_KIS_TRADER_MIN_CONFIDENCE`
- `TRADECRAFT_KIS_TRADER_DEFAULT_ORDER_TYPE`
- `TRADECRAFT_KIS_TRADER_ALLOW_SELL`
- `TRADECRAFT_KIS_TRADER_MAX_CANDIDATE_CODES`
- `TRADECRAFT_KIS_TRADER_REPORT_CONTEXT_TOP_K`
- `TRADECRAFT_NAVER_REPORTS_ENABLED`
- `TRADECRAFT_NAVER_REPORTS_DB_PATH`
- `TRADECRAFT_NAVER_REPORTS_SEED_URL`
- `TRADECRAFT_NAVER_REPORTS_SEED_URLS`
- `TRADECRAFT_NAVER_REPORTS_INTERVAL_SEC`
- `TRADECRAFT_NAVER_REPORTS_MAX_PAGES`
- `TRADECRAFT_NAVER_REPORTS_SINCE_DATE`
- `TRADECRAFT_NAVER_REPORTS_REQUEST_DELAY_SEC`
- `TRADECRAFT_RAG_ENABLED`
- `TRADECRAFT_RAG_PERSIST_PATH`
- `TRADECRAFT_RAG_COLLECTION_NAME`
- `TRADECRAFT_RAG_SYNC_CHUNK_LIMIT`
- `TRADECRAFT_RAG_QUERY_TOP_K`
- `TRADECRAFT_HOST`
- `TRADECRAFT_PORT`
- `TRADECRAFT_ALLOW_ORIGINS`

RAG is optional. If `chromadb` is not installed, RAG endpoints stay available but return `available=false`.

운영 기준:
- 자동매매 세션/오픈 포지션 연동은 `FREQTRADE_*` 설정을 사용합니다.
- 전략별 포트 분리가 필요하면 `FREQTRADE_BOT_API_URLS`(`bot_id=url,bot_id=url`)로 봇별 API URL을 지정할 수 있습니다.
- `TRADECRAFT_RUNTIME_*`는 로컬 런타임 스냅샷 호환용(옵션)입니다.

## Reference Docs

- Exchange/Broker official API links:
  - `/Users/juhwan/hermes_v2/docs/exchange_api_official_docs.md`

## API (current)

- `GET /api/health`
- `GET /api/dashboard`
- `GET /api/telegram/status`
- `POST /api/telegram/webhook`
- `GET /api/freqtrade/strategies`
- `POST /api/freqtrade/strategies/start-all`
- `POST /api/freqtrade/strategies/stop-all`
- `POST /api/freqtrade/strategies/{bot_id}/start`
- `POST /api/freqtrade/strategies/{bot_id}/stop`
- `POST /api/freqtrade/strategies/{bot_id}/usdt-limit`
- `GET /api/kis/trader/status`
- `POST /api/kis/trader/run-once`
- `GET /api/reports/status`
- `POST /api/reports/crawl-once`
- `GET /api/reports/search`
- `GET /api/rag/status`
- `POST /api/rag/sync`
- `GET /api/rag/search`

웹훅 URL이 비어 있어도 서버가 `getUpdates` 폴링으로 명령을 처리합니다.

Upbit 키가 설정되면 `GET /api/dashboard`의 업비트 자산이 실계정 잔고로 대체됩니다.

Freqtrade Spot/Futures API 정보가 설정되면 `GET /api/dashboard`의 세션 목록에 Freqtrade 오픈 포지션이 반영됩니다.

전략 중지 API는 정지 전에 해당 봇의 `/forceexit all`을 호출해 오픈 포지션 정리를 시도한 후 프로세스를 중지합니다.

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
