#!/usr/bin/env python3
"""Batch-check ROS bags with the project's current 40 ms alignment rules."""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from core import (
    DEFAULT_EXCLUDE_TOPICS,
    DEFAULT_INPUT,
    DEFAULT_REF_TOPIC,
    copy_keepable_bags,
    get_bag_info,
    is_keepable,
    iter_measure_bags,
    scan_bags,
)

THRESHOLD_MS = 40.0
EXCLUDED_TOPICS = frozenset(
    {
        *DEFAULT_EXCLUDE_TOPICS,
        "/humanoid_wheel/eePoses",
        "/manus/left/finger_curl",
        "/manus/right/finger_curl",
        "/cam_h/depth/camera_info",
        "/cam_h/depth/image_raw/compressedDepth",
        "/cam_h/depth/metadata",
    }
)


def build_target_topics(first_bag: Path) -> list[str]:
    """Use the first bag's ordered topic set, matching the web tool's behavior."""
    info = get_bag_info(first_bag)
    targets: list[str] = []
    seen: set[str] = set()
    for item in info.topics:
        topic = item.topic
        if (
            topic not in seen
            and topic != DEFAULT_REF_TOPIC
            and topic not in EXCLUDED_TOPICS
        ):
            seen.add(topic)
            targets.append(topic)
    return targets


def result_row(stat) -> dict[str, object]:
    failed_topics: list[str] = []
    reasons: list[str] = []
    for topic in stat.topic_stats:
        if topic.missing or topic.empty or topic.max_delay_ms is None:
            failed_topics.append(topic.topic)
            reasons.append(f"{topic.topic}: {topic.message or 'missing/unusable'}")
        elif topic.max_delay_ms > THRESHOLD_MS:
            failed_topics.append(topic.topic)
            reasons.append(f"{topic.topic}: {topic.max_delay_ms:.3f} ms")
    if stat.missing_reference:
        reasons.insert(0, stat.message or "reference missing/unusable")
    passed = is_keepable(stat, THRESHOLD_MS)
    return {
        "Bag": stat.bag_name,
        "判定": "合格" if passed else "不合格",
        "阈值(ms)": THRESHOLD_MS,
        "Worst Max Delay(ms)": (
            "" if stat.max_delay_ms is None else f"{stat.max_delay_ms:.3f}"
        ),
        "不合格Topic数": len(failed_topics),
        "总检查Topic": len(stat.topic_stats),
        "不合格Topic": "; ".join(failed_topics),
        "原因": "; ".join(reasons),
        "路径": stat.bag_path,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="按当前完整 TF 链算法批量检查 Bag；固定阈值 40 ms。"
    )
    parser.add_argument(
        "--input",
        default=str(DEFAULT_INPUT),
        help=f"Bag 输入目录（默认：{DEFAULT_INPUT}）",
    )
    parser.add_argument(
        "--report",
        default="alignment_report_40ms.csv",
        help="CSV 报告路径（默认：alignment_report_40ms.csv）",
    )
    parser.add_argument(
        "--copy-to",
        help="可选：把合格 Bag 复制到该目录；不传则只生成报告",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_root = Path(args.input).expanduser().resolve()
    try:
        bags = scan_bags(input_root)
    except Exception as exc:  # noqa: BLE001
        print(f"输入目录错误：{exc}", file=sys.stderr)
        return 2
    if not bags:
        print(f"未找到 Bag：{input_root}", file=sys.stderr)
        return 2

    targets = build_target_topics(bags[0])
    if not targets:
        print("排除指定 Topic 后，没有可检查的目标 Topic。", file=sys.stderr)
        return 2

    print(f"Bag 数量：{len(bags)}")
    print(f"Reference：{DEFAULT_REF_TOPIC}")
    print(f"固定阈值：{THRESHOLD_MS:g} ms")
    print(f"检查 Topic：{len(targets)} 个")
    print("排除 Topic：" + ", ".join(sorted(EXCLUDED_TOPICS)))

    stats = []
    for progress, status, current_stats in iter_measure_bags(
        bags,
        reference_topic=DEFAULT_REF_TOPIC,
        target_topics=targets,
    ):
        stats = current_stats
        print(f"[{progress:5.1f}%] {status}", flush=True)

    rows = [result_row(stat) for stat in stats]
    rows.sort(key=lambda row: (0 if row["判定"] == "不合格" else 1, row["Bag"]))
    report_path = Path(args.report).expanduser().resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    passed = sum(1 for stat in stats if is_keepable(stat, THRESHOLD_MS))
    print(f"完成：合格 {passed}/{len(stats)}；报告：{report_path}")

    if args.copy_to:
        copy_to = Path(args.copy_to).expanduser().resolve()
        copied = failed = 0
        for _progress, line, _status, copied, failed in copy_keepable_bags(
            stats,
            threshold_ms=THRESHOLD_MS,
            input_root=input_root,
            output_folder=copy_to,
        ):
            print(line, flush=True)
        print(f"复制完成：成功/跳过 {copied}，失败 {failed}，目录：{copy_to}")
        if failed:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
