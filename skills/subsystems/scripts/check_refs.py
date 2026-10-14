#!/usr/bin/env python3
"""检查子系统参考文档里的引用是否仍然成立（只读）。

检查项：
  1. 相对链接指向的文件/目录存在；指向 .md 的 #锚点 能对上标题。
  2. 绑定到文件的符号仍在该文件里：
       [`Sym`](path)            链接文字是符号而不是文件名
       `Sym` in [..](path)      也接受 “在” “位于”
       [..](path) -> `Sym`      也接受 → : ： 或直接跟在链接后
  3. “验证：/Verification:” 段落里点名的测试名或测试文件在仓库里能找到。
     带 `*` 的按前缀匹配；含空格的视为命令，不检查。
  4. 索引页列出了目录下每一份参考，且没有指向不存在的页面。
  5. `<!-- verified-at: <commit> -->` 之后，被引用的源文件是否改过（提示过期，不算失败）。

用法：
  python check_refs.py [--root 仓库根] [--docs docs/subsystems] [--doc 名称 ...] [--strict]

退出码：0 没有失败项；1 有失败项（--strict 时过期提示也算失败）；2 用法错误。
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_DOC_DIRS = ["docs/subsystems", "docs/architecture/subsystems", "doc/subsystems"]
INDEX_NAMES = ["README.md", "index.md"]
SKIP_DIRS = {".git", "node_modules", "vendor", "dist", "build", "target", ".venv", "venv", "__pycache__"}

LINK_RE = re.compile(r"(?<!!)\[((?:[^\[\]]|\[[^\]]*\])*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
SYM_BEFORE_RE = re.compile(r"`([A-Za-z_$][\w$.:]*)`\s*(?:\(\)\s*)?(?:in|在|位于)\s*$")
SYM_AFTER_RE = re.compile(r"^\s*(?:->|→|:|：)?\s*`([A-Za-z_$][\w$.:]*)`")
VERIFY_START_RE = re.compile(r"(?:(?<![\w])Verification|验证)(?:\*\*)?\s*[:：]", re.I)
VERIFY_HEADING_RE = re.compile(r"^(?:\d+[.、]\s*)?(?:Verification|验证)", re.I)
TESTLIKE_RE = re.compile(r"(^Test[A-Z_]|^test_|_test\b|[._-](test|spec)\.|^tests?/|\.test$|\btest\w*\*$)")
VERIFIED_AT_RE = re.compile(r"<!--\s*verified-at:\s*([0-9a-fA-F]{7,40})\b")
TOKEN_RE = re.compile(r"`([^`]+)`")
IDENT_RE = re.compile(r"^[A-Za-z_$][\w$.:\-/]*\*?$")
FENCE_RE = re.compile(r"^\s*(```|~~~)")


@dataclass
class DocReport:
    path: Path
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checked_links: int = 0
    checked_symbols: int = 0
    checked_tests: int = 0


def run_git(root: Path, *args: str) -> tuple[int, str]:
    try:
        p = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return 127, ""
    return p.returncode, p.stdout


def repo_files(root: Path) -> list[Path]:
    code, out = run_git(root, "ls-files", "-co", "--exclude-standard")
    if code == 0 and out.strip():
        return [root / line for line in out.splitlines() if line]
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        files.extend(Path(dirpath) / f for f in filenames)
    return files


def read_text(path: Path) -> str | None:
    try:
        if path.stat().st_size > 4 * 1024 * 1024:
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def strip_code_blocks(text: str) -> list[tuple[int, str]]:
    """返回 (行号, 行内容)，去掉围栏代码块，避免把示例当成真实引用。"""
    lines, in_fence = [], False
    for no, line in enumerate(text.splitlines(), 1):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            lines.append((no, line))
    return lines


def slugify(heading: str) -> str:
    s = heading.strip().lower()
    s = re.sub(r"[`*_~]", "", s)
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"[^\w\- \u4e00-\u9fff]", "", s)
    return s.replace(" ", "-")


def md_anchors(path: Path, cache: dict[Path, set[str]]) -> set[str]:
    if path not in cache:
        anchors: set[str] = set()
        seen: dict[str, int] = {}
        text = read_text(path) or ""
        for _, line in strip_code_blocks(text):
            m = re.match(r"^#{1,6}\s+(.*?)\s*#*\s*$", line)
            if m:
                slug = slugify(m.group(1))
                n = seen.get(slug, 0)
                anchors.add(slug if n == 0 else f"{slug}-{n}")
                seen[slug] = n + 1
            for a in re.findall(r"<a\s+(?:name|id)=\"([^\"]+)\"", line):
                anchors.add(a)
        cache[path] = anchors
    return cache[path]


def looks_like_path(text: str) -> bool:
    t = text.strip("`")
    return "/" in t or "\\" in t or bool(re.search(r"\.[A-Za-z0-9]{1,6}$", t))


def symbol_in_file(sym: str, target: Path) -> bool:
    if target.is_dir():
        for f in target.rglob("*"):
            if f.is_file() and not any(p in SKIP_DIRS for p in f.parts):
                text = read_text(f)
                if text and re.search(rf"(?<![\w$]){re.escape(sym)}(?![\w$])", text):
                    return True
        return False
    text = read_text(target)
    if text is None:
        return False
    last = re.split(r"[.:]+", sym)[-1] or sym
    return any(re.search(rf"(?<![\w$]){re.escape(s)}(?![\w$])", text) for s in {sym, last})


def verification_tokens(lines: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """收集“验证：”之后到段落结束的反引号标识符；“## 验证”章节里只收像测试的名字。"""
    tokens, active, in_section = [], False, False
    for no, line in lines:
        heading = re.match(r"^(#{1,6})\s+(.*)", line)
        if heading:
            in_section = bool(VERIFY_HEADING_RE.match(heading.group(2)))
            active = False
            continue
        start = VERIFY_START_RE.search(line)
        if not line.strip():
            active = False
        if start:
            active = True
            line = line[start.end():]
        if not (active or in_section):
            continue
        for tok in TOKEN_RE.findall(line):
            tok = tok.strip()
            if " " in tok or not IDENT_RE.match(tok):
                continue
            if active or TESTLIKE_RE.search(tok):
                tokens.append((no, tok))
    return tokens


CMD_PATH_RE = re.compile(r"(?<![\w./-])((?:[\w.-]+/)*[\w.-]+\.(?:py|ts|tsx|js|jsx|mjs|cjs|go|rs|java|kt|rb|php|cs|swift|sh))(?![\w/])")
CD_RE = re.compile(r"\bcd\s+([^\s;&|]+)")


def verification_command_paths(text: str) -> list[tuple[int, str, list[str]]]:
    """“## 验证”章节里代码块引用的源文件路径，连同块内 cd 过的目录。"""
    out, in_section, in_fence, cds = [], False, False, []
    for no, line in enumerate(text.splitlines(), 1):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            cds = []
            continue
        if not in_fence:
            heading = re.match(r"^#{1,6}\s+(.*)", line)
            if heading:
                in_section = bool(VERIFY_HEADING_RE.match(heading.group(1)))
            continue
        if not in_section:
            continue
        cds += [c for c in CD_RE.findall(line) if not c.startswith(("/", "~", "$"))]
        for p in CMD_PATH_RE.findall(line):
            out.append((no, p, list(cds)))
    return out


class TestIndex:
    def __init__(self, root: Path, files: list[Path]):
        self.root = root
        self.files = files
        self.names = {f.name for f in files}
        self.rel = {f.relative_to(root).as_posix() for f in files if f.is_relative_to(root)}
        self._found: dict[str, bool] = {}

    def resolve(self, tokens: set[str]) -> None:
        pending = [t for t in tokens if t not in self._found]
        for t in pending:
            bare = t.rstrip("*")
            if t in self.names or t in self.rel or any(n.startswith(bare) for n in self.names if t.endswith("*") and looks_like_path(bare)):
                self._found[t] = True
        pending = [t for t in pending if t not in self._found]
        if not pending:
            return
        exact = [t for t in pending if not t.endswith("*")]
        prefix = [t.rstrip("*") for t in pending if t.endswith("*")]
        hits = self._git_grep(exact, whole_word=True) | self._git_grep(prefix, whole_word=False)
        if hits is None:
            hits = set()
        for t in pending:
            self._found[t] = t.rstrip("*") in hits

    def _git_grep(self, needles: list[str], whole_word: bool) -> set[str]:
        if not needles:
            return set()
        found: set[str] = set()
        for i in range(0, len(needles), 200):
            chunk = needles[i:i + 200]
            args = ["grep", "-h", "-o", "-F", "-I", "--untracked"]
            if whole_word:
                args.append("-w")
            for n in chunk:
                args += ["-e", n]
            args += ["--", ".", ":(exclude)*.md"]
            code, out = run_git(self.root, *args)
            if code in (0, 1):
                found.update(line.strip() for line in out.splitlines())
            else:
                found.update(self._walk_grep(chunk))
        return {n for n in needles if n in found or any(f.startswith(n) for f in found)}

    def _walk_grep(self, needles: list[str]) -> set[str]:
        found = set()
        for f in self.files:
            if f.suffix == ".md":
                continue
            text = read_text(f)
            if text:
                found.update(n for n in needles if n in text)
        return found

    def found(self, token: str) -> bool:
        return self._found.get(token, False)


def check_doc(doc: Path, root: Path, tests: TestIndex, anchor_cache: dict[Path, set[str]],
              is_index: bool = False) -> DocReport:
    rep = DocReport(doc)
    text = read_text(doc) or ""
    lines = strip_code_blocks(text)
    linked_files: set[Path] = set()

    for no, line in lines:
        for m in LINK_RE.finditer(line):
            label, target = m.group(1), m.group(2)
            if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I) or target.startswith("#") and not target[1:]:
                continue
            path_part, _, anchor = target.partition("#")
            if not path_part:
                if anchor and anchor not in md_anchors(doc, anchor_cache):
                    rep.failures.append(f"L{no}: 本页锚点不存在 #{anchor}")
                continue
            path_part = re.sub(r":\d+(-\d+)?$", "", path_part)
            resolved = (doc.parent / path_part).resolve()
            rep.checked_links += 1
            if not resolved.exists():
                rep.failures.append(f"L{no}: 链接目标不存在 {target}")
                continue
            if resolved.is_relative_to(root.resolve()) and resolved.is_file() and resolved.suffix != ".md":
                linked_files.add(resolved)
            if anchor and resolved.suffix == ".md" and not re.match(r"^L\d+", anchor):
                if anchor.lower() not in md_anchors(resolved, anchor_cache):
                    rep.failures.append(f"L{no}: 锚点不存在 {target}")

            syms = []
            inner = label.strip()
            if re.fullmatch(r"`[^`]+`", inner) and not looks_like_path(inner) and resolved.suffix != ".md":
                syms.append(inner.strip("`"))
            before = SYM_BEFORE_RE.search(line[:m.start()])
            if before:
                syms.append(before.group(1))
            after = SYM_AFTER_RE.match(line[m.end():])
            if after and not looks_like_path(after.group(1)):
                syms.append(after.group(1))
            for sym in syms:
                rep.checked_symbols += 1
                if not symbol_in_file(sym, resolved):
                    rep.failures.append(f"L{no}: 符号 `{sym}` 不在 {path_part}")

    vtokens = verification_tokens(lines)
    tests.resolve({t for _, t in vtokens})
    for no, tok in vtokens:
        rep.checked_tests += 1
        if not tests.found(tok):
            rep.failures.append(f"L{no}: 验证项 `{tok}` 在仓库里找不到")

    for no, p, cds in verification_command_paths(text):
        rep.checked_tests += 1
        bases = [root] + [root / c for c in cds]
        if not any((b / p).exists() for b in bases) and not any(r.endswith("/" + p) or r == p for r in tests.rel):
            rep.failures.append(f"L{no}: 验证命令引用的 {p} 不存在")
        if re.search(r"(^|\s)/(?:tmp|home|Users|mnt)/|[A-Za-z]:\\", text.splitlines()[no - 1]):
            rep.warnings.append(f"L{no}: 验证命令含绝对路径，换台机器跑不了")

    m = VERIFIED_AT_RE.search(text)
    if not m and not is_index:
        rep.warnings.append("没有 <!-- verified-at: <commit> --> 标记，无法判断是否过期")
    elif linked_files:
        sha = m.group(1)
        code, _ = run_git(root, "cat-file", "-e", f"{sha}^{{commit}}")
        if code != 0:
            rep.warnings.append(f"verified-at {sha} 不是本仓库的提交")
        else:
            rels = [str(p.relative_to(root.resolve())) for p in sorted(linked_files)]
            code, out = run_git(root, "diff", "--name-only", sha, "--", *rels)
            changed = [l for l in out.splitlines() if l]
            if changed:
                rep.warnings.append(f"verified-at {sha[:9]} 之后改过的源文件：" + ", ".join(changed))
    rep.warnings = list(dict.fromkeys(rep.warnings))
    return rep


def check_index(index: Path, pages: list[Path]) -> list[str]:
    text = read_text(index) or ""
    linked = set()
    for _, line in strip_code_blocks(text):
        for m in LINK_RE.finditer(line):
            target = m.group(2).partition("#")[0]
            if target and not re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I):
                linked.add((index.parent / target).resolve())
    return [f"索引没有列出 {p.name}" for p in pages if p.resolve() not in linked]


def main() -> int:
    ap = argparse.ArgumentParser(description="检查子系统参考文档的引用（只读）")
    ap.add_argument("--root", default=".", help="仓库根目录，默认当前目录")
    ap.add_argument("--docs", help="参考文档目录，默认自动探测 " + ", ".join(DEFAULT_DOC_DIRS))
    ap.add_argument("--doc", action="append", default=[], help="只检查指定参考（文件名，可省略 .md），可重复")
    ap.add_argument("--strict", action="store_true", help="过期提示也算失败")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    docs_dir = Path(args.docs) if args.docs else next((root / d for d in DEFAULT_DOC_DIRS if (root / d).is_dir()), None)
    if docs_dir is not None and not docs_dir.is_absolute():
        docs_dir = root / docs_dir
    if docs_dir is None or not docs_dir.is_dir():
        print(f"找不到参考文档目录（试过 {', '.join(DEFAULT_DOC_DIRS)}），用 --docs 指定", file=sys.stderr)
        return 2

    index = next((docs_dir / n for n in INDEX_NAMES if (docs_dir / n).is_file()), None)
    pages = sorted(p for p in docs_dir.glob("*.md") if p != index)
    if args.doc:
        wanted = {d if d.endswith(".md") else d + ".md" for d in args.doc}
        targets = [p for p in pages + ([index] if index else []) if p.name in wanted]
        missing = wanted - {p.name for p in targets}
        if missing:
            print("没有这些参考：" + ", ".join(sorted(missing)), file=sys.stderr)
            return 2
    else:
        targets = pages + ([index] if index else [])

    tests = TestIndex(root, repo_files(root))
    anchor_cache: dict[Path, set[str]] = {}
    reports = [check_doc(p, root, tests, anchor_cache, is_index=p == index) for p in targets]

    index_problems = []
    if not args.doc:
        index_problems = ["没有索引页 README.md"] if index is None else check_index(index, pages)

    fail_count = sum(len(r.failures) for r in reports) + len(index_problems)
    warn_count = sum(len(r.warnings) for r in reports)
    for r in reports:
        rel = r.path.relative_to(root) if r.path.is_relative_to(root) else r.path
        status = "失败" if r.failures else ("提示" if r.warnings else "通过")
        print(f"[{status}] {rel.as_posix()}  链接 {r.checked_links} / 符号 {r.checked_symbols} / 验证项 {r.checked_tests}")
        for f in r.failures:
            print(f"    ✗ {f}")
        for w in r.warnings:
            print(f"    ! {w}")
    for p in index_problems:
        print(f"[失败] 索引：{p}")
    print(f"\n共 {len(reports)} 页，失败 {fail_count} 项，提示 {warn_count} 项")
    return 1 if fail_count or (args.strict and warn_count) else 0


if __name__ == "__main__":
    sys.exit(main())
