#!/usr/bin/env python3
"""列出复盘范围内的返工候选（只读）。

对范围内每个非合并提交，取它删除或改写的行，用 git blame 找出这些行是哪个提交写的。
写这些行的提交离得很近（默认按提交者时间算 72 小时内），且本次提交累计改写达到
--min-lines（默认 3）行时，记为一条候选，不据此判定返工；阈值跨来源提交累加。
另外列出提交标题里带 revert / 回滚 的提交；已列为改写候选的只标注回滚，不重复列出。
重命名和移动文件跟随旧路径，只计算真正删除或改写的行。支持 SHA-1 和 SHA-256 仓库。

候选只是线索：重构、格式化、同一功能的连续开发也会命中。是否算返工，要读两边的 diff 判断。

用法：
  python rework.py [--root 仓库根] [--since <commit>] [--last N] [--hours 72] [--min-lines 3]
                   [--include-docs] [--max-commits 400]

  --since 一般填 LESSONS.md 第一行 reviewed-through 的提交；不填时看最近 --last 个提交（默认 50）。
  reviewed-through 记的是扫描时的 HEAD，下一期从它之后开始，合并进来的分支不会重复扫描。
  范围超过 --max-commits、浅克隆或任何 git 调用失败时退出，不输出 reviewed-through。
退出码：0 正常（有没有候选都是 0）；2 用法错误、范围超限或历史不完整。
"""
from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@")
GITLINK_RE = re.compile(r"^(index \S+ |deleted file mode |old mode )160000\b")
REVERT_RE = re.compile(r"\brevert|回滚|撤销", re.I)
SKIP_PATH_RE = re.compile(r"(^|/)(package-lock\.json|pnpm-lock\.yaml|yarn\.lock|go\.sum|uv\.lock|Cargo\.lock)$|\.(min\.js|snap)$")
DOC_PATH_RE = re.compile(r"\.(md|mdx|rst|txt)$", re.I)
TEST_PATH_RE = re.compile(r"(^|/)(tests?|__tests__|spec|testdata|fixtures)/|(^|/)test_[^/]*$|[._-](test|spec)\.[^/]+$", re.I)
FIX_RE = re.compile(r"^(fix|hotfix|bugfix)\b|^修复|\bfix(es|ed)?\b", re.I)
BLAME_BATCH = 40


def git(root: Path, *args: str) -> tuple[int, str, str]:
    p = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.returncode, p.stdout, p.stderr


def must(root: Path, *args: str) -> str:
    code, out, err = git(root, *args)
    if code != 0:
        print(f"git {' '.join(args)} 失败：{err.strip()}", file=sys.stderr)
        raise SystemExit(2)
    return out


def commits_in_range(root: Path, head: str, since: str | None, last: int, cap: int) -> list[str]:
    args = ["rev-list", "--no-merges", "--reverse"]
    args += [f"{since}..{head}"] if since else [f"--max-count={last}", head]
    code, out, _ = git(root, *args)
    if code != 0:
        print(f"git rev-list 失败，检查 --since 是否是本仓库的提交：{since}", file=sys.stderr)
        raise SystemExit(2)
    shas = out.split()
    if len(shas) > cap:
        print(f"范围内有 {len(shas)} 个提交，超过上限 {cap}；未扫描，请用 --max-commits 调大上限后重跑", file=sys.stderr)
        raise SystemExit(2)
    return shas


def meta(root: Path, sha: str) -> tuple[int, list[str], str]:
    ts, parents, subject = must(root, "show", "-s", "--format=%ct%x00%P%x00%s", sha).strip().split("\x00", 2)
    return int(ts), parents.split(), subject


def touched_old_lines(root: Path, parent: str, sha: str, include_docs: bool) -> dict[str, list[tuple[int, int]]]:
    """父提交里被这次提交删除或改写的行区间，按父提交里的路径分组。"""
    out = must(root, "-c", "core.quotepath=false", "diff", "-U0", "--no-color", "--no-ext-diff", "--no-textconv",
               "-M", "--inter-hunk-context=0", "--src-prefix=a/", "--dst-prefix=b/", parent, sha)
    ranges: dict[str, list[tuple[int, int]]] = defaultdict(list)
    path, in_header, gitlink = None, False, False
    for line in out.splitlines():
        if line.startswith("diff --git "):
            path, in_header, gitlink = None, True, False
        elif in_header and GITLINK_RE.match(line):
            gitlink = True
        elif in_header and line.startswith("--- "):
            old_path = line[4:].rstrip("\t")
            if old_path.startswith('"'):
                old_path = ast.literal_eval(old_path)
            if old_path.startswith("a/") and not gitlink:
                path = old_path[2:]
        elif line.startswith("@@"):
            in_header = False
            if path and not SKIP_PATH_RE.search(path) and (include_docs or not DOC_PATH_RE.search(path)):
                m = HUNK_RE.match(line)
                if m:
                    start, count = int(m.group(1)), int(m.group(2) or 1)
                    if count:
                        ranges[path].append((start, start + count - 1))
    return ranges


def blame_origins(root: Path, parent: str, path: str, spans: list[tuple[int, int]]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for i in range(0, len(spans), BLAME_BATCH):
        args = ["blame", "--line-porcelain", "-w"]
        for a, b in spans[i:i + BLAME_BATCH]:
            args += ["-L", f"{a},{b}"]
        for line in must(root, *args, parent, "--", path).splitlines():
            match = re.match(r"^([0-9a-f]{40}|[0-9a-f]{64}) \d+ \d+", line)
            if match:
                counts[match.group(1)] += 1
    return counts


def main() -> int:
    ap = argparse.ArgumentParser(description="列出复盘范围内的返工候选（只读）")
    ap.add_argument("--root", default=".")
    ap.add_argument("--since", help="从这个提交之后开始看（不含它本身）")
    ap.add_argument("--last", type=int, default=50, help="没有 --since 时看最近多少个提交")
    ap.add_argument("--hours", type=float, default=72, help="被返工的代码写成后多久以内算候选")
    ap.add_argument("--min-lines", type=int, default=3, help="改写少于这么多行的不列出")
    ap.add_argument("--include-docs", action="store_true", help="也算 .md 等文档文件的改写")
    ap.add_argument("--max-commits", type=int, default=400)
    args = ap.parse_args()

    root = Path(args.root).resolve()
    if git(root, "rev-parse", "--git-dir")[0] != 0:
        print(f"{root} 不是 git 仓库", file=sys.stderr)
        return 2
    if must(root, "rev-parse", "--is-shallow-repository").strip() == "true":
        print("这是浅克隆，blame 会把边界之前的行算到边界提交上；先 git fetch --unshallow 再重跑", file=sys.stderr)
        return 2

    head = must(root, "rev-parse", "HEAD").strip()
    shas = commits_in_range(root, head, args.since, args.last, args.max_commits)
    cache: dict[str, tuple[int, list[str], str]] = {}

    def info(s: str) -> tuple[int, list[str], str]:
        if s not in cache:
            cache[s] = meta(root, s)
        return cache[s]

    findings, reverts = [], []
    for sha in shas:
        ts, parents, subject = info(sha)
        if REVERT_RE.search(subject):
            reverts.append((sha, subject))
        if not parents:
            continue
        per_origin: dict[str, dict[str, int]] = defaultdict(dict)
        for path, spans in touched_old_lines(root, parents[0], sha, args.include_docs).items():
            for origin, n in blame_origins(root, parents[0], path, spans).items():
                age = (ts - info(origin)[0]) / 3600
                if 0 <= age <= args.hours:
                    per_origin[origin][path] = n
        origins = list(per_origin.items())
        if origins and sum(sum(files.values()) for _, files in origins) >= args.min_lines:
            origins.sort(key=lambda of: -sum(of[1].values()))
            findings.append((sha, subject, ts, origins))

    def product_lines(f) -> int:
        return sum(n for _, files in f[3] for p, n in files.items() if not TEST_PATH_RE.search(p))

    findings.sort(key=lambda f: (not FIX_RE.search(f[1]), product_lines(f) == 0, -product_lines(f)))
    finding_shas = {finding[0] for finding in findings}
    reverts = [(sha, subject) for sha, subject in reverts if sha not in finding_shas]
    span = f"（{shas[0][:9]}..{shas[-1][:9]}）" if shas else ""
    print(f"看了 {len(shas)} 个非合并提交{span}，截至 HEAD {head[:9]}")
    if reverts:
        print("\n回滚：")
        for sha, subject in reverts:
            print(f"  {sha[:9]} {subject}")
    scope = "含文档" if args.include_docs else "不含文档"
    print(f"\n返工候选（改写了 {args.hours:g} 小时内写成的至少 {args.min_lines} 行，{scope}），"
          f"共 {len(findings)} 个提交。修复类在前；仅命中近期测试行的排在同类最后，仍须读完整 diff 判断：")
    for sha, subject, ts, origins in findings:
        revert_label = " [回滚]" if REVERT_RE.search(subject) else ""
        print(f"  {sha[:9]} {subject}{revert_label}")
        for origin, files in origins:
            test = sum(n for p, n in files.items() if TEST_PATH_RE.search(p))
            prod = sum(files.values()) - test
            where = ", ".join(f"{p}({n})" for p, n in sorted(files.items(), key=lambda kv: -kv[1])[:3])
            age = (ts - info(origin)[0]) / 3600
            print(f"    改写 {origin[:9]} 的产品代码 {prod} 行、测试 {test} 行，相隔 {age:.1f} 小时：{info(origin)[2]}")
            print(f"      {where}")
    when = must(root, "show", "-s", "--format=%ci", head).strip()
    stamp = when[:16] + " " + when[-5:]
    print(f"\n复盘写完后，把 LESSONS.md 第一行改为：<!-- reviewed-through: {head[:9]} ({stamp}) -->")
    return 0


if __name__ == "__main__":
    sys.exit(main())
