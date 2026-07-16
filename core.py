"""Core logic for ROS bag scan / topic info / time-alignment / copy."""

from __future__ import annotations

import logging
import shutil
from bisect import bisect_left
from dataclasses import asdict, dataclass, field
from pathlib import Path
from statistics import median
from typing import Callable, Iterator, Optional

logger = logging.getLogger(__name__)

DEFAULT_INPUT = Path("/mnt/data/kuavo/raw_bags")
DEFAULT_OUTPUT = Path("/mnt/data/kuavo/tmp")
DEFAULT_REF_TOPIC = "/cam_h/color/image_raw/compressed"
DEFAULT_TGT_TOPIC = "/dexhand/command"  # legacy single-target default
DEFAULT_EXCLUDE_TOPICS: frozenset[str] = frozenset({"/tf_static"})
TF_TOPICS: frozenset[str] = frozenset({"/tf", "/tf_static"})
DEFAULT_THRESHOLD_MS = 40.0
DEFAULT_THRESHOLDS_MS: list[float] = [30.0, 40.0, 50.0, 60.0, 80.0, 100.0, 300.0]
REFERENCE_HEAD_TRIM_SECONDS = 2.0
REFERENCE_SAMPLE_STRIDE = 3
REFERENCE_TAIL_TRIM_FRAMES = 10
TF_CHAIN_MAX_SPREAD_MS = 5.0
TF_ARM_CHAINS: dict[str, tuple[tuple[str, str], ...]] = {
    "左臂": (
        ("waist_link", "waist_yaw_link"),
        ("waist_yaw_link", "zarm_l1_link"),
        ("zarm_l1_link", "zarm_l2_link"),
        ("zarm_l2_link", "zarm_l3_link"),
        ("zarm_l3_link", "zarm_l4_link"),
        ("zarm_l4_link", "zarm_l5_link"),
        ("zarm_l5_link", "zarm_l6_link"),
        ("zarm_l6_link", "zarm_l7_link"),
    ),
    "右臂": (
        ("waist_link", "waist_yaw_link"),
        ("waist_yaw_link", "zarm_r1_link"),
        ("zarm_r1_link", "zarm_r2_link"),
        ("zarm_r2_link", "zarm_r3_link"),
        ("zarm_r3_link", "zarm_r4_link"),
        ("zarm_r4_link", "zarm_r5_link"),
        ("zarm_r5_link", "zarm_r6_link"),
        ("zarm_r6_link", "zarm_r7_link"),
    ),
}


# ---------------------------------------------------------------------------
# Bag backend: prefer ROS1 rosbag, fall back to pure-Python rosbags
# ---------------------------------------------------------------------------

def _backend() -> str:
    try:
        import rosbag  # noqa: F401

        return "rosbag"
    except ImportError:
        pass
    try:
        from rosbags.highlevel import AnyReader  # noqa: F401

        return "rosbags"
    except ImportError as exc:
        raise ImportError(
            "Need either ROS1 'rosbag' or pip package 'rosbags'."
        ) from exc


@dataclass
class TopicInfo:
    topic: str
    msg_type: str
    message_count: int
    frequency_hz: float


@dataclass
class BagInfo:
    path: str
    name: str
    duration_sec: float
    topics: list[TopicInfo] = field(default_factory=list)


@dataclass
class AlignResult:
    bag_name: str
    bag_path: str
    relative_path: str
    status: str  # Good / Bad / Missing Topic / Error
    max_delay_ms: Optional[float] = None
    mean_delay_ms: Optional[float] = None
    bad_frames: Optional[int] = None
    reference_count: Optional[int] = None
    target_count: Optional[int] = None
    message: str = ""

    def to_row(self) -> list:
        return [
            self.bag_name,
            "-" if self.max_delay_ms is None else round(self.max_delay_ms, 3),
            "-" if self.mean_delay_ms is None else round(self.mean_delay_ms, 3),
            "-" if self.bad_frames is None else self.bad_frames,
            self.status,
            self.relative_path,
        ]


@dataclass
class TopicAlignStat:
    """单个 Target Topic 相对 Reference 的对齐结果。"""

    topic: str
    missing: bool  # bag 中不存在该 topic
    max_delay_ms: Optional[float] = None
    message: str = ""
    empty: bool = False  # topic 存在但无消息
    # (Reference header.stamp ns, delay ms)，仅保存高于最低阈值的候选坏点。
    delay_samples: list[tuple[int, float]] = field(default_factory=list)
    tf_chain_sample_count: int = 0
    tf_chain_strict_equal_count: int = 0
    tf_chain_max_spread_ms: Optional[float] = None
    tf_chain_incomplete_count: int = 0
    tf_chain_over_spread_count: int = 0
    tf_chain_names: list[str] = field(default_factory=list)


@dataclass
class BagDelayStat:
    """单 bag 对齐统计（多 Target Topic），供多阈值汇总表使用。"""

    bag_name: str
    bag_path: str
    missing_target: bool
    missing_reference: bool
    max_delay_ms: Optional[float] = None
    message: str = ""
    topic_stats: list[TopicAlignStat] = field(default_factory=list)
    bag_topics: list[str] = field(default_factory=list)  # bag 内全部 topic（保序）
    reference_start_ns: Optional[int] = None  # 第一条有效 Reference header.stamp


STATUS_ORDER = (
    "超过阈值",
    "无消息",
    "缺少Topic",
    "合格",
    "主时间轴",
    "未选择",
    "已忽略",
)


def classify_bag_topic_rows(
    stat: BagDelayStat,
    *,
    check_topics: list[str],
    threshold_ms: float,
    reference_topic: str = DEFAULT_REF_TOPIC,
    exclude_topics: frozenset[str] | set[str] | None = None,
) -> list[dict]:
    """生成单个 Bag 的 Topic 行，异常置顶并附带动态阈值坏点。"""
    exclude = set(exclude_topics if exclude_topics is not None else DEFAULT_EXCLUDE_TOPICS)
    checked = {topic for topic in check_topics if topic and topic != reference_topic}
    measured = {topic.topic: topic for topic in stat.topic_stats}

    names: list[str] = []
    seen: set[str] = set()
    for topic in [reference_topic, *stat.bag_topics, *checked, *measured.keys()]:
        if topic and topic not in seen:
            seen.add(topic)
            names.append(topic)

    rows: list[dict] = []
    for topic in names:
        delay: float | str = "-"
        note = ""
        bad_points: list[dict] = []
        measured_topic = measured.get(topic)
        if topic == reference_topic:
            status = "主时间轴"
            if stat.missing_reference:
                note = stat.message or "reference missing / unusable"
        elif topic in exclude:
            status = "已忽略"
        elif topic not in checked:
            status = "未选择"
        else:
            measured_topic = measured.get(topic)
            if measured_topic is None:
                status = "缺少Topic"
                note = "未测量"
            elif measured_topic.empty or (
                measured_topic.missing and "0 messages" in (measured_topic.message or "")
            ):
                status = "无消息"
                note = measured_topic.message or ""
            elif measured_topic.missing:
                status = "缺少Topic"
                note = measured_topic.message or ""
            elif measured_topic.max_delay_ms is None:
                status = "无消息"
                note = measured_topic.message or "no delay"
            elif measured_topic.max_delay_ms > threshold_ms:
                status = "超过阈值"
                delay = round(measured_topic.max_delay_ms, 3)
                origin_ns = stat.reference_start_ns
                if origin_ns is None and measured_topic.delay_samples:
                    origin_ns = measured_topic.delay_samples[0][0]
                for timestamp_ns, sample_delay_ms in measured_topic.delay_samples:
                    if sample_delay_ms <= threshold_ms:
                        continue
                    relative_sec = (
                        (timestamp_ns - origin_ns) / 1e9 if origin_ns is not None else 0.0
                    )
                    bad_points.append(
                        {
                            "relative_time_sec": relative_sec,
                            "reference_time_ns": timestamp_ns,
                            "delay_ms": sample_delay_ms,
                        }
                    )
                bad_points.sort(key=lambda point: point["reference_time_ns"])
            else:
                status = "合格"
                delay = round(measured_topic.max_delay_ms, 3)

        tf_chain_sync = "-"
        if topic == "/tf" and measured_topic is not None:
            chain_names = "/".join(measured_topic.tf_chain_names) or "未识别"
            max_spread = measured_topic.tf_chain_max_spread_ms
            spread_text = "-" if max_spread is None else f"{max_spread:.3f} ms"
            tf_chain_sync = (
                f"{chain_names}；有效链={measured_topic.tf_chain_sample_count}；"
                f"严格相同={measured_topic.tf_chain_strict_equal_count}/"
                f"{measured_topic.tf_chain_sample_count}；最大链内差={spread_text}"
            )
            invalid = (
                measured_topic.tf_chain_incomplete_count
                + measured_topic.tf_chain_over_spread_count
            )
            if invalid:
                tf_chain_sync += (
                    f"；不完整={measured_topic.tf_chain_incomplete_count}；"
                    f">=5ms={measured_topic.tf_chain_over_spread_count}"
                )

        rows.append(
            {
                "Topic": topic,
                "Max Delay (ms)": delay,
                "状态": status,
                "TF 链内同步": tf_chain_sync,
                "坏点数": len(bad_points),
                "坏点时刻": bad_points,
                "说明": note,
            }
        )

    rank = {name: index for index, name in enumerate(STATUS_ORDER)}
    rows.sort(key=lambda row: (rank.get(row["状态"], 99), row["Topic"]))
    return rows

def default_check_topics(
    all_topics: list[str],
    *,
    reference_topic: str = DEFAULT_REF_TOPIC,
    exclude: frozenset[str] | set[str] | None = None,
) -> list[str]:
    """默认要检查的 Target Topics：除 Reference 与排除列表外全部。"""
    skip = set(exclude if exclude is not None else DEFAULT_EXCLUDE_TOPICS)
    skip.add(reference_topic)
    return [t for t in all_topics if t not in skip]


def scan_bags(input_folder: str | Path) -> list[Path]:
    root = Path(input_folder).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Input folder not found: {root}")
    bags = sorted(p for p in root.rglob("*.bag") if p.is_file())
    logger.info("Scanned %d bags under %s", len(bags), root)
    return bags


def get_bag_info(bag_path: Path) -> BagInfo:
    backend = _backend()
    if backend == "rosbag":
        return _info_rosbag(bag_path)
    return _info_rosbags(bag_path)


def _info_rosbag(bag_path: Path) -> BagInfo:
    import rosbag

    with rosbag.Bag(str(bag_path), "r") as bag:
        info = bag.get_type_and_topic_info()
        start = float(bag.get_start_time())
        end = float(bag.get_end_time())
        topics = [
            TopicInfo(
                topic=name,
                msg_type=t.msg_type,
                message_count=int(t.message_count),
                frequency_hz=float(t.frequency or 0.0),
            )
            for name, t in sorted(info.topics.items())
        ]
    return BagInfo(
        path=str(bag_path),
        name=bag_path.name,
        duration_sec=max(0.0, end - start),
        topics=topics,
    )


def _info_rosbags(bag_path: Path) -> BagInfo:
    from rosbags.highlevel import AnyReader

    with AnyReader([bag_path]) as reader:
        duration = max(0.0, (int(reader.end_time) - int(reader.start_time)) / 1e9)
        topics = []
        for conn in sorted(reader.connections, key=lambda c: c.topic):
            count = int(conn.msgcount)
            freq = (count / duration) if duration > 0 and count > 0 else 0.0
            topics.append(
                TopicInfo(
                    topic=conn.topic,
                    msg_type=conn.msgtype,
                    message_count=count,
                    frequency_hz=round(freq, 3),
                )
            )
    return BagInfo(
        path=str(bag_path),
        name=bag_path.name,
        duration_sec=round(duration, 3),
        topics=topics,
    )


def _header_stamp_ns(message: object) -> int | None:
    """读取消息顶层 header.stamp；0 时间戳按无效处理。"""
    header = getattr(message, "header", None)
    stamp = getattr(header, "stamp", None) if header is not None else None
    if stamp is None:
        return None
    sec = getattr(stamp, "sec", getattr(stamp, "secs", None))
    nsec = getattr(stamp, "nanosec", getattr(stamp, "nsecs", None))
    if sec is None or nsec is None:
        return None
    value = int(sec) * 1_000_000_000 + int(nsec)
    return value if value > 0 else None


@dataclass
class TfChainDiagnostics:
    """Aggregated validation data for complete dynamic arm TF chains."""

    accepted_count: int = 0
    strict_equal_count: int = 0
    max_spread_ms: Optional[float] = None
    incomplete_count: int = 0
    over_spread_count: int = 0
    chain_names: list[str] = field(default_factory=list)


def _normalize_frame_id(frame_id: object) -> str:
    return str(frame_id or "").strip().strip("/")


def _tf_arm_chain_timestamps_ns(
    message: object,
) -> tuple[list[int], TfChainDiagnostics]:
    """Validate complete waist-to-arm chains and return one median stamp per chain."""
    diagnostics = TfChainDiagnostics()
    transforms = getattr(message, "transforms", None)
    if transforms is None:
        diagnostics.incomplete_count = 1
        return [], diagnostics

    edge_stamps: dict[tuple[str, str], int | None] = {}
    for transform in transforms:
        parent = _normalize_frame_id(
            getattr(getattr(transform, "header", None), "frame_id", "")
        )
        child = _normalize_frame_id(getattr(transform, "child_frame_id", ""))
        if parent and child:
            edge_stamps[(parent, child)] = _header_stamp_ns(transform)

    timestamps: list[int] = []
    for chain_name, chain_edges in TF_ARM_CHAINS.items():
        present = [edge for edge in chain_edges if edge in edge_stamps]
        # The first waist edge is shared by both arms. A chain is considered
        # present only when at least one side-specific arm edge is present.
        if not any(edge in edge_stamps for edge in chain_edges[1:]):
            continue
        if chain_name not in diagnostics.chain_names:
            diagnostics.chain_names.append(chain_name)
        if len(present) != len(chain_edges):
            diagnostics.incomplete_count += 1
            continue

        stamps = [edge_stamps[edge] for edge in chain_edges]
        if any(stamp is None for stamp in stamps):
            diagnostics.incomplete_count += 1
            continue
        valid_stamps = [int(stamp) for stamp in stamps if stamp is not None]
        spread_ms = (max(valid_stamps) - min(valid_stamps)) / 1e6
        if (
            diagnostics.max_spread_ms is None
            or spread_ms > diagnostics.max_spread_ms
        ):
            diagnostics.max_spread_ms = spread_ms
        if spread_ms >= TF_CHAIN_MAX_SPREAD_MS:
            diagnostics.over_spread_count += 1
            continue

        diagnostics.accepted_count += 1
        if len(set(valid_stamps)) == 1:
            diagnostics.strict_equal_count += 1
        timestamps.append(int(median(valid_stamps)))

    return timestamps, diagnostics


def _message_header_timestamps_detailed(
    message: object,
    topic: str,
) -> tuple[list[int], int, int, TfChainDiagnostics | None]:
    if topic == "/tf":
        timestamps, diagnostics = _tf_arm_chain_timestamps_ns(message)
        expected = (
            diagnostics.accepted_count
            + diagnostics.incomplete_count
            + diagnostics.over_spread_count
        )
        failures = diagnostics.incomplete_count + diagnostics.over_spread_count
        return timestamps, expected, failures, diagnostics

    if topic == "/tf_static":
        transforms = getattr(message, "transforms", None)
        if transforms is None:
            return [], 1, 1, None
        timestamp_sources = list(transforms)
    else:
        timestamp_sources = [message]

    timestamps: list[int] = []
    failures = 0
    for source in timestamp_sources:
        try:
            timestamp = _header_stamp_ns(source)
        except Exception:  # noqa: BLE001
            failures += 1
            continue
        if timestamp is not None:
            timestamps.append(timestamp)
    return timestamps, len(timestamp_sources), failures, None


def _message_header_timestamps_ns(
    message: object,
    topic: str,
) -> tuple[list[int], int, int]:
    """Return valid header stamps, expected entries, and failed entries.

    Dynamic /tf is represented by the median stamp of each complete validated arm
    chain. /tf_static keeps its per-transform behavior; normal topics use the
    message's top-level header.stamp.
    """
    timestamps, expected, failures, _diagnostics = (
        _message_header_timestamps_detailed(message, topic)
    )
    return timestamps, expected, failures


def _merge_tf_chain_diagnostics(
    total: TfChainDiagnostics,
    current: TfChainDiagnostics,
) -> None:
    total.accepted_count += current.accepted_count
    total.strict_equal_count += current.strict_equal_count
    total.incomplete_count += current.incomplete_count
    total.over_spread_count += current.over_spread_count
    if current.max_spread_ms is not None and (
        total.max_spread_ms is None or current.max_spread_ms > total.max_spread_ms
    ):
        total.max_spread_ms = current.max_spread_ms
    for chain_name in current.chain_names:
        if chain_name not in total.chain_names:
            total.chain_names.append(chain_name)


def _read_header_timestamps_detailed(
    bag_path: Path,
    topics: list[str],
) -> tuple[
    dict[str, list[int]],
    dict[str, str],
    dict[str, TfChainDiagnostics],
]:
    """Read header stamps and validate complete dynamic arm chains in /tf."""
    requested = list(dict.fromkeys(topic for topic in topics if topic))
    requested_set = set(requested)
    times: dict[str, list[int]] = {topic: [] for topic in requested}
    entry_counts: dict[str, int] = {topic: 0 for topic in requested}
    message_counts: dict[str, int] = {topic: 0 for topic in requested}
    failures: dict[str, int] = {topic: 0 for topic in requested}
    tf_diagnostics: dict[str, TfChainDiagnostics] = {
        topic: TfChainDiagnostics() for topic in requested if topic == "/tf"
    }
    backend = _backend()

    def consume(topic: str, message: object) -> None:
        message_counts[topic] += 1
        timestamps, expected, failed, diagnostics = (
            _message_header_timestamps_detailed(message, topic)
        )
        times[topic].extend(timestamps)
        entry_counts[topic] += expected
        failures[topic] += failed
        if diagnostics is not None:
            _merge_tf_chain_diagnostics(tf_diagnostics[topic], diagnostics)

    if backend == "rosbag":
        import rosbag

        with rosbag.Bag(str(bag_path), "r") as bag:
            for topic, message, _bag_time in bag.read_messages(topics=requested):
                consume(topic, message)
    else:
        from rosbags.highlevel import AnyReader

        with AnyReader([bag_path]) as reader:
            conns = [
                connection
                for connection in reader.connections
                if connection.topic in requested_set
            ]
            for connection, _bag_time, rawdata in reader.messages(connections=conns):
                topic = connection.topic
                try:
                    message = reader.deserialize(rawdata, connection.msgtype)
                    consume(topic, message)
                except Exception:  # noqa: BLE001
                    message_counts[topic] += 1
                    entry_counts[topic] += 1
                    failures[topic] += 1

    # Left/right medians commonly match exactly; duplicates add no timeline value.
    if "/tf" in times:
        times["/tf"] = sorted(set(times["/tf"]))

    issues: dict[str, str] = {}
    for topic in requested:
        messages = message_counts[topic]
        expected = entry_counts[topic]
        valid = len(times[topic])
        failed = failures[topic]
        if messages == 0:
            continue
        if topic == "/tf":
            diagnostics = tf_diagnostics[topic]
            if diagnostics.accepted_count == 0:
                issues[topic] = "no complete valid waist-to-arm TF chain found"
            elif diagnostics.incomplete_count or diagnostics.over_spread_count:
                issues[topic] = (
                    "TF arm-chain validation failed: "
                    f"incomplete={diagnostics.incomplete_count}, "
                    f"spread>={TF_CHAIN_MAX_SPREAD_MS:g}ms="
                    f"{diagnostics.over_spread_count}"
                )
            continue
        if expected == 0:
            issues[topic] = (
                "TF topic has messages but contains no transforms"
                if topic == "/tf_static"
                else "topic has no timestamp-bearing entries"
            )
        elif valid == 0:
            if failed:
                issues[topic] = "header.stamp extraction/deserialization failed"
            elif topic == "/tf_static":
                issues[topic] = "TF transforms have no valid header.stamp"
            else:
                issues[topic] = "topic has no valid top-level header.stamp"
        elif valid != expected or failed:
            source_name = "transforms" if topic == "/tf_static" else "messages"
            issues[topic] = (
                f"header.stamp incomplete: valid={valid}, {source_name}={expected}, "
                f"messages={messages}, failures={failed}"
            )
    return times, issues, tf_diagnostics


def read_header_timestamps(
    bag_path: Path,
    topics: list[str],
) -> tuple[dict[str, list[int]], dict[str, str]]:
    """Compatibility wrapper returning timestamps and validation issues."""
    times, issues, _diagnostics = _read_header_timestamps_detailed(bag_path, topics)
    return times, issues


def _tf_chain_topic_kwargs(
    topic: str,
    diagnostics: dict[str, TfChainDiagnostics],
) -> dict:
    if topic != "/tf" or topic not in diagnostics:
        return {}
    item = diagnostics[topic]
    return {
        "tf_chain_sample_count": item.accepted_count,
        "tf_chain_strict_equal_count": item.strict_equal_count,
        "tf_chain_max_spread_ms": item.max_spread_ms,
        "tf_chain_incomplete_count": item.incomplete_count,
        "tf_chain_over_spread_count": item.over_spread_count,
        "tf_chain_names": list(item.chain_names),
    }

def read_timestamps(bag_path: Path, topic: str) -> list[int]:
    """兼容旧调用：读取消息内部 header.stamp，而非 bag record time。"""
    times, issues = read_header_timestamps(bag_path, [topic])
    if topic in issues:
        raise ValueError(issues[topic])
    return times[topic]


def _nearest_delay_ms(sorted_times_ns: list[int], target_ns: int) -> float:
    if not sorted_times_ns:
        return float("inf")
    idx = bisect_left(sorted_times_ns, target_ns)
    cands = []
    if idx < len(sorted_times_ns):
        cands.append(sorted_times_ns[idx])
    if idx > 0:
        cands.append(sorted_times_ns[idx - 1])
    return min(abs(c - target_ns) for c in cands) / 1e6


def select_reference_samples(times_ns: list[int]) -> list[int]:
    """跳过 Reference 起始 2 秒、删除结尾 10 帧，再每 3 帧取 1 帧。"""
    ordered = sorted(times_ns)
    if not ordered:
        return []
    cutoff_ns = ordered[0] + int(REFERENCE_HEAD_TRIM_SECONDS * 1_000_000_000)
    first_kept = bisect_left(ordered, cutoff_ns)
    head_trimmed = ordered[first_kept:]
    if len(head_trimmed) <= REFERENCE_TAIL_TRIM_FRAMES:
        return []
    trimmed = head_trimmed[:-REFERENCE_TAIL_TRIM_FRAMES]
    return trimmed[::REFERENCE_SAMPLE_STRIDE]


def measure_bag_delay(
    bag_path: Path,
    *,
    reference_topic: str,
    target_topics: list[str],
) -> BagDelayStat:
    """以 reference 为主轴，对多个 target topic 计算 max delay。

    bag 级 max_delay_ms 取各 topic 中的最差值；任一 target 缺失则 missing_target=True。
    """
    targets = [t for t in target_topics if t and t != reference_topic]
    try:
        info = get_bag_info(bag_path)
        bag_topics: list[str] = []
        seen: set[str] = set()
        for t in info.topics:
            if t.topic not in seen:
                seen.add(t.topic)
                bag_topics.append(t.topic)
        names = set(bag_topics)
        missing_ref = reference_topic not in names
        if missing_ref:
            topic_stats = [
                TopicAlignStat(topic=t, missing=True, message="reference missing")
                for t in targets
            ]
            return BagDelayStat(
                bag_name=bag_path.name,
                bag_path=str(bag_path),
                missing_target=bool(targets),
                missing_reference=True,
                message=f"Missing reference: {reference_topic}",
                topic_stats=topic_stats,
                bag_topics=bag_topics,
            )

        readable_topics = [reference_topic, *[t for t in targets if t in names]]
        header_times, header_issues, tf_chain_diagnostics = (
            _read_header_timestamps_detailed(bag_path, readable_topics)
        )
        if reference_topic in header_issues:
            topic_stats = [
                TopicAlignStat(
                    topic=t,
                    missing=True,
                    message=f"reference unusable: {header_issues[reference_topic]}",
                )
                for t in targets
            ]
            return BagDelayStat(
                bag_name=bag_path.name,
                bag_path=str(bag_path),
                missing_target=bool(targets),
                missing_reference=True,
                message=header_issues[reference_topic],
                topic_stats=topic_stats,
                bag_topics=bag_topics,
            )

        reference_times = sorted(header_times[reference_topic])
        reference_start_ns = reference_times[0] if reference_times else None
        ref = select_reference_samples(reference_times)
        if not ref:
            topic_stats = [
                TopicAlignStat(
                    topic=t,
                    missing=True,
                    message="reference has 0 usable samples after trim/stride",
                )
                for t in targets
            ]
            return BagDelayStat(
                bag_name=bag_path.name,
                bag_path=str(bag_path),
                missing_target=bool(targets),
                missing_reference=True,
                message=(
                    "Reference topic exists but has 0 usable messages after "
                    f"sampling (skip first {REFERENCE_HEAD_TRIM_SECONDS:g}s, "
                    f"drop last {REFERENCE_TAIL_TRIM_FRAMES} frames, "
                    f"stride {REFERENCE_SAMPLE_STRIDE})"
                ),
                topic_stats=topic_stats,
                bag_topics=bag_topics,
            )

        topic_stats: list[TopicAlignStat] = []
        for topic in targets:
            if topic not in names:
                topic_stats.append(
                    TopicAlignStat(topic=topic, missing=True, message="topic not in bag")
                )
                continue
            if topic in header_issues:
                topic_stats.append(
                    TopicAlignStat(
                        topic=topic,
                        missing=False,
                        empty=True,
                        message=header_issues[topic],
                        **_tf_chain_topic_kwargs(topic, tf_chain_diagnostics),
                    )
                )
                continue
            tgt = sorted(header_times[topic])
            if not tgt:
                topic_stats.append(
                    TopicAlignStat(
                        topic=topic,
                        missing=False,
                        empty=True,
                        message="topic has 0 messages",
                        **_tf_chain_topic_kwargs(topic, tf_chain_diagnostics),
                    )
                )
                continue
            samples = [(timestamp_ns, _nearest_delay_ms(tgt, timestamp_ns)) for timestamp_ns in ref]
            delays = [delay_ms for _, delay_ms in samples]
            minimum_threshold = min(DEFAULT_THRESHOLDS_MS)
            candidate_bad_points = [
                (timestamp_ns, delay_ms)
                for timestamp_ns, delay_ms in samples
                if delay_ms > minimum_threshold
            ]
            topic_stats.append(
                TopicAlignStat(
                    topic=topic,
                    missing=False,
                    max_delay_ms=max(delays) if delays else None,
                    delay_samples=candidate_bad_points,
                    **_tf_chain_topic_kwargs(topic, tf_chain_diagnostics),
                )
            )

        # 无消息也视为不可保留
        missing_any = any(s.missing or s.empty for s in topic_stats)
        ok_delays = [
            s.max_delay_ms
            for s in topic_stats
            if (not s.missing and not s.empty and s.max_delay_ms is not None)
        ]
        bad_names = [s.topic for s in topic_stats if s.missing or s.empty]
        msg = ""
        if bad_names:
            msg = f"Missing/empty: {', '.join(bad_names)}"

        return BagDelayStat(
            bag_name=bag_path.name,
            bag_path=str(bag_path),
            missing_target=missing_any,
            missing_reference=False,
            max_delay_ms=max(ok_delays) if ok_delays else None,
            message=msg,
            topic_stats=topic_stats,
            bag_topics=bag_topics,
            reference_start_ns=reference_start_ns,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("measure_bag_delay failed: %s", bag_path)
        return BagDelayStat(
            bag_name=bag_path.name,
            bag_path=str(bag_path),
            missing_target=True,
            missing_reference=True,
            message=str(exc),
            topic_stats=[
                TopicAlignStat(topic=t, missing=True, message=str(exc)) for t in targets
            ],
            bag_topics=[],
        )


def build_threshold_summary(
    stats: list[BagDelayStat],
    *,
    target_topic: str | None = None,
    thresholds_ms: list[float] | None = None,
) -> list[dict]:
    """生成多阈值质量总览，包含完整性、合格率和阈值增益。"""
    del target_topic  # unused; kept for call-site compatibility
    thresholds = thresholds_ms or list(DEFAULT_THRESHOLDS_MS)
    total = len(stats)
    missing = sum(1 for s in stats if s.missing_target or s.missing_reference)
    evaluable = total - missing
    previous_keep: int | None = None
    rows: list[dict] = []

    for tau in thresholds:
        over = 0
        for stat in stats:
            if stat.missing_target or stat.missing_reference:
                continue
            if stat.max_delay_ms is None or stat.max_delay_ms > tau:
                over += 1

        excluded = over + missing
        keep = total - excluded
        pass_rate = (keep / total * 100.0) if total else 0.0
        if previous_keep is None:
            incremental = "-"
        else:
            gained = keep - previous_keep
            incremental = f"+{gained}" if gained > 0 else "0"
        previous_keep = keep
        tau_label = f"{int(tau)} ms" if float(tau).is_integer() else f"{tau} ms"

        rows.append(
            {
                "阈值τ": tau_label,
                "总 Bag 数": total,
                "可评估 Bag 数": evaluable,
                "可保留": keep,
                "合格率": f"{pass_rate:.1f}%",
                "较前档新增": incremental,
                "超过阈值 Bag 数": over,
                "缺/不可用 Bag 数": missing,
                "总排除": excluded,
            }
        )
    return rows

def is_keepable(stat: BagDelayStat, threshold_ms: float) -> bool:
    """该 bag 在给定阈值下是否可保留（所有检查 topic 均对齐且不缺）。"""
    if stat.missing_reference or stat.missing_target:
        return False
    if not stat.topic_stats:
        return False
    for t in stat.topic_stats:
        if t.missing or t.empty or t.max_delay_ms is None or t.max_delay_ms > threshold_ms:
            return False
    return True


def filter_keepable(
    stats: list[BagDelayStat],
    threshold_ms: float,
) -> list[BagDelayStat]:
    return [s for s in stats if is_keepable(s, threshold_ms)]


def copy_keepable_bags(
    stats: list[BagDelayStat],
    *,
    threshold_ms: float,
    input_root: Path,
    output_folder: str | Path,
) -> Iterator[tuple[float, str, str, int, int]]:
    """复制可保留 bag。yield (progress, log_line, status, copied, failed)。"""
    selected = filter_keepable(stats, threshold_ms)
    out = Path(output_folder).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    total = len(selected)
    if total == 0:
        msg = f"阈值 {threshold_ms:g} ms 下没有可保留的 bag。"
        yield 100.0, msg, msg, 0, 0
        return

    copied = failed = 0
    for i, s in enumerate(selected, start=1):
        src = Path(s.bag_path)
        try:
            rel = src.relative_to(input_root)
        except ValueError:
            rel = Path(src.name)
        dest = out / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            if dest.exists():
                line = f"[{i}/{total}] 已存在，跳过 {dest}"
            else:
                shutil.copy2(src, dest)
                line = f"[{i}/{total}] 已复制 {src.name} -> {dest}"
            copied += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            line = f"[{i}/{total}] 失败 {src.name}: {exc}"
            logger.exception("Copy failed: %s", src)
        status = f"复制中 {i}/{total} → {out}"
        yield round(i / total * 100, 1), line, status, copied, failed

    summary = (
        f"完成。阈值={threshold_ms:g}ms，可保留={total}，"
        f"已复制={copied}，失败={failed}，输出目录={out}"
    )
    yield 100.0, summary, summary, copied, failed


def iter_measure_bags(
    bags: list[Path],
    *,
    reference_topic: str,
    target_topics: list[str],
    should_stop: Callable[[], bool] | None = None,
) -> Iterator[tuple[float, str, list[BagDelayStat]]]:
    """逐个测量 bag，yield (progress, status, stats_so_far)。"""
    stats: list[BagDelayStat] = []
    total = len(bags)
    targets = [t for t in target_topics if t and t != reference_topic]
    if total == 0:
        yield 100.0, "没有可分析的 bag。", stats
        return
    if not targets:
        yield 100.0, "请至少选择一个要检查的 Target Topic。", stats
        return

    for i, bag in enumerate(bags, start=1):
        if should_stop and should_stop():
            yield round((i - 1) / total * 100, 1), "分析已停止。", stats
            return

        yield (
            round((i - 1) / total * 100, 1),
            f"[{i}/{total}] 正在分析 {bag.name} ({len(targets)} topics) ...",
            stats,
        )

        if should_stop and should_stop():
            yield round((i - 1) / total * 100, 1), "分析已停止。", stats
            return

        stats.append(
            measure_bag_delay(
                bag,
                reference_topic=reference_topic,
                target_topics=targets,
            )
        )
        s = stats[-1]
        n_ok = sum(1 for t in s.topic_stats if not t.missing and t.max_delay_ms is not None)
        n_all = len(s.topic_stats)
        if s.missing_reference:
            detail = f"[{i}/{total}] {bag.name} -> 缺少 Reference | {s.message}"
        elif s.missing_target:
            detail = (
                f"[{i}/{total}] {bag.name} -> 缺少Topic "
                f"({n_ok}/{n_all} ok) | {s.message}"
            )
        else:
            detail = (
                f"[{i}/{total}] {bag.name} -> "
                f"worst_max_delay={s.max_delay_ms:.3f}ms ({n_ok}/{n_all} topics)"
            )
        yield round(i / total * 100, 1), detail, stats

    yield (
        100.0,
        f"完成。共分析 {total} 个 bag，每个对照 {len(targets)} 个 Topic。",
        stats,
    )

def analyze_bag(
    bag_path: Path,
    *,
    input_root: Path,
    reference_topic: str,
    target_topic: str,
    threshold_ms: float,
) -> AlignResult:
    try:
        rel = str(bag_path.relative_to(input_root))
    except ValueError:
        rel = bag_path.name

    try:
        info = get_bag_info(bag_path)
        names = {t.topic for t in info.topics}
        missing = [t for t in (reference_topic, target_topic) if t not in names]
        if missing:
            return AlignResult(
                bag_name=bag_path.name,
                bag_path=str(bag_path),
                relative_path=rel,
                status="Missing Topic",
                message=f"Missing: {', '.join(missing)}",
            )

        ref = select_reference_samples(read_timestamps(bag_path, reference_topic))
        tgt = sorted(read_timestamps(bag_path, target_topic))
        if not ref or not tgt:
            return AlignResult(
                bag_name=bag_path.name,
                bag_path=str(bag_path),
                relative_path=rel,
                status="Missing Topic",
                reference_count=len(ref),
                target_count=len(tgt),
                message=(
                    "Topic exists but has 0 usable messages after reference "
                    f"sampling (skip first {REFERENCE_HEAD_TRIM_SECONDS:g}s, "
                    f"drop last {REFERENCE_TAIL_TRIM_FRAMES} frames, "
                    f"stride {REFERENCE_SAMPLE_STRIDE})"
                ),
            )

        delays = [_nearest_delay_ms(tgt, t) for t in ref]
        max_d = max(delays)
        mean_d = sum(delays) / len(delays)
        bad = sum(1 for d in delays if d > threshold_ms)
        status = "Bad" if max_d > threshold_ms else "Good"
        return AlignResult(
            bag_name=bag_path.name,
            bag_path=str(bag_path),
            relative_path=rel,
            status=status,
            max_delay_ms=max_d,
            mean_delay_ms=mean_d,
            bad_frames=bad,
            reference_count=len(ref),
            target_count=len(tgt),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Analyze failed: %s", bag_path)
        return AlignResult(
            bag_name=bag_path.name,
            bag_path=str(bag_path),
            relative_path=rel,
            status="Error",
            message=str(exc),
        )


def analyze_all(
    bags: list[Path],
    *,
    input_root: Path,
    reference_topic: str,
    target_topic: str,
    threshold_ms: float,
    should_stop: Callable[[], bool] | None = None,
) -> Iterator[tuple[float, str, list[AlignResult], str]]:
    """Yield (progress_pct, log_line, results_so_far, status_text)."""
    results: list[AlignResult] = []
    total = len(bags)
    if total == 0:
        yield 100.0, "没有可分析的 bag。", results, "没有 bag"
        return

    for i, bag in enumerate(bags, start=1):
        if should_stop and should_stop():
            yield (
                round((i - 1) / total * 100, 1),
                "分析已停止。",
                results,
                "分析已停止。",
            )
            return

        line = f"[{i}/{total}] 正在分析 {bag.name} ..."
        yield round((i - 1) / total * 100, 1), line, results, line

        if should_stop and should_stop():
            yield (
                round((i - 1) / total * 100, 1),
                "分析已停止。",
                results,
                "分析已停止。",
            )
            return

        r = analyze_bag(
            bag,
            input_root=input_root,
            reference_topic=reference_topic,
            target_topic=target_topic,
            threshold_ms=threshold_ms,
        )
        results.append(r)
        detail = (
            f"[{i}/{total}] {bag.name} -> {r.status}"
            + (
                f" | max={r.max_delay_ms:.3f}ms mean={r.mean_delay_ms:.3f}ms "
                f"bad_frames={r.bad_frames}"
                if r.max_delay_ms is not None
                else (f" | {r.message}" if r.message else "")
            )
        )
        yield round(i / total * 100, 1), detail, results, detail

    good = sum(1 for r in results if r.status == "Good")
    bad = sum(1 for r in results if r.status == "Bad")
    missing = sum(1 for r in results if r.status == "Missing Topic")
    summary = (
        f"完成。总数={total} 合格={good} 不合格={bad} 缺少Topic={missing}"
    )
    yield 100.0, summary, results, summary


def copy_bags(
    results: list[AlignResult],
    *,
    status_filter: str,
    input_root: Path,
    output_folder: str | Path,
) -> Iterator[tuple[float, str, str]]:
    """Yield (progress_pct, log_line, status_text)."""
    selected = [r for r in results if r.status == status_filter]
    out = Path(output_folder).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    total = len(selected)
    if total == 0:
        msg = f"No {status_filter} bags to copy."
        yield 100.0, msg, msg
        return

    copied = failed = 0
    for i, r in enumerate(selected, start=1):
        src = Path(r.bag_path)
        try:
            rel = src.relative_to(input_root)
        except ValueError:
            rel = Path(src.name)
        dest = out / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            if dest.exists():
                line = f"[{i}/{total}] Skip existing {dest}"
            else:
                shutil.copy2(src, dest)
                line = f"[{i}/{total}] Copied {src.name} -> {dest}"
            copied += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            line = f"[{i}/{total}] FAILED {src.name}: {exc}"
            logger.exception("Copy failed: %s", src)
        yield round(i / total * 100, 1), line, line

    summary = f"Done. copied={copied} failed={failed} -> {out}"
    yield 100.0, summary, summary


def result_detail(r: AlignResult) -> str:
    d = asdict(r)
    lines = [f"{k}: {v}" for k, v in d.items()]
    return "\n".join(lines)
