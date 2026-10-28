import unittest

from src.ingestion.window_planner import (
    IncrementalWindowPolicy,
    PlannedWindow,
    plan_closed_windows,
)


class IncrementalWindowPlannerTests(unittest.TestCase):
    def test_initial_load_is_split_into_closed_windows(self):
        windows = plan_closed_windows(
            "2023-04-01",
            "2023-04-05",
            policy=IncrementalWindowPolicy(
                overlap_days=2, max_window_days=2
            ),
        )
        self.assertEqual(
            windows,
            (
                PlannedWindow("2023-04-01", "2023-04-02"),
                PlannedWindow("2023-04-03", "2023-04-04"),
                PlannedWindow("2023-04-05", "2023-04-05"),
            ),
        )

    def test_watermark_revisits_configured_number_of_covered_days(self):
        windows = plan_closed_windows(
            "2023-04-01",
            "2023-04-13",
            confirmed_watermark="2023-04-10T23:59:59.999999Z",
            policy=IncrementalWindowPolicy(
                overlap_days=2, max_window_days=1
            ),
        )
        self.assertEqual(
            windows,
            (
                PlannedWindow("2023-04-09", "2023-04-09", True),
                PlannedWindow("2023-04-10", "2023-04-10", True),
                PlannedWindow("2023-04-11", "2023-04-11"),
                PlannedWindow("2023-04-12", "2023-04-12"),
                PlannedWindow("2023-04-13", "2023-04-13"),
            ),
        )

    def test_zero_overlap_starts_after_confirmed_day(self):
        self.assertEqual(
            plan_closed_windows(
                "2023-04-01",
                "2023-04-12",
                confirmed_watermark="2023-04-10",
                policy=IncrementalWindowPolicy(
                    overlap_days=0, max_window_days=2
                ),
            ),
            (PlannedWindow("2023-04-11", "2023-04-12"),),
        )

    def test_initial_floor_limits_overlap_and_future_watermark_is_empty(self):
        self.assertEqual(
            plan_closed_windows(
                "2023-04-10",
                "2023-04-11",
                confirmed_watermark="2023-04-10",
                policy=IncrementalWindowPolicy(overlap_days=30),
            )[0],
            PlannedWindow("2023-04-10", "2023-04-10", True),
        )
        self.assertEqual(
            plan_closed_windows(
                "2023-04-01",
                "2023-04-10",
                confirmed_watermark="2023-04-20",
                policy=IncrementalWindowPolicy(overlap_days=0),
            ),
            (),
        )

    def test_window_crossing_watermark_is_marked_for_reprocessing(self):
        self.assertEqual(
            plan_closed_windows(
                "2023-04-01",
                "2023-04-12",
                confirmed_watermark="2023-04-10",
                policy=IncrementalWindowPolicy(
                    overlap_days=2, max_window_days=3
                ),
            ),
            (
                PlannedWindow("2023-04-09", "2023-04-11", True),
                PlannedWindow("2023-04-12", "2023-04-12"),
            ),
        )

    def test_rejects_invalid_policy_and_dates(self):
        with self.assertRaisesRegex(ValueError, "Sobreposição"):
            IncrementalWindowPolicy(overlap_days=-1)
        with self.assertRaisesRegex(ValueError, "Tamanho"):
            IncrementalWindowPolicy(max_window_days=0)
        with self.assertRaises(ValueError):
            plan_closed_windows("data-inválida", "2023-04-01")


if __name__ == "__main__":
    unittest.main()
