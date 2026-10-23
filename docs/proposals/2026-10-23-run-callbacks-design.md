# Run callbacks: design

**Status:** design, for the owner's review. Nothing here is built.
**Date:** 2026-10-23
**Release:** its own change after 0.6.0.
**Read at:** master `fc4999d0`.

---

## The idea in plain words

When you start a run, you may give Trestle a URL:

```text
run(plugin="repo_tests", args={…}, callback="http://127.0.0.1:8765/trestle/abc123")
```

When that run ends, however it ends, Trestle sends one HTTP `POST` to that URL. The body is a small
JSON document saying which run it was and how it ended. That is the whole feature.

- **Trestle does not provide the listener.** Whatever is listening on that port is someone else's:
  a future Claude Code or OpenHands callback endpoint, a script, a test. Trestle promises only that
  the request is sent, and what it looks like.
- **The body is a versioned contract**, so a listener written later can rely on it.
- **By default only this machine can be called**: `127.0.0.1`, `::1` or `localhost`. The operator
  can allow more addresses in `config.toml`. Any other URL is refused before the run starts.
- **Delivery is retried** a few times if the listener is not answering, and the outcome is recorded
  on the run, so you can see whether the callback got through.
- **A restart does not lose it.** A run interrupted because the service died still gets its
  callback, sent by the next start.

Nothing else changes. `completion="terminal"` and `await_runs` keep working as before, and a run
with a callback can still be waited on. Two alternatives were not taken: chaining a follow-up
plugin with `after`, and a plugin that writes an inbox file. A URL is simpler for the caller, and it
is the shape an agent host is most likely to offer.

---

## The argument

`run` gains one optional argument, `callback: str | None` (default `None`, meaning no callback).

Admission checks it before anything exists: no run id, no run directory, no process.

| Check | Refusal |
|---|---|
| A string of at most 2,048 bytes, an absolute `http` or `https` URL, with no user/password part and no `#fragment` | `admission.invalid_args` |
| The host is loopback (`127.0.0.1`, `[::1]` or `localhost`), or the URL matches an entry in `[callbacks] allow` | `admission.callback_not_allowed` (new, not retryable) |
| The port is not Trestle's own MCP or ops port | `admission.callback_not_allowed` |

- **Recorded once**, on the run's `created` row, as `callback`. It is fixed at admission like the args.
- **Idempotency keys.** `callback` is not part of a key's identity. A re-send that joins an existing
  run keeps that run's original callback, and the new value is ignored (Q-2).
- **Shown without its path.** Views show only the URL's origin (`http://127.0.0.1:8765`), never the
  path or query. A listener may put a secret token there.

## The request

```http
POST /trestle/abc123 HTTP/1.1
Host: 127.0.0.1:8765
Content-Type: application/json
User-Agent: trestle/0.7.0
Trestle-Event: run.ended
Trestle-Delivery: r_7f3c…:ended
```

```json
{
  "type": "trestle.run.ended",
  "version": 1,
  "delivery_id": "r_7f3c…:ended",
  "attempt": 1,
  "sent_at": "2026-10-23T10:00:03Z",
  "run": {
    "run_id": "r_7f3c…",
    "plugin": "repo_tests",
    "idempotency_key": "tests:abc",
    "state": "failed",
    "outcome_class": "execution_error",
    "answer_outcome": "failed",
    "error": {"code": "execution.worker_exit", "message": "…"},
    "started_at": "2026-10-23T09:20:00Z",
    "ended_at": "2026-10-23T10:00:02Z"
  }
}
```

**The contract (version 1):**

- `state` is one of `succeeded`, `failed`, `cancelled`, `timed_out`, `worker_exit`, `interrupted`.
- `outcome_class` is the run's `outcome.class`. `answer_outcome` is `answer.outcome`, and it is the
  verdict to act on, as agents are already told (`agents.md`). For a workflow plugin the two can
  differ.
- `error` is `null` for a run with no error record. `message` is the existing bounded message of at
  most 512 bytes (K-10).
- `idempotency_key` and `started_at` are `null` when absent. `started_at` is absent for a run that
  ended without starting, for example a held run whose `after` was not met.
- Every listed field is always present. A field may be added within version 1, so listeners must
  ignore fields they do not know. Removing or changing a field is version 2.
- The body is at most 2 KiB. It carries no args, no result and no paths. A listener that wants more
  asks Trestle with `run_id`.
- **When it is sent:** after the run's evidence is finalized and its terminal row is written. So a
  listener that calls `query` or `fetch` on receipt finds the run finished.

## Delivery

| Rule | Behaviour |
|---|---|
| Success | Any `2xx` answer. The response body is ignored |
| Retry | Connection refused, a timeout, `408`, `429` or `5xx`. Attempts at 0 s, 2 s, 10 s, 30 s and 2 min: 5 in total, each with a 5 s timeout |
| Give up | Any other `3xx` or `4xx` stops at once. Redirects are never followed. After the fifth failure the callback is `failed` |
| Duplicates | At-least-once: the listener may see the same event twice (a retry after a lost `2xx`, or a restart). `delivery_id` is the same on every attempt; drop repeats by it |
| Ordering | One event per run, so ordering within a run does not arise. Across runs there is no order |
| Listener gone | The run is unaffected. Only its callback status says `failed` |

**Recorded on the run.** The server that sends the callback writes `<run_dir>/callback.json`
atomically after each attempt. This is a separate file, because the run's ledger ends at its
terminal row and stays that way. `RunView` and `query(view="run")` gain:

```json
"callback": {"origin": "http://127.0.0.1:8765", "status": "pending | delivered | failed | abandoned",
             "attempts": 2, "last_http_status": 503, "last_error": null, "delivered_at": null}
```

The field is `null` for a run sent without a callback. Whether and how the run ended stays in its
ledger and `state.json`. `callback.json` says only what happened to the POST.

**Surviving a restart.** At admission, Trestle creates an empty marker
`home/callbacks/pending/<run_id>`. The sender removes it once the status is `delivered`, `failed`
or `abandoned`. While it sends, it holds a lock on the marker, so only one server sends a given
callback. At start, each server goes through that folder. For each marker whose lock it can take
and whose run has ended, it continues the remaining attempts. That includes runs that recovery has
just finalized `interrupted`. Nothing scans the run history. A marker whose run ended more than 24
hours ago is closed `abandoned` without sending, so a long outage does not release a burst of
stale events.

## Security

`security.md` requires that an agent-chosen value be a closed set or validated, and that no
arbitrary URL be chosen by an agent unless justified. The justification here is narrow:

- **Default: this machine only.** No outside host is reachable unless the operator lists it. An
  agent cannot widen the list, because the server reads `config.toml` once at start. Under the
  restricted profile the rules are the same.
- **A blind, fixed POST.** The agent chooses only the URL. Method, headers and body are Trestle's.
  The response never reaches the agent, apart from the status code and a short error in the
  callback status. So a callback cannot be used to read another local service, only to send it
  this one fixed JSON body.
- **Trestle's own ports are refused**, so a callback cannot be aimed at Trestle itself.
- **`localhost` is not looked up in DNS.** Trestle connects to `127.0.0.1` (then `::1`) directly
  and sends `Host: localhost`, so a hosts-file or resolver trick cannot redirect it.
- **The operator's list:**

  ```toml
  [callbacks]
  allow = ["https://hooks.example.internal/trestle/"]   # default: [] (loopback only)
  ```

  Each entry is a URL prefix. A callback matches it when scheme, host and port are equal and the
  path starts with the entry's path at a `/` boundary. Entries for hosts other than loopback must
  be `https`. A malformed entry stops the server from starting, as other config errors do.
- **No signature header in version 1** (Q-1). A local listener can already tell the callback is
  genuine by putting an unguessable token in its own URL path, and views never show the path.

## Test plan

| # | Test |
|---|---|
| 1 | Admission: each refusal row above, including `[::1]`, `localhost`, user/password, fragment, over 2,048 bytes, Trestle's own ports, `ftp:`. Each is refused with no run id and nothing on disk |
| 2 | The allowlist: prefix match on a `/` boundary (`/trestle/` allows `/trestle/x` but not `/trestlex`); `http` to a host other than loopback refused at config load; a malformed entry stops the server |
| 3 | One test per terminal state (`succeeded`, `failed`, `cancelled`, `timed_out`, `worker_exit`, `interrupted`, and a held run ended by `after_unmet`): a test listener receives one POST, the body validates against the version 1 schema, and the run is finalized when it arrives |
| 4 | Retries: a listener answering 503, 503, 200 is called three times, with the same `delivery_id` and `attempt` counting up, and the status is `delivered`. A `404` stops after one attempt. A `302` is not followed. No listener at all gives five attempts, then `failed` |
| 5 | `callback.json` and `query(view="run")` show the status after each case. The path and query never appear in any view |
| 6 | Restart: SIGKILL the server with a run live and its listener down. Bring the listener up and start the server: the run is `interrupted` and its callback is `delivered` |
| 7 | Restart in the middle of retries: the attempts continue, with no more than five in total. Two servers on one home: exactly one sends. A marker older than 24 hours ends `abandoned` with no POST |
| 8 | Idempotency: a join with a different `callback` keeps the original. Exactly one POST per run |
| 9 | `localhost` connects without a DNS lookup (a resolver stub that would answer another address is never called) |
| 10 | Doc test: `agents.md` and the guide describe `callback`, the version 1 body, and at-least-once delivery |

## Open questions

**Q-1. A signature header (an HMAC of the body with a secret in `$TRESTLE_HOME`)?**
*Recommendation: no, for now.* A future host's listener would not have Trestle's secret, so it
could not check the signature, and secret distribution is what makes signatures complicated. An
unguessable path in the listener's own URL does the same job with nothing to set up. The header
name `Trestle-Signature` is reserved for a later version.

**Q-2. A re-send that joins an existing run with a different callback URL: keep the original or
replace it?** *Recommendation: keep the original.* There is one delivery per run and nothing to
reconcile. A caller whose listener has moved can still read the result with
`await_runs(keys=[…])`.

**Q-3. Events other than "ended" (started, progress)?** *Recommendation: no.* `type` and the
`Trestle-Event` header leave room to add them later without changing version 1.

**Q-4. Make the retry schedule or the 24-hour cut-off configurable?** *Recommendation: no.* Fixed
values make the contract simpler to state, and nothing yet needs another value.
