---
skill_id: evidence_audit
name: Evidence Audit
version: 1
scope: shared
source_inspiration:
  - https://github.com/anthropics/financial-services
  - https://raw.githubusercontent.com/anthropics/financial-services/main/plugins/agent-plugins/statement-auditor/agents/statement-auditor.md
  - https://raw.githubusercontent.com/anthropics/financial-services/main/plugins/vertical-plugins/financial-analysis/skills/audit-xls/SKILL.md
required_outputs:
  - evidence_score
  - contradiction
  - freshness
  - missing_data
max_prompt_chars: 1500
---
# Evidence Audit

Before a decision becomes an action, inspect evidence quality.

Check:
- source freshness
- source type: quote, order book, account, report, RAG, valuation, whale, closing-flow, quant, catalyst, memory, user directive
- whether evidence is directly about the symbol or only sector/market context
- whether evidence conflicts with current price action
- whether a stale data gap should block action, shrink size, or merely reduce confidence

Every create/update/close action should include at least one concrete evidence reference or a clear reason why it is a rule-driven risk action.
