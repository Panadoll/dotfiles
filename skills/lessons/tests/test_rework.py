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

    def test_removed_line_starting_with_double_dash_keeps_later_hunks(self):
        source = self.root / "init.lua"
        source.write_text("-- header\nk = 0\nk = 0\nk = 0\nx = 1\ny = 2\nz = 3\n", encoding="utf-8")
        origin = self.commit("feat: init")
        source.write_text("k = 0\nk = 0\nk = 0\nx = 10\ny = 20\nz = 30\n", encoding="utf-8")
        self.commit("fix: init")

        result = self.run_script("rework.py", "--last", "2")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"改写 {origin[:9]} 的产品代码 4 行", result.stdout)

    def test_counts_every_hunk_of_a_large_rewrite(self):
        source = self.root / "values.py"
        source.write_text("".join(f"v{i} = 0\npad{i} = 1\n" for i in range(100)), encoding="utf-8")
        origin = self.commit("feat: values")
        source.write_text("".join(f"v{i} = 9\npad{i} = 1\n" for i in range(100)), encoding="utf-8")
        self.commit("fix: values")

        result = self.run_script("rework.py", "--last", "2")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"改写 {origin[:9]} 的产品代码 100 行", result.stdout)

    def test_moved_file_is_not_rework(self):
        (self.root / "old").mkdir()
        (self.root / "old" / "tool.py").write_text("".join(f"a{i} = {i}\n" for i in range(20)), encoding="utf-8")
        self.commit("feat: tool")
        self.git("mv", "old", "new")
        moved = self.commit("refactor: move tool")

        result = self.run_script("rework.py", "--last", "2")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(f"{moved[:9]} refactor", result.stdout)

    def test_marker_is_head_so_merged_branch_is_not_rescanned(self):
        (self.root / "a.py").write_text("a = 1\n", encoding="utf-8")
        base = self.commit("feat: a")
        self.git("checkout", "-qb", "feat")
        (self.root / "b.py").write_text("b = 1\n", encoding="utf-8")
        branch = self.commit("feat: b")
        self.git("checkout", "-q", "-")
        (self.root / "c.py").write_text("c = 1\n", encoding="utf-8")
        self.commit("feat: c")
        self.git("merge", "-q", "--no-ff", "feat", "-m", "merge feat")
        head = self.git("rev-parse", "HEAD")

        first = self.run_script("rework.py", "--since", base)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn(f"reviewed-through: {head[:9]}", first.stdout)

        second = self.run_script("rework.py", "--since", head[:9])
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("看了 0 个非合并提交", second.stdout)
        self.assertNotIn(branch[:9], second.stdout)

    def test_refuses_shallow_clone(self):
        source = self.root / "settings.py"
        for i in range(2):
            source.write_text(f"mode = {i}\n", encoding="utf-8")
            self.commit(f"feat: settings {i}")
        clone_dir = tempfile.TemporaryDirectory()
        self.addCleanup(clone_dir.cleanup)
        shallow = Path(clone_dir.name) / "clone"
        subprocess.run(["git", "clone", "-q", "--depth", "1", self.root.as_uri(), str(shallow)],
                       env=self.env, capture_output=True, check=True)

        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "rework.py"), "--root", str(shallow)],
            env=self.env, text=True, encoding="utf-8", capture_output=True,
        )

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertNotIn("reviewed-through:", result.stdout)


if __name__ == "__main__":
    unittest.main()
