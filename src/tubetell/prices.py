"""What an Azure request cost, from its usage and a small price table.

Prices are Azure's Sweden Central / Global Standard list prices in USD, read
from the retail price API on 2026-11-06; update the table when they move. A
deployment is matched to a price by the model name inside the deployment
name, longest name first, so `gpt-4o-transcribe-diarize` is not priced as
`gpt-4o-transcribe` and a deployment called `my-gpt-6.1-terra-eu` is found.
An unknown deployment gets no price rather than a wrong one.
"""

from __future__ import annotations

from dataclasses import dataclass

PER_MILLION = 1_000_000


@dataclass(frozen=True)
class Price:
    """USD per million tokens, or per hour of audio for duration-billed models."""

    text_in: float = 0.0
    text_out: float = 0.0
    audio_in: float = 0.0
    cached_in: float = 0.0
    per_hour: float = 0.0


PRICES: dict[str, Price] = {
    "gpt-4o-transcribe-diarize": Price(text_in=2.5, text_out=10.0, audio_in=6.0),
    "gpt-4o-transcribe": Price(text_in=2.5, text_out=10.0, audio_in=6.0),
    "gpt-4o-mini-transcribe": Price(text_in=1.25, text_out=5.0, audio_in=3.0),
    "gpt-transcribe": Price(per_hour=0.27),
    "gpt-6.1-terra": Price(text_in=1.0, text_out=5.0, cached_in=0.05),
    "gpt-6.1-sol": Price(text_in=2.0, text_out=10.0, cached_in=0.2),
    "gpt-6-astra": Price(text_in=10.0, text_out=50.0, cached_in=1.0),
}


def price_for(deployment: str) -> Price | None:
    name = (deployment or "").lower()
    for model in sorted(PRICES, key=len, reverse=True):
        if model in name:
            return PRICES[model]
    return None


def _field(obj, name, default=0):
    value = obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)
    return value if value is not None else default


def transcription_cost(deployment: str, usage) -> float | None:
    """USD for one transcription response, or None without a price or usage."""
    price = price_for(deployment)
    if price is None or usage is None:
        return None
    if _field(usage, "type") == "duration":
        return float(_field(usage, "seconds")) / 3600 * price.per_hour
    details = _field(usage, "input_token_details", None)
    audio = int(_field(details, "audio_tokens")) if details is not None else 0
    text_in = int(_field(usage, "input_tokens")) - audio
    out = int(_field(usage, "output_tokens"))
    return (audio * price.audio_in + text_in * price.text_in + out * price.text_out) / PER_MILLION


def chat_cost(deployment: str, usage) -> float | None:
    """USD for one chat response: cached input at its own price."""
    price = price_for(deployment)
    if price is None or usage is None:
        return None
    details = _field(usage, "prompt_tokens_details", None)
    cached = int(_field(details, "cached_tokens")) if details is not None else 0
    prompt = int(_field(usage, "prompt_tokens")) - cached
    out = int(_field(usage, "completion_tokens"))
    return (prompt * price.text_in + cached * price.cached_in + out * price.text_out) / PER_MILLION


def format_usd(amount: float) -> str:
    return f"{amount:.2f} USD" if amount >= 0.01 else f"{amount:.3f} USD"
