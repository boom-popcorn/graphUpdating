from __future__ import annotations

from odkg.environment import configure_model_environment, load_env_file


def test_env_file_parser_does_not_evaluate_shell_syntax(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    path = tmp_path / "key.env"
    path.write_text(
        "OPENAI_API_KEY='literal-$(not-executed)'\n"
        r"OPENAI_API_BASE=https\://api.deepseek.com/v1" + "\n",
        encoding="utf-8",
    )

    loaded = load_env_file(path)

    assert loaded == {"OPENAI_API_KEY", "OPENAI_API_BASE"}
    assert __import__("os").environ["OPENAI_API_KEY"] == "literal-$(not-executed)"
    assert __import__("os").environ["OPENAI_API_BASE"] == (
        "https://api.deepseek.com/v1"
    )


def test_openai_aliases_configure_deepseek_text_and_vision(tmp_path, monkeypatch) -> None:
    names = [
        "OPENAI_API_KEY",
        "OPENAI_API_BASE",
        "ODKG_LLM_API_KEY",
        "ODKG_LLM_BASE_URL",
        "ODKG_LLM_MODEL",
        "ODKG_VLM_API_KEY",
        "ODKG_VLM_BASE_URL",
        "ODKG_VLM_MODEL",
    ]
    for name in names:
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "key.env").write_text(
        "OPENAI_API_KEY=fixture-key\n"
        "OPENAI_API_BASE=https://api.deepseek.com/v1\n",
        encoding="utf-8",
    )

    status = configure_model_environment(tmp_path)

    assert status["api_key_configured"] is True
    assert status["model"] == "deepseek-flash"
    assert status["vision_configured"] is True
    assert __import__("os").environ["ODKG_LLM_BASE_URL"].endswith("/v1")

