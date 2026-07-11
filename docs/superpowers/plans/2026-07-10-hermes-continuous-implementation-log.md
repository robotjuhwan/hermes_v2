# HERMES/쥬 연속 개선 구현 로그

## 2026-07-10 검증 완료 묶음

### P0 작업트리·계약 안정화

- `AppSettings` 482개 필드와 설정 명세의 수를 일치시켰다.
- pytest import 이전에 설정 경로를 임시 runtime으로 전환한다.
- 직접 `.runtime` 접근도 open, SQLite, stat, directory scan, mkdir, touch,
  rename, replace, unlink, rmdir 단계에서 pytest 전용 경로로 전환한다.
- Binance/KIS runtime prompt의 `decision_inputs`, `candidates`, `blocks`는 항상
  배열이며 감사 저장용 `{item_count, items}`와 분리한다.
- 예산 또는 자료형 계약 위반은 `prompt_budget_contract_violation`으로 종료하고
  LLM 및 주문 호출을 차단한다.

### P0 readiness

- Jue Wiki와 application 상태는 러너가 생성한 `OpsSectionSnapshotV1`만 읽는다.
- readiness 공급자는 timeout, 병렬 조회, last-good cache를 사용한다.
- compact readiness는 full payload를 만들지 않고 직접 조합한다.
- 측정값: compact cold 약 597ms, warm p95 약 0.012ms, full cold 약 585ms.
- SQLite 상태 조회 쓰기 검증: snapshot 생성 후 `status()` 호출에서 DB bytes와
  mtime 변화 없음.

### P1 검증 피드백

- `python scripts/verify.py fast`
- `python scripts/verify.py domain --area binance`
- `python scripts/verify.py domain --area kis`
- `python scripts/verify.py full`
- unit, contract, integration, slow 및 도메인 marker를 수집 시 자동 적용한다.
- xdist가 설치되면 격리된 fast unit/contract만 병렬 실행한다.
- 모든 실행은 상위 50개 느린 테스트와 JSON 결과를 `.verification/`에 기록한다.
- 측정값:
  - fast: 1,773 passed, 35.3초, 90초 기준 이내
  - Binance domain: 1,197 passed, 62.0초
  - KIS domain: 713 passed, 40.4초
  - full: 4,602 passed, pytest 537.08초, 전체 540.3초

### P1 경계·runtime 저장소

- main의 runtime 저장소 설정 조립을 `runtime_storage_policy.py`로 이동했다.
- Binance/KIS 저장소 status SQL을 venue별 status reader로 이동했다.
- 도메인별 읽기 전용 설정 view와 기존 환경변수 alias 조회를 제공한다.
- `RUNNER_SPECS.log_path`를 canonical log 목록으로 사용하고 삭제 후보에서 보호한다.
- 중복 runner log는 7일 유예 후 별도 후보로 분류한다.
- dryrun은 14일 및 시나리오별 최근 3개를 보존하고 보호 manifest 경로는 제외한다.
- Jue Wiki raw outcome은 효과지표 투영 후 30일이 지나면 gzip archive로 이동하며
  outcome ID와 SHA-256 감사 식별자를 보존한다.
- 정리 API 기본값은 계속 `dry_run=true`다.
- live runtime 읽기 전용 측정: 5,553,865,087 bytes(5.172GiB), `warning`.
  4GiB warning, 6GiB risk가 readiness의 disk section에 노출된다.

### P2 텔레메트리·성과 귀속

- `ManagerRunTelemetryV1`은 context 생성시간, 압축 전/후 prompt 크기와 감소율,
  LLM 지연, token 필드, 행동 수, 결과, fill provenance를 저장한다.
- KIS/Binance manager 성공·실패 기록 모두 텔레메트리를 남긴다.
- 기존 포지션 채택, wallet 채택, 실패/거절 entry, paper fill, exchange fill을
  별도 집계하며 adoption은 alpha fill에 포함하지 않는다.
- live performance DB는 `fill_provenance`와 `pnl_state`를 저장하고 lane별로
  paper/exchange, realized/unrealized 수를 분리한다.
- fill-proven 표본과 비용 반영 성과가 부족하면 authority gate는
  `observe_only` 또는 `restricted`를 반환하며 자동 scale-up은 허용하지 않는다.

## 기존 작업트리 소유 묶음

현재 미커밋 변경은 다음 계획에 매핑한다. 파일 간 공통 계약 때문에 일부는 두 묶음에
걸치며, 기존 사용자 변경을 분리하거나 되돌리지 않는다.

- main/ops 경계: `main.py`, `api/app_route_groups.py`, `api/ops*.py`,
  `api/ops_process_payloads.py`, `process_status.py`, 관련 2026-07-06 설계/계획.
- Binance 프롬프트·activity gap: `binance_block_trader.py`, `binance_manager_*`,
  `binance_blocks*`, `binance_ledger.py`, Binance runner와 관련 2026-07-07/08 계획.
- KIS ETF discovery: `daily_discovery.py`, `api/discovery.py`, `naver_reports.py`,
  `symbol_analysis.py`, KIS trader/runner/prompt, 관련 2026-07-07 계획.
- 운영 복구·저장소: readiness/status provider, watchdog, runtime maintenance,
  investment-memory runner, live performance, pytest runtime isolation.
- 이번 연속 개선 공통 계약: `scripts/verify.py`, manager prompt bundle/telemetry,
  ops snapshot, venue status readers, runtime storage policy, settings views.

## 남은 연속 큐

- Investment Memory의 context compaction, reflection, policy review를 물리 모듈로
  더 분리한다. 현재 repository/service 경계는 있으나 파일 크기는 여전히 크다.
- KIS/Binance manager orchestration 본문을 독립 coordinator로 단계적으로 이동한다.
- 실행 텔레메트리 누적 후 prompt/context 비용 25% 감소 여부를 실제 분포로 판정한다.
- full에서 가장 느린 dashboard adapter 테스트 2개(각 약 65초)의 timeout 구조를
  진단해 전체 피드백 시간을 추가로 줄인다.
- live daemon이 `.runtime`을 계속 갱신하므로 전역 checksum 불변 비교는 운영 중에는
  의미가 없다. 대신 pytest 경로 전환 감사와 격리 테스트를 완료 조건으로 사용한다.

## 2026-07-11 운영 readiness 수용 기록

- `completed_at`: `2026-07-10T16:04:18.876615+00:00`.
- `baseline_signals`: blocker 0, warning 8(`restart_required`,
  `reports_db_stale`, `llm_prompt_payload_large`, Jue Wiki count/coverage 5종),
  strategy advisory 5.
- `final_signals`: blocker 0, stale process 0, warning 2
  (`runtime_storage_warning`, `jue_wiki_repair_queue_growing`), advisory 10.
  따라서 green 완료 게이트는 아직 통과하지 않았고 현재 상태는 `yellow`다.
- `changed_files`: readiness/API는 `src/tradecraft/api/ops_payloads.py`,
  `src/tradecraft/api/ops_readiness.py`, `src/tradecraft/api/ops.py`,
  `src/tradecraft/main.py`, `src/tradecraft/config.py`,
  `src/tradecraft/web/static/app.js`; 신규 경계는
  `src/tradecraft/services/ops_readiness_snapshot.py`,
  `src/tradecraft/services/jue_wiki_repair_health.py`,
  `src/tradecraft/services/market_judge_prompt.py`,
  `src/tradecraft/runtime/naver_reports_worker.py`,
  `src/tradecraft/runtime/runner_recovery.py`; runtime/도메인은
  `src/tradecraft/runtime/naver_reports_runner.py`,
  `src/tradecraft/runtime/runner_manifest.py`,
  `src/tradecraft/runtime/process_status.py`,
  `src/tradecraft/runtime/watchdog_runner.py`,
  `src/tradecraft/runtime/jue_wiki_runner.py`,
  `src/tradecraft/services/jue_wiki.py`,
  `src/tradecraft/services/jue_wiki_repair.py`,
  `src/tradecraft/services/market_judgment.py`,
  `src/tradecraft/services/kis_manager_prompt.py`; 관련 계약 테스트와
  `docs/spec/12_config_env.md`, `.env.example`을 함께 갱신했다.
- `verification_commands`: readiness/Wiki/reports/prompt 집중 테스트 4개 묶음 PASS;
  `python scripts/verify.py fast` 1,806 passed, 2 skipped, pytest 29.37초,
  전체 33.9초;
  `python scripts/verify.py domain --area binance` 1,197 passed, 71.7초;
  `python scripts/verify.py domain --area kis` 714 passed, 43.0초;
  `python scripts/verify.py full` 4,650 passed, 2 skipped, pytest 558.66초;
  Naver 최종 집중 테스트 38 passed; `ruff check`와 프로젝트 계약 검사 PASS.
- `latency_p50_p95_max`: compact warm 20회 `4.560/24.602/38.864ms`,
  full snapshot 5회 `38.988/57.800/57.800ms`.
- `sqlite_write_count`: readiness 25회 전후 검사에서 0.
- `runner_before_after`: Naver Reports `12001 -> 66152`, Investment Memory
  `86959 -> 46630`, Live Evaluator `86987 -> 46745`, Jue Wiki
  `87114 -> 46869`, Market Judge `86999 -> 47016`, KIS Block Trader
  `57409 -> 47132`, Strategy Insights `87126 -> 50339`, Control
  `86915 -> 47245`. 모두 PID 파일 `ok`, source-stale 0. Naver는 최근 성공
  cycle(19건 삽입, RAG 202건 동기화)을 provenance로 사용해 cadence 대기 중이다.
- `runtime_checksum_comparison`: 기준 48개 중 11개 불변, 37개 변경, 누락 0.
  변경은 승인된 러너 상태/로그/readiness snapshot, Wiki repair projection,
  Naver 수집 결과 및 정상 가동 중인 runtime DB 갱신이다. 상세 목록은
  `.runtime/verification/hermes-readiness-after.json`에 기록했다.
- `kis_order_count_before_after`: `149 -> 149`.
- `binance_order_count_before_after`: `4234 -> 4234`.
- `remaining_strategy_advisories`: KIS probe/lane 확대 제한 2종, Binance 진단
  실패/probe/lane 확대 제한 3종, Jue Wiki 진행 중 repair/coverage 5종. KIS와
  Binance 작업공간에서 server-side 제한이 유지되는 것을 인증 UI로 확인했다.
- `remaining_risks`: `.runtime` 5.2GB(4GiB warning, 6GiB risk)이며 dry-run 삭제
  후보는 약 303MB라 보존 규칙을 지키면 4GiB 아래로 내려가지 않는다. 실제 삭제는
  실행하지 않았다. Wiki repair queue는 24시간 opened 623/resolved 588,
  net growth 35로 정책 임계값 25를 실제 초과한다. 경고를 숨기거나 임계값을
  완화하지 않았으며, 다음 자연 repair cycle과 별도 승인된 저장소 정리를 계속
  추적한다.

## 2026-07-11 무경고 운영 수용 완료 기록

- `completed_at`: `2026-07-11T01:00:41Z`.
- `storage_before_after`: hot `.runtime` `5,717,147,833 -> 3,139,241,668`
  bytes(`5.325 -> 2.924GiB`). cold archive는 최종 Jue cycle 반영 후 37개 entry,
  `620,664,567` bytes이며 전체 재검증 결과 `status=ok`, corrupt/unverified 0이다.
- `lossless_migration`: 완료된 dry-run bundle 12개, 추출 완료 PDF 139개,
  repair backup 1개를 검증 후 hot에서 제거했다. Wiki rejected selection은
  13개 일별 partition의 2,870,972행과 경계 partition 3,119행을 gzip JSONL 및
  exact keyset으로 보존한 뒤 검증된 키만 삭제했다. 사전 SQLite backup
  `20260711T001114693834Z-bc64d2aba682`를 검증하고 VACUUM으로
  `959,397,888` bytes를 회수했다.
- `restore_proof`: rehearsal entry
  `20260711T000807190796Z-7d1cdebd69de`를 새 디렉터리에 복원해 SQLite
  `integrity_check=ok`를 확인했다. Wiki partition
  `2026-06-28-0b2cfacefe974e45`는 5,687행을 복원했고 stream SHA 및 row key
  계약이 manifest와 일치했다.
- `readiness_acceptance`: compact/full 모두 `green`, blockers/warnings 0;
  missing/stale/duplicate process 0. 인증 UI `#opsBanner`는 hidden이며 Binance/KIS
  작업공간의 strategy advisory와 authority 제한은 유지됐다.
- `readiness_latency`: live compact 50회 p95 `2.728ms`(max `13.172ms`), full
  20회 p95 `28.772ms`(max `74.289ms`). Jue runner를 수 초간 일시 정지한
  분리 검증에서 readiness 25회 전후 Wiki DB/WAL/SHM checksum, size, mtime이
  모두 같아 SQLite write 0건을 확인했다.
- `database_and_orders`: Jue Wiki, KIS, Binance DB 모두
  `PRAGMA integrity_check=ok`; KIS order count `149 -> 149`, Binance
  `4234 -> 4234`. 최종 핵심 PID는 KIS `47132`, Binance `91407`, Jue Wiki
  `48927`, control `55965`, watchdog `56515`이며 모두 실행 중이다.
- `repair_lanes`: scheduled integrity 0, evidence 249, strategy 129,
  unclassified 0. evidence/strategy는 advisory로만 노출되고 전역 readiness는
  integrity lane만 소유한다.
- `verification_commands`: focused archive/maintenance/Wiki/readiness 계약 PASS;
  `python scripts/verify.py fast` 37.686초(1,822 passed), Binance domain
  75.366초(1,197 passed), KIS domain 46.496초(714 passed),
  `python scripts/verify.py full` 560.669초(4,683 passed, 2 skipped), 프로젝트
  계약 검사와 `ruff check src tests scripts` PASS. 고정 날짜로 신선도 경계를
  넘던 Wiki fixture는 실행 시각 기준으로 고쳐 관련 4개 테스트를 재검증했다.
- `remaining_risks`: 손상된
  `.runtime/rag_chroma.rebuild-backup-20260701T072439Z`(135,874,721 bytes)는
  SQLite 무결성 실패 때문에 archive/remove 권한을 얻지 못해 hot에 그대로
  보존했다. Jue Wiki의 30분 rebuild에서 Chroma physical footprint peak 약
  2.4GiB, cycle 종료 후 약 0.95GiB가 관측돼 다음 효율화 큐의 메모리 상한/재사용
  항목으로 남긴다. 두 항목 모두 현재 readiness 경고나 거래 권한 확대를 만들지
  않는다.
- `.env`는 수정하지 않았고 manager/executor/tick/order API를 호출하지 않았으며,
  stage/commit도 수행하지 않았다.

### Jue cycle 이후 무경고 지속성 보강

- Jue selection compaction이 cold manifest를 변경한 뒤 수동 `verify` 전까지
  `runtime_cold_archive_unverified`가 재발할 수 있던 경계를 제거했다.
  `jue_wiki_runner`가 archive 변경이 있는 cycle에서 전체 core/Jue manifest를
  검증하고 `status-v1.json`을 원자적으로 갱신한 뒤 ops snapshot을 게시한다.
- 무변경 compaction은 검증을 생략하며 경고로 승격하지 않고, 실제 변경에서
  archive 검증 실패·corruption·non-current snapshot은 fail-open하지 않고 runner
  warning으로 남긴다. readiness 조회 자체는 계속 읽기 전용이다.
- live Jue runner를 `48927 -> 10300`으로 단독 교체했다. 첫 cycle에서 rejected
  selection 9,480행을 entry `2026-07-10-2dca68169fa74d88`로 archive하고,
  pre-VACUUM backup `20260711T011236799432Z-21ad6324f949`를 만든 뒤 39개
  entry, 652,328,628 bytes 전체를 자동 검증했다. `verified_at`은
  `2026-07-11T01:13:20.699747+00:00`, snapshot은 `current`다.
- 수동 archive verify 없이 다음 provider refresh에서 compact/full readiness 모두
  `green`, blockers/warnings 0을 확인했다. KIS/Binance order count는 각각
  149/4,234로 불변이고 관련 runner도 중단되지 않았다.
- 검증: Jue runner/archive/readiness 집중 묶음 35 passed;
  `python scripts/verify.py fast` 1,820 passed, 2 skipped, 36.1초;
  `python scripts/verify.py full` 4,683 passed, 2 skipped, 564.5초. 프로젝트 계약,
  Ruff, 전체 15분 예산을 모두 통과했다.

### Cold archive writer·pytest 격리 폐쇄

- 실제 인증 UI에서 전체 pytest 직후 `runtime_cold_archive_unverified`가 재현됐다.
  원인은 테스트 hot runtime만 격리되고 `.runtime-cold-archive`는 live 경로를
  사용해, repair-backup cleanup 테스트가 315-byte test artifact를 core manifest에
  기록한 것이었다.
- `tests/conftest.py`가 `runtime_cold_archive_root` 설정과 cold root의 직접
  open/stat/list/mkdir/rename/replace/unlink 접근까지 pytest 임시 cold root로
  리다이렉트한다. `RuntimeStoragePolicy`의 미지정 cold root도 사용자 지정
  `runtime_dir`의 sibling으로 유도해 테스트 외 직접 사용에서도 전역 경로 누출을
  막는다. 명시된 운영 cold root는 그대로 유지된다.
- 문제를 만든 cleanup 테스트를 live manifest/status SHA-256 전후 비교와 함께
  재실행해 checksum 불변을 확인했다. 최종 전체 테스트의 신규 cold entry
  provenance 검사에서도 `pytest-of-*`/`tradecraft-pytest-runtime-*` source 0건을
  확인했다. 이미 생성된 315-byte test entry는 승인 없는 archive 삭제를 피하기
  위해 보존했으며 무결성 검증은 통과한다.
- Jue runner 외 마지막 운영 writer인 `POST /api/runtime/storage/cleanup`도 실제
  archive가 생긴 apply 요청에서 전체 cold verification snapshot을 갱신한 뒤
  응답한다. dry-run과 archive 무변경 apply는 불필요한 전체 hash를 수행하지 않는다.
- 최신 control PID는 `72152`, Jue PID는 `10300`이다. 최종 compact/full은
  `green`, blockers/warnings 0, missing/stale/duplicate runner 0; cold archive
  42 entries, corrupt 0, snapshot `current`; KIS/Binance order count 149/4,234다.
- 실제 인증 브라우저에서 `#opsBanner`는 `hidden=true`, text empty,
  `aria-live=polite`로 확인했다. 최종 `python scripts/verify.py full`은
  4,684 passed, 2 skipped, 582.8초이며 pytest cold contamination 0과 snapshot
  current 후속 게이트까지 통과했다. `.env`, 거래 실행 API, stage/commit은
  변경·호출하지 않았다.

## 2026-07-12 Task 9 — 저장 readiness·장애 복구·Wiki-first 문서화

- 계획의 `docs/spec/09_runtime_processes.md`는 저장소에 없으므로 실제 문서인
  `docs/spec/03_runtime_processes.md`로 경로를 교정했다.
- Jue runner가 `OpsSectionSnapshotV1.v3.by_scope`에 KIS/Binance별 snapshot id,
  생성시각, ingest/compile/lint/publish/projection/index 상태와
  stale/conflict/orphan/repair backlog를 저장한다. 설정 read mode와 Task 8의
  서명 검증된 eligibility(version, venue, sample, blockers,
  evaluated_at/evaluated_through)도 같은 저장 snapshot에 투영한다.
- full/compact readiness는 저장 사전만 읽고 configured/stored mode 및 불일치,
  publication age, scope health, 비교 수, eligibility와 정확한 venue blocker를
  노출한다. required로 설정됐는데 status/DB/snapshot이 없으면 shadow로 오인하지
  않고 red다. 요청 경로에서 compile/lint/repair/rebuild/SQLite write는 없다.
- `JueWikiContextService`는 required 신규 위험 전 health reader를 검사한다.
  snapshot identity, 현재시각으로 재계산한 3,600초 freshness, 모든 단계/index,
  품질 카운트와 signed eligibility 중 하나라도 실패하면 create 및 증액 update를
  차단한다. 감액, close/exit, reconciliation, kill-switch는 보존한다. 이 계약은
  main, KIS, Binance, market judge provider에 연결됐다.
- 문서에는 네 소유 계층, V3 identity, prompt/read mode 분리,
  `shadow -> prefer -> required`, shadow rollback, 자동 live 설정 변경 금지,
  RAG의 bounded repair/audit/backfill/index rebuild 역할을 기록했다.
- 변경 파일: `src/tradecraft/api/ops.py`, `ops_payloads.py`,
  `ops_readiness.py`, `src/tradecraft/main.py`, `services/jue_wiki.py`,
  `jue_wiki_context.py`, `jue_wiki_application.py`,
  `runtime/jue_wiki_runner.py`, KIS/Binance/market-judge runners, 관련 readiness,
  context, runner, application, recovery 테스트와 운영 문서 4개다.
- focused 검증: `pytest tests/test_ops_readiness_signals.py
  tests/test_ops_payloads.py tests/test_jue_wiki_failure_recovery.py
  tests/test_jue_wiki_context.py tests/test_ops_api_router.py
  tests/test_jue_wiki_runner.py tests/test_jue_wiki_runner_v3.py
  tests/test_jue_wiki_application.py tests/test_market_judge_runner.py -q` —
  535 passed, `/usr/bin/time -p` real 11.80초. `pytest
  tests/test_docs_spec.py -q`는 설정 문서의 실제 504-field parity를 복구한 뒤
  18 passed다.
- 독립 검토는 초기 API-only 복구 테스트의 허점을 찾아 실제 required 결정 경로에
  저장 health gate를 연결했고, R3에서 Critical/Important/Minor 0건으로 승인됐다.
  `python scripts/verify.py domain --area jue`는 1,314 passed/99.05초,
  `--area kis`는 741 selected/44.95초, `--area binance`는 1,210 passed/69.64초다.
  세 도메인 모두 project contracts와 전체 Ruff를 포함해 exit 0이고 5분 예산
  안이다. 최초 Jue 실행에서 새 shadow-health advisory를 기존 exact-list assertion이
  거부한 1건은 additive 계약으로 보정한 뒤 전체 Jue 도메인을 재통과했다.
- `python scripts/verify.py full`은 project contracts와 전체 Ruff를 포함해
  5,226 passed, 2 skipped, 651.50초(exit 0)로 15분 예산 안이다.
  `tests/test_runtime_test_isolation.py` 5건도 별도 통과해 pytest의 설정 경로,
  직접 file/SQLite 접근이 모두 `/tmp/tradecraft-pytest-runtime-*`로 격리됨을
  확인했다.
- live `.runtime`의 전역 집계는 검증 전후 `4,367 -> 4,459` 파일로 달라졌다.
  이는 검증 전부터 실행 중인 9개 운영 daemon의 정상 cadence 쓰기이며, 수정 시각은
  Jue Wiki, investment memory, market pulse/judge, strategy insight, Naver reports,
  live evaluator, crypto research, Binance runner 주기와 일치한다. 운영 daemon을
  중단하지 않았으므로 전역 checksum/mtime의 문자 그대로 동일성은 성립하지
  않았지만 pytest의 live runtime 접근은 격리 테스트로 0건임을 검증했다.
  required 활성화, 현재 설정 변경, 주문, runtime 삭제, stage/commit은 모두 0건이다.
