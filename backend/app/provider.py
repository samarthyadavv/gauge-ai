import re, time
import httpx
from .config import settings


def mock_response(prompt):
    if prompt.startswith("Uppercase: "):
        return prompt[len("Uppercase: ") :].upper()
    if prompt.startswith("Lowercase: "):
        return prompt[len("Lowercase: ") :].lower()
    if prompt.startswith("Reverse: "):
        return prompt[len("Reverse: ") :][::-1]
    match = re.fullmatch(r"Add: (-?\d{1,6}) and (-?\d{1,6})", prompt)
    if match:
        return str(int(match[1]) + int(match[2]))
    if prompt == "Return JSON with status equal to ok":
        return '{"status":"ok"}'
    return "Mock provider: no supported instruction."


def generate(run, prompt):
    start = time.perf_counter()
    if run.provider == "mock":
        output = mock_response(prompt)
        return {
            "output": output,
            "input_tokens": 0,
            "output_tokens": 0,
            "latency_ms": (time.perf_counter() - start) * 1000,
            "cost_usd": 0.0,
        }
    with httpx.Client(
        base_url="https://api.openai.com/v1/",
        timeout=httpx.Timeout(35, connect=10),
        transport=httpx.HTTPTransport(retries=1),
    ) as client:
        messages = []
        if run.instruction:
            messages.append({"role": "system", "content": run.instruction})
        messages.append({"role": "user", "content": prompt})
        r = client.post(
            "chat/completions",
            headers={"Authorization": "Bearer " + settings().openai_api_key},
            json={
                "model": run.model,
                "messages": messages,
                "max_completion_tokens": run.max_tokens,
            },
        )
        r.raise_for_status()
        data = r.json()
        usage = data.get("usage", {})
        incoming = usage.get("prompt_tokens")
        outgoing = usage.get("completion_tokens")
        cost = (
            (incoming * run.input_price + outgoing * run.output_price) / 1000000
            if incoming is not None
            and outgoing is not None
            and run.input_price is not None
            and run.output_price is not None
            else None
        )
        output = data["choices"][0]["message"]["content"]
        if not isinstance(output, str):
            raise ValueError("Provider returned no text")
        return {
            "output": output,
            "input_tokens": incoming,
            "output_tokens": outgoing,
            "latency_ms": (time.perf_counter() - start) * 1000,
            "cost_usd": cost,
        }
