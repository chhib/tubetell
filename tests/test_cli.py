import os

import pytest
from google.genai import errors as genai_errors

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
