#!/usr/bin/env python3
"""Listener integration example. Reads an event from stdin and secrets from env."""

import hashlib
import hmac
import json
import os
import sys
import time
import urllib.parse
import urllib.request


def submit(base_url, secret, event):
    url = base_url.rstrip("/") + "/wechat-router/v1/events"
    parsed = urllib.parse.urlsplit(url)
    if (parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.scheme not in ("https", "http")
            or (parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"))):
        raise ValueError("use HTTPS for a remote listener endpoint, without userinfo, query or fragment")
    body = json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode()
    timestamp = str(int(time.time()))
    signed = f"v1\n{timestamp}\nPOST\n{parsed.path}\n".encode() + body
    sig = "sha256=" + hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    request = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json", "X-Wechat-Router-Timestamp": timestamp,
        "X-Wechat-Router-Signature": sig,
    })
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    with urllib.request.build_opener(NoRedirect).open(request, timeout=10) as response:
        return json.load(response)


if __name__ == "__main__":
    print(json.dumps(submit(os.environ.get("WECHAT_ROUTER_URL", "http://127.0.0.1:8768"),
                            os.environ["WECHAT_ROUTER_SECRET"], json.load(sys.stdin)), ensure_ascii=False))
