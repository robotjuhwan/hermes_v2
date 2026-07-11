# HERMES Jue GPT-5.6 역할 기반 모델 정책

## 결정

HERMES는 하나의 공용 모델을 모든 작업에 사용하지 않는다. 실제 거래 영향도와
추론 깊이에 따라 네 정책 tier로 라우팅한다.

| tier | 모델 | effort | 적용 작업 |
| --- | --- | --- | --- |
| critical | `gpt-5.6-sol` | `xhigh` | KIS/Binance 매니저, Market Judge |
| reasoning | `gpt-5.6-terra` | `high` | 리서치, 종목분석, 발견, 전략, 메모리 |
| utility | `gpt-5.6-luna` | `medium` | 보고서 사실 추출, 반복 요약·분류 |
| offline | `gpt-5.6-sol` | `max` | 주·월간 정책 개정, Jue Codex Lab |

실시간 거래에는 `max`를 사용하지 않는다. 응답 지연으로 시장 판단이 낡는 위험이
깊은 추론의 이득보다 크기 때문이다.

## 경계

- `usage_component + operation`을 중앙 정책의 입력으로 사용한다.
- 알 수 없는 신규 컴포넌트는 `reasoning`으로 안전하게 기본 분류한다.
- 기존 `TRADECRAFT_LLM_MODEL`, Binance, crypto research, crypto alpha 환경변수는
  호환 override로 유지한다.
- 주·월간 메모리 정책 개정은 동일 investment-memory 런타임 안에서 operation
  override로 Sol/max를 사용한다.
- readiness와 모델 상태 API는 실제 유효 모델을 표시한다.

## 검증

- 세 모델 각각 ephemeral 최소 호출이 성공해야 한다.
- 모델 계약, 설정 기본값, workflow manifest 테스트가 통과해야 한다.
- KIS/Binance/Market Judge는 Sol, 분석 작업은 Terra, 추출 작업은 Luna로 런타임
  descriptor에 표시되어야 한다.
- 재시작 후 신규 LLM 호출의 텔레메트리 모델이 정책과 일치해야 한다.
