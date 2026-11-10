import subprocess
import sys
from pathlib import Path

import yaml

from buddy.schemas import UserProfile

ROOT = Path(__file__).resolve().parents[2]


def test_st0_qa_1_synthetic_seed_contracts():
    users = yaml.safe_load((ROOT / "seed/users.yaml").read_text(encoding="utf-8"))["users"]
    profiles = [UserProfile(**{k: v for k, v in u.items() if k != "mentor_id"}) for u in users]
    assert len({u.user_id for u in profiles}) == len(profiles)
    assert {u.role for u in profiles} == {"intern", "mentor", "teamlead"}
    assert len({u.team_id for u in profiles}) == 2
    for file in ["seed/access_catalog.yaml", "seed/team_cards/demo-orders.yaml"]:
        assert yaml.safe_load((ROOT / file).read_text(encoding="utf-8"))["synthetic"] is True


def test_st0_infra_1_compose_contract():
    services = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))["services"]
    assert set(services) == {"postgres", "redis", "grafana", "backend", "agent"}
    assert all("healthcheck" in s for s in services.values())
    assert "pg16" in services["postgres"]["image"]
    assert services["agent"]["depends_on"]["backend"]["condition"] == "service_healthy"
    assert "CREATE EXTENSION IF NOT EXISTS vector" in (ROOT / "infra/postgres/init.sql").read_text()


def test_st0_qa_1_no_tracked_secrets():
    result = subprocess.run(["git", "check-ignore", ".env"], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0
    assert "TELEGRAM_BOT_TOKEN=\n" in (ROOT / ".env.example").read_text()


def test_st0_qa_1_offline_smoke_process():
    result = subprocess.run([sys.executable, "-m", "buddy", "--smoke"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout
