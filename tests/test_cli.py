import os

import pytest
from google.genai import errors as genai_errors
from google.genai import types

from tubetell import TubetellError
from tubetell import cli


def run_main(monkeypatch, argv):
    monkeypatch.setattr("sys.argv", ["tubetell", *argv])
    cli.main()


def test_dotenv_loaded_from_cwd(tmp_path, monkeypatch):
    # Regression: pipx installs must find ./.env in the shell cwd, not
    # relative to the installed package.
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    (tmp_path / ".env").write_text("GOOGLE_CLOUD_PROJECT=from-dotenv\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "analyze", lambda url, **kw: "ok")

    run_main(monkeypatch, ["vOVKnYoH1p4"])

    assert os.environ["GOOGLE_CLOUD_PROJECT"] == "from-dotenv"


def test_real_env_wins_over_dotenv(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "from-env")
    (tmp_path / ".env").write_text("GOOGLE_CLOUD_PROJECT=from-dotenv\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "analyze", lambda url, **kw: "ok")

    run_main(monkeypatch, ["vOVKnYoH1p4"])

    assert os.environ["GOOGLE_CLOUD_PROJECT"] == "from-env"


def test_out_writes_file(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "analyze", lambda url, **kw: "the result")
    out = tmp_path / "result.md"

    run_main(monkeypatch, ["vOVKnYoH1p4", "--out", str(out)])

    assert out.read_text(encoding="utf-8") == "the result\n"
    assert capsys.readouterr().out == ""


def test_result_printed_to_stdout(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "analyze", lambda url, **kw: "the result")

    run_main(monkeypatch, ["vOVKnYoH1p4"])

    assert capsys.readouterr().out == "the result\n"


def test_tubetell_error_exits_with_message(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def boom(url, **kw):
        raise TubetellError("something is missing")

    monkeypatch.setattr(cli, "analyze", boom)

    with pytest.raises(SystemExit, match="something is missing"):
        run_main(monkeypatch, ["vOVKnYoH1p4"])


def test_api_error_exits_with_clean_message(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    err = genai_errors.APIError(403, {"error": {"message": "Vertex AI API has not been used"}})

    def boom(url, **kw):
        raise err

    monkeypatch.setattr(cli, "analyze", boom)

    with pytest.raises(SystemExit, match="Vertex AI error 403"):
        run_main(monkeypatch, ["vOVKnYoH1p4"])

def test_analyze_sends_the_prompt_with_the_timestamp_rule(monkeypatch):
    sent = {}
    monkeypatch.setattr(cli, "make_client", lambda: object())
    monkeypatch.setattr(cli, "source_part", lambda source, **kw: types.Part(text="media"))
    monkeypatch.setattr(cli, "source_duration", lambda source: 5732.0)
    def capture(client, *, model, contents, low_res=False):
        sent["c"] = contents
        return "ok"

    monkeypatch.setattr(cli, "generate", capture)

    assert cli.analyze("vOVKnYoH1p4", mode="claims", prompt=None, model="m", max_comments=0) == "ok"
    text = sent["c"].parts[1].text
    assert text.startswith(cli.MODE_PROMPTS["claims"])
    assert "runs 1:35:32" in text


def test_analyze_skips_the_runtime_when_a_clip_narrows_the_source(monkeypatch):
    sent = {}
    monkeypatch.setattr(cli, "make_client", lambda: object())
    monkeypatch.setattr(cli, "source_part", lambda source, **kw: types.Part(text="media"))
    monkeypatch.setattr(
        cli, "source_duration", lambda source: pytest.fail("must not probe a clipped source")
    )
    def capture(client, *, model, contents, low_res=False):
        sent["c"] = contents
        return "ok"

    monkeypatch.setattr(cli, "generate", capture)

    cli.analyze(
        "vOVKnYoH1p4", mode="summary", prompt="Vad händer?", model="m", max_comments=0,
        clip="1:30-2:45",
    )
    text = sent["c"].parts[1].text
    assert text.startswith("Vad händer?")
    assert "no timestamp may be later than its end" in text


def test_analyze_drops_resolution_for_an_hour_long_video(monkeypatch):
    sent = {}
    monkeypatch.setattr(cli, "make_client", lambda: object())
    monkeypatch.setattr(cli, "source_part", lambda source, **kw: types.Part(text="media"))
    monkeypatch.setattr(cli, "source_duration", lambda source: 61 * 60.0)

    def capture(client, *, model, contents, low_res=False):
        sent["low_res"] = low_res
        return "ok"

    monkeypatch.setattr(cli, "generate", capture)
    cli.analyze("vOVKnYoH1p4", mode="summary", prompt=None, model="m", max_comments=0)
    assert sent["low_res"] is True


def test_analyze_splits_a_very_long_video_and_merges(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "make_client", lambda: object())
    monkeypatch.setattr(cli, "source_part", lambda source, **kw: types.Part(text=str(kw["clip"])))
    monkeypatch.setattr(cli, "source_duration", lambda source: 6 * 3600.0)

    def capture(client, *, model, contents, low_res=False):
        calls.append(contents)
        return "piece"

    monkeypatch.setattr(cli, "generate", capture)
    out = cli.analyze("vOVKnYoH1p4", mode="summary", prompt=None, model="m", max_comments=0)
    assert out == "piece"
    assert len(calls) >= 3  # at least two spans plus the merge
    assert all("None" not in c.parts[0].text for c in calls[:-1])
    assert "--- ANSWERS ---" in calls[-1]
