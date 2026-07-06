---
skill_id: block_design
name: Block Design
version: 1
scope: shared
source_inspiration:
  - https://github.com/anthropics/financial-services
  - https://raw.githubusercontent.com/anthropics/financial-services/main/plugins/vertical-plugins/equity-research/skills/idea-generation/SKILL.md
  - https://raw.githubusercontent.com/anthropics/financial-services/main/plugins/vertical-plugins/equity-research/skills/thesis-tracker/SKILL.md
required_outputs:
  - executable_block
  - price_structure
  - invalidation
  - override_reason
max_prompt_chars: 1900
---
# Block Design

A block is not an opinion. A block is an executable structure with independent identity, time horizon, size, entry rule, target rule, stop rule, thesis, risk note, and invalidation.

Required block fields:
- symbol
- market or account scope
- side when relevant
- horizon: short, mid, long, core_etf, futures, or cash
- quantity or quote budget
- entry style: immediate/aggressive_limit or wait_for_price
- entry trigger price and operator for waiting blocks
- target price
- stop price
- thesis
- risk note
- confidence
- evidence references

Calculated price plans are defaults. Jue may override them only when it carries the calculated inputs and writes an override reason that mentions the evidence or market microstructure that justifies the change.

KIS value-cycle preference:
- default to patient `wait_for_price` entries for undervalued or fairly valued quality names
- treat extended momentum as a reason to design a better pullback trigger, not as automatic permission to chase
- use mid or long horizons when valuation and thesis are the main reasons; do not manage every value block like a short scalp
- record whether the entry price was a low-risk location, a fair-value discount, or a tactical exception
- when closing, distinguish fair-value/overvaluation trims from thesis-break exits and noise-driven exits
