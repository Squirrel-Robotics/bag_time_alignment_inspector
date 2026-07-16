import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from batch_align_40ms import EXCLUDED_TOPICS, THRESHOLD_MS, build_target_topics
from core import (
    BagDelayStat,
    BagInfo,
    TopicAlignStat,
    TopicInfo,
    copy_keepable_bags,
)


class BatchAlign40msTests(unittest.TestCase):
    def test_multi_root_export_adds_unique_root_prefixes(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root_a = base / "left" / "bags"
            root_b = base / "right" / "bags"
            output = base / "output"
            root_a.mkdir(parents=True)
            root_b.mkdir(parents=True)
            source_a = root_a / "same.bag"
            source_b = root_b / "same.bag"
            source_a.write_bytes(b"a")
            source_b.write_bytes(b"b")

            stats = [
                BagDelayStat(
                    bag_name=source.name,
                    bag_path=str(source),
                    missing_target=False,
                    missing_reference=False,
                    max_delay_ms=1.0,
                    topic_stats=[TopicAlignStat("/target", False, 1.0)],
                )
                for source in (source_a, source_b)
            ]
            list(
                copy_keepable_bags(
                    stats,
                    threshold_ms=40.0,
                    input_roots=[root_a, root_b],
                    output_folder=output,
                )
            )

            self.assertEqual((output / "01_bags" / "same.bag").read_bytes(), b"a")
            self.assertEqual((output / "02_bags" / "same.bag").read_bytes(), b"b")

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
