"""Regression tests for cwd-independent configuration.

Bug (2026-08-26): run from a scratch directory with no .env, tubetell died with
"GOOGLE_CLOUD_PROJECT is not set" even though a perfectly good .env existed in
the project it was installed for. A relative GOOGLE_APPLICATION_CREDENTIALS
was also resolved against the cwd instead of the .env that set it.
"""

import os

import pytest

from tubetell import TubetellError, cli
from tubetell.config import check_credentials_file, load_env, user_config_path

ENV_KEYS = [
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_LOCATION",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "YOUTUBE_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GOOGLE_GENAI_USE_VERTEXAI",
    "GOOGLE_GENAI_USE_ENTERPRISE",
    "TUBETELL_ENV",
]


@pytest.fixture(autouse=True)
def clean_env(tmp_path, monkeypatch):
    for k in ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    # Isolate from the developer's real ~/.config/tubetell/.env
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    yield


def run_main(monkeypatch, argv):
    monkeypatch.setattr("sys.argv", ["tubetell", *argv])
    cli.main()


def write_user_config(text):
    p = user_config_path()
    p.parent.mkdir(parents=True)
    p.write_text(text)
    return p


def test_clean_env_clears_the_gemini_key():
    assert "GEMINI_API_KEY" not in os.environ

def test_missing_credentials_error_names_both_variables(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "analyze", lambda url, **kw: pytest.fail("must not run"))

    with pytest.raises(SystemExit) as exc:
        run_main(monkeypatch, ["vOVKnYoH1p4"])

    msg = str(exc.value)
    assert "GEMINI_API_KEY" in msg
    assert "GOOGLE_CLOUD_PROJECT" in msg
    assert "tubetell/.env" in msg

def test_user_config_used_when_cwd_has_no_dotenv(tmp_path, monkeypatch):
    write_user_config("GOOGLE_CLOUD_PROJECT=from-user-config\n")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.chdir(scratch)
    monkeypatch.setattr(cli, "analyze", lambda url, **kw: "ok")

    run_main(monkeypatch, ["vOVKnYoH1p4"])

    assert os.environ["GOOGLE_CLOUD_PROJECT"] == "from-user-config"


def test_cwd_dotenv_wins_over_user_config(tmp_path, monkeypatch):
    write_user_config("GOOGLE_CLOUD_PROJECT=from-user-config\nYOUTUBE_API_KEY=user-key\n")
    (tmp_path / ".env").write_text("GOOGLE_CLOUD_PROJECT=from-cwd\n")
    monkeypatch.chdir(tmp_path)

    load_env()

    assert os.environ["GOOGLE_CLOUD_PROJECT"] == "from-cwd"
    # keys missing from the nearer file still fill in from the user config
    assert os.environ["YOUTUBE_API_KEY"] == "user-key"


def test_tubetell_env_variable_points_at_a_file(tmp_path, monkeypatch):
    custom = tmp_path / "elsewhere" / "creds.env"
    custom.parent.mkdir()
    custom.write_text("GOOGLE_CLOUD_PROJECT=from-explicit\n")
    monkeypatch.setenv("TUBETELL_ENV", str(custom))
    monkeypatch.chdir(tmp_path)

    assert load_env() == [custom]
    assert os.environ["GOOGLE_CLOUD_PROJECT"] == "from-explicit"


def test_relative_credentials_resolve_against_the_dotenv_not_cwd(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    key = project / "sa.json"
    key.write_text("{}")
    (project / ".env").write_text("GOOGLE_CLOUD_PROJECT=p\nGOOGLE_APPLICATION_CREDENTIALS=sa.json\n")
    monkeypatch.setenv("TUBETELL_ENV", str(project / ".env"))
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.chdir(scratch)

    load_env()
    check_credentials_file()  # must not raise

    assert os.environ["GOOGLE_APPLICATION_CREDENTIALS"] == str(key.resolve())


def test_missing_credentials_file_fails_clearly(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(tmp_path / "nope.json"))
    with pytest.raises(TubetellError, match="does not exist"):
        check_credentials_file()


def test_missing_project_error_lists_files_read(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("YOUTUBE_API_KEY=k\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "analyze", lambda url, **kw: pytest.fail("must not run"))

    with pytest.raises(SystemExit) as exc:
        run_main(monkeypatch, ["vOVKnYoH1p4"])

    msg = str(exc.value)
    assert "GOOGLE_CLOUD_PROJECT" in msg
    assert str(tmp_path / ".env") in msg
    assert "tubetell/.env" in msg


def test_real_env_beats_every_file(tmp_path, monkeypatch):
    write_user_config("GOOGLE_CLOUD_PROJECT=from-user-config\n")
    (tmp_path / ".env").write_text("GOOGLE_CLOUD_PROJECT=from-cwd\n")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "from-env")
    monkeypatch.chdir(tmp_path)

    load_env()

    assert os.environ["GOOGLE_CLOUD_PROJECT"] == "from-env"


def test_out_creates_missing_parent_directories(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "p")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "analyze", lambda url, **kw: "the result")
    out = tmp_path / "deep" / "nested" / "result.md"

    run_main(monkeypatch, ["vOVKnYoH1p4", "--out", str(out)])

    assert out.read_text() == "the result\n"
