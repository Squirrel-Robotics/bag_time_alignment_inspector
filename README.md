# ROS Bag Time Alignment Inspector

一个基于 Gradio 的 ROS1 Bag 批量时间对齐检查工具。它以固定 Reference Topic 为时间主轴，同时检查多个目标 Topic，提供多阈值汇总、逐 Bag 判定、逐 Topic 详情，以及合格 Bag 导出。

## 主要功能

1. 扫描输入目录下的所有 `.bag` 文件，并浏览每个 Bag 的 Topic 元信息。
2. 自动排除 Reference Topic 与配置中的忽略 Topic，支持手动选择检查项。
3. 逐 Bag、逐 Topic 计算相对 Reference 的最大时间偏差。
4. 同时生成多个阈值的总体统计。
5. 按选定阈值动态判定每个 Bag 是否合格，不合格项优先展示；汇总表会列出具体异常 Topic、Max Delay 与阈值相关的坏点时刻。
6. 以卡片和状态色展示每个 Bag 的 Topic 明细，并按当前阈值列出坏点相对时刻、ROS 时间戳和 Delay；可勾选“只看不合格 Bag”过滤独立卡片。
7. 按阈值筛选并复制合格 Bag，保留相对目录结构。

## 项目结构

```text
bag_time_alignment_inspector/
├── app.py                 # Gradio 页面编排、状态和事件处理
├── batch_align_40ms.py    # 固定 40 ms 的命令行批量检查与可选复制脚本
├── core.py                # Bag 读取、时间对齐、筛选与复制
├── presentation.py        # 判定结果转换、DataFrame 与详情 HTML
├── styles.py              # 全局主题、卡片、表格和响应式样式
├── tests/
│   └── test_presentation.py
├── requirements.txt
└── README.md
```

## 判定规则

- 固定 Reference Topic：`/cam_h/color/image_raw/compressed`
- 用户可选择多个目标 Topic。
- 任一目标 Topic 缺失、无消息或 `max_delay > threshold`，该 Bag 判定为不合格。
- 所有选中 Topic 均存在且 `max_delay <= threshold`，该 Bag 才可导出。
- 切换详情阈值时，Bag 汇总判定与逐 Topic 状态会同步刷新。

所有对齐时间均读取自 ROS 消息内部的顶层 `header.stamp`，不使用 bag record timestamp。普通 Topic 使用消息顶层 `header.stamp`；`/tf` 与 `/tf_static` 会遍历 `TFMessage.transforms`，使用每条 transform 自己的 `header.stamp`，不使用 Bag record time。

Reference 时间戳会跳过起始 2.0 秒、删除结尾 10 帧，再每 3 个时间样本采样一次。对每个采样时间戳，使用二分搜索查找目标 Topic 的最近消息，并统计最大时间偏差。没有有效 `header.stamp`、Header 不完整或反序列化失败的已选 Topic 会被视为不可用。

## 依赖

- Python 3.10+
- Gradio
- pandas
- 优先使用 ROS1 `rosbag`；不可用时自动回退到 `rosbags`

```bash
cd /mnt/data/kuavo/bag_time_alignment_inspector
pip install -r requirements.txt
```

服务器上可使用现有 `openpi` 环境：

```bash
conda activate openpi
python app.py
```

浏览器访问：`http://<服务器 IP>:7860`

## 默认配置

| 配置 | 默认值 |
|---|---|
| 输入目录 | `/mnt/data/kuavo/raw_bags` |
| 输出目录 | `/mnt/data/kuavo/tmp` |
| Reference Topic | `/cam_h/color/image_raw/compressed` |
| 阈值 | 以 `core.py` 中的 `DEFAULT_THRESHOLDS_MS` 为准 |

## 测试

```bash
python -m unittest discover -s tests -v
```

测试覆盖展示层的阈值判定、不合格优先排序和 HTML 转义。


## TF 完整手臂链时间戳

- 动态 `/tf` 分别检查 `waist_link` 经 `waist_yaw_link` 到 `zarm_l7_link`、`zarm_r7_link` 的完整 8 段链。
- 每条完整链内所有 transform 使用各自的 `header.stamp`；最大值与最小值之差必须严格小于 5 ms，并同时统计是否严格相同。
- 通过链内检查后，取 8 个 `header.stamp` 的中位数作为整条链的时间戳，再与头部相机 Reference 时间轴做最近邻对齐。
- `/tf_static` 仍按每条 transform 自身的时间戳读取；静态末端执行器链接不参与动态逐帧同步。


## 40 ms 批量对齐脚本

脚本复用网页工具当前的 `header.stamp`、完整 TF 手臂链中位数和最近邻对齐算法，固定阈值为 40 ms。默认排除 `/tf_static`、`/humanoid_wheel/eePoses`、`/manus/left/finger_curl`、`/manus/right/finger_curl`。

只检查并生成 CSV 报告：

```bash
conda activate openpi
python batch_align_40ms.py --input /mnt/data/kuavo/raw_bags --report alignment_report_40ms.csv
```

检查后额外复制合格 Bag（保留相对目录结构）：

```bash
python batch_align_40ms.py --input /mnt/data/kuavo/raw_bags --report alignment_report_40ms.csv --copy-to /mnt/data/kuavo/tmp
```
