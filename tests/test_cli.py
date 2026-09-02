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

def test_main_exits_when_neither_key_nor_project_is_set(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "analyze", lambda url, **kw: pytest.fail("must not run"))

    with pytest.raises(SystemExit) as exc:
        run_main(monkeypatch, ["vOVKnYoH1p4"])

    msg = str(exc.value)
    assert "GEMINI_API_KEY" in msg
    assert "GOOGLE_CLOUD_PROJECT" in msg

def test_main_passes_the_credentials_gate_with_only_a_gemini_key(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.chdir(tmp_path)
    seen = {}
    monkeypatch.setattr(cli, "analyze", lambda url, **kw: seen.update(kw) or "ok")

    run_main(monkeypatch, ["vOVKnYoH1p4", "--processing", "agentic"])

    assert seen["processing"] == "agentic"
    assert seen["model"] == "gemini-3.7-flash"

def test_help_lists_processing_choices_and_model_default(monkeypatch, capsys):
    with pytest.raises(SystemExit) as exc:
        run_main(monkeypatch, ["--help"])

    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--processing {auto,agentic,static}" in out
    assert "gemini-3.7-flash" in out
    assert "Vertex AI itself" not in out

def test_analyze_sends_the_prompt_with_the_timestamp_rule(monkeypatch):
    sent = {}
    monkeypatch.setattr(cli, "make_client", lambda: object())
    monkeypatch.setattr(cli, "source_part", lambda source, **kw: types.Part(text="media"))
    monkeypatch.setattr(cli, "source_duration", lambda source: 5732.0)
    def capture(client, *, model, contents, low_res=False):
        sent["c"] = contents
        return "ok"

    monkeypatch.setattr(cli, "generate", capture)

    assert cli.analyze("vOVKnYoH1p4", mode="claims", prompt=None, model="m", max_comments=0, processing="static") == "ok"
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
        clip="1:30-2:45", processing="static",
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
    cli.analyze("vOVKnYoH1p4", mode="summary", prompt=None, model="m", max_comments=0, processing="static")
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
    out = cli.analyze("vOVKnYoH1p4", mode="summary", prompt=None, model="m", max_comments=0, processing="static")
    assert out == "piece"
    assert len(calls) >= 3  # at least two spans plus the merge
    assert all("None" not in c.parts[0].text for c in calls[:-1])
    assert "--- ANSWERS ---" in calls[-1]


def _agentic_env(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(cli, "make_client", lambda: pytest.fail("Vertex client must not be built"))
    monkeypatch.setattr(cli, "generate", lambda *a, **kw: pytest.fail("generate must not run"))
    monkeypatch.setattr(cli, "source_duration", lambda source: 3452.0)


def test_analyze_auto_takes_the_agentic_path_with_a_key(monkeypatch):
    _agentic_env(monkeypatch)
    seen = {}

    def fake_answer(source, *, model, text):
        seen.update(source=source, model=model, text=text)
        return "agentic ok"

    monkeypatch.setattr(cli, "agentic_answer", fake_answer)
    out = cli.analyze("vOVKnYoH1p4", mode="claims", prompt=None, model="gemini-3.7-flash", max_comments=0)
    assert out == "agentic ok"
    assert seen["source"] == "vOVKnYoH1p4"
    assert seen["text"].startswith(cli.MODE_PROMPTS["claims"])
    assert "runs 57:32" in seen["text"]


def test_analyze_auto_without_a_runtime_still_carries_the_timestamp_rule(monkeypatch):
    _agentic_env(monkeypatch)
    monkeypatch.setattr(cli, "source_duration", lambda source: None)
    seen = {}
    monkeypatch.setattr(cli, "agentic_answer", lambda s, *, model, text: seen.update(text=text) or "ok")
    cli.analyze("vOVKnYoH1p4", mode="summary", prompt=None, model="gemini-3.7-flash", max_comments=0)
    assert "mm:ss" in seen["text"]
    assert "runs " not in seen["text"]


def test_analyze_auto_falls_back_to_static_for_a_clip_and_says_why(monkeypatch, capsys):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(cli, "agentic_answer", lambda *a, **kw: pytest.fail("agentic must not run"))
    monkeypatch.setattr(cli, "make_client", lambda: object())
    monkeypatch.setattr(cli, "source_part", lambda source, **kw: types.Part(text="media"))
    monkeypatch.setattr(cli, "generate", lambda client, *, model, contents, low_res=False: "static ok")
    out = cli.analyze("vOVKnYoH1p4", mode="summary", prompt=None, model="gemini-3.7-flash", max_comments=0, clip="10:00-20:00")
    assert out == "static ok"
    assert "processing: static — --clip is static-only" in capsys.readouterr().err


def test_analyze_auto_is_silent_about_static_without_a_key(monkeypatch, capsys):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(cli, "make_client", lambda: object())
    monkeypatch.setattr(cli, "source_part", lambda source, **kw: types.Part(text="media"))
    monkeypatch.setattr(cli, "source_duration", lambda source: 60.0)
    monkeypatch.setattr(cli, "generate", lambda client, *, model, contents, low_res=False: "static ok")
    cli.analyze("vOVKnYoH1p4", mode="summary", prompt=None, model="gemini-3.7-flash", max_comments=0)
    assert "processing:" not in capsys.readouterr().err


def test_analyze_explicit_static_with_a_key_stays_on_vertex(monkeypatch, capsys):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(cli, "agentic_answer", lambda *a, **kw: pytest.fail("agentic must not run"))
    monkeypatch.setattr(cli, "make_client", lambda: object())
    monkeypatch.setattr(cli, "source_part", lambda source, **kw: types.Part(text="media"))
    monkeypatch.setattr(cli, "source_duration", lambda source: 60.0)
    monkeypatch.setattr(cli, "generate", lambda client, *, model, contents, low_res=False: "static ok")
    assert cli.analyze("vOVKnYoH1p4", mode="summary", prompt=None, model="gemini-3.7-flash", max_comments=0, processing="static") == "static ok"
    assert "processing:" not in capsys.readouterr().err


def test_analyze_comments_mode_ignores_processing(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(cli, "agentic_answer", lambda *a, **kw: pytest.fail("agentic must not run"))
    monkeypatch.setattr(cli, "make_client", lambda: "vertex")
    monkeypatch.setattr(cli, "fetch_comments", lambda source, n: ("- hi", 1))
    seen = {}

    def capture(client, *, model, contents, low_res=False):
        seen["client"] = client
        return "sentiment"

    monkeypatch.setattr(cli, "generate", capture)
    out = cli.analyze("vOVKnYoH1p4", mode="comments", prompt=None, model="gemini-3.7-flash", max_comments=5, processing="agentic")
    assert out == "sentiment" and seen["client"] == "vertex"


def test_analyze_explicit_agentic_rejects_gs_before_any_client(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(cli, "make_client", lambda: pytest.fail("no client"))
    monkeypatch.setattr(cli, "agentic_answer", lambda *a, **kw: pytest.fail("no agentic"))
    with pytest.raises(TubetellError, match="Cloud Storage"):
        cli.analyze("gs://b/clip.mp4", mode="summary", prompt=None, model="gemini-3.7-flash", max_comments=0, processing="agentic")


def test_vertex_404_for_a_gemini3_model_hints_at_the_global_location(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "p")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "europe-west1")
    err = genai_errors.APIError(404, {"error": {"message": "Publisher model was not found"}})

    def boom(url, **kw):
        raise err

    monkeypatch.setattr(cli, "analyze", boom)
    with pytest.raises(SystemExit, match="GOOGLE_CLOUD_LOCATION=global"):
        run_main(monkeypatch, ["vOVKnYoH1p4", "--processing", "static"])


def test_vertex_404_on_global_has_no_location_hint(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "p")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "global")
    err = genai_errors.APIError(404, {"error": {"message": "Publisher model was not found"}})

    def boom(url, **kw):
        raise err

    monkeypatch.setattr(cli, "analyze", boom)
    with pytest.raises(SystemExit) as e:
        run_main(monkeypatch, ["vOVKnYoH1p4"])
    assert "hint" not in str(e.value)
