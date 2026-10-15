"""通过真实 CLI 和临时 Git 仓库检查 rework.py 的边界行为。"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


class ReworkTests(unittest.TestCase):
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

    def commit(self, subject):
        self.git("add", ".")
        self.git("commit", "-q", "-m", subject)
        return self.git("rev-parse", "HEAD")

    def run_script(self, name, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPTS / name), "--root", str(self.root), *args],
            env=self.env, text=True, encoding="utf-8", capture_output=True,
        )

    def test_finds_non_ascii_source_path_with_spaces(self):
        source = self.root / "状态 设置.py"
        source.write_text("mode = 0\nlimit = 1\ntimeout = 2\n", encoding="utf-8")
        origin = self.commit("feat: initial settings")
        source.write_text("mode = 1\nlimit = 2\ntimeout = 3\n", encoding="utf-8")
        fix = self.commit("fix: settings")

        result = self.run_script("rework.py", "--last", "2")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"{fix[:9]} fix: settings", result.stdout)
        self.assertIn(f"改写 {origin[:9]} 的产品代码 3 行、测试 0 行", result.stdout)
        self.assertIn("状态 设置.py(3)", result.stdout)

    def test_refuses_truncated_review_range(self):
        source = self.root / "settings.py"
        commits = []
        for i in range(4):
            source.write_text(f"mode = {i}\nlimit = {i + 1}\ntimeout = {i + 2}\n", encoding="utf-8")
            commits.append(self.commit(f"fix: settings {i}"))

        truncated = self.run_script("rework.py", "--since", commits[0], "--max-commits", "2")
        self.assertEqual(truncated.returncode, 2, truncated.stdout + truncated.stderr)
        self.assertNotIn("reviewed-through:", truncated.stdout)

        complete = self.run_script("rework.py", "--since", commits[0], "--max-commits", "3")
        self.assertEqual(complete.returncode, 0, complete.stderr)
        for i, sha in enumerate(commits[1:], 1):
            self.assertIn(f"{sha[:9]} fix: settings {i}", complete.stdout)
        self.assertIn(f"reviewed-through: {commits[-1][:9]}", complete.stdout)


if __name__ == "__main__":
    unittest.main()
