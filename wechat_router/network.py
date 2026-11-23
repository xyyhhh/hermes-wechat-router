"""Use Hermes' proxy policy for both inbox inference and Dashboard tests."""
from urllib.parse import urlsplit

from .jev import JevError


def model_http_options(endpoint):
    from gateway.platforms.base import proxy_kwargs_for_aiohttp, resolve_proxy_url
    proxy = resolve_proxy_url(target_hosts=urlsplit(endpoint).hostname)
    session_options, request_options = proxy_kwargs_for_aiohttp(proxy)
    if proxy and not session_options and not request_options:
        # A selected SOCKS route without its connector must not silently become a direct request.
        raise JevError("jev_proxy_unavailable")
    # Hermes already resolved its environment, gateway.trust_env and NO_PROXY rules.
    # Do not ask aiohttp to reinterpret them or implicitly read .netrc credentials.
    return {"trust_env": False, **session_options}, request_options
