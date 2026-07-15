import unittest

from core import select_reference_samples


SECOND = 1_000_000_000


class ReferenceSamplingTests(unittest.TestCase):
    def test_skips_first_two_seconds_and_keeps_tail(self):
        timestamps = [index * SECOND // 10 for index in range(31)]
        self.assertEqual(
            select_reference_samples(timestamps),
            [2 * SECOND, 23 * SECOND // 10, 26 * SECOND // 10, 29 * SECOND // 10],
        )

    def test_sorts_before_time_based_trim(self):
        timestamps = list(reversed([index * SECOND // 10 for index in range(31)]))
        self.assertEqual(select_reference_samples(timestamps)[0], 2 * SECOND)

    def test_sequence_shorter_than_two_seconds_has_no_samples(self):
        timestamps = [index * SECOND // 10 for index in range(20)]
        self.assertEqual(select_reference_samples(timestamps), [])

    def test_timestamp_exactly_at_two_seconds_is_kept(self):
        self.assertEqual(select_reference_samples([0, SECOND, 2 * SECOND]), [2 * SECOND])

    def test_empty_reference_has_no_samples(self):
        self.assertEqual(select_reference_samples([]), [])


if __name__ == "__main__":
    unittest.main()
