import json
from types import SimpleNamespace

import pytest

from tubetell import TubetellError
from tubetell import glossary
from tubetell.foundry import settings
from tubetell.glossary import apply_changes, correct, correction_request, glossary_tag, load_terms
from tubetell.transcript import Segment

ENDPOINT = "https://data-and-analytics-resource.cognitiveservices.azure.com"
SEGMENTS = [
    Segment(0.0, 4.0, "Speaker A", "We looked at it in Apsis Central."),
    Segment(101.0, 105.0, "Speaker B", "Then Kronan checked the one screen report."),
]


def test_load_terms_skips_comments_and_blank_lines(tmp_path):
    f = tmp_path / "glossary.txt"
    f.write_text("# products\nBusiness Central\n\nOneStream\n  Kiana  \n")
    assert load_terms(f) == ["Business Central", "OneStream", "Kiana"]


def test_load_terms_missing_or_empty_is_a_clear_error(tmp_path):
    with pytest.raises(TubetellError, match="No such glossary"):
        load_terms(tmp_path / "nope.txt")
    (tmp_path / "empty.txt").write_text("# nothing\n")
    with pytest.raises(TubetellError, match="no terms"):
        load_terms(tmp_path / "empty.txt")


def test_glossary_tag_depends_on_the_terms_not_their_order():
    assert glossary_tag(["B", "A"]) == glossary_tag(["A", "B"])
    assert glossary_tag(["A"]) != glossary_tag(["B"])
    assert glossary_tag(["A"]).startswith("glossary-")
    assert glossary_tag([]) == ""


def test_correction_request_numbers_the_lines_and_lists_the_terms():
    text = correction_request(SEGMENTS, ["Business Central", "Kiana"])
    assert "1\t[00:00] Speaker A: We looked at it in Apsis Central." in text
    assert "2\t[01:41] Speaker B:" in text
    assert "- Business Central\n- Kiana" in text
    assert "JSON" in text


def test_apply_changes_replaces_only_glossary_terms_and_logs_each():
    changes = [
        {"line": 1, "from": "Apsis Central", "to": "Business Central"},
        {"line": 2, "from": "Kronan", "to": "Kiana"},
        {"line": 2, "from": "one screen", "to": "OneStream"},
        {"line": 2, "from": "checked", "to": "verified"},  # not a glossary term: dropped
        {"line": 9, "from": "x", "to": "Kiana"},  # no such line: dropped
        {"line": 1, "from": "missing words", "to": "Kiana"},  # not in the line: dropped
    ]
    out, log = apply_changes(SEGMENTS, changes, ["Business Central", "Kiana", "OneStream"])
    assert out[0].text == "We looked at it in Business Central."
    assert out[1].text == "Then Kiana checked the OneStream report."
    assert out[0].speaker == "Speaker A" and out[1].start == 101.0
    assert log == [
        '[00:00] "Apsis Central" -> "Business Central"',
        '[01:41] "Kronan" -> "Kiana"',
        '[01:41] "one screen" -> "OneStream"',
    ]


def _client(reply):
    def create(**kw):
        parsed = SimpleNamespace(
            choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=reply))],
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )
        return parsed

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def test_correct_applies_the_models_changes_and_reports(monkeypatch, capsys):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", ENDPOINT)
    reply = "```json\n" + json.dumps({"changes": [{"line": 2, "from": "Kronan", "to": "Kiana"}]}) + "\n```"
    out = correct(_client(reply), settings(), SEGMENTS, ["Kiana"])
    assert out[1].text == "Then Kiana checked the one screen report."
    err = capsys.readouterr().err
    assert 'glossary: 1 change\n  [01:41] "Kronan" -> "Kiana"' in err


def test_correct_with_an_unreadable_answer_keeps_the_transcript(monkeypatch, capsys):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", ENDPOINT)
    out = correct(_client("I cannot do that."), settings(), SEGMENTS, ["Kiana"])
    assert out == SEGMENTS
    assert "glossary: the model's answer was not JSON; transcript left as it is" in capsys.readouterr().err


def test_correct_with_nothing_to_change_says_so(monkeypatch, capsys):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", ENDPOINT)
    assert correct(_client('{"changes": []}'), settings(), SEGMENTS, ["Kiana"]) == SEGMENTS
    assert "glossary: no changes" in capsys.readouterr().err
