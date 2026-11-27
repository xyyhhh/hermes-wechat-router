"""Native TypeSafe System One and Google Decisions; no generative model calls."""

import json
import math
import time

from dataclasses import asdict

from .config import (DEFAULT_CHOICE_CRITERIA, DEFAULT_EVENT_QUESTION, DEFAULT_JEV_INSTRUCTIONS,
                     DEFAULT_NEGATIVE_CRITERIA, DEFAULT_POSITIVE_CRITERIA, GOOGLE_ENDPOINT, TYPESAFE_ENDPOINT)


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
    if snapshot.get("input_format") == 2:
        from .compact import compact_state
        state = compact_state(snapshot, snapshot.get("jev_instructions", DEFAULT_JEV_INSTRUCTIONS))
    question = {"instructions": snapshot.get("decision_question", DEFAULT_EVENT_QUESTION)}
    if snapshot.get("decision_mode", "bool") == "bool":
        positive, negative = (("true_description", "false_description") if snapshot.get("provider") == "google"
                              else ("true", "false"))
        question.update(type="noul", criteria={
            positive: snapshot.get("positive_criteria", DEFAULT_POSITIVE_CRITERIA),
            negative: snapshot.get("negative_criteria", DEFAULT_NEGATIVE_CRITERIA),
        })
    else:
        question.update(type="choice", criteria=snapshot.get("choice_criteria", DEFAULT_CHOICE_CRITERIA))
    content = ({"input": json.dumps(state, ensure_ascii=False, separators=(",", ":"))} if snapshot.get("provider") == "google"
               else {"state": state})
    return {"model": snapshot["model"], **content, "questions": {"needs_followup": question}}


def preview_request(settings):
    snapshot = asdict(settings)
    snapshot.update(input_format=2, messages=[{"text": "（此处为合并后的消息；预览不读取真实消息）"}], context=[], candidates=[])
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

    GOOGLE_ENDPOINT = GOOGLE_ENDPOINT

    def __init__(self, session, api_key, timeout=15, *, endpoint=None, provider="typesafe", request_options=None, audit=None):
        if provider not in ("typesafe", "custom", "google"):
            raise ValueError("unsupported provider")
        self.session, self.api_key, self.timeout = session, api_key, timeout
        self.provider = provider
        self.request_options = request_options or {}
        self.audit = audit
        self.endpoint = endpoint or (self.GOOGLE_ENDPOINT if provider == "google" else self.ENDPOINT)

    async def evaluate(self, request):
        import asyncio
        import aiohttp
        attempt_id = None
        if self.audit:
            try:
                attempt_id = await asyncio.to_thread(self.audit.begin, request, self.provider, self.endpoint, self.api_key)
            except Exception:
                raise JevError("audit_unavailable") from None
        started = time.monotonic()
        status, data, response_bytes, truncated, error_code = None, bytearray(), 0, False, None
        try:
            async with self.session.post(
                self.endpoint, json=request,
                headers=({"x-goog-api-key": self.api_key} if self.provider == "google"
                         else {"Authorization": f"Bearer {self.api_key}"}),
                timeout=aiohttp.ClientTimeout(total=self.timeout), allow_redirects=False, **self.request_options,
            ) as response:
                status = response.status
                async for part in response.content.iter_chunked(65536):
                    response_bytes += len(part)
                    data.extend(part[:max(0, 2 * 1024 * 1024 - len(data))])
                    if response_bytes > 2 * 1024 * 1024:
                        truncated = True
                        raise JevError("jev_response_too_large")
                if status != 200:
                    raise JevError(f"jev_http_{status}")
                try:
                    value = json.loads(data)
                except (ValueError, UnicodeError):
                    raise JevError("jev_invalid_json") from None
                if not isinstance(value, dict):
                    raise JevError("jev_invalid_response")
                if self.provider == "google":
                    # Google Decisions uses the Interactions usage schema and returns no model field.
                    usage = value.get("usage")
                    value = {**value, "model": request["model"], "usage": {
                        "input_tokens": usage.get("total_input_tokens"),
                        "output_tokens": usage.get("total_output_tokens"),
                    } if isinstance(usage, dict) else {}}
                if self.audit and request.get("questions"):
                    try:
                        validate_response(value, request)
                    except JevError as exc:
                        error_code = exc.code  # Journal the invalid answer; Engine still owns enforcement/accounting.
                return value
        except asyncio.TimeoutError:
            error_code = "jev_timeout"
            raise JevError("jev_timeout") from None
        except aiohttp.ClientError:
            error_code = "jev_transport_error"
            raise JevError("jev_transport_error") from None
        except asyncio.CancelledError:
            error_code = "jev_cancelled"
            raise
        except Exception as exc:
            error_code = exc.code if isinstance(exc, JevError) else "jev_client_error"
            raise
        finally:
            if attempt_id is not None:
                try:
                    await asyncio.shield(asyncio.to_thread(self.audit.finish, attempt_id,
                        status=status, body=bytes(data), response_bytes=response_bytes, truncated=truncated,
                        elapsed_ms=round((time.monotonic() - started) * 1000), error_code=error_code, secret=self.api_key))
                except Exception:
                    raise JevError("audit_unavailable") from None
