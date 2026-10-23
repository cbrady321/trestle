# A POST when a run ends

`run(..., callback="http://127.0.0.1:8765/your/token")` sends one HTTP `POST` to that URL once the
run has ended, however it ended, after its evidence is finalized. The listener is yours, not
Trestle's. Waiting (`completion="terminal"`, `await_runs`) still works.

The host must be `127.0.0.1`, `[::1]` or `localhost`, or match the operator's `[callbacks] allow`;
never Trestle's own port. Else `admission.callback_not_allowed`; a malformed URL (not http(s), a
user/password, a `#fragment`, over 2,048 bytes) is `admission.invalid_args`. Neither is a run.

The body (version 1, at most 2 KiB), with `Trestle-Event: run.ended`:

```json
{"type": "trestle.run.ended", "version": 1, "delivery_id": "r_…:ended", "attempt": 1,
 "sent_at": "…Z", "run": {"run_id": "r_…", "plugin": "…", "idempotency_key": null,
 "state": "failed", "outcome_class": "execution_error", "answer_outcome": "failed",
 "error": {"code": "…", "message": "…"}, "started_at": "…Z", "ended_at": "…Z"}}
```

Act on `answer_outcome`. Ignore fields you do not know; ask Trestle with `run_id` for more.

Delivery is at-least-once: a `2xx` delivers; no answer, `408`, `429` or `5xx` retries at 2 s,
10 s, 30 s and 2 min; anything else stops; redirects are not followed. Drop repeats by
`delivery_id`. A join keeps the run's first callback. The run's `callback` field shows
`status` (`pending`, `delivered`, `failed`, `abandoned`) and the origin, never your path.
