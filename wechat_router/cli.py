"""Local operations. Does not call Jev or modify the Kanban board."""

import argparse
import json
from pathlib import Path

from .config import data_path
from .store import Store


def configure_parser(p):
    p.add_argument("--home", type=Path, help="Explicit Hermes home; defaults to the current Hermes profile")
    sub = p.add_subparsers(dest="wechat_router_action", required=True)
    sub.add_parser("status")
    show = sub.add_parser("event")
    show.add_argument("source")
    show.add_argument("event_id")
    retry = sub.add_parser("retry")
    retry.add_argument("batch_id")
    sub.add_parser("pause")
    sub.add_parser("resume")
    return p


def run_args(args):
    if args.home is None:
        from hermes_constants import get_hermes_home
        args.home = get_hermes_home()
    path = data_path(args.home.expanduser().resolve())
    if not path.is_file():
        raise SystemExit("router inbox does not exist; start the configured Hermes gateway first")
    store = Store(path)
    if args.wechat_router_action == "status":
        result = store.status()
    elif args.wechat_router_action == "event":
        result = store.event_status(args.source, args.event_id)
    elif args.wechat_router_action == "retry":
        result = {"queued": store.retry(args.batch_id)}
    else:
        store.pause(args.wechat_router_action == "pause")
        result = {"paused": store.paused()}
    print(json.dumps(result, ensure_ascii=False, indent=2))


def status_tool(params, **kwargs):
    from hermes_constants import get_hermes_home
    path = data_path(get_hermes_home())
    if not path.is_file():
        return json.dumps({"running_state": "not_initialized"})
    return json.dumps(Store(path).status(), ensure_ascii=False)


def main(argv=None):
    p = configure_parser(argparse.ArgumentParser(description="Inspect and control the WeChat router inbox"))
    run_args(p.parse_args(argv))


if __name__ == "__main__":
    main()
