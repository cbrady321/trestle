#!/usr/bin/env python3
"""stub_cloud: an AWS-CLI-shaped demo client (MC-B-06, TM-B4-1.3, D-9: AWS is DEMO ONLY).

    stub_cloud.py sts get-caller-identity

is the one command it knows, shaped like `aws sts get-caller-identity`: it reads the demo token from
the credentials file named by `STUB_CLOUD_CREDENTIALS_FILE` (the way the SDK reads
`AWS_SHARED_CREDENTIALS_FILE`), presents it to the issuer at `STUB_CLOUD_ENDPOINT_URL` (loopback
only) and prints one JSON object on stdout:

    authenticated:  {"UserId", "Account", "Arn", "Generation"}   exit 0
    otherwise:      {"Error": "InvalidClientTokenId", "Generation": <claimed or null>}  exit 254

`Generation` is the generation the presented token claims (what a consumer "saw"); the token itself
is never printed. It is named neither `aws` nor anything a PATH search finds, and is run only by
absolute path. It reads no real credential store and calls no host but the issuer.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

NOT_AUTHENTICATED = 254
NO_CREDENTIALS = 255
ACCOUNT = "000000000000"
CALL_BOUND_S = 10.0


def main(argv: list[str]) -> int:
    if argv[:2] != ["sts", "get-caller-identity"] or len(argv) != 2:
        print("usage: stub_cloud.py sts get-caller-identity", file=sys.stderr)
        return 2
    path = os.environ.get("STUB_CLOUD_CREDENTIALS_FILE", "")
    endpoint = os.environ.get("STUB_CLOUD_ENDPOINT_URL", "")
    try:
        with open(path, encoding="utf-8") as handle:
            token = handle.read().strip()
    except OSError:
        print(json.dumps({"Error": "NoCredentials", "Generation": None}))
        return NO_CREDENTIALS
    request = urllib.request.Request(  # noqa: S310 - the issuer's loopback URL, from the caller
        f"{endpoint}/whoami", headers={"Authorization": f"Bearer {token}"}
    )
    try:
        with urllib.request.urlopen(request, timeout=CALL_BOUND_S) as reply:  # noqa: S310
            body = json.loads(reply.read())
        status = 200
    except urllib.error.HTTPError as error:
        body, status = json.loads(error.read() or b"{}"), error.code
    except (urllib.error.URLError, OSError):
        print(json.dumps({"Error": "EndpointConnectionError", "Generation": None}))
        return NO_CREDENTIALS
    generation = body.get("generation")
    if status == 200 and body.get("authenticated"):
        print(
            json.dumps(
                {
                    "UserId": "STUBUSERID",
                    "Account": ACCOUNT,
                    "Arn": f"arn:stub:iam::{ACCOUNT}:user/demo",
                    "Generation": generation,
                }
            )
        )
        return 0
    print(json.dumps({"Error": "InvalidClientTokenId", "Generation": generation}))
    return NOT_AUTHENTICATED


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
