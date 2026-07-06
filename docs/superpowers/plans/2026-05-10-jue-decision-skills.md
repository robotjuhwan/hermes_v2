# Jue Decision Skills Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make 쥬’s trading judgment process explicit, versioned, testable, and injected into both block trading and intraday judgment prompts.

**Architecture:** Add generated Markdown skill files under the existing investment memory root, then load them through `InvestmentMemoryService.context_pack()`. The KIS block manager and market judgment engine consume those skills as structured prompt context while keeping safety gates and rule execution outside the LLM. Policy rules remain soft data rules, not strategy hard filters.

**Tech Stack:** Python 3.10+, FastAPI service modules, SQLite-backed memory repositories, static HTML/CSS/JS frontend, pytest, existing `CodexNativeRuntime` with `gpt-5.5`.

---

## File Structure

- Modify `src/tradecraft/services/investment_memory.py`
  - Generate `.runtime/investment_memory/skills/*.md`.
  - Load skills into `context_pack()` as structured `decision_skills`.
  - Add skill status metadata to `status()` and `today()`.
- Modify `src/tradecraft/services/kis_block_trader.py`
  - Require `block_manager`, `risk_manager`, and `reflection` skills in manager/adoption prompts.
  - Keep output schemas unchanged.
- Modify `src/tradecraft/services/market_judgment.py`
  - Add an optional `memory_context_provider`.
  - Inject `market_judge` and `risk_manager` skills into the intraday judgment prompt.
- Modify `src/tradecraft/main.py`
  - Wire `investment_memory_service.context_pack` into `MarketJudgmentEngine`.
- Modify `src/tradecraft/runtime/market_judge_runner.py`
  - Build `InvestmentMemoryService` and wire its `context_pack` into the runner engine.
- Modify `src/tradecraft/web/static/app.js`
  - Show decision skill versions/status inside the memory or operations area.
- Modify `src/tradecraft/web/static/style.css`
  - Add compact dark-theme chips/list styling for decision skills.
- Modify tests:
  - `tests/test_investment_memory.py`
  - `tests/test_kis_block_trader.py`
  - `tests/test_market_judgment.py`
  - `tests/test_investment_memory_api.py`
  - `tests/test_prompt_identity.py`

## Task 1: Generate And Load 쥬 Decision Skill Files

**Files:**
- Modify: `src/tradecraft/services/investment_memory.py`
- Test: `tests/test_investment_memory.py`

- [ ] **Step 1: Write the failing test**

Append this test to `tests/test_investment_memory.py`:

```python
def test_initialize_creates_decision_skill_files_and_context_pack_loads_them(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    result = service.initialize()
    root = tmp_path / "memory"
    pack = service.context_pack(symbols=["005930"], block_ids=["blk_005930_1"])

    assert result["status"] == "ok"
    expected = {
        "block_manager",
        "market_judge",
        "risk_manager",
        "reflection",
    }
    for skill_id in expected:
        path = root / "skills" / f"{skill_id}.md"
        assert path.exists()
        text = path.read_text(encoding="utf-8")
        assert f"skill_id: jue.{skill_id}.v1" in text
        assert "쥬" in text

    assert set(pack["decision_skills"]) == expected
    assert pack["decision_skills"]["block_manager"]["version"] == "jue.block_manager.v1"
    assert "블록" in pack["decision_skills"]["block_manager"]["content_md"]
    assert pack["decision_skill_status"]["count"] == 4
    assert pack["decision_skill_status"]["missing"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/test_investment_memory.py::test_initialize_creates_decision_skill_files_and_context_pack_loads_them -q
```

Expected: FAIL because `decision_skills` is not present and `skills/*.md` files are not generated.

- [ ] **Step 3: Add default skill files**

In `InvestmentMemoryService._ensure_dirs()`, include `self.root / "skills"`.

In `InvestmentMemoryService._default_memory_files()`, add these files:

```python
self.root / "skills" / "block_manager.md": "\n".join(
    [
        "---",
        "skill_id: jue.block_manager.v1",
        "owner: HERMES",
        "purpose: KIS block manager live trading decisions",
        "---",
        "# 쥬 블록 매니저 스킬",
        "",
        "- 각 블록은 같은 종목이라도 독립된 약속이다.",
        "- 신규 블록은 근거, 목표가, 손절가, 무효화 조건, 수량 이유를 함께 가져야 한다.",
        "- 기존 블록 수정은 가격 경로, 마켓 펄스, 보유 비중, 메모리 정책을 비교해서 제안한다.",
        "- 청산 의도는 이유와 트리거를 남기고, 실제 주문은 안전 게이트와 룰 실행기가 검증한다.",
        "- 불확실하면 큰 결론보다 작은 블록, 관찰, 목표/손절 재확인을 우선한다.",
    ]
),
self.root / "skills" / "market_judge.md": "\n".join(
    [
        "---",
        "skill_id: jue.market_judge.v1",
        "owner: HERMES",
        "purpose: intraday account and market judgment",
        "---",
        "# 쥬 장중 판단 스킬",
        "",
        "- 국장1 현금, 보유 비중, 평가손익, 가용 수량을 먼저 확인한다.",
        "- 판단은 보유 블록, 신규 후보, 시장 국면을 분리해서 쓴다.",
        "- 마켓 펄스 v3의 지수, 수급, 프로그램, 환율, 선물 베이시스, 섹터, 블록 노출을 반영한다.",
        "- 결과는 stance, account_action, horizon, confidence, reasons, risks, triggers, data_gaps로 정리한다.",
        "- 수량과 주문가는 블록 트레이더가 검증하므로 장중 판단은 트리거와 운영 의도에 집중한다.",
    ]
),
self.root / "skills" / "risk_manager.md": "\n".join(
    [
        "---",
        "skill_id: jue.risk_manager.v1",
        "owner: HERMES",
        "purpose: risk checks for live block trading",
        "---",
        "# 쥬 리스크 매니저 스킬",
        "",
        "- kill switch, 현금 초과 금지, 보유수량 초과 금지, 중복주문 방지는 항상 우선한다.",
        "- 수익 중인 블록은 목표가 근접, 급락 반전, 시장 압박을 함께 본다.",
        "- 손실 중인 블록은 손절가까지 거리, thesis 훼손, 데이터 공백을 분리한다.",
        "- 블록 노출이 특정 섹터나 시장에 몰리면 신규 진입보다 비중 점검을 우선한다.",
        "- 정책 룰은 진입 금지가 아니라 수량, 확인 조건, 목표/손절, 리스크 노트 보정으로 사용한다.",
    ]
),
self.root / "skills" / "reflection.md": "\n".join(
    [
        "---",
        "skill_id: jue.reflection.v1",
        "owner: HERMES",
        "purpose: post-trade reflection and memory update",
        "---",
        "# 쥬 거래 반성 스킬",
        "",
        "- 닫힌 블록은 진입 가설, 가격 경로, 룰 준수, 청산 품질, 놓친 위험을 분리해서 기록한다.",
        "- 한 번의 손익으로 종목을 단정하지 않는다.",
        "- 반복된 교훈만 observation, caution, preference 정책 후보로 승격한다.",
        "- 반성은 다음 블록의 수량, 확인 조건, 목표/손절 보정에 쓰일 수 있어야 한다.",
    ]
),
```

- [ ] **Step 4: Add the loader**

Add this helper near `_read_memory_file()`:

```python
def _decision_skills(self) -> dict[str, dict[str, Any]]:
    skills: dict[str, dict[str, Any]] = {}
    for skill_id in ("block_manager", "market_judge", "risk_manager", "reflection"):
        relative = f"skills/{skill_id}.md"
        content = self._read_memory_file(relative, limit=1800)
        version = f"jue.{skill_id}.v1" if f"skill_id: jue.{skill_id}.v1" in content else ""
        skills[skill_id] = {
            "skill_id": skill_id,
            "version": version,
            "content_md": content,
        }
    return skills
```

Update `context_pack()`:

```python
decision_skills = self._decision_skills()
missing_skills = [
    key
    for key, value in decision_skills.items()
    if not str(value.get("content_md") or "").strip()
]
payload = {
    ...
    "decision_skills": decision_skills,
    "decision_skill_status": {
        "count": len(decision_skills),
        "missing": missing_skills,
    },
    ...
}
```

- [ ] **Step 5: Run the test**

Run:

```bash
pytest tests/test_investment_memory.py::test_initialize_creates_decision_skill_files_and_context_pack_loads_them -q
```

Expected: PASS.

- [ ] **Step 6: Run focused memory tests**

Run:

```bash
pytest tests/test_investment_memory.py -q
```

Expected: PASS.

- [ ] **Step 7: Checkpoint**

Run:

```bash
git diff -- src/tradecraft/services/investment_memory.py tests/test_investment_memory.py
```

Expected: diff shows only memory skill generation/loading and the new test. Do not commit unless the user explicitly requests it.

## Task 2: Inject Decision Skills Into KIS Block Manager Prompts

**Files:**
- Modify: `src/tradecraft/services/kis_block_trader.py`
- Test: `tests/test_kis_block_trader.py`

- [ ] **Step 1: Write the failing test**

Add this assertion block inside `test_manager_prompt_includes_investment_memory_context()` after the existing `policy_rule_evaluation` fixture:

```python
"decision_skills": {
    "block_manager": {
        "version": "jue.block_manager.v1",
        "content_md": "# 쥬 블록 매니저 스킬\n- 블록 생성과 수정 원칙",
    },
    "risk_manager": {
        "version": "jue.risk_manager.v1",
        "content_md": "# 쥬 리스크 매니저 스킬\n- 비중과 손절 우선순위",
    },
    "reflection": {
        "version": "jue.reflection.v1",
        "content_md": "# 쥬 거래 반성 스킬\n- 닫힌 블록에서 배운다",
    },
},
"decision_skill_status": {"count": 3, "missing": []},
```

Then add these assertions after `prompt = json.loads(...)`:

```python
assert prompt["required_decision_skills"] == [
    "block_manager",
    "risk_manager",
    "reflection",
]
assert prompt["investment_memory"]["decision_skills"]["block_manager"]["version"] == (
    "jue.block_manager.v1"
)
assert "비중과 손절" in prompt["investment_memory"]["decision_skills"]["risk_manager"]["content_md"]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/test_kis_block_trader.py::test_manager_prompt_includes_investment_memory_context -q
```

Expected: FAIL because `required_decision_skills` is not present.

- [ ] **Step 3: Add required skill ids to manager prompt**

In `KISBlockTrader.run_manager_once()`, add this field next to `policy_rules`:

```python
"required_decision_skills": [
    "block_manager",
    "risk_manager",
    "reflection",
],
```

In `KISBlockTrader.adopt_existing_positions_once()`, add:

```python
"required_decision_skills": [
    "block_manager",
    "risk_manager",
],
```

No output schema changes are needed.

- [ ] **Step 4: Run focused test**

Run:

```bash
pytest tests/test_kis_block_trader.py::test_manager_prompt_includes_investment_memory_context -q
```

Expected: PASS.

- [ ] **Step 5: Run block trader tests**

Run:

```bash
pytest tests/test_kis_block_trader.py -q
```

Expected: PASS.

- [ ] **Step 6: Checkpoint**

Run:

```bash
git diff -- src/tradecraft/services/kis_block_trader.py tests/test_kis_block_trader.py
```

Expected: diff shows prompt metadata and test assertions only. Do not commit unless the user explicitly requests it.

## Task 3: Inject Memory Skills Into Intraday Market Judgment

**Files:**
- Modify: `src/tradecraft/services/market_judgment.py`
- Modify: `src/tradecraft/main.py`
- Modify: `src/tradecraft/runtime/market_judge_runner.py`
- Test: `tests/test_market_judgment.py`

- [ ] **Step 1: Write the failing test**

Append this test to `tests/test_market_judgment.py`:

```python
def test_market_judgment_prompt_includes_jue_decision_skills(
    tmp_path: Path,
) -> None:
    llm = _FakeLLM()
    engine = MarketJudgmentEngine(
        config=MarketJudgmentConfig(
            db_path=str(tmp_path / "market_judgment.db"),
            max_symbols=10,
            llm_max_symbols=5,
            use_naver_fallback=False,
        ),
        kis=_FakeKIS(),  # type: ignore[arg-type]
        codex_runtime=llm,  # type: ignore[arg-type]
        strategy_engine=_FakeStrategy(),  # type: ignore[arg-type]
        calendar=_OpenCalendar(),  # type: ignore[arg-type]
        memory_context_provider=lambda **_: {
            "status": "ok",
            "persona": "쥬는 한국장 투자 파트너다.",
            "decision_skills": {
                "market_judge": {
                    "version": "jue.market_judge.v1",
                    "content_md": "# 쥬 장중 판단 스킬\n- 계좌와 마켓 펄스를 먼저 본다.",
                },
                "risk_manager": {
                    "version": "jue.risk_manager.v1",
                    "content_md": "# 쥬 리스크 매니저 스킬\n- 손절과 비중을 확인한다.",
                },
            },
            "decision_skill_status": {"count": 2, "missing": []},
        },
    )

    result = asyncio.run(engine.run_once(use_llm=True))
    prompt = json.loads(llm.last_payload["messages"][1]["content"])

    assert result["status"] == "ok"
    assert prompt["required_decision_skills"] == ["market_judge", "risk_manager"]
    assert prompt["investment_memory"]["persona"].startswith("쥬는")
    assert prompt["investment_memory"]["decision_skills"]["market_judge"]["version"] == (
        "jue.market_judge.v1"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/test_market_judgment.py::test_market_judgment_prompt_includes_jue_decision_skills -q
```

Expected: FAIL because `MarketJudgmentEngine` has no `memory_context_provider` argument.

- [ ] **Step 3: Add provider type and constructor argument**

In `src/tradecraft/services/market_judgment.py`, add the provider type near the existing provider aliases:

```python
MemoryContextProvider = Callable[..., dict[str, Any] | None]
```

Update `MarketJudgmentEngine.__init__()`:

```python
memory_context_provider: MemoryContextProvider | None = None,
```

Store it:

```python
self.memory_context_provider = memory_context_provider
```

- [ ] **Step 4: Add memory context collection**

Add this method near `_market_pulse_context()`:

```python
def _investment_memory_context(
    self,
    *,
    symbols: list[str],
    quotes: list[dict[str, Any]],
    account: dict[str, Any],
    strategy_payload: dict[str, Any],
    market_pulse: dict[str, Any],
) -> dict[str, Any]:
    provider = self.memory_context_provider
    if provider is None:
        return {"status": "missing"}
    try:
        payload = provider(
            symbols=symbols,
            quotes=quotes,
            account=account,
            strategy=strategy_payload,
            market_pulse=market_pulse,
        )
    except Exception as exc:
        return {"status": "error", "error_message": str(exc)}
    return payload if isinstance(payload, dict) else {"status": "invalid"}
```

- [ ] **Step 5: Add memory context to `_build_prompt()`**

Extend `_build_prompt()` with:

```python
investment_memory: dict[str, Any] | None,
```

Add to the returned prompt:

```python
"investment_memory": investment_memory or {"status": "missing"},
"required_decision_skills": ["market_judge", "risk_manager"],
```

In `run_once()`, call `_investment_memory_context()` after `market_pulse` is available:

```python
investment_memory = self._investment_memory_context(
    symbols=focus_symbols,
    quotes=quotes,
    account=account,
    strategy_payload=strategy_payload,
    market_pulse=market_pulse,
)
```

Pass `investment_memory=investment_memory` into `_build_prompt()`.

- [ ] **Step 6: Wire main app**

In `src/tradecraft/main.py`, add this argument to the `MarketJudgmentEngine(...)` construction:

```python
memory_context_provider=investment_memory_service.context_pack,
```

- [ ] **Step 7: Wire market judge runner**

In `src/tradecraft/runtime/market_judge_runner.py`, create an `InvestmentMemoryService` with the same pattern used by `kis_block_trader_runner.py`, then pass:

```python
memory_context_provider=investment_memory.context_pack,
```

Use the existing settings fields:

```python
InvestmentMemoryConfig(
    root_path=settings.investment_memory_root_path,
    db_path=settings.investment_memory_db_path,
    strategy_md_path=settings.investment_memory_strategy_md_path,
    policy_mode=settings.investment_memory_policy_mode,
    persona_tone=settings.investment_memory_persona_tone,
    telegram_enabled=settings.investment_memory_send_telegram,
    context_max_chars=settings.investment_memory_context_max_chars,
)
```

- [ ] **Step 8: Run focused tests**

Run:

```bash
pytest tests/test_market_judgment.py::test_market_judgment_prompt_includes_jue_decision_skills -q
pytest tests/test_market_judgment.py -q
```

Expected: PASS.

- [ ] **Step 9: Run runner tests**

Run:

```bash
pytest tests/test_market_judge_runner.py -q
```

Expected: PASS.

- [ ] **Step 10: Checkpoint**

Run:

```bash
git diff -- src/tradecraft/services/market_judgment.py src/tradecraft/main.py src/tradecraft/runtime/market_judge_runner.py tests/test_market_judgment.py
```

Expected: diff shows memory context injection only. Do not commit unless the user explicitly requests it.

## Task 4: Surface Decision Skill Status In API And UI

**Files:**
- Modify: `src/tradecraft/services/investment_memory.py`
- Modify: `src/tradecraft/web/static/app.js`
- Modify: `src/tradecraft/web/static/style.css`
- Test: `tests/test_investment_memory_api.py`

- [ ] **Step 1: Write the failing API test**

Append this test to `tests/test_investment_memory_api.py`:

```python
def test_memory_status_exposes_decision_skill_status(client_with_admin) -> None:
    client, headers = client_with_admin

    response = client.get("/api/memory/status", headers=headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["decision_skill_status"]["count"] >= 4
    assert "block_manager" in payload["decision_skills"]
    assert payload["decision_skills"]["block_manager"]["version"] == "jue.block_manager.v1"
```

If this project uses a different authenticated fixture name in this file, use the existing admin-authenticated fixture from the same file and keep the assertions unchanged.

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/test_investment_memory_api.py::test_memory_status_exposes_decision_skill_status -q
```

Expected: FAIL because status does not expose `decision_skill_status`.

- [ ] **Step 3: Add service status fields**

In `InvestmentMemoryService.status()`, after `self.sync_policy_rules()`, add:

```python
decision_skills = self._decision_skills()
missing_skills = [
    key
    for key, value in decision_skills.items()
    if not str(value.get("content_md") or "").strip()
]
```

Include in the returned dict:

```python
"decision_skill_status": {
    "count": len(decision_skills),
    "missing": missing_skills,
},
"decision_skills": {
    key: {
        "version": value.get("version"),
        "preview": _truncate(value.get("content_md"), 180),
    }
    for key, value in decision_skills.items()
},
```

- [ ] **Step 4: Run API test**

Run:

```bash
pytest tests/test_investment_memory_api.py::test_memory_status_exposes_decision_skill_status -q
```

Expected: PASS.

- [ ] **Step 5: Add UI rendering**

In `src/tradecraft/web/static/app.js`, add a small renderer near existing memory render helpers:

```javascript
function renderDecisionSkills(memoryStatus) {
  const skills = memoryStatus?.decision_skills || {};
  const entries = Object.entries(skills);
  if (!entries.length) {
    return `<div class="memory-skill-strip muted">판단 스킬 상태 없음</div>`;
  }
  return `
    <div class="memory-skill-strip">
      ${entries
        .map(([key, value]) => {
          const version = escapeHtml(value?.version || "unknown");
          const preview = escapeHtml(value?.preview || "");
          return `
            <article class="memory-skill-chip">
              <strong>${escapeHtml(key)}</strong>
              <span>${version}</span>
              <p>${preview}</p>
            </article>
          `;
        })
        .join("")}
    </div>
  `;
}
```

Call it in the memory/status area where `state.memoryStatus` is rendered:

```javascript
${renderDecisionSkills(state.memoryStatus)}
```

- [ ] **Step 6: Add CSS**

In `src/tradecraft/web/static/style.css`, add:

```css
.memory-skill-strip {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 10px;
  margin-top: 12px;
}

.memory-skill-chip {
  border: 1px solid var(--border-subtle);
  background: var(--surface-soft);
  border-radius: 8px;
  padding: 10px;
}

.memory-skill-chip strong {
  display: block;
  color: var(--text-strong);
  font-size: 13px;
}

.memory-skill-chip span {
  display: block;
  color: var(--accent-source);
  font-size: 12px;
  margin-top: 4px;
}

.memory-skill-chip p {
  color: var(--text-muted);
  font-size: 12px;
  line-height: 1.5;
  margin: 8px 0 0;
}
```

- [ ] **Step 7: Run UI checks**

Run:

```bash
node --check src/tradecraft/web/static/app.js
git diff --check -- src/tradecraft/web/static/app.js src/tradecraft/web/static/style.css
```

Expected: both commands pass.

- [ ] **Step 8: Run API tests**

Run:

```bash
pytest tests/test_investment_memory_api.py tests/test_api_smoke.py -q
```

Expected: PASS.

- [ ] **Step 9: Checkpoint**

Run:

```bash
git diff -- src/tradecraft/services/investment_memory.py src/tradecraft/web/static/app.js src/tradecraft/web/static/style.css tests/test_investment_memory_api.py
```

Expected: diff shows status/UI skill visibility only. Do not commit unless the user explicitly requests it.

## Task 5: Protect 쥬 Identity And Prompt Quality

**Files:**
- Modify: `tests/test_prompt_identity.py`
- Test only unless the test exposes stale wording in source files.

- [ ] **Step 1: Add skill files to identity scan**

In `tests/test_prompt_identity.py`, extend the scanned path list with:

```python
Path("src/tradecraft/services/investment_memory.py"),
Path(".runtime/investment_memory/persona.md"),
Path(".runtime/investment_memory/policies/trading.md"),
Path(".runtime/investment_memory/skills/block_manager.md"),
Path(".runtime/investment_memory/skills/market_judge.md"),
Path(".runtime/investment_memory/skills/risk_manager.md"),
Path(".runtime/investment_memory/skills/reflection.md"),
```

Add assertions that the generated skill docs contain trading identity:

```python
def test_jue_decision_skill_files_keep_live_trading_identity() -> None:
    root = Path(".runtime/investment_memory/skills")
    expected = {
        "block_manager.md": "블록",
        "market_judge.md": "장중",
        "risk_manager.md": "리스크",
        "reflection.md": "반성",
    }
    for filename, keyword in expected.items():
        text = (root / filename).read_text(encoding="utf-8")
        assert "쥬" in text
        assert keyword in text
        assert "skill_id: jue." in text
```

- [ ] **Step 2: Run test**

Run:

```bash
pytest tests/test_prompt_identity.py -q
```

Expected: PASS. If it fails on a stale phrase, update the source prompt or generated runtime memory file to use the HERMES live block-trading identity.

- [ ] **Step 3: Run prompt-adjacent tests**

Run:

```bash
pytest tests/test_prompt_identity.py tests/test_investment_memory.py tests/test_market_judgment.py tests/test_kis_block_trader.py -q
```

Expected: PASS.

- [ ] **Step 4: Checkpoint**

Run:

```bash
git diff -- tests/test_prompt_identity.py
```

Expected: diff shows identity scan coverage for decision skill files. Do not commit unless the user explicitly requests it.

## Task 6: Full Verification And Runtime Restart

**Files:**
- No source changes expected in this task.

- [ ] **Step 1: Run Python syntax checks**

Run:

```bash
python3 -m py_compile \
  src/tradecraft/services/investment_memory.py \
  src/tradecraft/services/kis_block_trader.py \
  src/tradecraft/services/market_judgment.py \
  src/tradecraft/main.py \
  src/tradecraft/runtime/market_judge_runner.py
```

Expected: no output and exit code `0`.

- [ ] **Step 2: Run focused pytest suite**

Run:

```bash
pytest \
  tests/test_investment_memory.py \
  tests/test_investment_memory_api.py \
  tests/test_kis_block_trader.py \
  tests/test_market_judgment.py \
  tests/test_market_judge_runner.py \
  tests/test_prompt_identity.py \
  tests/test_api_smoke.py \
  -q
```

Expected: PASS.

- [ ] **Step 3: Run frontend checks**

Run:

```bash
node --check src/tradecraft/web/static/app.js
git diff --check -- \
  src/tradecraft/services/investment_memory.py \
  src/tradecraft/services/kis_block_trader.py \
  src/tradecraft/services/market_judgment.py \
  src/tradecraft/main.py \
  src/tradecraft/runtime/market_judge_runner.py \
  src/tradecraft/web/static/app.js \
  src/tradecraft/web/static/style.css \
  tests/test_investment_memory.py \
  tests/test_investment_memory_api.py \
  tests/test_kis_block_trader.py \
  tests/test_market_judgment.py \
  tests/test_prompt_identity.py
```

Expected: both commands pass.

- [ ] **Step 4: Restart local runtime sessions**

Run:

```bash
python3 - <<'PY'
import subprocess

for name in [
    "hermes-control",
    "hermes-investment-memory",
    "hermes-kis-block-trader",
    "hermes-market-judge",
    "hermes-market-pulse",
]:
    subprocess.run(["tmux", "kill-session", "-t", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
PY

tmux new-session -d -s hermes-control 'cd /Users/juhwan/hermes_v2 && .venv/bin/python -m uvicorn tradecraft.main:app --host 127.0.0.1 --port 18080 2>&1 | tee -a .runtime/tradecraft-control-18080.log'
tmux new-session -d -s hermes-investment-memory 'cd /Users/juhwan/hermes_v2 && .venv/bin/python -m tradecraft.runtime.investment_memory_runner 2>&1 | tee -a .runtime/investment-memory-runner.log'
tmux new-session -d -s hermes-kis-block-trader 'cd /Users/juhwan/hermes_v2 && .venv/bin/python -m tradecraft.runtime.kis_block_trader_runner 2>&1 | tee -a .runtime/kis-block-trader-runner.log'
tmux new-session -d -s hermes-market-judge 'cd /Users/juhwan/hermes_v2 && .venv/bin/python -m tradecraft.runtime.market_judge_runner 2>&1 | tee -a .runtime/market-judge-runner.log'
tmux new-session -d -s hermes-market-pulse 'cd /Users/juhwan/hermes_v2 && .venv/bin/python -m tradecraft.runtime.market_pulse_runner 2>&1 | tee -a .runtime/market-pulse-runner.log'
```

Expected: all five tmux sessions start.

- [ ] **Step 5: Verify readiness and memory skill status**

Run:

```bash
python3 - <<'PY'
import json
import os
from pathlib import Path
from urllib import request

root = Path("/Users/juhwan/hermes_v2")
for line in (root / ".env").read_text().splitlines():
    if not line or line.lstrip().startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

token = os.environ.get("TRADECRAFT_ADMIN_TOKEN") or next(
    (part.strip() for part in os.environ.get("TRADECRAFT_ADMIN_TOKENS", "").split(",") if part.strip()),
    "",
)
headers = {"Authorization": f"Bearer {token}"}

def get(path: str) -> dict:
    req = request.Request(f"http://127.0.0.1:18080{path}", headers=headers)
    with request.urlopen(req, timeout=5) as response:
        return json.loads(response.read().decode())

readiness = get("/api/ops/readiness")
memory = get("/api/memory/status")
print(json.dumps({
    "readiness": readiness.get("status"),
    "decision_skill_status": memory.get("decision_skill_status"),
    "decision_skill_keys": sorted((memory.get("decision_skills") or {}).keys()),
}, ensure_ascii=False, indent=2))
PY
```

Expected output includes:

```json
{
  "readiness": "green",
  "decision_skill_status": {
    "count": 4,
    "missing": []
  },
  "decision_skill_keys": [
    "block_manager",
    "market_judge",
    "reflection",
    "risk_manager"
  ]
}
```

- [ ] **Step 6: Browser verification**

Open `http://127.0.0.1:18080/`, authenticate with the admin token, then verify:

- Research room memory or operations area shows the four decision skills.
- 장중 판단 still renders Market Pulse v3.
- 블록 트레이딩 page still shows block board and live/paper state.
- No horizontal overflow on desktop and mobile viewport.

- [ ] **Step 7: Final checkpoint**

Run:

```bash
git status --short
```

Expected: only intended source/test/static files are modified. Do not commit unless the user explicitly requests it.

## Self-Review

**Spec coverage:**
This plan covers explicit skill-style guidance files, runtime loading, KIS block manager prompt injection, market judgment prompt injection, UI/API visibility, identity protection, and verification.

**Placeholder scan:**
The plan has concrete file paths, test functions, commands, expected failures, and expected passes. There are no deferred implementation slots.

**Type consistency:**
The same keys are used across tasks: `decision_skills`, `decision_skill_status`, `required_decision_skills`, `memory_context_provider`, `content_md`, and `version`.
