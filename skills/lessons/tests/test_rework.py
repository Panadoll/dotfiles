"""通过真实 CLI 和临时 Git 仓库检查 rework.py 的边界行为。"""
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import re
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

    def write_source(self, name, count=3, value=0):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(f"value{i} = {value}\n" for i in range(count)), encoding="utf-8")
        return path

    def set_dates(self, committer_hours, author_hours=None):
        start = datetime(2026, 10, 1, 8, tzinfo=timezone.utc)
        self.env["GIT_COMMITTER_DATE"] = (start + timedelta(hours=committer_hours)).isoformat()
        self.env["GIT_AUTHOR_DATE"] = (
            start + timedelta(hours=committer_hours if author_hours is None else author_hours)
        ).isoformat()

    def candidate_shas(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return re.findall(r"^  ([0-9a-f]{9}) ", result.stdout, re.MULTILINE)

    def rewritten_counts(self, result):
        return {
            sha: (int(non_test), int(tests))
            for sha, non_test, tests in re.findall(
                r"^    改写 ([0-9a-f]{9}) 的非测试 (\d+) 行、测试 (\d+) 行",
                result.stdout, re.MULTILINE,
            )
        }

    def test_finds_non_ascii_source_path_with_spaces(self):
        source = self.root / "状态 设置.py"
        source.write_text("mode = 0\nlimit = 1\ntimeout = 2\n", encoding="utf-8")
        origin = self.commit("feat: initial settings")
        source.write_text("mode = 1\nlimit = 2\ntimeout = 3\n", encoding="utf-8")
        fix = self.commit("fix: settings")

        result = self.run_script("rework.py", "--last", "2")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"{fix[:9]} fix: settings", result.stdout)
        self.assertEqual(self.rewritten_counts(result), {origin[:9]: (3, 0)})
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
        self.assertEqual(self.rewritten_counts(result), {origin[:9]: (4, 0)})

    def test_counts_every_hunk_of_a_large_rewrite(self):
        source = self.root / "values.py"
        source.write_text("".join(f"v{i} = 0\npad{i} = 1\n" for i in range(100)), encoding="utf-8")
        origin = self.commit("feat: values")
        source.write_text("".join(f"v{i} = 9\npad{i} = 1\n" for i in range(100)), encoding="utf-8")
        self.commit("fix: values")

        result = self.run_script("rework.py", "--last", "2")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.rewritten_counts(result), {origin[:9]: (100, 0)})

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

    def test_window_uses_committer_dates_and_includes_exactly_72_hours(self):
        """71 和 72 小时命中、73 小时不命中，不受相反方向的作者时间影响。"""
        self.set_dates(0, author_hours=-240)
        for hours in (71, 72, 73):
            self.write_source(f"window_{hours}.py")
        origin = self.commit("feat: window fixtures")

        for hours in (71, 72, 73):
            with self.subTest(hours=hours):
                self.set_dates(hours, author_hours=-241 if hours <= 72 else -239)
                self.write_source(f"window_{hours}.py", value=1)
                changed = self.commit(f"fix: window {hours}")
                result = self.run_script("rework.py", "--last", "1")
                expected = [changed[:9]] if hours <= 72 else []
                self.assertEqual(self.candidate_shas(result), expected)
                self.assertEqual(
                    self.rewritten_counts(result), {origin[:9]: (3, 0)} if hours <= 72 else {},
                )

    def test_revert_is_reported_outside_recent_line_window(self):
        """真实 git revert 即使改写已超过时间窗口的代码也必须列出。"""
        self.write_source("app.py")
        self.commit("feat: initial app")
        self.write_source("app.py", value=1)
        changed = self.commit("feat: change app")
        self.set_dates(100)
        self.git("revert", "--no-edit", changed)
        reverted = self.git("rev-parse", "HEAD")

        result = self.run_script("rework.py", "--last", "1")

        self.assertEqual(self.candidate_shas(result), [reverted[:9]])
        self.assertEqual(self.rewritten_counts(result), {})

    def test_recent_revert_appears_once_with_origin_counts(self):
        """同时满足近期改写和回滚的提交只列一次，且保留来源计数。"""
        self.write_source("app.py")
        self.commit("feat: initial app")
        self.write_source("app.py", value=1)
        changed = self.commit("feat: change app")
        self.git("revert", "--no-edit", changed)
        reverted = self.git("rev-parse", "HEAD")

        result = self.run_script("rework.py", "--last", "1")

        self.assertEqual(self.candidate_shas(result), [reverted[:9]])
        self.assertEqual(self.rewritten_counts(result), {changed[:9]: (3, 0)})

    def test_fix_sorting_prioritizes_product_before_test_only_changes(self):
        """修复先于更大的功能改写，同类中非测试行先于仅测试改写。"""
        self.write_source("feature.py", count=12)
        self.write_source("tests/test_app.py", count=6)
        self.write_source("app.py")
        origin = self.commit("feat: initial files")
        self.write_source("feature.py", count=12, value=1)
        feature = self.commit("feat: expand feature")
        self.write_source("tests/test_app.py", count=6, value=1)
        test_fix = self.commit("fix: repair assertions")
        self.write_source("app.py", value=1)
        product_fix = self.commit("fix: repair app")

        result = self.run_script("rework.py", "--since", origin)

        self.assertEqual(
            self.candidate_shas(result), [product_fix[:9], test_fix[:9], feature[:9]],
        )

    def test_counts_product_and_test_files_separately(self):
        """非测试、测试和文档分别计数，测试路径下的文档也不重复计入。"""
        paths = (
            ("src/app.py", 2), ("tests/test_app.py", 3), ("ui/button.spec.ts", 4),
            ("guide.MD", 5), ("fixtures/README.md", 6),
        )
        for name, count in paths:
            self.write_source(name, count=count)
        origin = self.commit("feat: product and tests")
        for name, count in paths:
            self.write_source(name, count=count, value=1)
        changed = self.commit("fix: product and tests")

        result = self.run_script("rework.py", "--last", "1")

        self.assertEqual(self.candidate_shas(result), [changed[:9]])
        self.assertEqual(self.rewritten_counts(result), {origin[:9]: (2, 7)})
        included = self.run_script("rework.py", "--last", "1", "--include-docs")
        self.assertEqual(self.candidate_shas(included), [changed[:9]])
        self.assertEqual(self.rewritten_counts(included), {origin[:9]: (2, 7)})
        self.assertEqual(re.findall(r"、文档 (\d+) 行", included.stdout), ["11"])

    def test_docs_are_opt_in(self):
        """默认排除文档；显式纳入时能追溯大写扩展名的文档。"""
        self.write_source("guide.MD")
        origin = self.commit("docs: initial guide")
        self.write_source("guide.MD", value=1)
        changed = self.commit("docs: rewrite guide")

        excluded = self.run_script("rework.py", "--last", "1")
        included = self.run_script("rework.py", "--last", "1", "--include-docs")

        self.assertEqual(self.candidate_shas(excluded), [])
        self.assertEqual(self.candidate_shas(included), [changed[:9]])
        self.assertEqual(self.rewritten_counts(included), {origin[:9]: (0, 0)})
        self.assertEqual(re.findall(r"、文档 (\d+) 行", included.stdout), ["3"])

    def test_lock_files_do_not_count_toward_candidate_threshold(self):
        """根目录和嵌套锁文件都不计数，也不能把两行代码推过三行阈值。"""
        for name in ("bun.lock", "bun.lockb", "poetry.lock", "Pipfile.lock", "composer.lock", "Gemfile.lock"):
            with self.subTest(lock=name):
                for path in (name, f"nested/{name}"):
                    self.write_source(path)
                self.write_source("app.py", count=2)
                origin = self.commit(f"feat: fixtures for {name}")
                for path in (name, f"nested/{name}"):
                    self.write_source(path, value=1)
                self.write_source("app.py", count=2, value=1)
                changed = self.commit(f"fix: update {name} and app")

                below_threshold = self.run_script("rework.py", "--last", "1")
                self.assertEqual(self.candidate_shas(below_threshold), [])
                included = self.run_script("rework.py", "--last", "1", "--min-lines", "2", "--include-docs")
                self.assertEqual(self.candidate_shas(included), [changed[:9]])
                self.assertEqual(self.rewritten_counts(included), {origin[:9]: (2, 0)})

    def test_since_is_exclusive_and_overrides_last(self):
        """最近 N 个提交包含 HEAD；since 排除起点并覆盖 last 的截断。"""
        commits = []
        for i in range(4):
            self.set_dates(i)
            self.write_source("app.py", value=i)
            commits.append(self.commit(f"fix: app {i}"))

        last = self.run_script("rework.py", "--last", "2", "--max-commits", "2")
        since = self.run_script("rework.py", "--since", commits[0], "--last", "1")

        self.assertEqual(self.candidate_shas(last), [sha[:9] for sha in commits[2:]])
        self.assertEqual(self.candidate_shas(since), [sha[:9] for sha in commits[1:]])

    def test_head_freezes_last_window_after_review_commit(self):
        """复盘提交落地后，指定原扫描头仍保留原候选窗口和标记。"""
        commits = []
        for i in range(3):
            self.set_dates(i)
            self.write_source("app.py", value=i)
            commits.append(self.commit(f"fix: app {i}"))
        first = self.run_script("rework.py", "--last", "2")
        self.set_dates(24)
        self.write_source("LESSONS.md")
        reviewed = self.commit("docs: record lessons")

        repeated = self.run_script("rework.py", "--head", commits[-1][:9], "--last", "2")

        self.assertEqual(self.candidate_shas(first), [sha[:9] for sha in commits[1:]])
        self.assertEqual(self.candidate_shas(repeated), self.candidate_shas(first))
        self.assertEqual(self.rewritten_counts(repeated), self.rewritten_counts(first))
        self.assertIn(
            f"reviewed-through: {commits[-1][:9]} (2026-10-01 10:00 +0000)", repeated.stdout,
        )
        self.assertNotIn(reviewed[:9], repeated.stdout)

    def test_head_bounds_since_and_resolves_commit_tag(self):
        """since 排除起点、止于解析后的 head，仍覆盖 last 且遵守范围上限。"""
        commits = []
        for i in range(4):
            self.set_dates(i)
            self.write_source("app.py", value=i)
            commits.append(self.commit(f"fix: app {i}"))
        self.git("tag", "-a", "review-head", "-m", "Frozen review head", commits[2])

        result = self.run_script(
            "rework.py", "--head", "review-head", "--since", commits[0],
            "--last", "1", "--max-commits", "2",
        )

        self.assertEqual(self.candidate_shas(result), [sha[:9] for sha in commits[1:3]])
        self.assertIn(f"reviewed-through: {commits[2][:9]}", result.stdout)
        self.assertNotIn(commits[3][:9], result.stdout)
        truncated = self.run_script(
            "rework.py", "--head", "review-head", "--since", commits[0], "--max-commits", "1",
        )
        self.assertEqual(truncated.returncode, 2, truncated.stdout + truncated.stderr)
        self.assertNotIn("reviewed-through:", truncated.stdout)

    def test_invalid_head_aborts_without_advancing_marker(self):
        """不存在的 head 或非提交对象必须拒绝，不能退回当前 HEAD。"""
        self.write_source("app.py")
        self.commit("feat: app")
        for head in ("missing-review-head", "HEAD:app.py", "HEAD^{tree}"):
            with self.subTest(head=head):
                result = self.run_script("rework.py", "--head", head)

                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn("rev-parse", result.stderr)
                self.assertNotIn("reviewed-through:", result.stdout)

    def test_merge_commit_is_excluded_but_branch_rewrite_is_scanned(self):
        """合并本身不列为改写，合并进来的分支提交仍受范围检查。"""
        self.write_source("app.py")
        origin = self.commit("feat: initial app")
        self.git("checkout", "-qb", "feature")
        self.set_dates(1)
        self.write_source("app.py", value=1)
        branch = self.commit("fix: branch app")
        self.git("checkout", "-q", "-")
        self.set_dates(2)
        self.write_source("other.py")
        last_non_merge = self.commit("feat: independent change")
        self.git("merge", "-q", "--no-ff", "feature", "-m", "fix: merge feature")
        merged = self.git("rev-parse", "HEAD")

        result = self.run_script("rework.py", "--since", origin, "--max-commits", "2")
        last = self.run_script("rework.py", "--last", "2", "--max-commits", "2")

        self.assertEqual(self.candidate_shas(result), [branch[:9]])
        self.assertEqual(self.candidate_shas(last), [branch[:9]])
        self.assertEqual(self.rewritten_counts(result), {origin[:9]: (3, 0)})
        self.assertIn(f"reviewed-through: {merged[:9]}", result.stdout)
        for scan in (result, last):
            summary = re.fullmatch(
                r"看了 (\d+) 个非合并提交（首个 ([0-9a-f]{9})，末个 ([0-9a-f]{9})），截至扫描头 ([0-9a-f]{9})",
                scan.stdout.splitlines()[0],
            )
            self.assertIsNotNone(summary, scan.stdout)
            self.assertEqual(summary.groups(), ("2", branch[:9], last_non_merge[:9], merged[:9]))

    def test_binary_changes_do_not_invent_line_origins(self):
        """二进制替换没有可归因的文本行，不能误报成返工候选。"""
        binary = self.root / "payload.bin"
        binary.write_bytes(b"\x00old\nfirst\nsecond\n")
        self.commit("feat: binary payload")
        binary.write_bytes(b"\x00new\nthird\nfourth\n")
        changed = self.commit("fix: binary payload")

        result = self.run_script("rework.py", "--last", "1")

        self.assertEqual(self.candidate_shas(result), [])
        self.assertEqual(self.rewritten_counts(result), {})
        self.assertIn(f"reviewed-through: {changed[:9]}", result.stdout)

    def test_whole_file_deletion_counts_old_lines(self):
        """删除整个文件仍应从父提交追溯所有被删除的行。"""
        source = self.write_source("app.py", count=5)
        origin = self.commit("feat: app")
        source.unlink()
        removed = self.commit("fix: remove app")

        result = self.run_script("rework.py", "--last", "1")

        self.assertEqual(self.candidate_shas(result), [removed[:9]])
        self.assertEqual(self.rewritten_counts(result), {origin[:9]: (5, 0)})

    def test_min_lines_is_applied_to_commit_across_multiple_origins(self):
        """跨来源共改写三行也达到阈值，且一个提交列出全部来源一次。"""
        first_file = self.write_source("first.py")
        first = self.commit("feat: first file")
        second_file = self.write_source("second.py")
        second = self.commit("feat: second file")
        first_file.write_text("value0 = 1\nvalue1 = 0\nvalue2 = 0\n", encoding="utf-8")
        second_file.write_text("value0 = 0\nvalue1 = 1\nvalue2 = 1\n", encoding="utf-8")
        changed = self.commit("fix: both files")

        result = self.run_script("rework.py", "--last", "1")

        self.assertEqual(self.candidate_shas(result), [changed[:9]])
        self.assertEqual(self.rewritten_counts(result), {first[:9]: (1, 0), second[:9]: (2, 0)})
        below_threshold = self.run_script("rework.py", "--last", "1", "--min-lines", "4")
        self.assertEqual(self.candidate_shas(below_threshold), [])

    def test_edited_rename_blames_old_path_and_only_changed_lines(self):
        """带改写的重命名沿旧路径归因，不把未改的搬运行计入。"""
        source = self.write_source("旧 设置.py", count=30)
        origin = self.commit("feat: settings")
        self.git("mv", source.name, "新 设置.py")
        moved = self.root / "新 设置.py"
        moved.write_text(
            "".join(f"value{i} = {1 if i < 3 else 0}\n" for i in range(30)), encoding="utf-8",
        )
        changed = self.commit("fix: move and update settings")

        result = self.run_script("rework.py", "--last", "1")

        self.assertEqual(self.candidate_shas(result), [changed[:9]])
        self.assertEqual(self.rewritten_counts(result), {origin[:9]: (3, 0)})
        self.assertIn("旧 设置.py(3)", result.stdout)

    def test_inter_hunk_context_configuration_does_not_count_unchanged_lines(self):
        """用户配置合并相邻 diff 块时，仍只计算实际删除或改写的行。"""
        source = self.write_source("app.py", count=10)
        origin = self.commit("feat: app")
        source.write_text(
            "".join(f"value{i} = {1 if i in (0, 3, 6) else 0}\n" for i in range(10)),
            encoding="utf-8",
        )
        changed = self.commit("fix: sparse values")
        self.git("config", "diff.interHunkContext", "10")

        result = self.run_script("rework.py", "--last", "1")

        self.assertEqual(self.candidate_shas(result), [changed[:9]])
        self.assertEqual(self.rewritten_counts(result), {origin[:9]: (3, 0)})

    def test_sha256_repository_retains_blame_origins(self):
        """Git 的 SHA-256 对象格式也必须保留来源和准确行数。"""
        self.root = self.root / "sha256"
        self.root.mkdir()
        initialized = subprocess.run(
            ["git", "-C", str(self.root), "init", "-q", "--object-format=sha256"],
            env=self.env, text=True, encoding="utf-8", capture_output=True,
        )
        if initialized.returncode != 0 and (
            "unknown option" in initialized.stderr or "unknown hash algorithm" in initialized.stderr
        ):
            self.skipTest("当前 Git 不支持 SHA-256 仓库")
        self.assertEqual(initialized.returncode, 0, initialized.stderr)
        self.write_source("app.py")
        origin = self.commit("feat: app")
        self.write_source("app.py", value=1)
        changed = self.commit("fix: app")

        result = self.run_script("rework.py", "--last", "1")

        self.assertEqual(self.candidate_shas(result), [changed[:9]])
        self.assertEqual(self.rewritten_counts(result), {origin[:9]: (3, 0)})

    def test_blame_failure_aborts_without_advancing_marker(self):
        """真实 blame 调用失败必须报错，不得把不完整扫描标为已复盘。"""
        self.write_source("app.py")
        self.commit("feat: app")
        self.write_source("app.py", value=1)
        self.commit("fix: app")
        self.git("config", "blame.ignoreRevsFile", str(self.root / "missing-ignore-revs"))

        result = self.run_script("rework.py", "--last", "1")

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("git blame", result.stderr)
        self.assertNotIn("reviewed-through:", result.stdout)

    def test_invalid_since_aborts_without_advancing_marker(self):
        """不存在的复盘起点必须拒绝，而不是退回最近提交继续扫描。"""
        self.write_source("app.py")
        self.commit("feat: app")

        result = self.run_script("rework.py", "--since", "missing-review-marker")

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("rev-list", result.stderr)
        self.assertNotIn("reviewed-through:", result.stdout)


if __name__ == "__main__":
    unittest.main()
