---
skill_id: execution_review
name: Execution Review
version: 1
scope: shared
source_inspiration:
  - https://github.com/anthropics/financial-services
  - https://raw.githubusercontent.com/anthropics/financial-services/main/plugins/vertical-plugins/private-equity/skills/returns-analysis/SKILL.md
  - https://raw.githubusercontent.com/anthropics/financial-services/main/plugins/vertical-plugins/private-equity/skills/portfolio-monitoring/SKILL.md
required_outputs:
  - execution_quality
  - slippage_note
  - rule_compliance
  - next_lesson
max_prompt_chars: 1500
---
# Execution Review

After every closed, errored, stale, or manually overridden block, review execution separately from idea quality.

Record:
- whether entry happened as designed
- whether exit happened by target, stop, manual close, timeout, stale quote, order error, or rule adjustment
- MFE and MAE when available
- slippage or non-fill cause
- whether a waiting-entry block would have been better
- whether target/stop distance was too tight, too loose, or structurally wrong
- lesson to feed policy_revision
