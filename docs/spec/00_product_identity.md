# Product Identity

## One Sentence

HERMES is an active trading partner system where 쥬 manages KIS and Binance block-trading decisions, while deterministic rule executors and safety gates handle order execution constraints.

## Core Characters

### HERMES

The full project: web UI, runtime runners, exchange adapters, research pipelines, memory, strategy intelligence, block ledgers, and operational controls.

### 쥬

The trading partner persona used by the LLM-backed managers. 쥬 reads research, memory, account state, block state, market context, and strategy signals, then proposes or updates trading blocks.

## Non-Negotiable Execution Model

- LLM managers decide block intent.
- Rule executors monitor open blocks without extra LLM calls.
- Safety gates override all generated intent.
- Block ledgers are the operational unit, not only account-level positions.
- Existing holdings can be adopted into blocks without sending buy orders.
- KIS and Binance share memory/process lessons only where explicitly scoped.

## Important Terms

| Term | Meaning |
| --- | --- |
| Block | Independent trading unit with symbol, quantity, entry, target, stop, status, thesis, and events. |
| Open Block | A block currently holding risk or awaiting rule-based management. |
| Waiting Entry Block | A proposed block that should enter only when a price trigger is reached. |
| Manager Run | LLM-backed review cycle that creates, updates, pauses, closes, or adopts blocks. |
| Rule Tick | Deterministic executor pass that checks target/stop/trigger conditions. |
| Memory | Local Markdown + SQLite provenance used to improve future decisions. |
| Safety Gate | Non-negotiable system guard such as kill switch, cash check, quantity check, duplicate-order prevention, auth, and rate limiting. |
