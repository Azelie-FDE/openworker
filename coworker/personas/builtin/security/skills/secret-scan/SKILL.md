---
name: secret-scan
description: Hunt committed secrets with gitleaks and drive safe rotation
---
Find committed credentials and get them rotated and removed — without ever exposing them
further yourself.

ABSOLUTE RULE: never print a secret's value — not in output, notes, todo items, commits,
or PRs. Refer to every hit as "<kind> in <file>:<line> (commit <short-sha>)".

1. Check the tool: `gitleaks version`. If missing, tell the user how to install it
   (`brew install gitleaks`) and STOP — ask before installing anything.
2. Scan working tree AND history:
   `gitleaks detect --source . --report-format json --report-path /tmp/gitleaks.json`
   (history matters: a secret deleted in HEAD is still live in every clone).
3. Triage each hit by reading its context:
   - Real credential, test fixture, or example placeholder? Say which and why.
   - For real ones: what does it grant access to, and is it plausibly still valid?
4. For every real secret, in this order:
   a. ROTATE first — tell the user exactly where to revoke/rotate it (the provider's
      console page or CLI command). Rotation beats removal: history rewrite without
      rotation is false comfort.
   b. Remove it from the code: move to env vars or the project's secret store, matching
      how this codebase already handles configuration.
   c. Prevent recurrence: add/extend `.gitignore` for local secret files and offer a
      `.gitleaks.toml` baseline plus a pre-commit hook.
   d. History purge (git filter-repo/BFG) is DESTRUCTIVE and rewrites shared history —
      describe the trade-off and only proceed if the user explicitly asks.
5. Deliver: a hit list (kind · location · verdict · rotation status), the cleanup
   branch/PR, and the prevention setup you added or recommend.
