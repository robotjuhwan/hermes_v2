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
- `TRADECRAFT_RUNTIME_MAX_AGE_SEC`
- `TRADECRAFT_RUNTIME_WRITE_INTERVAL_SEC`
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
