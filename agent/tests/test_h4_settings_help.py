"""FR-H4-09/12/18: deployment settings and deterministic upload instructions."""

import pytest
from pydantic import ValidationError

from buddy.config import Settings
from buddy.demo.controller import DemoController
from buddy.router.service import Router
from buddy.service import AgentService


@pytest.mark.parametrize(
    "field",
    ["h4_recommended_max_slides", "h4_recommended_max_words_per_slide", "h4_history_ttl_days"],
)
@pytest.mark.parametrize("value", [0, -1, 1.5, "bad"])
def test_fr_h4_09_12_invalid_setting_rejected(field, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


def test_fr_h4_09_12_environment_settings(monkeypatch):
    monkeypatch.setenv("H4_RECOMMENDED_MAX_SLIDES", "15")
    monkeypatch.setenv("H4_RECOMMENDED_MAX_WORDS_PER_SLIDE", "150")
    monkeypatch.setenv("H4_HISTORY_TTL_DAYS", "14")
    settings = Settings(_env_file=None)
    assert (settings.h4_recommended_max_slides, settings.h4_recommended_max_words_per_slide) == (15, 150)
    assert settings.h4_history_ttl_days == 14


def test_fr_h4_12_history_limit_matches_backend():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, h4_history_ttl_days=36501)


async def test_fr_h4_18_upload_instruction_without_model():
    controller = DemoController(True)
    controller.command(42)
    service = AgentService(Router(None), demo_mode=True)
    _, response = await service.handle("Как проверить презентацию к экватору?", controller.context(42))
    assert "PDF" in response.result and "PPTX" in response.result
    assert "не редактирую" in response.result
    assert "Отправить наставнику" in response.result
