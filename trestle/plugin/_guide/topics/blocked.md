# When a person must act: `Blocked`

```python
from trestle.plugin import Blocked

raise Blocked("aws_login", "Run `aws sso login --profile dev`, then resend.")
```

The run ends `succeeded` with `outcome: "blocked"`. `answer.primary` carries
`code: "plugin.aws_login"`, the `human_action` and `resend`: `"succeeds_after_action"` (the
default), `"will_not_succeed"` or `"unknown"`. A code is lower-case words joined by `_` or `.`.
Raise `Blocked` for an expired login, a missing tool or a prompt nobody will answer, instead of
failing. A workflow unit returns the `Blocked` step from `trestle.workflow` instead.
