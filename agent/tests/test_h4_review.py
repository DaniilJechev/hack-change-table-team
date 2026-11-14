"""Independent H4 review regressions: persistent demo isolation and stale delivery safety."""

from datetime import UTC, datetime, timedelta

import pytest

from buddy.data.base import DataError
from buddy.equator.checks import mentions, title_markers
from buddy.equator.history import DemoHistory
from buddy.equator.slides import Slide

OWNER = "demo:42:demo-orders:demo-daniil"


def record(generation, *, check_id="check-1", passed=True, expired=False):
    now = datetime.now(UTC)
    return {
        "id": check_id,
        "user_id": "demo-daniil",
        "session": 42,
        "team_id": "demo-orders",
        "demo": True,
        "passed": passed,
        "report": "Synthetic report",
        "links": [],
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(days=-1 if expired else 7)).isoformat(),
        "generation": generation,
        "current": True,
        "delivery_file": "pending",
        "delivery_report": "pending",
    }


def test_fr_h4_14_demo_save_from_old_generation_cannot_restore_stale_button(tmp_path):
    path = str(tmp_path / "history.sqlite")
    first, second = DemoHistory(path), DemoHistory(path)
    old_generation = first.invalidate(OWNER)
    stale = record(old_generation)
    second.invalidate(OWNER)
    saved = first.save(OWNER, stale)
    assert saved["current"] is False
    with pytest.raises(DataError):
        second.claim(OWNER, stale["id"], "file")


def test_fr_h4_14_demo_reset_cannot_be_undone_by_inflight_check(tmp_path):
    history = DemoHistory(str(tmp_path / "history.sqlite"))
    old_generation = history.invalidate(OWNER)
    history.clear_session(42)
    assert history.invalidate(OWNER) > old_generation
    saved = history.save(OWNER, record(old_generation))
    assert not saved["current"]


@pytest.mark.parametrize("passed, expired", [(False, False), (True, True)])
def test_fr_h4_15_demo_claim_rejects_failed_or_expired_check(tmp_path, passed, expired):
    history = DemoHistory(str(tmp_path / "history.sqlite"))
    generation = history.invalidate(OWNER)
    history.save(OWNER, record(generation, passed=passed, expired=expired))
    with pytest.raises(DataError):
        history.claim(OWNER, "check-1", "file")


def test_fr_h4_12_demo_expired_payload_is_removed_from_storage(tmp_path):
    history = DemoHistory(str(tmp_path / "history.sqlite"))
    generation = history.invalidate(OWNER)
    history.save(OWNER, record(generation, expired=True))
    assert history.history(OWNER) == []
    assert history.db.execute("SELECT count(*) FROM h4_history").fetchone()[0] == 0


def test_fr_h4_12_demo_other_owner_cannot_claim_record_and_restart_keeps_history(tmp_path):
    path = str(tmp_path / "history.sqlite")
    history = DemoHistory(path)
    generation = history.invalidate(OWNER)
    history.save(OWNER, record(generation))
    restarted = DemoHistory(path)
    assert restarted.history(OWNER)[0]["id"] == "check-1"
    with pytest.raises(DataError):
        restarted.claim("demo:99:demo-orders:demo-daniil", "check-1", "file")


@pytest.mark.parametrize("heading", ["Цели", "Выполненные задачи"])
def test_fr_h4_06_explicit_status_before_key_is_not_ignored(heading):
    found = mentions([Slide(1, heading, heading + "\nВ работе ORD-118")])
    assert len(found) == 1 and found[0].said == "progress"


def test_fr_h4_05_name_prefix_does_not_confirm_another_person():
    profile = {"display_name": "Александр", "mentor_id": "mentor"}
    card = {"name": "Заказы", "people": [{"user_id": "mentor", "name": "Анна"}]}
    slide = Slide(1, "Экватор", "Алексей\nКоманда Заказы\nНаставник Анна\nСентябрь — ноябрь 2026")
    assert "имя стажёра" in title_markers(slide, profile, card)
