# Jue Reflection Policy Revision Design

## Summary

쥬의 성장 루프를 “반성 기록”에서 “반성으로 운용 방식이 바뀌는 체계”로 확장한다. 기존 블록 반성, 일일 저널, 정책 점수카드, 버전 정책 룰을 유지하되, 주간/월간 단위의 성과 압축과 정책 개정안을 별도 데이터로 저장하고 다음 블록 매니저 프롬프트에 주입한다.

## Design Brainstorm

### Approach A: deterministic scorecard only

반성 데이터를 승률, 평균 손익, 룰 준수율로 계산해 바로 정책 룰을 만든다.

장점은 빠르고 예측 가능하다. 단점은 “왜 쥬가 그렇게 바뀌었는지”가 얕고, 사용자가 말한 단기/중기/ETF/사용자 매수분 같은 질적 운용 원칙을 다루기 어렵다.

### Approach B: LLM free-form self rewrite

주간/월간 반성 때 gpt-5.5가 `persona.md`, `trading.md`, `risk.md`를 직접 다시 쓴다.

장점은 인간적인 성장감이 강하다. 단점은 정책이 과격하게 흔들릴 수 있고, 어떤 거래 결과 때문에 어떤 원칙이 바뀌었는지 추적하기 어렵다.

### Approach C: hybrid policy revision loop

성과 지표는 deterministic하게 집계하고, LLM은 그 지표와 실제 반성들을 바탕으로 정책 개정안을 만든다. 개정안은 바로 hard rule이 되지 않고 `candidate`, `active_caution`, `active_preference`, `retired` 상태를 가진 버전 규칙으로 저장된다. 안전 게이트는 그대로 최우선이다.

추천은 C다. 쥬가 성장하는 느낌과 운영 안전성을 모두 잡을 수 있고, 현재 `policy_scorecards`, `policy_rules`, `context_pack` 구조와 가장 잘 맞는다.

## Target Behavior

1. 블록이 닫히면 기존처럼 블록 반성을 만든다.
2. 주간 리뷰는 매주 금요일 장마감 이후 또는 일요일 저녁에 지난 5거래일을 압축한다.
3. 월간 리뷰는 매월 마지막 거래일 장마감 이후 또는 다음 달 첫 거래 전 지난 한 달을 압축한다.
4. 리뷰는 단기/중기/장기/ETF/core/사용자 매수분/쥬 신규매수/현금 운용을 분리해서 평가한다.
5. 리뷰 결과는 정책 개정안으로 이어진다.
6. 정책 개정안은 DB와 `.runtime/investment_memory/policies/revisions/`에 versioned JSON/Markdown으로 저장된다.
7. 활성 정책은 다음 `context_pack()`과 `KISBlockTrader.run_manager_once()`에 들어간다.
8. 다음 주/다음 달 리뷰에서 정책 적용 후 성과를 다시 평가해 유지, 강화, 약화, 폐기한다.

## Data Model

### period_reviews

Stores weekly and monthly review outputs.

Fields:
- `period_key`: `2026-W21` or `2026-05`
- `period_type`: `weekly` or `monthly`
- `start_date`, `end_date`
- `status`: `ok`, `llm_unavailable`, `partial`
- `mode`: `llm` or `deterministic`
- `metrics_json`
- `review_md`
- `policy_revision_ids_json`
- `created_at`, `updated_at`

### policy_revisions

Stores specific policy changes proposed from reviews.

Fields:
- `revision_id`
- `period_key`, `period_type`
- `policy_id`
- `action`: `create`, `strengthen`, `weaken`, `retire`, `keep`
- `status`: `candidate`, `active_caution`, `active_preference`, `retired`, `rejected`
- `scope`: `short`, `mid`, `long`, `core_etf`, `cash`, `user_position`, `discovery`
- `condition_json`
- `effect_json`
- `evidence_json`
- `reason_md`
- `confidence`
- `created_at`, `activated_at`, `retired_at`

### policy_outcomes

Tracks whether an active policy later helped.

Fields:
- `policy_id`, `rule_id`
- `period_key`, `period_type`
- `sample_count`
- `avg_pnl_pct`, `win_rate`, `expectancy_pct`
- `max_drawdown_pct`
- `rule_follow_rate`
- `helped_count`, `hurt_count`
- `notes_md`
- `updated_at`

## Review Metrics

Each weekly/monthly review computes:
- total closed blocks
- realized PnL KRW and percent
- win rate
- average win, average loss
- expectancy percent
- average holding time
- MFE/MAE when available
- stop discipline: stop hit, manual close, target close, error close
- block source: `jue`, `user`, `existing_position`, `daily_discovery`
- horizon: `short`, `mid`, `long`, `core_etf`, `cash`
- symbol and sector concentration
- ETF/core allocation drift
- number of LLM manager runs
- number of policy rules applied
- policy impact by rule
- daily discovery conversion: sampled, block_candidate, created block, profitable block

## Policy Revision Rules

Policy changes are not hard filters. They modify review pressure, sizing bias, target/stop review, horizon choice, and confirmation strictness.

Promotion rules:
- `candidate`: any plausible repeated lesson or LLM proposal.
- `active_caution`: at least 3 relevant samples or strong loss-prevention evidence, confidence >= 0.65.
- `active_preference`: at least 5 relevant samples and positive expectancy or clear drawdown reduction.
- `retired`: policy has negative expectancy in at least 5 samples or contradicts newer stronger evidence.
- `rejected`: malformed, unsafe, hard-filter-like, or unsupported by metrics.

Hard bans remain disallowed except system safety gates:
- kill switch
- cash/orderable cash limit
- position quantity limit
- duplicate order prevention
- KIS/order error freeze

## LLM Role

gpt-5.5 receives compact metrics, recent block reflections, daily journals, discovery outcomes, market pulse, and policy outcomes. It returns JSON:

```json
{
  "review_title": "string",
  "review_md": "string",
  "observations": ["string"],
  "policy_revisions": [
    {
      "policy_id": "string",
      "action": "create|strengthen|weaken|retire|keep",
      "scope": "short|mid|long|core_etf|cash|user_position|discovery",
      "condition": {},
      "effect": {},
      "reason_md": "string",
      "confidence": 0.0
    }
  ],
  "memory_updates": {
    "notes": [],
    "symbols": [],
    "blocks": []
  }
}
```

If LLM is unavailable, deterministic review still writes metrics and a conservative review. No new active policy is promoted from LLM-unavailable output.

## Context Injection

`InvestmentMemoryService.context_pack()` includes:
- current active policies
- last weekly review
- last monthly review
- top policy outcomes
- pending policy revisions
- active revision rules

`KISBlockTrader` receives these through the existing `memory_context_provider`. The block manager prompt treats them as data-backed soft operating rules, not as direct order commands.

## UI/API

New APIs:
- `GET /api/memory/reviews/latest?period_type=weekly|monthly`
- `GET /api/memory/reviews/history?period_type=weekly|monthly&limit=12`
- `POST /api/memory/reviews/run-once` with `period_type`, `force`
- `GET /api/memory/policies/revisions`
- `POST /api/memory/policies/revisions/{revision_id}/activate`
- `POST /api/memory/policies/revisions/{revision_id}/reject`

UI additions:
- Memory tab: “주간/월간 반성” panel.
- Block trading tab: “현재 적용 중인 운용 개정” compact chips.
- Policy rules area: version, source review, evidence, status, outcome.

Telegram additions:
- `/weekly-review`
- `/monthly-review`
- `/policy`
- `/policy <policy_id>`

## Testing Strategy

Tests verify:
- weekly/monthly due slot calculation
- metrics aggregation from fake blocks/orders/reflections
- LLM JSON policy revisions are validated
- hard-filter-like revisions are rejected
- active policy revisions appear in context packs
- KIS block manager prompt receives revised policies
- outcomes can retire a harmful policy
- APIs require admin token
- UI fetch/render code passes static JS check

## Self-Review

No placeholder requirements remain. The design is focused on one subsystem: reflection-driven policy revision. It does not alter order execution or safety gates. It extends current memory infrastructure rather than replacing it.
