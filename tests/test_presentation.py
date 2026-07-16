import unittest

from core import BagDelayStat, TopicAlignStat
from presentation import (
    BAG_DETAIL_HEADERS,
    SUMMARY_HEADERS,
    bag_verdict,
    sampling_stats_to_summary_df,
    stats_to_bag_detail_df,
    stats_to_per_bag_detail_html,
    stats_to_summary_df,
)


def make_stat(name: str, delay: float) -> BagDelayStat:
    topic = TopicAlignStat(
        topic="/camera",
        missing=False,
        max_delay_ms=delay,
    )
    return BagDelayStat(
        bag_name=name,
        bag_path=f"/bags/{name}",
        missing_target=False,
        missing_reference=False,
        max_delay_ms=delay,
        topic_stats=[topic],
        bag_topics=["/camera"],
    )


class PresentationTests(unittest.TestCase):
    def test_sampling_summary_expands_head_skip_and_threshold_dimensions(self):
        frame = sampling_stats_to_summary_df(
            {
                0: [make_stat("a.bag", 35.0)],
                30: [make_stat("a.bag", 45.0)],
            }
        )
        self.assertEqual(len(frame), 2 * 7)
        self.assertEqual(frame["开头跳过帧数"].drop_duplicates().tolist(), [0, 30])

    def test_threshold_changes_verdict(self):
        stat = make_stat("sample.bag", 45.0)
        self.assertEqual(bag_verdict(stat, threshold_ms=40)[0], "不合格")
        self.assertEqual(bag_verdict(stat, threshold_ms=50)[0], "合格")

    def test_failed_bags_sort_first(self):
        frame = stats_to_bag_detail_df(
            [make_stat("good.bag", 10.0), make_stat("bad.bag", 80.0)],
            threshold_ms=40,
        )
        self.assertEqual(list(frame.columns), BAG_DETAIL_HEADERS)
        self.assertEqual(frame.iloc[0]["Bag"], "bad.bag")
        self.assertEqual(frame.iloc[0]["判定"], "不合格")

    def test_summary_includes_rate_and_incremental_gain(self):
        frame = stats_to_summary_df(
            [make_stat("fast.bag", 35.0), make_stat("slow.bag", 55.0)]
        )
        self.assertEqual(list(frame.columns), SUMMARY_HEADERS)
        row_40 = frame.loc[frame["阈值τ"] == "40 ms"].iloc[0]
        row_60 = frame.loc[frame["阈值τ"] == "60 ms"].iloc[0]
        self.assertEqual(row_40["合格率"], "50.0%")
        self.assertEqual(row_40["较前档新增"], "+1")
        self.assertEqual(row_60["合格率"], "100.0%")
        self.assertEqual(row_60["较前档新增"], "+1")


    def test_bad_point_times_follow_selected_threshold(self):
        topic = TopicAlignStat(
            topic="/camera",
            missing=False,
            max_delay_ms=70.0,
            delay_samples=[
                (2_000_000_000, 35.0),
                (3_000_000_000, 45.0),
                (4_000_000_000, 70.0),
            ],
        )
        stat = BagDelayStat(
            bag_name="bad-points.bag",
            bag_path="/bags/bad-points.bag",
            missing_target=False,
            missing_reference=False,
            max_delay_ms=70.0,
            topic_stats=[topic],
            bag_topics=["/camera"],
            reference_start_ns=1_000_000_000,
        )
        at_40 = stats_to_per_bag_detail_html(
            [stat], check_topics=["/camera"], threshold_ms=40
        )
        at_60 = stats_to_per_bag_detail_html(
            [stat], check_topics=["/camera"], threshold_ms=60
        )
        self.assertIn("2 个坏点", at_40)
        self.assertIn("+2.000 s", at_40)
        self.assertIn("45.000 ms", at_40)
        self.assertIn("1 个坏点", at_60)
        self.assertNotIn("+2.000 s", at_60)
        self.assertIn("+3.000 s", at_60)


    def test_detail_html_escapes_values_and_marks_status(self):
        stat = make_stat("<script>.bag", 80.0)
        rendered = stats_to_per_bag_detail_html(
            [stat],
            check_topics=["/camera"],
            threshold_ms=40,
        )
        self.assertIn("&lt;script&gt;.bag", rendered)
        self.assertNotIn("<script>.bag", rendered)
        self.assertIn("bag-card--fail", rendered)
        self.assertIn("topic-status--danger", rendered)


if __name__ == "__main__":
    unittest.main()
