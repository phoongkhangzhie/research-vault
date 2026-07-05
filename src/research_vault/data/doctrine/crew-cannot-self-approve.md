# crew-cannot-self-approve

**The rule:** Crew agents cannot advance a human-go gate on their own behalf.
A dispatched subagent cannot call `rv dag approve` and have it succeed.

## Why it is enforced mechanically, not by convention

A convention ("agents should not self-approve") is only as strong as the
weakest prompt.  An adversarial or hallucinating agent could generate a
plausible approval rationale and call `rv dag approve` anyway.

SR-APPROVE-GATE makes the rule *structural*:

```
security = stdin.isatty()  (full stop)
```

A dispatched subagent has **no controlling TTY**.  `rv dag approve` checks
`sys.stdin.isatty()` at the single chokepoint (`cmd_approve` in
`dag/verbs.py`, after the `awaiting-go` check, before any state write).
If the TTY test fails *and* no valid pre-provisioned token is present, the
command returns 1 with state **unchanged**.  There is no flag or
environment-variable trick that changes this — `--yes` is honoured only
when a TTY is actually present.

## Two authorized paths

1. **TTY (default):** The human operator runs `rv dag approve <run> <node>`
   at their terminal.  The gate prints a one-line summary and accepts a
   single `y` / enter / `approve` keystroke.  `--yes` skips the keystroke.

2. **Token (CI/scripts):** The operator provisions `RV_APPROVER_TOKEN` once
   via `rv approval setup`.  The fingerprint is stored in config.  A
   non-interactive call resolves the token and verifies the fingerprint
   (timing-safe HMAC comparison).  This is the "informed operator pre-approval"
   path — the operator decided, ahead of time, that a specific script may
   advance gates.

## The `enforce=false` escape hatch (Slice 3)

An adopter who needs to disable the gate entirely can do so — but *only*
via the gate itself:

```
rv approval disable      # presence-checked; writes enforce=false + enforce_sig
rv approval enable       # re-arms
```

A raw toml edit (`enforce = false`) is **inert** when a token is provisioned:
`cmd_approve` verifies `enforce_sig` (an HMAC keyed on the approver token).
An agent that edits the toml gets nothing — it cannot compute a valid HMAC
without the token, and the token never appears in code or config.

## Honest limitation

This mechanism defends the **normal dispatched path** (subagent called via
`rv dag brief` / `subprocess`, no TTY).  It is not airtight against a
same-user adversary who has both shell access AND the approver token — but
that adversary could approve via the token path directly, so the gate is
not intended to protect against a compromised local account.  Its purpose
is to prevent an agent from *accidentally* or *autonomously* advancing gates
that require human judgment.

## Discovery

- `rv dag status <run>` prints the exact `rv dag approve` command for each
  awaiting-go node so the operator sees what to run.
- `rv approval status` shows the current gate state.
- `rv doctor` includes the approval gate status in its report.
- Fail-closed message:
  ```
  rv dag approve: this human-go gate needs you.
    → At your terminal: rv dag approve <run> <node>
    → For scripts/CI: rv approval setup (one-time approver token)
  Crew agents can't self-approve — by design. [crew-cannot-self-approve]
  ```
