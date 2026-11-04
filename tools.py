"""Eager, import-light operations for the otherwise deferred platform plugin."""


def register_tools(ctx):
    from .wechat_router.cli import configure_parser, run_args, status_tool

    ctx.register_cli_command(name="wechat-router", help="Inspect or control the WeChat router inbox",
                             setup_fn=configure_parser, handler_fn=run_args)
    ctx.register_tool(name="wechat_router_status", toolset="wechat_router",
                      schema={"name": "wechat_router_status", "description": "Read local WeChat router queue and token counters; no inference or board writes.",
                              "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
                      handler=status_tool)
