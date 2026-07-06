---
skill_id: valuation_frame
name: Valuation Frame
version: 1
scope: kis
source_inspiration:
  - https://raw.githubusercontent.com/anthropics/financial-services/main/plugins/vertical-plugins/equity-research/skills/valuation-frame/SKILL.md
required_outputs:
  - valuation_method
  - fair_value_range
  - key_assumptions
  - margin_of_error
max_prompt_chars: 1500
---
# Valuation Frame

Jue builds a decision-grade valuation frame for KIS symbols and ETF/core candidates. Match the method to available evidence and symbol type.

Include:
- valuation method: multiples, sum of parts, yield, asset value, or scenario range
- peer or index reference when useful
- fair value range, bear/base/bull anchors, and current price distance
- assumptions that move the range most
- what new evidence would widen, narrow, raise, or lower the range

Use valuation to discipline entries, exits, and sizing, not to force action.
