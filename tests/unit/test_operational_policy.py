import json
from pathlib import Path
import unittest

from src.ingestion.incremental_runner import resource_definition
from src.ingestion.window_planner import IncrementalWindowPolicy


class OperationalPolicyTests(unittest.TestCase):
    def test_frozen_pilot_policy_matches_runtime_defaults(self):
        path = Path(__file__).parents[2] / "config/pncp_incremental_policy.json"
        policy = json.loads(path.read_text(encoding="utf-8"))

        window = IncrementalWindowPolicy()
        self.assertEqual(
            policy["window_policy"],
            {
                "max_window_days": window.max_window_days,
                "overlap_days": window.overlap_days,
            },
        )
        for resource, expected in policy["reconciliation"].items():
            definition = resource_definition(resource)
            self.assertEqual(
                expected,
                {
                    "cadence_days": definition.reconciliation_cadence_days,
                    "horizon_days": definition.reconciliation_horizon_days,
                },
            )

        self.assertEqual(policy["status"], "pilot_baseline")
        self.assertTrue(policy["review"]["not_an_api_sla"])


if __name__ == "__main__":
    unittest.main()
