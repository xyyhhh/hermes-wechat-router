from datetime import datetime, timezone
import hashlib
import json


class InvalidEvent(ValueError):
    pass


def normalize_event(value, settings):
    if not isinstance(value, dict):
        raise InvalidEvent("body must be an object")
    allowed = {"event_id", "source", "conversation_id", "chat_type", "sender_id", "sender_name",
               "occurred_at", "text", "is_self", "mentions", "reply_to", "conversation_name", "message_type"}
    if set(value) - allowed:
        raise InvalidEvent("unknown event fields")
    out = {}
    for key in ("event_id", "source", "conversation_id", "sender_id", "occurred_at"):
        v = value.get(key)
        if not isinstance(v, str) or not v.strip() or len(v) > 256 or any(ord(c) < 32 for c in v):
            raise InvalidEvent(f"invalid {key}")
        out[key] = v
    if settings.sources and out["source"] not in settings.sources:
        raise InvalidEvent("source is not configured")
    out["chat_type"] = value.get("chat_type", "dm")
    if out["chat_type"] not in ("dm", "group"):
        raise InvalidEvent("chat_type must be dm or group")
    try:
        dt = datetime.fromisoformat(out["occurred_at"].replace("Z", "+00:00"))
        if dt.tzinfo is None or dt.utcoffset() is None:
            raise ValueError()
        out["occurred_at"] = dt.astimezone(timezone.utc).isoformat()
    except (ValueError, OverflowError):
        raise InvalidEvent("occurred_at needs an ISO 8601 timestamp with timezone") from None
    kind = value.get("message_type", "text")
    if kind not in ("text", "image", "voice", "video", "file", "link", "system", "other"):
        raise InvalidEvent("invalid message_type")
    out["message_type"] = kind
    text = value.get("text", "")
    if kind != "text" and isinstance(text, str) and not text.strip():
        text = f"[{kind} 消息，监听器未提供文本内容；需要上下文或后续核对]"
    if not isinstance(text, str) or not text.strip() or len(text) > settings.max_text_chars:
        raise InvalidEvent("text is empty or exceeds max_text_chars")
    out["text"] = text
    for key in ("sender_name", "conversation_name", "reply_to"):
        v = value.get(key, "")
        if not isinstance(v, str) or len(v) > 256:
            raise InvalidEvent(f"invalid {key}")
        out[key] = v
    own = value.get("is_self", out["sender_id"] in settings.owner_ids)
    if not isinstance(own, bool):
        raise InvalidEvent("is_self must be boolean")
    out["is_self"] = own
    mentions = value.get("mentions", [])
    if not isinstance(mentions, list) or len(mentions) > 100 or any(
        not isinstance(x, str) or not x or len(x) > 256 for x in mentions
    ):
        raise InvalidEvent("invalid mentions")
    out["mentions"] = sorted(set(mentions))
    return out


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()
