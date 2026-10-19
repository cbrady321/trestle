# Cancel, deadline and `stop_grace`

The deadline is fixed at admission (queue time included) and enforced. A cancel request, or the
deadline, stops the run: its process tree gets SIGTERM, then SIGKILL after a grace of about
10 s. Read `ctx.cancelled` in loops and return promptly. With `stop_grace`, `ctx.cancelled`
turns true that many seconds before the kill, so you can write a final status. A run stopped
this way still ends `cancelled` or `timed_out`, and what it attached is kept. A run cancelled
while queued never starts.
