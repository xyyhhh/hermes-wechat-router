#!/usr/bin/env python3
"""Export the private model journal to a new mode-0600 JSONL file. Never calls a model."""
import argparse
import base64
import json
import os
from pathlib import Path
import sqlite3
from datetime import datetime, timezone


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--since", help="UTC ISO 8601 start, e.g. 2026-11-25T00:00:00+00:00")
    args = parser.parse_args()
    if args.since:
        try:
            since = datetime.fromisoformat(args.since.replace("Z", "+00:00"))
            if since.tzinfo is None:
                raise ValueError()
            args.since = since.astimezone(timezone.utc).isoformat()
        except ValueError:
            parser.error("--since must be an ISO 8601 timestamp with timezone")
    connection = sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    count = 0
    try:
        rows = connection.execute("SELECT * FROM attempts WHERE (? IS NULL OR started_at>=?) ORDER BY started_at,id",
                                  (args.since, args.since))
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            for row in rows:
                item = dict(row)
                item["context"] = json.loads(item.pop("context_json"))
                item["request"] = json.loads(item.pop("request_json"))
                body = item.pop("response_body")
                item["response"] = None
                if body is not None:
                    try:
                        text = body.decode("utf-8")
                        try:
                            item["response"] = json.loads(text)
                        except ValueError:
                            item["response_text"] = text
                    except UnicodeError:
                        item["response_base64"] = base64.b64encode(body).decode("ascii")
                output.write(json.dumps(item, ensure_ascii=False) + "\n")
                count += 1
    finally:
        connection.close()
    print(f"Exported {count} attempts to {args.output}")


if __name__ == "__main__":
    main()
