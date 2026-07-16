import unittest

from core import REFERENCE_TAIL_TRIM_FRAMES, select_reference_samples


SECOND = 1_000_000_000


class ReferenceSamplingTests(unittest.TestCase):
    def test_skips_first_two_seconds_drops_last_ten_and_strides(self):
        timestamps = [index * SECOND // 10 for index in range(51)]
        self.assertEqual(
            select_reference_samples(timestamps),
            [index * SECOND // 10 for index in range(20, 39, 3)],
        )

    def test_sorts_before_time_based_trim(self):
        timestamps = list(reversed([index * SECOND // 10 for index in range(51)]))
        self.assertEqual(select_reference_samples(timestamps)[0], 2 * SECOND)

    def test_sequence_shorter_than_two_seconds_has_no_samples(self):
        timestamps = [index * SECOND // 10 for index in range(20)]
        self.assertEqual(select_reference_samples(timestamps), [])

    def test_ten_or_fewer_frames_after_head_trim_has_no_samples(self):
        timestamps = [index * SECOND // 10 for index in range(30)]
        self.assertEqual(select_reference_samples(timestamps), [])

    def test_exactly_last_ten_frames_are_removed_before_stride(self):
        timestamps = [index * SECOND // 10 for index in range(31)]
        self.assertEqual(select_reference_samples(timestamps), [2 * SECOND])
        self.assertEqual(REFERENCE_TAIL_TRIM_FRAMES, 10)

    def test_empty_reference_has_no_samples(self):
        self.assertEqual(select_reference_samples([]), [])


if __name__ == "__main__":
    unittest.main()
