"""Native TypeSafe System One protocol; no chat/completions or auxiliary LLM."""

import json
import math

from dataclasses import asdict

from .config import (DEFAULT_CHOICE_CRITERIA, DEFAULT_EVENT_QUESTION, DEFAULT_JEV_INSTRUCTIONS,
                     DEFAULT_NEGATIVE_CRITERIA, DEFAULT_POSITIVE_CRITERIA, TYPESAFE_ENDPOINT)


class JevError(RuntimeError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def build_request(snapshot):
    messages = snapshot["messages"]
    state = {
        "routing_policy": snapshot.get("jev_instructions", DEFAULT_JEV_INSTRUCTIONS),
        "owner": {"name": snapshot["owner_name"], "ids": snapshot["owner_ids"], "interests": snapshot["interests"]},
        "timezone": snapshot["timezone"],
        "history": snapshot["context"],
        "messages": [{"ref": f"m{i}", **m} for i, m in enumerate(messages)],
        "existing_threads": [{"ref": f"t{i}", "category": t["category"], "phase": t["phase"],
                              "excerpt": t["excerpt"]} for i, t in enumerate(snapshot["candidates"])],
    }
    question = {"instructions": snapshot.get("decision_question", DEFAULT_EVENT_QUESTION)}
    if snapshot.get("decision_mode", "bool") == "bool":
        question.update(type="noul", criteria={
            "true": snapshot.get("positive_criteria", DEFAULT_POSITIVE_CRITERIA),
            "false": snapshot.get("negative_criteria", DEFAULT_NEGATIVE_CRITERIA),
        })
    else:
        question.update(type="choice", criteria=snapshot.get("choice_criteria", DEFAULT_CHOICE_CRITERIA))
    return {"model": snapshot["model"], "state": state, "questions": {"needs_followup": question}}


def preview_request(settings):
    snapshot = asdict(settings)
    snapshot.update(messages=[{"text": "（此处为合并后的消息；预览不读取真实消息）"}], context=[], candidates=[])
    return build_request(snapshot)


def validate_response(response, request):
    if not isinstance(response, dict) or not isinstance(response.get("answers"), dict):
        raise JevError("jev_invalid_response")
    answers = response["answers"]
    validated = {}
    for key, question in request["questions"].items():
        answer = answers.get(key)
        if question["type"] == "noul":
            value = answer.get("noul") if isinstance(answer, dict) else None
            if (not isinstance(answer, dict) or answer.get("type") != "noul" or isinstance(value, bool)
                    or not isinstance(value, (float, int)) or not math.isfinite(value) or not 0 <= value <= 1):
                raise JevError("jev_invalid_noul")
            validated[key] = {"noul": value}
            continue
        if not isinstance(answer, dict) or answer.get("type") != "choice" or answer.get("choice") not in question["criteria"]:
            raise JevError("jev_invalid_choice")
        confidence = answer.get("confidence")
        if (isinstance(confidence, bool) or not isinstance(confidence, (float, int))
                or not math.isfinite(confidence) or not 0 <= confidence <= 1):
            raise JevError("jev_invalid_confidence")
        validated[key] = {"choice": answer["choice"], "confidence": confidence}
    return {"answers": validated, "model": response.get("model"), "usage": response.get("usage", {})}


class JevClient:
    ENDPOINT = TYPESAFE_ENDPOINT

    def __init__(self, session, api_key, timeout=15, *, endpoint=None):
        self.session, self.api_key, self.timeout = session, api_key, timeout
        self.endpoint = endpoint or self.ENDPOINT

    async def evaluate(self, request):
        import asyncio
        import aiohttp
        try:
            async with self.session.post(
                self.endpoint, json=request,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=aiohttp.ClientTimeout(total=self.timeout), allow_redirects=False,
            ) as response:
                if response.status != 200:
                    raise JevError(f"jev_http_{response.status}")
                data = bytearray()
                async for part in response.content.iter_chunked(65536):
                    data.extend(part)
                    if len(data) > 2 * 1024 * 1024:
                        raise JevError("jev_response_too_large")
                try:
                    value = json.loads(data)
                except (ValueError, UnicodeError):
                    raise JevError("jev_invalid_json") from None
                if not isinstance(value, dict):
                    raise JevError("jev_invalid_response")
                return value
        except (asyncio.TimeoutError, aiohttp.ClientError):
            raise JevError("jev_transport_error") from None
