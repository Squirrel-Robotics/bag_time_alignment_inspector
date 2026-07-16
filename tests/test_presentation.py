import unittest

from core import BagDelayStat, TopicAlignStat
from presentation import (
    BAG_DETAIL_HEADERS,
    SUMMARY_HEADERS,
    bag_verdict,
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

    def test_bag_table_reason_lists_topic_and_threshold_bad_times(self):
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
            bag_name="bad-times.bag",
            bag_path="/bags/bad-times.bag",
            missing_target=False,
            missing_reference=False,
            max_delay_ms=70.0,
            topic_stats=[topic],
            bag_topics=["/camera"],
            reference_start_ns=1_000_000_000,
        )
        at_40 = stats_to_bag_detail_df([stat], threshold_ms=40).iloc[0][
            "不合格原因"
        ]
        at_60 = stats_to_bag_detail_df([stat], threshold_ms=60).iloc[0][
            "不合格原因"
        ]
        self.assertIn("/camera：Max 70.000 ms", at_40)
        self.assertIn("坏点 2 个", at_40)
        self.assertIn("+2.000 s (45.000 ms)", at_40)
        self.assertNotIn("35.000 ms", at_40)
        self.assertIn("坏点 1 个", at_60)
        self.assertNotIn("+2.000 s", at_60)
        self.assertIn("+3.000 s (70.000 ms)", at_60)

    def test_bag_table_reason_names_missing_topic(self):
        missing = TopicAlignStat(
            topic="/missing/topic",
            missing=True,
            message="topic not in bag",
        )
        stat = BagDelayStat(
            bag_name="missing.bag",
            bag_path="/bags/missing.bag",
            missing_target=True,
            missing_reference=False,
            topic_stats=[missing],
        )
        reason = stats_to_bag_detail_df([stat], threshold_ms=40).iloc[0][
            "不合格原因"
        ]
        self.assertIn("/missing/topic：缺少 Topic", reason)
        self.assertIn("topic not in bag", reason)

    def test_bag_table_reason_limits_many_bad_points(self):
        topic = TopicAlignStat(
            topic="/camera",
            missing=False,
            max_delay_ms=80.0,
            delay_samples=[
                (index * 1_000_000_000, 50.0 + index)
                for index in range(1, 11)
            ],
        )
        stat = BagDelayStat(
            bag_name="many-points.bag",
            bag_path="/bags/many-points.bag",
            missing_target=False,
            missing_reference=False,
            max_delay_ms=80.0,
            topic_stats=[topic],
            reference_start_ns=0,
        )
        reason = stats_to_bag_detail_df([stat], threshold_ms=40).iloc[0][
            "不合格原因"
        ]
        self.assertIn("坏点 10 个（显示首尾各 4 个）", reason)
        self.assertIn("完整列表见下方 Topic 详情", reason)
        self.assertNotIn("+5.000 s", reason)


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


    def test_failed_only_hides_passing_bag_cards(self):
        rendered = stats_to_per_bag_detail_html(
            [make_stat("good.bag", 10.0), make_stat("bad.bag", 80.0)],
            check_topics=["/camera"],
            threshold_ms=40,
            failed_only=True,
        )
        self.assertNotIn("good.bag", rendered)
        self.assertIn("bad.bag", rendered)
        self.assertIn("仅显示不合格 Bag：1 / 2", rendered)
        self.assertIn("Bag 02", rendered)

    def test_failed_only_empty_state_follows_threshold(self):
        rendered = stats_to_per_bag_detail_html(
            [make_stat("good.bag", 10.0)],
            check_topics=["/camera"],
            threshold_ms=40,
            failed_only=True,
        )
        self.assertIn("当前 40 ms 阈值下没有不合格 Bag", rendered)


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
