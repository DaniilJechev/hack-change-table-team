import importlib.util
import json
from pathlib import Path

import pytest
import yaml

from buddy.mcp_hub.hub import Hub

ROOT = Path(__file__).resolve().parents[2]


def test_st1_data_1_consistent_synthetic_objects():
    scenario = json.loads((ROOT / "seed/day_scenario.json").read_text(encoding="utf-8"))
    users = yaml.safe_load((ROOT / "seed/users.yaml").read_text(encoding="utf-8"))["users"]
    assert scenario["synthetic"] is True
    assert set(scenario["user_ids"]).issubset({user["user_id"] for user in users})
    assert len(scenario["resources"]) == 5
    assert len(scenario["calls"]) == 4
    assert all(resource["live_status"] == "not_created" for resource in scenario["resources"].values())
    assert all(
        resource["url"].startswith("https://example.invalid/") for resource in scenario["resources"].values()
    )
    for step in scenario["steps"]:
        for server, call in scenario["calls"].items():
            catalog = json.loads(
                (ROOT / "fixtures" / step / server / "catalog.json").read_text(encoding="utf-8")
            )
            assert catalog["origin"] == "synthetic"
            assert catalog["tools"][0]["name"] == call["tool"]


def test_st1_data_1_generation_is_deterministic(tmp_path):
    spec = importlib.util.spec_from_file_location("demo_seed", ROOT / "seed/build_demo_fixtures.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.build(tmp_path)
    first = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*.json")}
    module.build(tmp_path)
    second = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*.json")}
    assert first == second
    assert first == {name: (ROOT / name).read_bytes() for name in first}


@pytest.mark.parametrize("server", ["github", "jira", "calendar"])
def test_st1_mcp_compat_1_official_schema_accepts_scenario(server):
    catalog = json.loads((ROOT / f"mcp/discovery/{server}.json").read_text(encoding="utf-8"))
    scenario = json.loads((ROOT / "seed/day_scenario.json").read_text(encoding="utf-8"))
    expected = json.loads((ROOT / "mcp/tools.json").read_text(encoding="utf-8"))
    assert catalog["origin"] == "official-runtime-discovery"
    assert catalog["credentials"] == "synthetic" and catalog["corporate_api_verified"] is False
    assert {tool["name"] for tool in catalog["tools"]} == set(
        expected["atlassian" if server == "jira" else server]
    )
    call = scenario["calls"][server]
    Hub._validate(server, call["tool"], call["arguments"], catalog["tools"])
