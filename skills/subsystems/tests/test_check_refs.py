"""通过真实 CLI 和临时 Git 仓库检查 check_refs.py 的边界行为。"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


class CheckRefsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        self.env.update({
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_AUTHOR_NAME": "Fixture",
            "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
            "GIT_COMMITTER_NAME": "Fixture",
            "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
            "GIT_AUTHOR_DATE": "2026-10-01T08:00:00+00:00",
            "GIT_COMMITTER_DATE": "2026-10-01T08:00:00+00:00",
            "PYTHONIOENCODING": "utf-8",
        })
        self.git("init", "-q")

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.root), *args], env=self.env,
            text=True, encoding="utf-8", capture_output=True, check=True,
        ).stdout.strip()

    def run_script(self, name, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPTS / name), "--root", str(self.root), *args],
            env=self.env, text=True, encoding="utf-8", capture_output=True,
        )

    def test_index_can_link_source_without_verification_stamp(self):
        docs = self.root / "docs" / "subsystems"
        docs.mkdir(parents=True)
        (self.root / "core.py").write_text("def start():\n    return 1\n", encoding="utf-8")
        (docs / "README.md").write_text(
            "# 索引\n\n入口：[`start`](../../core.py)。\n", encoding="utf-8",
        )

        valid = self.run_script("check_refs.py", "--strict")
        self.assertEqual(valid.returncode, 0, valid.stdout + valid.stderr)

        (self.root / "core.py").write_text("def renamed():\n    return 1\n", encoding="utf-8")
        invalid = self.run_script("check_refs.py")
        self.assertEqual(invalid.returncode, 1, invalid.stdout + invalid.stderr)
        self.assertIn("符号 `start` 不在 ../../core.py", invalid.stdout)


if __name__ == "__main__":
    unittest.main()
