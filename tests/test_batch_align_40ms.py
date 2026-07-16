import unittest
from pathlib import Path
from unittest.mock import patch

from batch_align_40ms import EXCLUDED_TOPICS, THRESHOLD_MS, build_target_topics
from core import BagInfo, TopicInfo


class BatchAlign40msTests(unittest.TestCase):
    def test_fixed_threshold_and_requested_exclusions(self):
        self.assertEqual(THRESHOLD_MS, 40.0)
        self.assertTrue(
            {
                "/humanoid_wheel/eePoses",
                "/manus/left/finger_curl",
                "/manus/right/finger_curl",
            }.issubset(EXCLUDED_TOPICS)
        )

    @patch("batch_align_40ms.get_bag_info")
    def test_target_topics_exclude_reference_static_and_requested(self, get_info):
        names = [
            "/cam_h/color/image_raw/compressed",
            "/tf",
            "/tf_static",
            "/humanoid_wheel/eePoses",
            "/manus/left/finger_curl",
            "/manus/right/finger_curl",
            "/dexhand/command",
        ]
        get_info.return_value = BagInfo(
            path="x.bag",
            name="x.bag",
            duration_sec=1.0,
            topics=[TopicInfo(name, "type", 1, 1.0) for name in names],
        )
        self.assertEqual(
            build_target_topics(Path("x.bag")),
            ["/tf", "/dexhand/command"],
        )


if __name__ == "__main__":
    unittest.main()
