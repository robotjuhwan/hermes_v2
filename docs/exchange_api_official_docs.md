# Exchange API Official Docs (HERMES BOT)

Last verified: 2026-02-14 (KST)

## 1) Current integration targets (now)

### 1.1 Crypto (active targets)

| Venue | Scope | Official docs | Auth | Test/Sandbox |
|---|---|---|---|---|
| Upbit | Spot | https://docs.upbit.com/kr<br>https://docs.upbit.com/kr/reference/get-balance | JWT (API Key/Secret) | 별도 testnet 문서 없음 |
| Binance Spot | Spot | https://developers.binance.com/docs/binance-spot-api-docs | API Key + signed request | Spot Testnet 지원 |
| Binance Futures | USD-M / COIN-M | https://developers.binance.com/docs/derivatives<br>https://developers.binance.com/docs/derivatives/usds-margined-futures/general-info<br>https://developers.binance.com/docs/derivatives/coin-margined-futures/general-info | API Key + signed request | Futures Testnet 지원 |
| Bithumb | Spot | https://apidocs.bithumb.com/ | API Key + signed request | 운영/테스트 정책은 문서 기준 확인 |

### 1.2 KR/US Stocks (active target)

| Provider | Scope | Official docs | Auth | Note |
|---|---|---|---|---|
| KIS (Korea Investment) | 국장 + 미장 | https://apiportal.koreainvestment.com/ | OAuth2 + App Key/Secret | 현재 프로젝트의 주 브로커 후보 |

Decision note:
- 사용자 계좌가 KIS에 있으므로, 국장/미장은 **KIS 단일 API**로 통합하는 방향이 가장 단순합니다.

## 2) Future candidates (trace only)

Status: not in current implementation. Keep as references for later.

| Candidate | Official docs |
|---|---|
| Bybit | https://bybit-exchange.github.io/docs/v5/intro |
| Coinbase | https://docs.cdp.coinbase.com/ |
| Kraken | https://docs.kraken.com/api/ |
| KuCoin | https://www.kucoin.com/docs-new |
| KX | https://code.kx.com/ |
| Tardis | https://docs.tardis.dev/ |

## 3) Why Kiwoom was listed before

Kiwoom was added earlier as an **optional fallback**, not as a mandatory target.

Reason:
- 한국 주식 자동매매에서 사용 사례가 많은 브로커 대안이라 비교군으로 넣어둠
- 브로커 단일 의존 리스크(장애/정책 변경)를 줄이기 위한 백업 후보
- 계좌/권한/서비스 정책에 따라 특정 기능 가능 여부가 달라질 수 있음

Current project direction:
- **Primary: KIS only** for KR/US stocks
- Kiwoom: later optional expansion (not in current build scope)

## 4) Next implementation order (pragmatic)

1. Upbit (balance/order/fill/cancel)
2. Binance Spot
3. Binance Futures (separate adapter from Spot)
4. Bithumb
5. KIS (KR/US unified stock adapter)

## 5) Suggested env keys (next phase)

- `UPBIT_ACCESS_KEY`, `UPBIT_SECRET_KEY`
- `BINANCE_SPOT_API_KEY`, `BINANCE_SPOT_API_SECRET`
- `BINANCE_FUTURES_API_KEY`, `BINANCE_FUTURES_API_SECRET`
- `BITHUMB_API_KEY`, `BITHUMB_API_SECRET`
- `KIS_BASE_URL`
- `KIS_PRIMARY_APP_KEY`, `KIS_PRIMARY_APP_SECRET`, `KIS_PRIMARY_ACCOUNT_NO`, `KIS_PRIMARY_PRODUCT_CODE`
- `KIS_SECONDARY_APP_KEY`, `KIS_SECONDARY_APP_SECRET`, `KIS_SECONDARY_ACCOUNT_NO`, `KIS_SECONDARY_PRODUCT_CODE`
