"""Offline regression checks for the Compose smoke acceptance contract."""

import unittest
from unittest.mock import patch

from infra import smoke


def core_rows():
    return [
        {"Service": name, "State": "running", "Health": "healthy"}
        for name in sorted(smoke.CORE_SERVICES)
    ]


class ComposeSmokeTests(unittest.TestCase):
    def test_nfr_boot_mcp_1_base_stack(self):
        smoke.check_services(core_rows())

    def test_nfr_boot_mcp_1_powerpoint_overlay(self):
        smoke.check_services(
            core_rows() + [{"Service": "powerpoint-mcp", "State": "running", "Health": "healthy"}]
        )

    def test_nfr_boot_mcp_1_unhealthy_core(self):
        for state, health in [("exited", "healthy"), ("running", "unhealthy")]:
            with self.subTest(state=state, health=health):
                rows = core_rows()
                rows[0].update(State=state, Health=health)
                with self.assertRaisesRegex(RuntimeError, rows[0]["Service"]):
                    smoke.check_services(rows)

    def test_nfr_boot_mcp_1_unhealthy_powerpoint(self):
        for state, health in [("exited", "healthy"), ("running", "unhealthy"), ("running", "")]:
            with self.subTest(state=state, health=health):
                with self.assertRaisesRegex(RuntimeError, "powerpoint-mcp"):
                    smoke.check_services(
                        core_rows()
                        + [{"Service": "powerpoint-mcp", "State": state, "Health": health}]
                    )

    def test_nfr_boot_mcp_1_missing_core(self):
        with self.assertRaisesRegex(RuntimeError, "Expected five core"):
            smoke.check_services(core_rows()[1:])

    def test_nfr_boot_mcp_1_unknown_service(self):
        with self.assertRaisesRegex(RuntimeError, "Expected five core"):
            smoke.check_services(core_rows() + [{"Service": "unexpected"}])

    def test_nfr_boot_mcp_1_other_optional_servers(self):
        smoke.check_services(
            core_rows() + [{"Service": name} for name in ["mcp-atlassian", "google-calendar-mcp"]]
        )

    def test_nfr_boot_mcp_1_cli_fails_on_unhealthy_powerpoint(self):
        import json

        rows = core_rows() + [{"Service": "powerpoint-mcp", "State": "exited", "Health": ""}]
        with patch("sys.argv", ["smoke.py"]), patch.object(smoke, "compose", return_value=json.dumps(rows)):
            with self.assertRaisesRegex(RuntimeError, "powerpoint-mcp"):
                smoke.main()


if __name__ == "__main__":
    unittest.main()
