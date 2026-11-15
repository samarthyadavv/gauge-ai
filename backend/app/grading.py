import json
from decimal import Decimal


def load_json(value):
    def invalid_constant(value):
        raise ValueError("Non-finite values are not JSON")

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    try:
        return json.loads(
            value,
            parse_float=Decimal,
            parse_constant=invalid_constant,
            object_pairs_hook=unique_object,
        )
    except RecursionError as exc:
        raise ValueError("JSON nesting is too deep") from exc


def json_equal(a, b):
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, (int, float, Decimal)) and isinstance(b, (int, float, Decimal)):
        return a == b
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(json_equal(a[key], b[key]) for key in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(json_equal(x, y) for x, y in zip(a, b))
    return a == b


def grade(output, case):
    text = output.strip()
    expected = case["expected"]
    if case["match"] == "exact":
        correct = text == expected.strip()
    elif case["match"] == "contains":
        correct = expected.casefold() in text.casefold()
    elif case["match"] == "json":
        try:
            correct = json_equal(load_json(text), load_json(expected))
        except (ValueError, TypeError):
            correct = False
    else:
        raise ValueError("Unknown match type")
    forbidden_hit = any(
        term.casefold() in text.casefold() for term in case.get("forbidden", [])
    )
    return {
        "correct": correct,
        "forbidden_hit": forbidden_hit,
        "passed": correct and not forbidden_hit,
    }


def percentile(values, fraction):
    if not values:
        return None
    values = sorted(values)
    index = (len(values) - 1) * fraction
    lo = int(index)
    hi = min(lo + 1, len(values) - 1)
    return round(values[lo] + (values[hi] - values[lo]) * (index - lo), 2)


def summarize(run, results):
    total = len(run.snapshot)
    done = len(results)
    latencies = [r.latency_ms for r in results if not r.error]
    priced = [r.cost_usd for r in results if r.cost_usd is not None]
    return {
        "total": total,
        "completed": done,
        "passed": sum(r.passed for r in results),
        "errors": sum(bool(r.error) for r in results),
        "forbidden_hits": sum(r.forbidden_hit for r in results),
        "pass_rate": sum(r.passed for r in results) / total if total else 0,
        "p50_ms": percentile(latencies, 0.5),
        "p95_ms": percentile(latencies, 0.95),
        "input_tokens": sum(r.input_tokens or 0 for r in results),
        "output_tokens": sum(r.output_tokens or 0 for r in results),
        "usage_complete": bool(done)
        and done == total
        and all(
            r.input_tokens is not None and r.output_tokens is not None
            for r in results
            if not r.error
        )
        and not any(r.error for r in results),
        "cost_usd": round(sum(priced), 8) if priced else None,
        "cost_complete": bool(done)
        and done == total
        and len(priced) == done
        and not any(r.error for r in results),
        "latency_samples": len(latencies),
    }
