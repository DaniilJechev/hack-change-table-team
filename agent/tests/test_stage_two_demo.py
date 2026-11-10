import importlib.util
import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml
from pydantic import ValidationError

from buddy.demo.contracts import AccessCatalog, TeamCard, validate_seed
from buddy.demo.controller import DATA, DemoController, DemoDisabled
from buddy.mcp_hub.errors import FixtureMissing

ROOT = Path(__file__).resolve().parents[2]


async def test_nfr_demo_1_st2_demo_1_step_isolation_and_replay_without_network():
    controller = DemoController(True)
    controller.command(1)
    controller.command(2)
    assert controller.context(1).demo is True
    call = controller.scenario["calls"]["miro"]
    first = await controller.replay(1, "miro", call["tool"], call["arguments"])
    snapshot = controller.hub(1)
    controller.command(1, "next")
    second = await controller.replay(1, "miro", call["tool"], call["arguments"])
    assert first != second
    assert first == await controller.replay(2, "miro", call["tool"], call["arguments"])
    assert first == await snapshot.call("stage1_probe", "miro", call["tool"], call["arguments"])
    hub = controller.hub(1)
    hub.connector = AsyncMock(side_effect=AssertionError("network forbidden"))
    for server, item in controller.scenario["calls"].items():
        assert await hub.call("stage1_probe", server, item["tool"], item["arguments"])
    with pytest.raises(FixtureMissing):
        await hub.call("stage1_probe", "miro", call["tool"], {"contentItemId": "unrecorded"})
    hub.connector.assert_not_called()


def test_st2_demo_1_boundaries_reset_off_and_bounded_memory():
    controller = DemoController(True, capacity=2)
    with pytest.raises(DemoDisabled):
        controller.context(1)
    with pytest.raises(DemoDisabled):
        controller.hub(1)
    assert "Команды демо" in controller.command(1, "invalid")
    assert not controller.active(1)
    controller.command(1)
    controller.command(1, "next")
    assert "последний" in controller.command(1, "next")
    assert "Шаг 1/2" in controller.command(1, "reset")
    controller.command(2)
    controller.command(3)
    assert not controller.active(1) and len(controller.sessions) == 2
    controller.command(2, "off")
    assert not controller.active(2)
    assert not DemoController(True).active(3)  # restart resets all sessions
    disabled = DemoController(False)
    assert "отключено" in disabled.command(1)
    assert not disabled.active(1)
    with pytest.raises(ValueError):
        DemoController(True, capacity=0)


def seed():
    card = yaml.safe_load((ROOT / "seed/team_cards/demo-orders.yaml").read_text(encoding="utf-8"))
    catalog = yaml.safe_load((ROOT / "seed/access_catalog.yaml").read_text(encoding="utf-8"))
    users = yaml.safe_load((ROOT / "seed/users.yaml").read_text(encoding="utf-8"))["users"]
    scenario = json.loads((ROOT / "seed/day_scenario.json").read_text(encoding="utf-8"))
    return card, catalog, users, scenario


def test_st2_data_1_seed_contracts_and_bundle_are_consistent(tmp_path):
    card, catalog, users, scenario = seed()
    validate_seed(TeamCard.model_validate(card), AccessCatalog.model_validate(catalog), users, scenario)
    assert len(catalog["objects"]) == 6
    assert sum(item["forbidden"] for item in catalog["objects"]) == 1
    assert {person["user_id"] for person in card["people"]} <= {user["user_id"] for user in users}
    assert json.loads((DATA / "scenario.json").read_text(encoding="utf-8")) == scenario
    for original in (ROOT / "fixtures").rglob("*.json"):
        assert (
            DATA / "fixtures" / original.relative_to(ROOT / "fixtures")
        ).read_bytes() == original.read_bytes()
    spec = importlib.util.spec_from_file_location("stage_two_seed", ROOT / "seed/build_stage_two.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.build(tmp_path)
    first = {
        str(path.relative_to(tmp_path)): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()
    }
    module.build(tmp_path)
    assert first == {
        str(path.relative_to(tmp_path)): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()
    }
    assert all((ROOT / name).read_bytes() == data for name, data in first.items())


@pytest.mark.parametrize("kind", ["role", "duplicate", "contact", "route", "rule", "url", "link"])
def test_fr_h2_01_st2_data_1_invalid_card_rejected(kind):
    card, *_ = seed()
    if kind == "role":
        del card["people"][0]["role"]
    elif kind == "duplicate":
        card["people"].append(deepcopy(card["people"][0]))
    elif kind == "contact":
        card["people"][0]["first_contact"] = True
    elif kind == "route":
        card["routing"][0]["owner_id"] = "unknown"
    elif kind == "rule":
        card["do_not_disturb"][0]["via_id"] = "unknown"
    elif kind == "url":
        card["links"][0]["url"] = "https://github.com/"
    else:
        card["links"].append(deepcopy(card["links"][0]))
    with pytest.raises(ValidationError):
        TeamCard.model_validate(card)


@pytest.mark.parametrize(
    "kind", ["instruments", "object", "duplicate", "forbidden", "reason", "base", "request", "roles"]
)
def test_fr_h1_01_st2_data_1_invalid_catalog_rejected(kind):
    _, catalog, *_ = seed()
    if kind == "instruments":
        catalog["instruments"].pop()
    elif kind == "object":
        catalog["objects"].pop(0)
    elif kind == "duplicate":
        catalog["objects"][1]["id"] = catalog["objects"][0]["id"]
    elif kind == "forbidden":
        catalog["objects"][-1]["forbidden"] = False
        catalog["objects"][-1]["request_url"] = catalog["objects"][0]["request_url"]
    elif kind == "reason":
        catalog["objects"][-1]["reason"] = ""
    elif kind == "request":
        catalog["objects"][0]["request_url"] = ""
    elif kind == "roles":
        catalog["objects"][0]["roles"] = ["mentor"]
    else:
        catalog["objects"][0]["forbidden"] = True
        catalog["objects"][0]["reason"] = "blocked"
    with pytest.raises(ValidationError):
        AccessCatalog.model_validate(catalog)


@pytest.mark.parametrize("kind", ["team", "owner", "url", "person"])
def test_nfr_priv_1_st2_data_1_invalid_cross_references_rejected(kind):
    card, catalog, users, scenario = seed()
    if kind == "team":
        catalog["team_id"] = "other-team"
    elif kind == "owner":
        catalog["objects"][0]["owner_id"] = "other-user"
    elif kind == "url":
        catalog["objects"][0]["url"] = "https://example.invalid/other/object"
    else:
        users = [user for user in users if user["user_id"] != "demo-anna"]
    with pytest.raises(ValueError):
        validate_seed(TeamCard.model_validate(card), AccessCatalog.model_validate(catalog), users, scenario)
