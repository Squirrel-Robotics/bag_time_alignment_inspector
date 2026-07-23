import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app


class MultiInputFoldersTest(unittest.TestCase):
    def test_parse_newlines_semicolons_and_deduplicate(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            value = f" {first}\n{second};{first}\n"
            self.assertEqual(
                app.parse_input_folders(value),
                [Path(first).resolve(), Path(second).resolve()],
            )

    def test_append_current_folder_only_once(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            value = app.append_input_folder(first, second)
            value = app.append_input_folder(value, second)
            self.assertEqual(
                value.splitlines(),
                [str(Path(first).resolve()), str(Path(second).resolve())],
            )

    def test_collect_bags_deduplicates_overlapping_roots(self):
        root_a = Path("/data/a")
        root_b = Path("/data/a/sub")
        shared = Path("/data/a/sub/shared.bag")
        only_a = Path("/data/a/only-a.bag")
        with patch.object(
            app,
            "scan_bags",
            side_effect=[[only_a, shared], [shared]],
        ):
            bags, counts = app.collect_bags([root_a, root_b])
        self.assertEqual(bags, [only_a, shared])
        self.assertEqual(counts, [(root_a, 2), (root_b, 1)])

    def test_multiple_roots_use_absolute_bag_labels(self):
        bag = Path("/data/a/example.bag")
        choices, choice_map = app._bag_choices(
            [bag], [Path("/data/a"), Path("/data/b")]
        )
        self.assertEqual(choices, [str(bag)])
        self.assertEqual(choice_map[str(bag)], str(bag))


if __name__ == "__main__":
    unittest.main()
