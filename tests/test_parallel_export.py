import errno
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import core
import app


def keepable_stat(path: Path) -> core.BagDelayStat:
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


class ParallelExportTest(unittest.TestCase):
    def test_flat_parallel_export_puts_all_bags_in_one_folder(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "input"
            output = Path(temp) / "output"
            first = root / "group-a" / "episode_1.bag"
            second = root / "group-b" / "episode_2.bag"
            first.parent.mkdir(parents=True)
            second.parent.mkdir(parents=True)
            first.write_bytes(b"first")
            second.write_bytes(b"second")

            updates = list(
                core.copy_keepable_bags(
                    [keepable_stat(first), keepable_stat(second)],
                    threshold_ms=40.0,
                    input_root=root,
                    output_folder=output,
                    max_workers=2,
                    flat_output=True,
                )
            )

            self.assertEqual((output / first.name).read_bytes(), b"first")
            self.assertEqual((output / second.name).read_bytes(), b"second")
            self.assertFalse((output / "group-a").exists())
            self.assertFalse((output / "group-b").exists())
            self.assertIn("worker=2", updates[-1][2])
            self.assertEqual(updates[-1][3:], (2, 0))

    def test_flat_export_rejects_duplicate_episode_names(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "input"
            output = Path(temp) / "output"
            first = root / "group-a" / "episode_1.bag"
            second = root / "group-b" / "episode_1.bag"
            first.parent.mkdir(parents=True)
            second.parent.mkdir(parents=True)
            first.write_bytes(b"first")
            second.write_bytes(b"second")

            updates = list(
                core.copy_keepable_bags(
                    [keepable_stat(first), keepable_stat(second)],
                    threshold_ms=40.0,
                    input_root=root,
                    output_folder=output,
                    max_workers=8,
                    flat_output=True,
                )
            )

            self.assertFalse((output / "episode_1.bag").exists())
            self.assertTrue(any("同名冲突" in update[1] for update in updates))
            self.assertEqual(updates[-1][3:], (0, 2))

    def test_duplicate_name_aborts_the_whole_flat_export(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "input"
            output = Path(temp) / "output"
            first = root / "group-a" / "episode_1.bag"
            duplicate = root / "group-b" / "episode_1.bag"
            unique = root / "group-c" / "episode_2.bag"
            for path, content in (
                (first, b"first"),
                (duplicate, b"duplicate"),
                (unique, b"unique"),
            ):
                path.parent.mkdir(parents=True)
                path.write_bytes(content)

            updates = list(
                core.copy_keepable_bags(
                    [keepable_stat(first), keepable_stat(duplicate), keepable_stat(unique)],
                    threshold_ms=40.0,
                    input_root=root,
                    output_folder=output,
                    max_workers=8,
                    flat_output=True,
                )
            )

            self.assertFalse((output / unique.name).exists())
            self.assertIn("导出已取消", updates[-1][2])
            self.assertEqual(updates[-1][3:], (0, 3))

    def test_flat_export_rejects_case_only_duplicate_names(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "input"
            output = Path(temp) / "output"
            first = root / "group-a" / "episode_1.bag"
            second = root / "group-b" / "EPISODE_1.BAG"
            first.parent.mkdir(parents=True)
            second.parent.mkdir(parents=True)
            first.write_bytes(b"first")
            second.write_bytes(b"second")

            updates = list(
                core.copy_keepable_bags(
                    [keepable_stat(first), keepable_stat(second)],
                    threshold_ms=40.0,
                    input_root=root,
                    output_folder=output,
                    max_workers=8,
                    flat_output=True,
                )
            )

            self.assertFalse((output / first.name).exists())
            self.assertFalse((output / second.name).exists())
            self.assertEqual(updates[-1][3:], (0, 2))

    def test_existing_destination_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "input"
            output = Path(temp) / "output"
            source = root / "episode_1.bag"
            destination = output / source.name
            source.parent.mkdir(parents=True)
            destination.parent.mkdir(parents=True)
            source.write_bytes(b"new")
            destination.write_bytes(b"existing")

            updates = list(
                core.copy_keepable_bags(
                    [keepable_stat(source)],
                    threshold_ms=40.0,
                    input_root=root,
                    output_folder=output,
                    max_workers=8,
                    flat_output=True,
                )
            )

            self.assertEqual(destination.read_bytes(), b"existing")
            self.assertIn("已存在未覆盖=1", updates[-1][2])
            self.assertEqual(updates[-1][3:], (0, 0))

    def test_destination_created_during_copy_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "input"
            output = Path(temp) / "output"
            source = root / "episode_1.bag"
            destination = output / source.name
            source.parent.mkdir(parents=True)
            output.mkdir(parents=True)
            source.write_bytes(b"source")
            real_copy2 = core.shutil.copy2

            def copy_then_race(src, dst):
                result = real_copy2(src, dst)
                destination.write_bytes(b"racer")
                return result

            with mock.patch.object(core.shutil, "copy2", side_effect=copy_then_race):
                updates = list(
                    core.copy_keepable_bags(
                        [keepable_stat(source)],
                        threshold_ms=40.0,
                        input_root=root,
                        output_folder=output,
                        max_workers=8,
                        flat_output=True,
                    )
                )

            self.assertEqual(destination.read_bytes(), b"racer")
            self.assertFalse(list(output.glob("*.part")))
            self.assertIn("已存在未覆盖=1", updates[-1][2])

    def test_failed_copy_leaves_no_destination_or_partial_bag(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "input"
            output = Path(temp) / "output"
            source = root / "episode_1.bag"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"source")

            with mock.patch.object(core.shutil, "copy2", side_effect=OSError("boom")):
                updates = list(
                    core.copy_keepable_bags(
                        [keepable_stat(source)],
                        threshold_ms=40.0,
                        input_root=root,
                        output_folder=output,
                        max_workers=8,
                        flat_output=True,
                    )
                )

            self.assertFalse((output / source.name).exists())
            self.assertFalse(list(output.glob("*.part")))
            self.assertEqual(updates[-1][3:], (0, 1))

    def test_filesystem_without_hard_links_uses_exclusive_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "input"
            output = Path(temp) / "output"
            source = root / "episode_1.bag"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"source")

            link_error = OSError(errno.EOPNOTSUPP, "hard links unsupported")
            with mock.patch.object(core.os, "link", side_effect=link_error):
                updates = list(
                    core.copy_keepable_bags(
                        [keepable_stat(source)],
                        threshold_ms=40.0,
                        input_root=root,
                        output_folder=output,
                        max_workers=8,
                        flat_output=True,
                    )
                )

            self.assertEqual((output / source.name).read_bytes(), b"source")
            self.assertFalse(list(output.glob("*.part")))
            self.assertEqual(updates[-1][3:], (1, 0))

    def test_invalid_output_path_returns_ui_error(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "input"
            source = root / "episode_1.bag"
            invalid_output = Path(temp) / "not-a-directory"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"source")
            invalid_output.write_bytes(b"file")
            state = {
                "stats": [keepable_stat(source)],
                "input_folder": str(root),
            }

            updates = list(app.do_export("40 ms", str(invalid_output), state, 8))

            self.assertEqual(len(updates), 1)
            self.assertEqual(updates[0][0], 0)
            self.assertIn("无法使用输出目录", updates[0][2])

    def test_legacy_relative_output_remains_available(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "input"
            output = Path(temp) / "output"
            source = root / "group-a" / "episode_1.bag"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"bag")

            list(
                core.copy_keepable_bags(
                    [keepable_stat(source)],
                    threshold_ms=40.0,
                    input_root=root,
                    output_folder=output,
                    max_workers=1,
                    flat_output=False,
                )
            )

            self.assertEqual(
                (output / "group-a" / "episode_1.bag").read_bytes(),
                b"bag",
            )


if __name__ == "__main__":
    unittest.main()
