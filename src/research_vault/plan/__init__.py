# SPDX-License-Identifier: AGPL-3.0-or-later
"""plan/ — pre-registered experiment + ablation plan support.

Modules:
  style   — plan_tips config seam (adopter-customizable prompt keys).
  check   — shape-lint: branch-presence + one-component-per-ablation (K-2).
  verbs   — rv plan subcommand dispatcher.

note.py-FREE by design: plan fields (plan_kind / covers / plan_role /
supports_main / stance) are agent-authored note CONTENT — they are NOT injected
via cmd_new templates and this module does NOT touch note.py.
"""
