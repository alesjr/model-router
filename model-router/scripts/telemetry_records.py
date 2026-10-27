"""Whitelisted rollout records and conservative token arithmetic."""

from datetime import datetime, timezone
import hashlib
import json

FIELDS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens",
          "output_tokens", "reasoning_output_tokens", "total_tokens")


def text(value):
    return value if isinstance(value, str) and value else None


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (ValueError, TypeError, AttributeError, OverflowError):
        return None


def stamp():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value):
    return hashlib.sha256(value).hexdigest()


def usage(value):
    value = value if isinstance(value, dict) else {}
    result = {k: value.get(k) if type(value.get(k)) is int and value[k] >= 0 else None
              for k in FIELDS}
    if result["total_tokens"] is None and all(result[k] is not None for k in ("input_tokens", "output_tokens")):
        result["total_tokens"] = result["input_tokens"] + result["output_tokens"]
    return result


def totals(records):
    """Total is a known subtotal; other dimensions require complete observations."""
    result = {}
    for key in FIELDS:
        values = [r["usage"][key] for r in records]
        known = [v for v in values if v is not None]
        result[key] = (sum(known) if known else None) if key == "total_tokens" else (
            sum(known) if values and len(known) == len(values) else None)
    return result


def normalize(row, thread):
    if not isinstance(row, dict) or not isinstance(row.get("payload"), dict):
        return None
    p = row["payload"]
    kind = row.get("type")
    if kind == "event_msg":
        kind = p.get("type")
    if kind not in {"turn_context", "token_usage_record", "token_count", "task_started", "task_complete", "turn_aborted"}:
        return None
    at = row.get("timestamp", p.get("timestamp"))
    parsed = timestamp(at)
    result = {"kind": kind, "thread_id": text(p.get("thread_id")) or thread,
              "turn_id": text(p.get("turn_id")), "timestamp": parsed.isoformat() if parsed else None}
    if kind == "turn_context":
        result.update(model_reported=text(p.get("model")),
                      reasoning_effort_reported=text(p.get("reasoning_effort")))
    elif kind == "token_usage_record":
        result.update(response_id=text(p.get("response_id")), usage=usage(p.get("usage")))
        if result["response_id"] is None:
            return {**result, "kind": "unidentified_usage"}
    elif kind == "token_count":
        info = p.get("info")
        if not isinstance(info, dict) or not isinstance(info.get("total_token_usage"), dict):
            return None
        result.update(usage=usage(info["total_token_usage"]), source="token_count")
    return result


def cumulative_records(row, thread):
    """Cumulative counters are checkpoints, never extra response usage."""
    if not isinstance(row, dict) or row.get("type") != "token_usage_record" or not isinstance(row.get("payload"), dict):
        return []
    p = row["payload"]
    at = timestamp(row.get("timestamp", p.get("timestamp")))
    result = []
    for field in ("thread_token_usage", "turn_token_usage"):
        if isinstance(p.get(field), dict):
            result.append({"kind": "token_count", "source": field, "thread_id": text(p.get("thread_id")) or thread,
                           "turn_id": text(p.get("turn_id")) if field == "turn_token_usage" else None,
                           "timestamp": at.isoformat() if at else None, "usage": usage(p[field])})
    return result
