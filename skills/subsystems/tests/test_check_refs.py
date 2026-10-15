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

    def write_file(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def commit(self, subject="fixture: initial state"):
        self.git("add", ".")
        self.git("commit", "-q", "-m", subject)
        return self.git("rev-parse", "HEAD")

    def page(self, text, name="core"):
        self.write_file("docs/subsystems/README.md", f"# 索引\n\n[{name}]({name}.md)\n")
        return self.write_file(f"docs/subsystems/{name}.md", text)

    def check(self, code=0, *args):
        result = self.run_script("check_refs.py", *args)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return result

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

    def test_relative_file_and_markdown_anchor_exist(self):
        """已有文件与中文重复标题锚点可以同时解析。"""
        self.write_file("src/core.py", "def start(): pass\n")
        self.write_file("guide.md", "# 状态转换\n\n## 状态转换\n")
        self.page("[源码](../../src/core.py)\n[标题](../../guide.md#状态转换-1)\n")
        self.check()

    def test_missing_relative_file_fails(self):
        """不存在的相对链接是失败，而不是过期提示。"""
        self.page("[源码](../../src/missing.py)\n")
        result = self.check(1)
        self.assertIn("../../src/missing.py", result.stdout)

    def test_missing_local_and_remote_anchors_fail(self):
        """本页与其他 Markdown 页的不存在锚点都必须失败。"""
        self.write_file("guide.md", "# Existing\n")
        self.page("# Existing\n[本页](#missing)\n[其他页](../../guide.md#missing)\n")
        result = self.check(1)
        self.assertIn("本页锚点不存在 #missing", result.stdout)
        self.assertIn("../../guide.md#missing", result.stdout)

    def test_existing_local_anchor_passes(self):
        """中文本页锚点与重复标题后缀有效。"""
        self.page("# 状态转换\n## 状态转换\n[本页](#状态转换-1)\n")
        self.check()

    def test_percent_encoded_links_and_index_pages(self):
        """标准 URL 编码不能使已有中文空格文件被误报缺失。"""
        self.write_file("状态 设置.py", "value = 1\n")
        self.write_file("docs/subsystems/状态 设置.md", "[源码](../../%E7%8A%B6%E6%80%81%20%E8%AE%BE%E7%BD%AE.py)\n")
        self.write_file("docs/subsystems/README.md", "[状态](%E7%8A%B6%E6%80%81%20%E8%AE%BE%E7%BD%AE.md)\n")
        self.check()

    def test_percent_encoded_anchor_passes(self):
        """锚点的百分号编码与未编码中文标题等价。"""
        self.page("# 状态\n[跳转](#%E7%8A%B6%E6%80%81)\n")
        self.check()

    def test_angle_bracket_destination_checks_spaces(self):
        """尖括号包裹的空格路径仍需做存在性检查。"""
        self.write_file("状态 设置.py", "value = 1\n")
        self.page("[源码](<../../状态 设置.py>)\n")
        self.check()
        self.page("[源码](<../../不存在 设置.py>)\n")
        result = self.check(1)
        self.assertIn("不存在 设置.py", result.stdout)

    def test_source_line_suffix_checks_local_targets(self):
        """同目录文件的行号后缀不能被误判为 URL scheme。"""
        self.write_file("docs/subsystems/core.py", "def start(): pass\n")
        self.page("[`start`](core.py:1-2)\n")
        self.check()
        self.page("[源码](missing.py:1)\n")
        result = self.check(1)
        self.assertIn("missing.py:1", result.stdout)

    def test_index_accepts_page_line_suffix(self):
        """索引与普通链接对行号后缀使用相同解析规则。"""
        self.page("# Core\n")
        self.write_file("docs/subsystems/README.md", "[Core](core.md:1)\n")
        self.check()

    def test_fenced_examples_are_ignored(self):
        """普通代码块中的示例链接和伪验证段落不参与检查。"""
        self.page("```md\n[示例](missing.py)\n验证：`test_missing`\n```\n")
        self.check()

    def test_fence_closer_must_match_character_and_length(self):
        """另一种围栏字符或过短围栏不能关闭外层示例块。"""
        for opening, inner in (("````md", "```"), ("~~~md", "```")):
            with self.subTest(opening=opening):
                closer = opening.removesuffix("md")
                self.page(f"{opening}\n{inner}\n[示例](missing.py)\n{closer}\n")
                self.check()

    def test_headings_inside_code_do_not_create_anchors(self):
        """代码块中的标题不应使错误锚点通过。"""
        self.page("```md\n# Hidden\n```\n[跳转](#hidden)\n")
        self.check(1)

    def test_all_symbol_binding_forms_detect_renames(self):
        """三类符号绑定及中文连接词都必须在目标文件内命中。"""
        forms = [
            "[`start`](../../core.py)",
            "`start` in [源码](../../core.py)",
            "`start` 在 [源码](../../core.py)",
            "`start` 位于 [源码](../../core.py)",
            "[源码](../../core.py) `start`",
            "[源码](../../core.py) -> `start`",
            "[源码](../../core.py) → `start`",
            "[源码](../../core.py): `start`",
            "[源码](../../core.py)：`start`",
        ]
        for binding in forms:
            with self.subTest(binding=binding):
                self.page(binding + "\n")
                self.write_file("core.py", "def start(): pass\n")
                self.check()
                self.write_file("core.py", "def restart(): pass\ndef start_extra(): pass\n")
                result = self.check(1)
                self.assertIn("符号 `start`", result.stdout)

    def test_symbol_in_other_file_does_not_satisfy_binding(self):
        """全仓库其他文件的同名符号不能挽救错误绑定。"""
        self.write_file("core.py", "value = 1\n")
        self.write_file("other.py", "def start(): pass\n")
        self.page("[`start`](../../core.py)\n")
        self.check(1)

    def test_directory_symbol_binding(self):
        """目录绑定递归查找普通源码，但不把缓存目录当作证据。"""
        self.write_file("src/nested/core.py", "def start(): pass\n")
        self.page("[`start`](../../src/)\n")
        self.check()
        self.write_file("src/nested/core.py", "def renamed(): pass\n")
        self.write_file("src/node_modules/unused.py", "def start(): pass\n")
        self.check(1)

    def test_filename_link_label_is_not_a_symbol(self):
        """文件名标签只检查路径，不要求源码包含自身文件名。"""
        self.write_file("core.py", "value = 1\n")
        self.page("[`core.py`](../../core.py)\n")
        self.check()

    def test_verification_paragraph_finds_names_files_and_prefixes(self):
        """验证段落支持测试名、真实文件与星号前缀。"""
        self.write_file("tests/test_core.py", "def test_start(): pass\ndef test_stop_fast(): pass\n")
        self.page("验证：`test_start`、`tests/test_core.py`、`test_stop*`。\n")
        self.check()

    def test_verification_paragraph_reports_missing_test(self):
        """点名的测试不存在时返回失败。"""
        self.page("**验证：** `test_missing`\n")
        result = self.check(1)
        self.assertIn("test_missing", result.stdout)

    def test_verification_section_ignores_non_test_identifiers(self):
        """章节中的一般源码标识符不是测试，离开章节后也不扫描。"""
        self.write_file("tests/test_core.py", "def test_start(): pass\n")
        self.page("## 验证\n`StateManager` 与 `test_start`。\n## 其他\n`test_missing`\n")
        self.check()

    def test_verification_nested_heading_keeps_section_active(self):
        """验证章节的子标题不能隐藏不存在的测试名。"""
        self.page("## 验证\n### 单元测试\n`test_missing`\n")
        result = self.check(1)
        self.assertIn("test_missing", result.stdout)

    def test_verification_does_not_find_tests_in_documentation(self):
        """文档里的引用文字不构成测试存在的证据。"""
        self.write_file("README.md", "`test_missing`\n")
        self.page("验证：`test_missing`\n")
        self.check(1)

    def test_exact_test_name_is_not_satisfied_by_another_requested_name(self):
        """同一批查询中的较长测试名不能满足较短的精确名称。"""
        self.write_file("tests/test_core.py", "def test_start_more(): pass\n")
        self.page("验证：`test_start`、`test_start_more`\n")
        result = self.check(1)
        self.assertIn("`test_start`", result.stdout)
        self.assertNotIn("验证项 `test_start_more`", result.stdout)

    def test_test_file_mention_is_not_a_real_file(self):
        """源码字符串中提到缺失路径不能当作真实测试文件。"""
        self.write_file("notes.py", 'example = "tests/test_missing.py"\n')
        self.page("验证：`tests/test_missing.py`\n")
        self.check(1)

    def test_non_ascii_test_file_is_found_in_git_inventory(self):
        """Git 默认引用中文路径时也能按真实文件名查找测试。"""
        self.write_file("tests/测试状态.py", "value = 1\n")
        self.page("验证：`tests/测试状态.py`\n")
        self.check()

    def test_file_prefix_matches_relative_path(self):
        """文件前缀应匹配相对路径，而不是只对 basename 生效。"""
        self.write_file("tests/test_core.py", "value = 1\n")
        self.page("验证：`tests/test_*`\n")
        self.check()

    def test_verification_command_checks_real_paths(self):
        """验证命令中真实存在和缺失的测试文件分别通过与失败。"""
        self.write_file("tests/test_core.py", "value = 1\n")
        self.page("## 验证\n```sh\npytest tests/test_core.py\n```\n")
        self.check()
        self.page("## 验证\n```sh\npytest tests/test_missing.py\n```\n")
        result = self.check(1)
        self.assertIn("tests/test_missing.py", result.stdout)

    def test_verification_commands_under_nested_heading(self):
        """子标题下的验证代码块仍须检查文件存在性。"""
        self.page("## 验证\n### 单元测试\n```sh\npytest tests/test_missing.py\n```\n")
        self.check(1)

    def test_verification_command_cd_into_subdirectory(self):
        """验证代码块内切换工作目录后按新目录检查路径。"""
        self.write_file("service/tests/test_core.py", "value = 1\n")
        self.page("## 验证\n```sh\ncd service\npytest tests/test_core.py\n```\n")
        self.check()

    def test_verification_command_rejects_wrong_working_directory(self):
        """其他目录里的同名文件不能使实际无法执行的命令通过。"""
        self.write_file("other/tests/test_core.py", "value = 1\n")
        self.write_file("service/config.py", "value = 1\n")
        self.page("## 验证\n```sh\ncd service\npytest tests/test_core.py\n```\n")
        self.check(1)

    def test_verification_command_tracks_sequential_cd(self):
        """连续 cd 和上级目录引用必须组合成实际工作目录。"""
        self.write_file("service/tests/test_core.py", "value = 1\n")
        self.write_file("service/tools/run.py", "value = 1\n")
        self.page("## 验证\n```sh\ncd service && cd tools\npytest ../tests/test_core.py\n```\n")
        self.check()

    def test_quoted_command_path_with_spaces_and_chinese(self):
        """带引号的中文空格测试路径不能截取成错误 basename。"""
        self.write_file("tests/状态 测试.py", "value = 1\n")
        self.page("## 验证\n```sh\npytest 'tests/状态 测试.py'\n```\n")
        self.check()

    def test_index_must_list_every_page(self):
        """完整索引通过；删掉某页的索引链接必须失败。"""
        self.page("# Core\n")
        self.write_file("docs/subsystems/other.md", "# Other\n")
        self.write_file("docs/subsystems/README.md", "[Core](core.md)\n[Other](other.md)\n")
        self.check()
        self.write_file("docs/subsystems/README.md", "[Core](core.md)\n")
        result = self.check(1)
        self.assertIn("索引没有列出 other.md", result.stdout)

    def test_index_link_to_missing_page_fails(self):
        """索引不能链接到不存在的参考页。"""
        self.write_file("docs/subsystems/README.md", "[缺失](missing.md)\n")
        self.check(1)

    def test_index_code_example_does_not_count_as_listing(self):
        """示例代码里的链接不算真正列入索引。"""
        self.page("# Core\n")
        self.write_file("docs/subsystems/README.md", "```md\n[Core](core.md)\n```\n")
        result = self.check(1)
        self.assertIn("索引没有列出 core.md", result.stdout)

    def test_missing_index_fails_but_single_doc_is_independent(self):
        """全量检查要求索引；单页模式不受其他页面和索引影响。"""
        self.write_file("docs/subsystems/core.md", "# Core\n")
        self.write_file("docs/subsystems/broken.md", "[缺失](missing.py)\n")
        self.check(1)
        self.check(0, "--doc", "core")
        self.check(0, "--doc", "core.md")
        self.check(1, "--doc", "core", "--doc", "broken")

    def test_single_index_still_checks_source_symbols(self):
        """显式选择索引页仍检查它的源码绑定。"""
        self.write_file("core.py", "def start(): pass\n")
        self.write_file("docs/subsystems/README.md", "[`missing`](../../core.py)\n")
        self.check(1, "--doc", "README")

    def test_alternative_index_and_explicit_docs_directory(self):
        """支持 index.md 和从仓库根解析的 --docs 路径。"""
        self.write_file("reference/index.md", "[Core](core.md)\n")
        self.write_file("reference/core.md", "# Core\n")
        self.check(0, "--docs", "reference")

    def test_usage_errors_return_two(self):
        """缺目录、缺指定参考与未知参数均属于用法错误。"""
        self.check(2)
        self.page("# Core\n")
        self.check(2, "--doc", "missing")
        self.check(2, "--unknown-option")

    def test_fresh_stamp_and_unrelated_source_change_pass_strict(self):
        """已验证源文件未变时，即使其他文件变化也不提示过期。"""
        self.write_file("core.py", "value = 1\n")
        sha = self.commit()
        self.page(f"<!-- verified-at: {sha} -->\n[源码](../../core.py)\n")
        self.check(0, "--strict")
        self.write_file("unrelated.py", "value = 2\n")
        self.commit("fixture: unrelated file")
        self.check(0, "--strict")

    def test_source_drift_warns_and_strict_fails(self):
        """只有源码未提交或单独提交时提示，并标明变化来源。"""
        self.write_file("core.py", "value = 1\n")
        sha = self.commit()
        self.page(f"<!-- verified-at: {sha[:9]} -->\n[源码](../../core.py)\n")
        self.commit("fixture: reference baseline")
        self.write_file("core.py", "value = 2\n")
        for staged in (False, True):
            with self.subTest(staged=staged):
                if staged:
                    self.git("add", "core.py")
                normal = self.check()
                self.assertIn("core.py", normal.stdout)
                self.assertIn("未提交", normal.stdout)
                self.check(1, "--strict")
        changed = self.commit("fixture: changed source")
        normal = self.check()
        self.assertIn(changed[:7], normal.stdout)
        self.check(1, "--strict")

    def test_code_and_reference_in_same_commit_pass_strict(self):
        """复现提交前 audit：标记指向旧 HEAD，代码与本页一起提交。"""
        self.write_file("core.py", "def start(): return 1\n")
        source_sha = self.commit()
        self.page(f"<!-- verified-at: {source_sha} -->\n[`start`](../../core.py)\n")
        baseline = self.commit("fixture: initialize reference")
        self.write_file("core.py", "def renamed(): return 1\n")
        self.page(f"<!-- verified-at: {baseline} -->\n[`renamed`](../../core.py)\n")
        self.commit("fixture: rename source with reference")
        self.check(0, "--strict")

    def test_later_source_only_commit_still_warns(self):
        """同提交复核只覆盖该提交，之后单改源码仍需报告其哈希。"""
        self.write_file("core.py", "value = 1\n")
        sha = self.commit()
        self.page(f"<!-- verified-at: {sha} -->\n[源码](../../core.py)\n")
        self.commit("fixture: reference baseline")
        self.write_file("core.py", "value = 2\n")
        self.page(f"<!-- verified-at: {sha} -->\n[源码](../../core.py)\n值为 2。\n")
        reviewed = self.commit("fixture: reviewed source change")
        self.write_file("core.py", "value = 3\n")
        changed = self.commit("fixture: source without review")
        normal = self.check()
        self.assertIn("core.py", normal.stdout)
        self.assertIn(changed[:7], normal.stdout)
        self.assertNotIn(reviewed[:7], normal.stdout)
        self.check(1, "--strict")

    def test_worktree_source_and_reference_changes_pass_strict(self):
        """暂存与未暂存可混合；只刷新标记也算本页随源码复核。"""
        self.write_file("core.py", "value = 1\n")
        sha = self.commit()
        self.page(f"<!-- verified-at: {sha} -->\n[源码](../../core.py)\n")
        baseline = self.commit("fixture: reference baseline")
        for value, (stage_source, stage_doc) in enumerate(
                ((False, False), (True, False), (False, True), (True, True)), 2):
            with self.subTest(stage_source=stage_source, stage_doc=stage_doc):
                self.write_file("core.py", f"value = {value}\n")
                self.page(f"<!-- verified-at: {baseline} -->\n[源码](../../core.py)\n")
                if stage_source:
                    self.git("add", "core.py")
                if stage_doc:
                    self.git("add", "docs/subsystems/core.md")
                self.check(0, "--strict")
                baseline = self.commit("fixture: reviewed worktree changes")

    def test_new_reference_covers_uncommitted_source_change(self):
        """init 新建的未跟踪参考页同样覆盖未提交源码变化。"""
        self.write_file("core.py", "value = 1\n")
        sha = self.commit()
        self.write_file("core.py", "value = 2\n")
        self.page(f"<!-- verified-at: {sha} -->\n[源码](../../core.py)\n")
        self.check(0, "--strict")

    def test_worktree_review_does_not_hide_earlier_source_only_commit(self):
        """本页的工作区修改不能追认基线之后已独立提交的源码。"""
        self.write_file("core.py", "value = 1\n")
        sha = self.commit()
        self.page(f"<!-- verified-at: {sha} -->\n[源码](../../core.py)\n")
        self.commit("fixture: reference baseline")
        self.write_file("core.py", "value = 2\n")
        changed = self.commit("fixture: source without review")
        self.write_file("core.py", "value = 3\n")
        self.page(f"<!-- verified-at: {sha} -->\n[源码](../../core.py)\n值为 3。\n")
        normal = self.check()
        self.assertIn(changed[:7], normal.stdout)
        self.assertNotIn("未提交", normal.stdout)
        self.check(1, "--strict")

    def test_other_reference_change_does_not_count_as_review(self):
        """同一提交改的是其他页时，不能替本页免除源码复核。"""
        self.write_file("core.py", "value = 1\n")
        sha = self.commit()
        self.page(f"<!-- verified-at: {sha} -->\n[源码](../../core.py)\n")
        self.commit("fixture: reference baseline")
        self.write_file("core.py", "value = 2\n")
        self.write_file("docs/subsystems/other.md", f"<!-- verified-at: {sha} -->\n其他页。\n")
        changed = self.commit("fixture: source with unrelated reference")
        normal = self.check(0, "--doc", "core")
        self.assertIn(changed[:7], normal.stdout)
        self.check(1, "--strict", "--doc", "core")

    def test_reverted_source_changes_still_need_review(self):
        """逐提交检查不能被最终源码恰好回到基线内容掩盖。"""
        self.write_file("core.py", "value = 1\n")
        sha = self.commit()
        self.page(f"<!-- verified-at: {sha} -->\n[源码](../../core.py)\n")
        self.commit("fixture: reference baseline")
        self.write_file("core.py", "value = 2\n")
        changed = self.commit("fixture: source without review")
        self.write_file("core.py", "value = 1\n")
        reverted = self.commit("fixture: revert source without review")
        normal = self.check()
        self.assertIn(changed[:7], normal.stdout)
        self.assertIn(reverted[:7], normal.stdout)
        self.check(1, "--strict")

    def test_freshness_handles_multiple_unicode_source_paths(self):
        """批量检查保留中文空格路径，并只报告没有随本页复核的文件。"""
        self.write_file("状态 设置.py", "value = 1\n")
        self.write_file("other.py", "value = 1\n")
        sha = self.commit()
        text = f"<!-- verified-at: {sha} -->\n[源码](<../../状态 设置.py>)\n[其他](../../other.py)\n"
        self.page(text)
        self.commit("fixture: reference baseline")
        self.write_file("other.py", "value = 2\n")
        self.page(text + "其他值为 2。\n")
        reviewed = self.commit("fixture: reviewed other source")
        self.write_file("状态 设置.py", "value = 2\n")
        changed = self.commit("fixture: source without review")
        normal = self.check()
        self.assertIn("状态 设置.py", normal.stdout)
        self.assertIn(changed[:7], normal.stdout)
        self.assertNotIn("other.py", normal.stdout)
        self.assertNotIn(reviewed[:7], normal.stdout)
        self.check(1, "--strict")

    def test_unknown_verification_commit_warns(self):
        """不存在的提交不被当成最新状态，strict 必须失败。"""
        self.write_file("core.py", "value = 1\n")
        self.commit()
        self.page("<!-- verified-at: 0000000000000000000000000000000000000000 -->\n[源码](../../core.py)\n")
        normal = self.check()
        self.assertIn("不是本仓库的提交", normal.stdout)
        self.check(1, "--strict")

    def test_missing_stamp_warns_only_for_reference_pages(self):
        """普通参考页缺标记时提示并受 strict 约束，索引豁免。"""
        self.page("# Core\n")
        self.check()
        self.check(1, "--strict")
        self.check(0, "--strict", "--doc", "README")

    def test_exact_name_does_not_reuse_prefix_hit(self):
        """星号查询的命中不能使同批精确查询也通过。"""
        self.write_file("tests/test_core.py", "def test_start_more(): pass\n")
        self.page("验证：`test_start`、`test_start*`\n")
        self.check(1)

    def test_test_prefix_requires_identifier_start(self):
        """星号代表名称前缀，不代表可出现在其他名称中间。"""
        self.write_file("tests/test_core.py", "def latest_test_start(): pass\n")
        self.page("验证：`test_start*`\n")
        self.check(1)

    def test_fallback_search_preserves_exact_name_boundary(self):
        """Git 不可用时的回退不能把子字符串算作精确测试名。"""
        self.write_file("tests/test_core.py", "def test_start_more(): pass\n")
        self.page("验证：`test_start`\n")
        self.env["PATH"] = str(self.root / "empty-path")
        self.check(1)

    def test_deleted_tracked_test_file_is_missing(self):
        """Git 索引里还保留名称的已删除文件不能通过存在性检查。"""
        source = self.write_file("tests/test_core.py", "value = 1\n")
        self.commit()
        source.unlink()
        self.page("验证：`tests/test_core.py`\n")
        self.check(1)

    def test_anchor_suffix_avoids_existing_heading_collision(self):
        """重复标题分配后缀时不能覆盖已有的同名锚点。"""
        self.page("# State\n# State-1\n# State\n[重复标题](#state-2)\n")
        self.check()

    def test_nested_verification_heading_does_not_end_outer_section(self):
        """同名验证子标题结束后仍属于外层验证章节。"""
        self.page("## 验证\n### 验证细节\n### 其他场景\n`test_missing`\n")
        self.check(1)

    def test_nested_verification_command_heading_keeps_outer_section(self):
        """代码块扫描与名称扫描保持相同章节层级语义。"""
        self.page("## 验证\n### 验证细节\n### 其他场景\n```sh\npytest tests/test_missing.py\n```\n")
        self.check(1)

    def test_inline_python_command_still_checks_quoted_test_path(self):
        """Shell 引号解析不能隐藏嵌在 Python 命令字符串中的路径。"""
        command = 'python -c "import runpy; runpy.run_path(\'tests/test_core.py\')"'
        self.write_file("tests/test_core.py", "value = 1\n")
        self.page(f"## 验证\n```sh\n{command}\n```\n")
        self.check()
        self.page(f"## 验证\n```sh\n{command.replace('test_core', 'test_missing')}\n```\n")
        self.check(1)

    def test_subshell_cd_does_not_leak_to_following_commands(self):
        """子 shell 内的 cd 不能影响后续仓库根命令。"""
        self.write_file("service/test_service.py", "value = 1\n")
        self.write_file("test_root.py", "value = 1\n")
        self.page("## 验证\n```sh\n(cd service && python test_service.py)\npython test_root.py\n```\n")
        self.check()

    def test_command_paths_follow_order_and_reset_per_fence(self):
        """同一行先查根路径再 cd，独立代码块重新从仓库根开始。"""
        self.write_file("test_root.py", "value = 1\n")
        self.write_file("service/test_service.py", "value = 1\n")
        self.page("## 验证\n```sh\npython test_root.py && cd service && python test_service.py\n```\n```sh\npython test_root.py\n```\n")
        self.check()

    def test_quoted_cd_and_missing_quoted_command_path(self):
        """含空格的 cd 目录有效，带引号的缺失路径仍需失败。"""
        self.write_file("子 系统/tests/test_core.py", "value = 1\n")
        self.page("## 验证\n```sh\ncd '子 系统'\npytest tests/test_core.py\n```\n")
        self.check()
        self.page("## 验证\n```sh\npytest 'tests/缺失 测试.py'\n```\n")
        self.check(1)

    def test_path_with_hash_is_decoded_after_fragment_split(self):
        """路径中的编码井号不能被当成锚点分隔符。"""
        self.write_file("core#state.py", "value = 1\n")
        self.page("[源码](../../core%23state.py)\n")
        self.check()

    def test_external_link_is_not_a_local_source(self):
        """外部 URL 和邮件地址不应触发本地路径或符号检查。"""
        self.write_file("docs/subsystems/README.md", "[`NotLocal`](https://example.invalid/source.py)\n[邮件](mailto:nobody@example.invalid)\n")
        self.check(0, "--strict")

    def test_qualified_test_name_is_not_mistaken_for_a_filename(self):
        """带短成员名的测试标识符仍可通过源码文本找到。"""
        self.write_file("tests/test_core.py", 'case_name = "Suite.start"\n')
        self.page("验证：`Suite.start`\n")
        self.check()


if __name__ == "__main__":
    unittest.main()
