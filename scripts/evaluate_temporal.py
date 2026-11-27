#!/usr/bin/env python3
"""Opt-in, paid synthetic semantic check. Stop at the first service error; never retries.

Uses only evals/temporal.json, never the inbox. Credentials come from the selected
provider's environment variable. Output is a new private JSONL file.
"""
import argparse
import asyncio
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aiohttp import ClientSession
from wechat_router.config import Settings
from wechat_router.jev import JevClient, JevError, build_request, validate_response
from wechat_router.audit import AuditLog, audit_context


def snapshot_for(case, settings):
    snapshot = asdict(settings)
    def messages(texts, prefix):
        return [{"event_id": f"{prefix}{i}", "source": "synthetic-evaluation", "conversation_id": "synthetic-chat",
                 "chat_type": "group", "sender_id": "synthetic-owner" if case.get("self") else "synthetic-colleague",
                 "sender_name": "测试同事", "is_self": bool(case.get("self")), "message_type": "text",
                 "occurred_at": "2030-01-01T10:00:00+08:00", "text": text,
                 "reply_to": "q0" if prefix == "m" and case.get("reply_to_quote") else ""}
                for i, text in enumerate(texts)]
    snapshot.update(input_format=2, context=messages(case.get("history", []), "h"),
                    messages=messages(case["messages"], "m"), reply_context=messages(case.get("quotes", []), "q"),
                    candidates=[])
    return snapshot


async def run(args):
    settings = Settings.parse({"provider": args.provider, "model": args.model, "provider_base_url": args.base_url,
                               "bool_threshold": args.threshold, "owner_ids": ["synthetic-owner"]})
    key = os.environ.get(settings.provider_key_name, "")
    if not key:
        raise SystemExit(f"Missing {settings.provider_key_name}; no requests sent")
    cases = json.loads((Path(__file__).resolve().parents[1] / "evals/temporal.json").read_text())[:args.limit]
    # Inside Hermes, use the same framework policy as the live plugin. Standalone
    # use may explicitly set HTTP(S)_PROXY for this diagnostic process.
    if args.hermes_proxy:
        from wechat_router.network import model_http_options
        session_options, request_options = model_http_options(settings.provider_endpoint)
    else:
        session_options, request_options = {"trust_env": True}, {}
    audit_file = args.output.with_suffix(".model-audit.sqlite3")
    if audit_file.exists() or args.output.exists():
        raise SystemExit("Evaluation output or its audit database already exists; no requests sent")
    audit = AuditLog(audit_file)
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    passed = 0
    with os.fdopen(fd, "w") as output:
        async with ClientSession(**session_options) as session:
            client = JevClient(session, key, settings.request_timeout, provider=settings.provider,
                               endpoint=settings.provider_endpoint if settings.provider == "custom" else None,
                               request_options=request_options, audit=audit)
            for case in cases:
                request = build_request(snapshot_for(case, settings))
                try:
                    with audit_context(origin="synthetic_evaluation", case_id=case["id"]):
                        raw = await client.evaluate(request)
                    value = validate_response(raw, request)
                    score = value["answers"]["needs_followup"]["noul"]
                    predicted = score >= args.threshold
                    result = {"id": case["id"], "expected": case["expected"], "score": score,
                              "predicted": predicted, "passed": predicted == case["expected"], "usage": value["usage"]}
                except JevError as exc:
                    result = {"id": case["id"], "error": exc.code, "stopped": True}
                    output.write(json.dumps(result, ensure_ascii=False) + "\n"); output.flush()
                    print(json.dumps(result)); return 2
                passed += result["passed"]
                output.write(json.dumps(result, ensure_ascii=False) + "\n"); output.flush()
                print(json.dumps(result), flush=True)
    print(json.dumps({"passed": passed, "tested": len(cases), "synthetic_only": True}))
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=["google", "typesafe", "custom"], default="google")
    parser.add_argument("--model", default="gemini-decision-1")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--threshold", type=float, default=.6)
    parser.add_argument("--limit", type=int, default=16)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--hermes-proxy", action="store_true")
    args = parser.parse_args()
    if args.limit < 1 or args.limit > 16:
        parser.error("--limit must be between 1 and 16")
    raise SystemExit(asyncio.run(run(args)))
