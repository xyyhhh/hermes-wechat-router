import importlib.util


def check_requirements():
    return importlib.util.find_spec("aiohttp") is not None


def validate_config(config):
    from .config import Settings
    try:
        Settings.parse(getattr(config, "extra", {}))
        return True
    except (ValueError, TypeError, KeyError):
        return False


def register(ctx):
    # Deferred platform imports: registration starts no listener and reads no secrets/files.
    def factory(config):
        from .adapter import WechatRouterAdapter
        return WechatRouterAdapter(config)

    ctx.register_platform(
        name="wechat_router", label="WeChat Message Router", adapter_factory=factory,
        check_fn=check_requirements, validate_config=validate_config,
        required_env=["WECHAT_ROUTER_SECRET", "TYPESAFE_API_KEY"],
        install_hint="Configure the wechat_router platform and its two profile secrets.",
        emoji="📥",
    )
