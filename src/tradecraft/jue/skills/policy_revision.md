---
skill_id: policy_revision
name: Policy Revision
version: 1
scope: shared
source_inspiration:
  - https://github.com/anthropics/financial-services
  - https://raw.githubusercontent.com/anthropics/financial-services/main/plugins/vertical-plugins/private-equity/skills/value-creation-plan/SKILL.md
  - https://raw.githubusercontent.com/anthropics/financial-services/main/plugins/vertical-plugins/wealth-management/skills/client-review/SKILL.md
required_outputs:
  - policy_candidate
  - scorecard_update
  - revision_action
  - confidence
max_prompt_chars: 1600
---
# Policy Revision

Jue improves by converting repeated block outcomes into policy candidates, then into preference or caution rules when evidence is strong enough.

Policy revision must:
- use multiple block reflections when available
- separate symbol-specific lessons from transferable process lessons
- avoid hard trading bans except system safety gates
- record whether the policy changes sizing, confirmation, target/stop design, waiting-entry preference, or review cadence
- track sample count, average PnL, win rate, expectancy, and rule compliance

When evidence is thin, keep the result as an observation. When evidence repeats, promote it to caution or preference.
