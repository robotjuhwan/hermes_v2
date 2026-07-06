# HERMES / 쥬 Specbook

This specbook describes the current HERMES system as implemented in this repository and local runtime. It is intended to be detailed enough to support future refactoring, operational debugging, and trading-system review.

## Reading Order

1. [Product Identity](00_product_identity.md)
2. [Current Inventory](01_current_inventory.md)
3. [Architecture](02_architecture.md)
4. [Runtime Processes](03_runtime_processes.md)
5. [Databases](04_databases.md)
6. [LLM System](05_llm_system.md)
7. [KIS 쥬](06_kis_ju.md)
8. [Binance 쥬](07_binance_ju.md)
9. [Research & Memory](08_research_memory.md)
10. [Strategy Intelligence](09_strategy_intelligence.md)
11. [UI](10_ui.md)
12. [API Reference](11_api_reference.md)
13. [Config & Env](12_config_env.md)
14. [Security & Ops](13_security_ops.md)
15. [Observability](14_observability.md)
16. [Known Gaps](15_known_gaps.md)
17. [Refactor Roadmap](16_refactor_roadmap.md)
18. [Refactor Reference](17_refactor_reference.md)
19. [Data Model Reference](18_data_model_reference.md)
20. [Trading Execution Contracts](19_trading_execution_contracts.md)
21. [Research Pipeline Contracts](20_research_pipeline_contracts.md)
22. [Memory & Learning Contracts](21_memory_learning_contracts.md)
23. [UI State Contracts](22_ui_state_contracts.md)
24. [Operations Runbook](23_operations_runbook.md)
25. [Trading Validation Lab](24_trading_validation_lab.md)

## Source of Truth Rules

- Prefer current source code over memory.
- Prefer runtime DB schema and logs over guesses.
- Mark unverified behavior as `검증 필요`.
- Record command output summaries, not huge raw dumps.
- Keep active-trading identity explicit: HERMES/쥬 is an active block-trading system with safety gates.
- Treat the spec as a refactoring contract: if code moves, the observable behavior, DB ownership, API shape, and safety gates documented here must still be preserved or explicitly migrated.

## Maintenance Rules

- Update this spec when adding a runner, API group, DB, trading gate, LLM prompt surface, or UI tab.
- When behavior and spec disagree, fix either the implementation or the spec before large refactors.
- Keep refactor proposals in `16_refactor_roadmap.md`; keep observed problems in `15_known_gaps.md`.
- New detailed subsystem sheets should be appended after `17_refactor_reference.md` unless they replace an older sheet.
- Do not duplicate secrets, account identifiers, or raw prompt payloads in this spec. Use paths, schemas, and summarized behavior instead.
