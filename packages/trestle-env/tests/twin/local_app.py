"""The local stdlib HTTP app the PROC cases share (L.RB-2.1, L.RB-5.2; not a test module).

`APP` is `tests/fixtures/apps/http_app.py`, launched with `PORT=0`: the local process port returns
from `create` and `restart` once the app has reported the port it chose (its endpoint contract), so
no case guesses a free port or waits on the app's log; readiness is the read facet's."""

from __future__ import annotations

from pathlib import Path

APP = Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "apps" / "http_app.py"
REFUSED = 3  # the app answers `/health` 503 this many times before the first 200
