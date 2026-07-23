import unittest
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path
from unittest.mock import patch

import core


def make_stat(path: Path) -> core.BagDelayStat:
    return core.BagDelayStat(
        bag_name=path.name,
        bag_path=str(path),
        missing_target=False,
        missing_reference=False,
        max_delay_ms=1.0,
        topic_stats=[
            core.TopicAlignStat(topic="/target", missing=False, max_delay_ms=1.0)
        ],
    )


class FakeFuture:
    def __init__(self, *, result=None, error: Exception | None = None):
        self._result = result
        self._error = error
        self.is_done = False
        self.cancelled = False

    def result(self):
        if self._error is not None:
            raise self._error
        return self._result

    def done(self):
        return self.is_done

    def cancel(self):
        self.cancelled = True
        return True


class FakeExecutor:
    def __init__(self, futures):
        self.futures = list(futures)
        self.submitted = []
        self.shutdown_args = None

    def submit(self, _fn, bag, **_kwargs):
        future = self.futures[len(self.submitted)]
        self.submitted.append(bag)
        return future

    def shutdown(self, *, wait, cancel_futures):
        self.shutdown_args = (wait, cancel_futures)


class ParallelAnalysisTest(unittest.TestCase):
    def test_worker_count_is_normalized(self):
        self.assertEqual(core.normalize_analysis_workers(None), 8)
        self.assertEqual(core.normalize_analysis_workers("invalid"), 8)
        self.assertEqual(core.normalize_analysis_workers(0), 1)
        self.assertEqual(core.normalize_analysis_workers(8.0), 8)
        self.assertEqual(core.normalize_analysis_workers(999), 16)

    def test_parallel_results_keep_input_order(self):
        bags = [
            Path("/tmp/nonexistent-c.bag"),
            Path("/tmp/nonexistent-a.bag"),
            Path("/tmp/nonexistent-b.bag"),
        ]
        updates = list(
            core.iter_measure_bags(
                bags,
                reference_topic="/reference",
                target_topics=["/target"],
                max_workers=2,
            )
        )
        progress, status, stats = updates[-1]
        self.assertEqual(progress, 100.0)
        self.assertIn("2 个并行 worker", status)
        self.assertEqual(
            [stat.bag_path for stat in stats],
            [str(path) for path in bags],
        )

    def test_one_worker_preserves_serial_path(self):
        bag = Path("/tmp/serial.bag")
        with (
            patch.object(core, "measure_bag_delay", return_value=make_stat(bag)),
            patch.object(core, "ProcessPoolExecutor") as pool,
        ):
            updates = list(
                core.iter_measure_bags(
                    [bag],
                    reference_topic="/reference",
                    target_topics=["/target"],
                    max_workers=1,
                )
            )
        pool.assert_not_called()
        self.assertEqual(updates[-1][2][0].bag_path, str(bag))

    def test_out_of_order_completion_is_sorted_and_bounded(self):
        bags = [Path(f"/tmp/bag-{index}.bag") for index in range(3)]
        futures = [FakeFuture(result=make_stat(path)) for path in bags]
        executor = FakeExecutor(futures)

        def fake_wait(pending, **_kwargs):
            selected = futures[1] if not futures[1].is_done else (
                futures[2] if not futures[2].is_done else futures[0]
            )
            self.assertIn(selected, pending)
            selected.is_done = True
            return {selected}, set(pending) - {selected}

        with (
            patch.object(core, "ProcessPoolExecutor", return_value=executor),
            patch.object(core, "wait", side_effect=fake_wait),
        ):
            generator = core.iter_measure_bags(
                bags,
                reference_topic="/reference",
                target_topics=["/target"],
                max_workers=2,
            )
            first_update = next(generator)
            self.assertEqual(first_update[0], 0.0)
            self.assertEqual(len(executor.submitted), 2)
            updates = list(generator)

        self.assertEqual(
            [stat.bag_path for stat in updates[-1][2]],
            [str(path) for path in bags],
        )
        self.assertEqual(executor.shutdown_args, (True, False))

    def test_stop_harvests_done_future_without_refilling(self):
        bags = [Path(f"/tmp/bag-{index}.bag") for index in range(3)]
        futures = [FakeFuture(result=make_stat(path)) for path in bags]
        executor = FakeExecutor(futures)
        stopped = False

        def fake_wait(pending, **_kwargs):
            futures[1].is_done = True
            return {futures[1]}, set(pending) - {futures[1]}

        with (
            patch.object(core, "ProcessPoolExecutor", return_value=executor),
            patch.object(core, "wait", side_effect=fake_wait),
        ):
            generator = core.iter_measure_bags(
                bags,
                reference_topic="/reference",
                target_topics=["/target"],
                should_stop=lambda: stopped,
                max_workers=2,
            )
            next(generator)
            stopped = True
            updates = list(generator)

        self.assertIn("分析已停止", updates[-1][1])
        self.assertEqual(
            [stat.bag_path for stat in updates[-1][2]],
            [str(bags[1])],
        )
        self.assertEqual(len(executor.submitted), 2)
        self.assertEqual(executor.shutdown_args, (True, True))

    def test_broken_pool_marks_every_remaining_bag_failed(self):
        bags = [Path(f"/tmp/bag-{index}.bag") for index in range(4)]
        broken = BrokenProcessPool("worker terminated")
        futures = [
            FakeFuture(error=broken),
            FakeFuture(result=make_stat(bags[1])),
        ]
        executor = FakeExecutor(futures)

        def fake_wait(pending, **_kwargs):
            for future in pending:
                future.is_done = True
            return set(pending), set()

        with (
            patch.object(core, "ProcessPoolExecutor", return_value=executor),
            patch.object(core, "wait", side_effect=fake_wait),
        ):
            updates = list(
                core.iter_measure_bags(
                    bags,
                    reference_topic="/reference",
                    target_topics=["/target"],
                    max_workers=2,
                )
            )

        progress, status, stats = updates[-1]
        self.assertEqual(progress, 100.0)
        self.assertIn("进程池异常", status)
        self.assertEqual(
            [stat.bag_path for stat in stats],
            [str(path) for path in bags],
        )
        self.assertTrue(stats[0].missing_reference)
        self.assertFalse(stats[1].missing_reference)
        self.assertTrue(stats[2].missing_reference)
        self.assertTrue(stats[3].missing_reference)
        self.assertEqual(len(executor.submitted), 2)


if __name__ == "__main__":
    unittest.main()
