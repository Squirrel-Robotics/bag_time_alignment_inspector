#!/usr/bin/env python3
"""增量扫描 kuavo_5w_sync_bags 中的新增文件夹，并生成按时间命名的 JSON 报告。"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from core import (
    DEFAULT_EXCLUDE_TOPICS,
    DEFAULT_REF_TOPIC,
    copy_keepable_bags,
    get_bag_info,
    is_keepable,
    iter_measure_bags,
    scan_bags,
)

THRESHOLD_MS = 40.0

DEFAULT_INPUT = Path("/mnt/data/kuavo/kuavo_5w_sync_bags")
REPORT_DIRECTORY_NAME = "alignment_reports"
PROCESSED_RECORD_NAME = "processed_folders.json"

# 非 bag 会话目录，不参与增量扫描
SKIP_FOLDER_NAMES = frozenset(
    {
        "reward_assignments",
        REPORT_DIRECTORY_NAME,
    }
)

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


def processed_record_path(report_directory: Path) -> Path:
    return report_directory / PROCESSED_RECORD_NAME


def load_processed_folders(record_path: Path) -> set[str]:
    if not record_path.is_file():
        return set()

    try:
        with record_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"处理记录 JSON 格式错误：{record_path}\n"
            f"详情：{exc}\n"
            "请检查是否有多余逗号，或重新运行：python check_bash.py --seed"
        ) from exc

    folders = data.get("folders", data if isinstance(data, list) else [])
    return {str(name) for name in folders}


def save_processed_folders(record_path: Path, folders: set[str]) -> None:
    record_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "folders": sorted(folders),
    }
    with record_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def list_session_folders(input_root: Path) -> list[Path]:
    """列出输入根目录下的一级会话文件夹。"""
    folders: list[Path] = []

    for path in sorted(input_root.iterdir()):
        if not path.is_dir():
            continue
        if path.name.startswith("."):
            continue
        if path.name in SKIP_FOLDER_NAMES:
            continue
        folders.append(path)

    return folders


def collect_bags_from_folders(folders: list[Path]) -> list[Path]:
    bags: list[Path] = []
    for folder in folders:
        bags.extend(scan_bags(folder))
    return sorted(bags)


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


def classify_topic_issue(topic_stat: Any) -> str | None:
    """Convert a topic result into a normalized issue type."""
    if topic_stat.missing:
        return "missing_topic"

    if topic_stat.empty:
        return "empty_topic"

    if topic_stat.max_delay_ms is None:
        return "unusable_topic"

    if topic_stat.max_delay_ms > THRESHOLD_MS:
        return "delay_exceeded"

    return None


def build_topic_result(topic_stat: Any) -> dict[str, Any]:
    """Build JSON information for one checked topic."""
    issue_type = classify_topic_issue(topic_stat)

    return {
        "topic": topic_stat.topic,
        "status": "failed" if issue_type else "passed",
        "issue_type": issue_type,
        "max_delay_ms": (
            None
            if topic_stat.max_delay_ms is None
            else round(float(topic_stat.max_delay_ms), 3)
        ),
        "missing": bool(topic_stat.missing),
        "empty": bool(topic_stat.empty),
        "message": topic_stat.message or None,
    }


def build_bag_result(stat: Any) -> dict[str, Any]:
    """Build the detailed JSON result for one bag."""
    passed = is_keepable(stat, THRESHOLD_MS)

    topic_results = [
        build_topic_result(topic_stat)
        for topic_stat in stat.topic_stats
    ]

    issues: list[dict[str, Any]] = []

    if stat.missing_reference:
        issues.append(
            {
                "type": "missing_reference",
                "topic": DEFAULT_REF_TOPIC,
                "max_delay_ms": None,
                "message": (
                    stat.message
                    or "Reference topic is missing or unusable."
                ),
            }
        )

    for topic_result in topic_results:
        if topic_result["status"] == "failed":
            issues.append(
                {
                    "type": topic_result["issue_type"],
                    "topic": topic_result["topic"],
                    "max_delay_ms": topic_result["max_delay_ms"],
                    "message": topic_result["message"],
                }
            )

    failed_topic_count = sum(
        1
        for result in topic_results
        if result["status"] == "failed"
    )

    return {
        "bag_name": stat.bag_name,
        "bag_path": str(stat.bag_path),
        "status": "passed" if passed else "failed",
        "threshold_ms": THRESHOLD_MS,
        "worst_max_delay_ms": (
            None
            if stat.max_delay_ms is None
            else round(float(stat.max_delay_ms), 3)
        ),
        "checked_topic_count": len(topic_results),
        "failed_topic_count": failed_topic_count,
        "missing_reference": bool(stat.missing_reference),
        "issues": issues,
        "topic_results": topic_results,
    }


def build_report(
    stats: list[Any],
    input_root: Path,
    target_topics: list[str],
    processed_folders: list[str],
) -> dict[str, Any]:
    """Build the complete JSON report."""
    bag_results = [build_bag_result(stat) for stat in stats]

    # 不合格 Bag 排在前面，方便直接查看问题
    bag_results.sort(
        key=lambda result: (
            0 if result["status"] == "failed" else 1,
            result["bag_name"],
        )
    )

    total_bags = len(bag_results)
    passed_bags = sum(
        result["status"] == "passed"
        for result in bag_results
    )
    failed_bags = total_bags - passed_bags

    pass_rate = (
        passed_bags / total_bags
        if total_bags
        else 0.0
    )

    issue_type_counter: Counter[str] = Counter()

    # 每个 Topic 的统计信息
    topic_issue_counts: dict[str, Counter[str]] = defaultdict(Counter)
    topic_failed_bags: dict[str, set[str]] = defaultdict(set)

    total_checked_topics = 0
    failed_topic_occurrences = 0

    for bag_result in bag_results:
        total_checked_topics += bag_result["checked_topic_count"]
        failed_topic_occurrences += bag_result["failed_topic_count"]

        for issue in bag_result["issues"]:
            issue_type = issue["type"]
            topic = issue["topic"]

            issue_type_counter[issue_type] += 1
            topic_issue_counts[topic][issue_type] += 1
            topic_failed_bags[topic].add(bag_result["bag_name"])

    failed_topic_statistics: dict[str, Any] = {}

    for topic in sorted(topic_issue_counts):
        type_counts = topic_issue_counts[topic]

        failed_topic_statistics[topic] = {
            "bag_count": len(topic_failed_bags[topic]),
            "issue_count": sum(type_counts.values()),
            "issue_types": dict(sorted(type_counts.items())),
        }

    return {
        "metadata": {
            "generated_at": datetime.now().astimezone().isoformat(
                timespec="seconds"
            ),
            "input_root": str(input_root),
            "processed_folders": processed_folders,
            "reference_topic": DEFAULT_REF_TOPIC,
            "threshold_ms": THRESHOLD_MS,
            "target_topics": target_topics,
            "excluded_topics": sorted(EXCLUDED_TOPICS),
            "report_version": "1.1",
        },
        "summary": {
            "total_bags": total_bags,
            "passed_bags": passed_bags,
            "failed_bags": failed_bags,
            "pass_rate": round(pass_rate, 6),
            "pass_rate_percent": round(pass_rate * 100, 2),
            "total_checked_topics": total_checked_topics,
            "failed_topic_occurrences": failed_topic_occurrences,
            "folder_count": len(processed_folders),
        },
        "issue_type_statistics": {
            "missing_reference": issue_type_counter["missing_reference"],
            "missing_topic": issue_type_counter["missing_topic"],
            "empty_topic": issue_type_counter["empty_topic"],
            "unusable_topic": issue_type_counter["unusable_topic"],
            "delay_exceeded": issue_type_counter["delay_exceeded"],
        },
        "failed_topic_statistics": failed_topic_statistics,
        "bags": bag_results,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "增量扫描 kuavo_5w_sync_bags 中的新增会话文件夹，"
            "按当前完整 TF 链算法批量检查 Bag；固定阈值 40 ms。"
        )
    )

    parser.add_argument(
        "--input",
        default=str(DEFAULT_INPUT),
        help=f"Bag 输入根目录（默认：{DEFAULT_INPUT}）",
    )

    parser.add_argument(
        "--copy-to",
        help="可选：把合格 Bag 复制到该目录；不传则只生成报告",
    )

    parser.add_argument(
        "--seed",
        action="store_true",
        help="仅把当前已有文件夹写入记录，不执行检查（用于初始化）",
    )

    parser.add_argument(
        "--all",
        action="store_true",
        help="忽略已处理记录，检查当前全部会话文件夹",
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_root = Path(args.input).expanduser().resolve()

    if not input_root.is_dir():
        print(f"输入目录不存在：{input_root}", file=sys.stderr)
        return 2

    report_directory = Path.cwd() / REPORT_DIRECTORY_NAME
    report_directory.mkdir(parents=True, exist_ok=True)
    record_path = processed_record_path(report_directory)

    known_folders = load_processed_folders(record_path)
    session_folders = list_session_folders(input_root)

    if args.seed:
        folder_names = {folder.name for folder in session_folders}
        save_processed_folders(record_path, folder_names)
        print(
            f"已写入记录：{len(folder_names)} 个文件夹 -> {record_path}"
        )
        for name in sorted(folder_names):
            print(f"  - {name}")
        return 0

    if args.all or not known_folders:
        new_folders = session_folders
        if not known_folders and not args.all:
            print(
                "未找到已处理记录，将检查当前全部会话文件夹；"
                f"检查完成后写入：{record_path}"
            )
    else:
        new_folders = [
            folder
            for folder in session_folders
            if folder.name not in known_folders
        ]

    if not new_folders:
        print(f"没有新增文件夹：{input_root}")
        print(f"已处理记录：{record_path}（{len(known_folders)} 个）")
        return 0

    new_folder_names = [folder.name for folder in new_folders]
    print(f"新增文件夹：{len(new_folder_names)} 个")
    for name in new_folder_names:
        print(f"  - {name}")

    try:
        bags = collect_bags_from_folders(new_folders)
    except Exception as exc:  # noqa: BLE001
        print(f"扫描 Bag 失败：{exc}", file=sys.stderr)
        return 2

    if not bags:
        print("新增文件夹中未找到 Bag。", file=sys.stderr)
        return 2

    targets = build_target_topics(bags[0])

    if not targets:
        print(
            "排除指定 Topic 后，没有可检查的目标 Topic。",
            file=sys.stderr,
        )
        return 2

    print(f"Bag 数量：{len(bags)}")
    print(f"Reference：{DEFAULT_REF_TOPIC}")
    print(f"固定阈值：{THRESHOLD_MS:g} ms")
    print(f"检查 Topic：{len(targets)} 个")
    print("排除 Topic：" + ", ".join(sorted(EXCLUDED_TOPICS)))

    stats: list[Any] = []

    for progress, status, current_stats in iter_measure_bags(
        bags,
        reference_topic=DEFAULT_REF_TOPIC,
        target_topics=targets,
    ):
        stats = current_stats
        print(f"[{progress:5.1f}%] {status}", flush=True)

    if not stats:
        print("没有生成任何 Bag 检查结果。", file=sys.stderr)
        return 2

    report = build_report(
        stats=stats,
        input_root=input_root,
        target_topics=targets,
        processed_folders=new_folder_names,
    )

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = report_directory / f"alignment_report_{stamp}.json"

    with report_path.open("w", encoding="utf-8") as handle:
        json.dump(
            report,
            handle,
            ensure_ascii=False,
            indent=2,
        )
        handle.write("\n")

    updated_folders = known_folders | set(new_folder_names)
    save_processed_folders(record_path, updated_folders)

    summary = report["summary"]

    print(
        "完成："
        f"合格 {summary['passed_bags']}/{summary['total_bags']}；"
        f"通过率 {summary['pass_rate_percent']:.2f}%"
    )
    print(f"JSON 报告：{report_path}")
    print(
        f"已更新处理记录：{record_path}"
        f"（共 {len(updated_folders)} 个文件夹）"
    )

    issue_stats = report["issue_type_statistics"]

    print("问题类型统计：")
    print(f"  参考 Topic 缺失：{issue_stats['missing_reference']}")
    print(f"  Topic 缺失：{issue_stats['missing_topic']}")
    print(f"  Topic 为空：{issue_stats['empty_topic']}")
    print(f"  Topic 不可用：{issue_stats['unusable_topic']}")
    print(f"  延迟超过阈值：{issue_stats['delay_exceeded']}")

    if args.copy_to:
        copy_to = Path(args.copy_to).expanduser().resolve()
        copied = 0
        failed = 0

        for (
            _progress,
            line,
            _status,
            copied,
            failed,
        ) in copy_keepable_bags(
            stats,
            threshold_ms=THRESHOLD_MS,
            input_root=input_root,
            output_folder=copy_to,
        ):
            print(line, flush=True)

        print(
            f"复制完成：成功/跳过 {copied}，"
            f"失败 {failed}，目录：{copy_to}"
        )

        if failed:
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
