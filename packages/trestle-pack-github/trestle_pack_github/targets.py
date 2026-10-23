"""Pull request targets: `https://<host>/<owner>/<repo>/pull/<n>` or `<owner>/<repo>#<n>`."""

from __future__ import annotations

import re
from dataclasses import dataclass

_NAME = r"[A-Za-z0-9_.-]+"
_SHORT = re.compile(rf"({_NAME})/({_NAME})#([0-9]+)")
_URL = re.compile(
    rf"https://([A-Za-z0-9.-]+(?::[0-9]+)?)/({_NAME})/({_NAME})/pull/([0-9]+)(?:[/?#].*)?"
)


class TargetError(ValueError):
    """A target that names no pull request on this run's host."""


@dataclass(frozen=True)
class PrRef:
    host: str
    owner: str
    repo: str
    number: int

    @property
    def url(self) -> str:
        return f"https://{self.host}/{self.owner}/{self.repo}/pull/{self.number}"

    @property
    def repo_arg(self) -> str:
        """The `-R` form gh takes: `HOST/OWNER/REPO`."""
        return f"{self.host}/{self.owner}/{self.repo}"


def parse_target(text: str, host: str) -> PrRef:
    """The pull request `text` names on `host`; `TargetError` when it names none there."""
    value = text.strip()
    short = _SHORT.fullmatch(value)
    if short:
        return PrRef(host, short.group(1), short.group(2), int(short.group(3)))
    url = _URL.fullmatch(value)
    if url is None:
        raise TargetError(f"{text!r} is not a pull request: give its URL or owner/repo#number")
    if url.group(1).lower() != host.lower():
        raise TargetError(
            f"{text!r} is on {url.group(1)}, but this run talks to {host} "
            "(the operator sets GH_HOST under [plugins.env] for another host)"
        )
    return PrRef(host, url.group(2), url.group(3), int(url.group(4)))
