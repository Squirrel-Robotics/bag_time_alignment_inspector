"""Presentation transforms for Gradio tables and per-Bag detail cards."""

from __future__ import annotations

import html

import pandas as pd

from core import (
    DEFAULT_EXCLUDE_TOPICS,
    DEFAULT_REF_TOPIC,
    DEFAULT_THRESHOLDS_MS,
    STATUS_ORDER,
    BagDelayStat,
    build_threshold_summary,
    classify_bag_topic_rows,
    is_keepable,
)

TOPIC_HEADERS = ["Topic", "消息类型", "消息数量", "频率 (Hz)"]
SUMMARY_HEADERS = [
    "开头跳过帧数",
    "阈值τ",
    "总 Bag 数",
    "可评估 Bag 数",
    "可保留",
    "合格率",
    "较前档新增",
    "超过阈值 Bag 数",
    "缺/不可用 Bag 数",
    "总排除",
]
BAG_DETAIL_HEADERS = [
    "Bag",
    "判定",
    "判定阈值 (ms)",
    "超过阈值 Topic 数",
    "缺/无消息 Topic 数",
    "合格 Topic 数",
    "总检查 Topic",
    "不合格原因",
]
PER_BAG_TOPIC_HEADERS = ["Topic", "Max Delay (ms)", "状态", "TF 链内同步", "坏点时刻", "说明"]


def empty_topic_df() -> pd.DataFrame:
    return pd.DataFrame(columns=TOPIC_HEADERS)


def empty_summary_df() -> pd.DataFrame:
    return pd.DataFrame(columns=SUMMARY_HEADERS)


def empty_bag_detail_df() -> pd.DataFrame:
    return pd.DataFrame(columns=BAG_DETAIL_HEADERS)


def empty_per_bag_detail_html() -> str:
    return (
        '<div class="bag-details-empty">'
        '分析完成后，这里会按 Bag 展示带状态标记的 Topic 详情。'
        '</div>'
    )

def stats_to_summary_df(
    stats: list[BagDelayStat],
    *,
    head_skip_frames: int | str = "按时间 2s",
) -> pd.DataFrame:
    rows = build_threshold_summary(
        stats,
        thresholds_ms=list(DEFAULT_THRESHOLDS_MS),
    )
    if not rows:
        return empty_summary_df()
    for row in rows:
        row["开头跳过帧数"] = head_skip_frames
    df = pd.DataFrame(rows)
    return df[SUMMARY_HEADERS]


def sampling_stats_to_summary_df(
    stats_by_head_skip: dict[int, list[BagDelayStat]],
) -> pd.DataFrame:
    """把多个开头跳帧配置展开为“跳帧数 × 阈值”报表。"""
    frames = {
        head_skip: stats_to_summary_df(stats, head_skip_frames=head_skip)
        for head_skip, stats in stats_by_head_skip.items()
        if stats
    }
    if not frames:
        return empty_summary_df()

    # 同一阈值下依次展示所有跳帧配置，避免表格首屏只出现一整组“0 帧”。
    rows: list[dict] = []
    row_count = max(len(frame) for frame in frames.values())
    for row_index in range(row_count):
        for frame in frames.values():
            if row_index < len(frame):
                rows.append(frame.iloc[row_index].to_dict())
    return pd.DataFrame(rows)[SUMMARY_HEADERS]


def bag_verdict(
    stat: BagDelayStat,
    *,
    threshold_ms: float,
) -> tuple[str, str, int, int, int]:
    """返回 (判定, 不合格原因, 超过阈值数, 缺/无消息数, 合格数)。"""
    n_over = 0
    n_miss = 0
    n_ok = 0
    for t in stat.topic_stats:
        if t.missing or t.empty:
            n_miss += 1
        elif t.max_delay_ms is None:
            n_miss += 1
        elif t.max_delay_ms > threshold_ms:
            n_over += 1
        else:
            n_ok += 1

    if is_keepable(stat, threshold_ms):
        return "合格", "", n_over, n_miss, n_ok

    reasons: list[str] = []
    if stat.missing_reference:
        reasons.append("缺 Reference / Reference 不可用")
    if n_miss:
        reasons.append(f"{n_miss} 个 Topic 缺少或无消息")
    if n_over:
        reasons.append(f"{n_over} 个 Topic 超过阈值")
    if not stat.topic_stats and not reasons:
        reasons.append("无检查结果")
    if not reasons and stat.max_delay_ms is not None and stat.max_delay_ms > threshold_ms:
        reasons.append(f"Worst max_delay={stat.max_delay_ms:.3f}ms > {threshold_ms:g}ms")
    if not reasons:
        reasons.append(stat.message or "未通过对齐检查")
    return "不合格", "；".join(reasons), n_over, n_miss, n_ok


def stats_to_bag_detail_df(
    stats: list[BagDelayStat],
    *,
    threshold_ms: float,
) -> pd.DataFrame:
    rows = []
    for s in stats:
        verdict, reason, n_over, n_miss, n_ok = bag_verdict(s, threshold_ms=threshold_ms)
        n_all = len(s.topic_stats)
        rows.append(
            {
                "Bag": s.bag_name,
                "判定": verdict,
                "判定阈值 (ms)": threshold_ms,
                "超过阈值 Topic 数": n_over,
                "缺/无消息 Topic 数": n_miss,
                "合格 Topic 数": n_ok,
                "总检查 Topic": n_all,
                "不合格原因": reason,
                "_sort": 0 if verdict == "不合格" else 1,
            }
        )
    if not rows:
        return empty_bag_detail_df()
    df = pd.DataFrame(rows)
    df = df.sort_values(["_sort", "Bag"], kind="stable").drop(columns=["_sort"])
    return df[BAG_DETAIL_HEADERS]



BAD_POINT_DISPLAY_LIMIT = 200


def _render_bad_points(points: list[dict]) -> str:
    if not points:
        return '<span class="bad-point-none">-</span>'

    total = len(points)
    if total <= BAD_POINT_DISPLAY_LIMIT:
        visible = points
        range_note = ""
    else:
        half = BAD_POINT_DISPLAY_LIMIT // 2
        visible = [*points[:half], *points[-half:]]
        range_note = (
            f'<div class="bad-point-note">共 {total} 个；为保证页面流畅，'
            f'显示最早 {half} 个和最晚 {half} 个。</div>'
        )

    chips: list[str] = []
    for point in visible:
        timestamp_ns = int(point["reference_time_ns"])
        seconds, nanoseconds = divmod(timestamp_ns, 1_000_000_000)
        tooltip = f"ROS header.stamp: {seconds}.{nanoseconds:09d} s"
        chips.append(
            '<span class="bad-point-chip" '
            f'title="{html.escape(tooltip, quote=True)}">'
            f'<strong>+{float(point["relative_time_sec"]):.3f} s</strong>'
            f'<em>{float(point["delay_ms"]):.3f} ms</em>'
            '</span>'
        )

    first_time = float(points[0]["relative_time_sec"])
    return (
        '<details class="bad-points">'
        f'<summary>{total} 个坏点 · 首个 +{first_time:.3f} s</summary>'
        '<div class="bad-point-list">'
        + "".join(chips)
        + range_note
        + '</div></details>'
    )

def _tf_chain_sync_text(row: dict) -> str:
    text = str(row.get("TF 链内同步") or "-")
    return html.escape(text)


def stats_to_per_bag_detail_html(
    stats: list[BagDelayStat],
    *,
    check_topics: list[str] | None,
    threshold_ms: float,
) -> str:
    if not stats:
        return empty_per_bag_detail_html()

    check = [t for t in (check_topics or []) if t and t != DEFAULT_REF_TOPIC]
    order = " → ".join(STATUS_ORDER)
    parts = [
        '<div class="bag-details">',
        (
            '<div class="bag-details-toolbar">'
            '<span class="toolbar-label">Topic 详情</span>'
            f'<span>判定阈值 <strong>{threshold_ms:g} ms</strong></span>'
            '<span class="toolbar-divider"></span>'
            f'<span>排序：{html.escape(order)}</span>'
            '</div>'
        ),
    ]

    for index, s in enumerate(stats, start=1):
        rows = classify_bag_topic_rows(
            s,
            check_topics=check,
            threshold_ms=threshold_ms,
            reference_topic=DEFAULT_REF_TOPIC,
            exclude_topics=DEFAULT_EXCLUDE_TOPICS,
        )
        worst = "-" if s.max_delay_ms is None else f"{s.max_delay_ms:.3f} ms"
        verdict, reason, n_over, n_miss, n_ok = bag_verdict(
            s, threshold_ms=threshold_ms
        )
        card_state = "pass" if verdict == "合格" else "fail"
        verdict_icon = "✓" if verdict == "合格" else "!"
        total_checked = n_over + n_miss + n_ok

        parts.extend(
            [
                f'<section class="bag-card bag-card--{card_state}">',
                '<header class="bag-card__header">',
                '<div>',
                f'<div class="bag-card__index">Bag {index:02d}</div>',
                f'<h3 class="bag-card__title">{html.escape(s.bag_name)}</h3>',
                (
                    f'<div class="bag-card__path" title="{html.escape(s.bag_path, quote=True)}">'
                    f'{html.escape(s.bag_path)}</div>'
                ),
                '</div>',
                (
                    f'<span class="verdict verdict--{card_state}">'
                    f'{verdict_icon}&nbsp; {html.escape(verdict)}</span>'
                ),
                '</header>',
                '<div class="bag-metrics">',
                (
                    '<div class="metric"><span class="metric__label">Worst max delay</span>'
                    f'<span class="metric__value">{html.escape(worst)}</span></div>'
                ),
                (
                    '<div class="metric metric--danger"><span class="metric__label">超过阈值</span>'
                    f'<span class="metric__value">{n_over}</span></div>'
                ),
                (
                    '<div class="metric metric--warning"><span class="metric__label">缺少 / 无消息</span>'
                    f'<span class="metric__value">{n_miss}</span></div>'
                ),
                (
                    '<div class="metric metric--success"><span class="metric__label">合格 Topic</span>'
                    f'<span class="metric__value">{n_ok}</span></div>'
                ),
                (
                    '<div class="metric"><span class="metric__label">总检查 Topic</span>'
                    f'<span class="metric__value">{total_checked}</span></div>'
                ),
                '</div>',
            ]
        )
        if reason:
            parts.append(
                '<div class="bag-reason"><strong>不合格原因</strong>'
                f'{html.escape(reason)}</div>'
            )

        parts.extend(
            [
                '<div class="topic-table-wrap">',
                '<table class="topic-table">',
                '<thead><tr>',
                '<th>Topic</th><th>Max Delay (ms)</th><th>状态</th><th>TF 链内同步</th><th>坏点时刻</th><th>说明</th>',
                '</tr></thead><tbody>',
            ]
        )
        for row in rows:
            status = str(row["状态"])
            if status == "超过阈值":
                tone = "danger"
            elif status in {"无消息", "缺少Topic"}:
                tone = "warning"
            elif status == "合格":
                tone = "success"
            elif status == "主时间轴":
                tone = "reference"
            else:
                tone = "muted"
            bad_point_html = _render_bad_points(row.get("坏点时刻") or [])
            parts.extend(
                [
                    f'<tr class="row--{tone}">',
                    f'<td class="topic-name">{html.escape(str(row["Topic"]))}</td>',
                    (
                        '<td class="delay-cell">'
                        f'{html.escape(str(row["Max Delay (ms)"]))}</td>'
                    ),
                    (
                        '<td><span class="topic-status '
                        f'topic-status--{tone}">{html.escape(status)}</span></td>'
                    ),
                    f'<td class="tf-chain-cell">{_tf_chain_sync_text(row)}</td>',
                    f'<td class="bad-point-cell">{bad_point_html}</td>',
                    f'<td>{html.escape(str(row["说明"] or "-"))}</td>',
                    '</tr>',
                ]
            )
        parts.extend(['</tbody></table></div>', '</section>'])

    parts.append('</div>')
    return "".join(parts)

