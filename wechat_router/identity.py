"""Local sender policy. Stable IDs choose rules; message text never assigns a role."""

IMPORTANCE = {"low": -10, "normal": 0, "high": 10}


def validate_sender_rules(rules):
    if not isinstance(rules, list) or len(rules) > 200:
        raise ValueError("sender_rules must contain at most 200 rules")
    seen = set()
    for rule in rules:
        if not isinstance(rule, dict) or set(rule) - {
            "source", "sender_id", "conversation_id", "name", "aliases", "importance", "role"
        }:
            raise ValueError("invalid sender rule fields")
        for key in ("source", "sender_id", "conversation_id", "name"):
            value = rule.get(key, "")
            if (not isinstance(value, str) or len(value) > 256 or
                    any(ord(c) < 32 for c in value) or (key in ("source", "sender_id") and not value.strip())):
                raise ValueError("invalid sender identity")
        aliases = rule.get("aliases", [])
        if (not isinstance(aliases, list) or len(aliases) > 32 or
                any(not isinstance(x, str) or not x.strip() or len(x) > 256 for x in aliases)):
            raise ValueError("invalid sender aliases")
        if rule.get("importance", "normal") not in IMPORTANCE or rule.get("role", "person") not in ("person", "assistant"):
            raise ValueError("invalid sender importance or role")
        identity = (rule["source"], rule["sender_id"], rule.get("conversation_id", ""))
        if identity in seen:
            raise ValueError("duplicate sender rule")
        seen.add(identity)


def sender_policy(message, snapshot):
    matches = [r for r in snapshot.get("sender_rules", [])
               if r["source"] == message.get("source") and r["sender_id"] == message.get("sender_id")
               and (not r.get("conversation_id") or r["conversation_id"] == message.get("conversation_id"))]
    value = {}
    for rule in sorted(matches, key=lambda r: bool(r.get("conversation_id"))):
        value.update(rule)
    own = bool(message.get("is_self") or message.get("sender_id") in snapshot.get("owner_ids", []))
    aliases = list(value.get("aliases", []))
    if own:
        aliases += [snapshot.get("owner_name", ""), *snapshot.get("owner_aliases", [])]
    return {"name": value.get("name") or message.get("sender_name") or ("本人" if own else "未命名发信人"),
            "aliases": list(dict.fromkeys(x for x in aliases if x and x not in ("我", "微信账号本人"))),
            "self": own, "role": "person" if own else value.get("role", "person"),
            "importance": value.get("importance", "normal")}


def visible_content(message):
    """Only known empty placeholders are unobservable; never guess media contents."""
    text = message.get("text", "").strip()
    for prefix in ("[消息更新]", "[消息]"):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
    placeholders = {"[图片]", "[表情]", "[视频]", "[语音]", "[文件]", "[小程序]", "[小程序] null", "[链接]"}
    if not text or text in placeholders:
        return False
    if text == f"[{message.get('message_type')} 消息，监听器未提供文本内容；需要上下文或后续核对]":
        return False
    return True


def screening_messages(snapshot):
    return [m for m in snapshot["messages"] if visible_content(m) and sender_policy(m, snapshot)["role"] != "assistant"]


def batch_importance(snapshot):
    values = [sender_policy(m, snapshot)["importance"] for m in screening_messages(snapshot)]
    return max(values, key=IMPORTANCE.get) if values else "normal"
