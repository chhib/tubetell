from tubetell.prices import chat_cost, format_usd, price_for, transcription_cost


def test_price_for_matches_the_model_name_inside_the_deployment_name():
    assert price_for("gpt-4o-transcribe-diarize").audio_in == 6.0
    assert price_for("my-gpt-4o-mini-transcribe-eu").audio_in == 3.0
    assert price_for("gpt-6.1-terra").text_in == 1.0
    assert price_for("gpt-transcribe").per_hour == 0.27
    assert price_for("something-else") is None


def test_transcription_cost_by_tokens_and_by_duration():
    tokens = {
        "type": "tokens", "input_tokens": 5204, "output_tokens": 8806, "total_tokens": 14010,
        "input_token_details": {"audio_tokens": 4872, "text_tokens": 332},
    }
    # 4,872 * 6 + 332 * 2.5 + 8,806 * 10 per million
    assert transcription_cost("gpt-4o-transcribe-diarize", tokens) == pytest_approx(0.118122)
    assert transcription_cost("gpt-transcribe", {"type": "duration", "seconds": 300}) == pytest_approx(0.0225)
    assert transcription_cost("unknown", tokens) is None
    assert transcription_cost("gpt-4o-transcribe-diarize", None) is None


def test_chat_cost_counts_cached_input_at_its_own_price():
    usage = {
        "prompt_tokens": 60_000, "completion_tokens": 3_000, "total_tokens": 63_000,
        "prompt_tokens_details": {"cached_tokens": 10_000},
    }
    # 50,000 * 1.00 + 10,000 * 0.05 + 3,000 * 5.00 per million
    assert chat_cost("gpt-6.1-terra", usage) == pytest_approx(0.0655)
    assert chat_cost("gpt-6.1-terra", {"prompt_tokens": 1_000_000, "completion_tokens": 0}) == pytest_approx(1.0)
    assert chat_cost("mystery", usage) is None


def test_format_usd_keeps_small_amounts_readable():
    assert format_usd(0.0655) == "0.07 USD"
    assert format_usd(0.0012) == "0.001 USD"
    assert format_usd(2.5) == "2.50 USD"


def pytest_approx(x):
    import pytest

    return pytest.approx(x, rel=1e-6)
