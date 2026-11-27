"""Small, lossless-text model view; full transport identities stay in the inbox."""
from datetime import datetime
from zoneinfo import ZoneInfo

from .identity import sender_policy, screening_messages, visible_content


def compact_state(snapshot, policy):
    history = snapshot.get("context", [])
    quotes = snapshot.get("reply_context", [])
    current = screening_messages(snapshot)
    groups = (("h", history), ("q", quotes), ("m", current))
    people, refs = {}, {}
    for prefix, messages in groups:
        for i, m in enumerate(messages):
            if m.get("event_id"):
                refs[m["event_id"]] = f"{prefix}{i}"
            key = (m.get("source", ""), m.get("sender_id", ""))
            if key not in people:
                person = sender_policy(m, snapshot)
                people[key] = {"ref": f"p{len(people)}", **{k: person[k] for k in ("name", "self", "role")}}
                if person["aliases"]:
                    people[key]["aliases"] = person["aliases"]
            elif m.get("sender_name") and m["sender_name"] != people[key]["name"]:
                aliases = people[key].setdefault("aliases", [])
                if m["sender_name"] not in aliases:
                    aliases.append(m["sender_name"])
    def time(value):
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
            ZoneInfo(snapshot["timezone"])).isoformat(timespec="seconds")
    def message(m, prefix, i):
        key = (m.get("source", ""), m.get("sender_id", ""))
        out = {"ref": f"{prefix}{i}", "sender": people[key]["ref"], "text": m["text"]}
        if m.get("occurred_at"):
            out["at"] = time(m["occurred_at"])
        # Normal delivery delay is irrelevant to this gate. Retain explicit arrival
        # time for delayed/backfilled messages; the full timestamp always stays local.
        if m.get("received_at") and (not m.get("occurred_at") or abs(
                datetime.fromisoformat(m["received_at"]).timestamp() -
                datetime.fromisoformat(m["occurred_at"]).timestamp()) > 300):
            out["received_at"] = time(m["received_at"])
        if m.get("message_type", "text") != "text":
            out["kind"] = m["message_type"]
        if not visible_content(m):
            out["content_available"] = False
        if m.get("truncated"):
            out["truncated"] = True
        if m.get("reply_to"):
            out["reply_to"] = refs.get(m["reply_to"], "unavailable")
        if m.get("mentions"):
            targets = []
            for target in m["mentions"]:
                k = (m.get("source", ""), target)
                if target in ("all", "@all", "所有人", "@所有人"):
                    targets.append("all")
                else:
                    if k not in people:
                        own = target in snapshot.get("owner_ids", [])
                        people[k] = {"ref": f"p{len(people)}", "name": snapshot.get("owner_name", "本人") if own else "未提供名称", "self": own, "role": "person"}
                    targets.append(people[k]["ref"])
            out["mentions"] = targets
        return out
    serialized = {"history": [message(m, "h", i) for i, m in enumerate(history)],
                  "messages": [message(m, "m", i) for i, m in enumerate(current)]}
    if quotes:
        serialized["quotes"] = [message(m, "q", i) for i, m in enumerate(quotes)]
    first = snapshot["messages"][0] if snapshot["messages"] else {}
    return {"routing_policy": policy, "timezone": snapshot["timezone"],
            "conversation": {"kind": first.get("chat_type", "dm"), "name": first.get("conversation_name", "")},
            "people": {v["ref"]: {k: x for k, x in v.items() if k != "ref"} for v in people.values()},
            **serialized}
