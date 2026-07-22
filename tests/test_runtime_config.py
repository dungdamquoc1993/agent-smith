from __future__ import annotations

import pytest

from agent_smith.infra.config import RuntimeSettings, validate_runtime_startup


def _settings(**updates: object) -> RuntimeSettings:
    return RuntimeSettings(_env_file=None).model_copy(update=updates)


def test_local_minio_configuration_is_valid_for_worker() -> None:
    validate_runtime_startup(
        _settings(),
        env={"AGENT_SMITH_WEB_SEARCH_PROVIDER": "tavily"},
        require_llm=False,
    )


def test_runtime_reports_missing_openrouter_and_selected_search_key() -> None:
    with pytest.raises(RuntimeError) as raised:
        validate_runtime_startup(
            _settings(openrouter_api_key=None),
            env={"AGENT_SMITH_WEB_SEARCH_PROVIDER": "tavily"},
        )

    message = str(raised.value)
    assert "OPENROUTER_API_KEY" in message
    assert "TAVILY_API_KEY" in message


def test_r2_configuration_rejects_local_minio_values() -> None:
    with pytest.raises(RuntimeError) as raised:
        validate_runtime_startup(
            _settings(s3_provider="r2", openrouter_api_key="test-openrouter"),
            env={},
        )

    message = str(raised.value)
    assert "r2.cloudflarestorage.com" in message
    assert "AGENT_SMITH_S3_REGION=auto" in message
    assert "AGENT_SMITH_S3_PATH_STYLE=false" in message
    assert "local MinIO credentials" in message


def test_valid_r2_configuration_passes_startup_validation() -> None:
    validate_runtime_startup(
        _settings(
            openrouter_api_key="test-openrouter",
            s3_provider="r2",
            s3_endpoint_url="https://account.r2.cloudflarestorage.com",
            s3_region="auto",
            s3_access_key_id="r2-access",
            s3_secret_access_key="r2-secret",
            s3_path_style=False,
        ),
        env={"AGENT_SMITH_WEB_SEARCH_PROVIDER": "tavily", "TAVILY_API_KEY": "tvly"},
    )


def test_aws_configuration_uses_standard_endpoint_and_virtual_addressing() -> None:
    validate_runtime_startup(
        _settings(
            openrouter_api_key="test-openrouter",
            s3_provider="aws",
            s3_endpoint_url="",
            s3_region="ap-southeast-1",
            s3_access_key_id="aws-access",
            s3_secret_access_key="aws-secret",
            s3_path_style=False,
        ),
        env={},
    )
