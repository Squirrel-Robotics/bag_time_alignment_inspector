import unittest

from core import (
    TF_ARM_CHAINS,
    _message_header_timestamps_ns,
    _tf_arm_chain_timestamps_ns,
)


class Stamp:
    def __init__(self, sec: int, nanosec: int):
        self.sec = sec
        self.nanosec = nanosec


class Header:
    def __init__(self, sec: int, nanosec: int, frame_id: str = ""):
        self.stamp = Stamp(sec, nanosec)
        self.frame_id = frame_id


class Transform:
    def __init__(self, parent: str, child: str, timestamp_ns: int):
        sec, nanosec = divmod(timestamp_ns, 1_000_000_000)
        self.header = Header(sec, nanosec, parent)
        self.child_frame_id = child


class TFMessage:
    def __init__(self, transforms):
        self.transforms = transforms
        self.header = Header(99, 0)


class PlainMessage:
    def __init__(self, sec: int, nanosec: int):
        self.header = Header(sec, nanosec)


def chain_message(name: str, stamps_ns: list[int]) -> TFMessage:
    edges = TF_ARM_CHAINS[name]
    return TFMessage(
        [Transform(parent, child, stamp) for (parent, child), stamp in zip(edges, stamps_ns)]
    )


class TfTimestampTests(unittest.TestCase):
    def test_identical_complete_chain_uses_one_median_stamp(self):
        message = chain_message("左臂", [2_000_000_000] * 8)
        timestamps, diagnostics = _tf_arm_chain_timestamps_ns(message)
        self.assertEqual(timestamps, [2_000_000_000])
        self.assertEqual(diagnostics.accepted_count, 1)
        self.assertEqual(diagnostics.strict_equal_count, 1)
        self.assertEqual(diagnostics.max_spread_ms, 0.0)

    def test_sub_5ms_chain_uses_median_and_is_not_strict_equal(self):
        stamps = [2_000_000_000 + offset for offset in range(0, 4_000_000, 500_000)]
        timestamps, diagnostics = _tf_arm_chain_timestamps_ns(
            chain_message("右臂", stamps)
        )
        self.assertEqual(timestamps, [2_001_750_000])
        self.assertEqual(diagnostics.accepted_count, 1)
        self.assertEqual(diagnostics.strict_equal_count, 0)
        self.assertEqual(diagnostics.max_spread_ms, 3.5)

    def test_exactly_5ms_chain_is_rejected(self):
        stamps = [2_000_000_000] * 7 + [2_005_000_000]
        timestamps, diagnostics = _tf_arm_chain_timestamps_ns(
            chain_message("左臂", stamps)
        )
        self.assertEqual(timestamps, [])
        self.assertEqual(diagnostics.over_spread_count, 1)

    def test_partial_chain_is_rejected(self):
        message = chain_message("左臂", [2_000_000_000] * 8)
        message.transforms.pop()
        timestamps, diagnostics = _tf_arm_chain_timestamps_ns(message)
        self.assertEqual(timestamps, [])
        self.assertEqual(diagnostics.incomplete_count, 1)

    def test_unrelated_dynamic_tf_is_ignored(self):
        message = TFMessage([Transform("map", "odom", 2_000_000_000)])
        timestamps, expected, failures = _message_header_timestamps_ns(message, "/tf")
        self.assertEqual(timestamps, [])
        self.assertEqual((expected, failures), (0, 0))

    def test_tf_static_still_uses_each_transform_stamp(self):
        message = TFMessage(
            [
                Transform("a", "b", 3_125_000_000),
                Transform("b", "c", 3_250_000_000),
            ]
        )
        timestamps, expected, failures = _message_header_timestamps_ns(
            message, "/tf_static"
        )
        self.assertEqual(timestamps, [3_125_000_000, 3_250_000_000])
        self.assertEqual((expected, failures), (2, 0))

    def test_plain_topic_uses_top_level_header(self):
        timestamps, expected, failures = _message_header_timestamps_ns(
            PlainMessage(4, 750_000_000), "/camera"
        )
        self.assertEqual(timestamps, [4_750_000_000])
        self.assertEqual((expected, failures), (1, 0))


if __name__ == "__main__":
    unittest.main()
