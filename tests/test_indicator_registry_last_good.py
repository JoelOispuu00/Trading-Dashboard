import os
import sys
import tempfile
import unittest


# Allow `import core.*` like the app does when running `python app/main.py`.
REPO_ROOT = os.path.dirname(os.path.dirname(__file__))
APP_DIR = os.path.join(REPO_ROOT, "app")
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)


from core.indicator_registry import discover_indicators


class IndicatorRegistryLastGoodTests(unittest.TestCase):
    def test_keep_last_good_on_syntax_error(self):
        with tempfile.TemporaryDirectory(prefix="ind_test_") as root:
            p = os.path.join(root, "foo.py")
            with open(p, "w", encoding="utf-8") as f:
                f.write(
                    "def schema():\n"
                    "    return {'id':'foo','name':'Foo','inputs':{},'pane':'price'}\n"
                )
            inds = discover_indicators(root)
            self.assertEqual(len(inds), 1)
            self.assertEqual(inds[0].indicator_id, "foo")
            self.assertIsNone(getattr(inds[0], "load_error", None))

            # Break the file.
            with open(p, "w", encoding="utf-8") as f:
                f.write("def schema():\n    return {\n")  # syntax error

            inds2 = discover_indicators(root)
            self.assertEqual(len(inds2), 1)
            self.assertEqual(inds2[0].indicator_id, "foo")
            self.assertIsNotNone(getattr(inds2[0], "load_error", None))


if __name__ == "__main__":
    unittest.main()

