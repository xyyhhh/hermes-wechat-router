"""Hermes directory-plugin entry point. Importing the plugin has no side effects."""

def register(ctx):
    from .tools import register_tools
    from .wechat_router.plugin import register as register_platform
    register_tools(ctx)
    return register_platform(ctx)

__all__ = ["register"]
