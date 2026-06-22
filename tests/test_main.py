import os

from src import fugle_live
from src import main as main_module


def test_load_env_value_prefers_os_environ(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("FINMIND_TOKEN=dotenv-token\n", encoding="utf-8")
    monkeypatch.setattr(main_module, "ROOT", tmp_path)
    monkeypatch.setenv("FINMIND_TOKEN", "env-token")

    assert main_module.load_env_value("FINMIND_TOKEN") == "env-token"


def test_load_env_value_falls_back_to_dotenv(monkeypatch, tmp_path):
    key = "OPTION_DASHBOARD_TEST_TOKEN"
    (tmp_path / ".env").write_text(f"{key}=dotenv-token\n", encoding="utf-8")
    monkeypatch.setattr(main_module, "ROOT", tmp_path)
    monkeypatch.delenv(key, raising=False)

    try:
        assert main_module.load_env_value(key) == "dotenv-token"
    finally:
        os.environ.pop(key, None)


def test_fugle_load_env_token_prefers_os_environ(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("FUGLE_TOKEN=dotenv-token\n", encoding="utf-8")
    monkeypatch.setattr(fugle_live, "ROOT", tmp_path)
    monkeypatch.setenv("FUGLE_TOKEN", "env-token")

    assert fugle_live.load_env_token() == "env-token"


def test_fugle_load_env_token_falls_back_to_dotenv_alias(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("FUGLE_MARKETDATA_API_KEY=dotenv-token\n", encoding="utf-8")
    monkeypatch.setattr(fugle_live, "ROOT", tmp_path)
    for key in ("FUGLE_TOKEN", "FUGLE_API_KEY", "FUGLE_MARKETDATA_API_KEY"):
        monkeypatch.delenv(key, raising=False)

    try:
        assert fugle_live.load_env_token() == "dotenv-token"
    finally:
        for key in ("FUGLE_TOKEN", "FUGLE_API_KEY", "FUGLE_MARKETDATA_API_KEY"):
            os.environ.pop(key, None)


def test_main_runs(capsys):
    main_module.main(["--smoke"])
    captured = capsys.readouterr()
    assert "quant-assistant project is ready." in captured.out
