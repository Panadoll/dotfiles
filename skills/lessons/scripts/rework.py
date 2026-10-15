#!/usr/bin/env python3
"""列出复盘范围内的返工候选（只读）。

对范围内每个非合并提交，取它删除或改写的行，用 git blame 找出这些行是哪个提交写的。
写这些行的提交离得很近（默认 72 小时内）时，记为一条候选，不据此判定返工。
另外列出提交说明里带 revert / 回滚 的提交。

候选只是线索：重构、格式化、同一功能的连续开发也会命中。是否算返工，要读两边的 diff 判断。

用法：
  python rework.py [--root 仓库根] [--since <commit>] [--last N] [--hours 72] [--min-lines 3]
                   [--include-docs] [--max-commits 400]

  --since 一般填 LESSONS.md 第一行 reviewed-through 的提交；不填时看最近 --last 个提交（默认 50）。
  范围超过 --max-commits 时退出，不扫描、不输出 reviewed-through；调大上限后重跑。
退出码：0 正常（有没有候选都是 0）；2 用法错误或范围超限。
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
REVERT_RE = re.compile(r"\brevert|回滚|撤销", re.I)
SKIP_PATH_RE = re.compile(r"(^|/)(package-lock\.json|pnpm-lock\.yaml|yarn\.lock|go\.sum|uv\.lock|Cargo\.lock)$|\.(min\.js|snap)$")
DOC_PATH_RE = re.compile(r"\.(md|mdx|rst|txt)$", re.I)
TEST_PATH_RE = re.compile(r"(^|/)(tests?|__tests__|spec|testdata|fixtures)/|(^|/)test_[^/]*$|[._-](test|spec)\.[^/]+$", re.I)
FIX_RE = re.compile(r"^(fix|hotfix|bugfix)\b|^修复|\bfix(es|ed)?\b", re.I)


def git(root: Path, *args: str) -> tuple[int, str]:
    p = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.returncode, p.stdout


def commits_in_range(root: Path, since: str | None, last: int, cap: int) -> list[str]:
    args = ["rev-list", "--no-merges", "--reverse"]
    args += [f"{since}..HEAD"] if since else [f"--max-count={last}", "HEAD"]
    code, out = git(root, *args)
    if code != 0:
        print(f"git rev-list 失败，检查 --since 是否是本仓库的提交：{since}", file=sys.stderr)
        raise SystemExit(2)
    shas = out.split()
    if len(shas) > cap:
        print(f"范围内有 {len(shas)} 个提交，超过上限 {cap}；未扫描，请用 --max-commits 调大上限后重跑", file=sys.stderr)
        raise SystemExit(2)
    return shas


def meta(root: Path, sha: str) -> tuple[int, str]:
    _, out = git(root, "show", "-s", "--format=%ct%x00%s", sha)
    ts, _, subject = out.strip().partition("\x00")
    return int(ts or 0), subject


def touched_old_lines(root: Path, sha: str, include_docs: bool) -> dict[str, list[tuple[int, int]]]:
    """父提交里被这次提交删除或改写的行区间，按文件分组。"""
    _, out = git(root, "-c", "core.quotepath=false", "diff", "-U0", "--no-color", "--no-renames", f"{sha}^", sha)
    ranges: dict[str, list[tuple[int, int]]] = defaultdict(list)
    path = None
    for line in out.splitlines():
        if line.startswith("--- "):
            old_path = line[4:].rstrip("\t")
            if old_path.startswith('"'):
                old_path = ast.literal_eval(old_path)
            path = old_path[2:] if old_path.startswith("a/") else None
        elif path and not SKIP_PATH_RE.search(path) and (include_docs or not DOC_PATH_RE.search(path)):
            m = HUNK_RE.match(line)
            if m:
                start, count = int(m.group(1)), int(m.group(2) or 1)
                if count:
                    ranges[path].append((start, start + count - 1))
    return ranges


def blame_origins(root: Path, sha: str, path: str, spans: list[tuple[int, int]]) -> dict[str, int]:
    args = ["blame", "--line-porcelain", "-w"]
    for a, b in spans[:40]:
        args += ["-L", f"{a},{b}"]
    code, out = git(root, *args, f"{sha}^", "--", path)
    counts: dict[str, int] = defaultdict(int)
    if code != 0:
        return counts
    for line in out.splitlines():
        if re.match(r"^[0-9a-f]{40} \d+ \d+", line):
            counts[line[:40]] += 1
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

    shas = commits_in_range(root, args.since, args.last, args.max_commits)
    cache: dict[str, tuple[int, str]] = {}

    def info(s: str) -> tuple[int, str]:
        if s not in cache:
            cache[s] = meta(root, s)
        return cache[s]

    findings, reverts = [], []
    for sha in shas:
        ts, subject = info(sha)
        if REVERT_RE.search(subject):
            reverts.append((sha, subject))
        per_origin: dict[str, dict[str, int]] = defaultdict(dict)
        for path, spans in touched_old_lines(root, sha, args.include_docs).items():
            for origin, n in blame_origins(root, sha, path, spans).items():
                if origin.startswith("0000000"):
                    continue
                ots, _ = info(origin)
                age = (ts - ots) / 3600
                if 0 <= age <= args.hours:
                    per_origin[origin][path] = n
        origins = [(o, f) for o, f in per_origin.items() if sum(f.values()) >= args.min_lines]
        if origins:
            origins.sort(key=lambda of: -sum(of[1].values()))
            findings.append((sha, subject, ts, origins))

    def product_lines(f) -> int:
        return sum(n for _, files in f[3] for p, n in files.items() if not TEST_PATH_RE.search(p))

    findings.sort(key=lambda f: (not FIX_RE.search(f[1]), product_lines(f) == 0, -product_lines(f)))
    print(f"看了 {len(shas)} 个提交（{shas[0][:9]}..{shas[-1][:9]}）" if shas else "范围内没有提交")
    if reverts:
        print("\n回滚：")
        for sha, subject in reverts:
            print(f"  {sha[:9]} {subject}")
    scope = "含文档" if args.include_docs else "不含文档"
    print(f"\n返工候选（改写了 {args.hours:g} 小时内写成的至少 {args.min_lines} 行，{scope}），"
          f"共 {len(findings)} 个提交。修复类在前；仅命中近期测试行的排在同类最后，仍须读完整 diff 判断：")
    for sha, subject, ts, origins in findings:
        print(f"  {sha[:9]} {subject}")
        for origin, files in origins:
            test = sum(n for p, n in files.items() if TEST_PATH_RE.search(p))
            prod = sum(files.values()) - test
            where = ", ".join(f"{p}({n})" for p, n in sorted(files.items(), key=lambda kv: -kv[1])[:3])
            age = (ts - info(origin)[0]) / 3600
            print(f"    改写 {origin[:9]} 的产品代码 {prod} 行、测试 {test} 行，相隔 {age:.1f} 小时：{info(origin)[1]}")
            print(f"      {where}")
    if shas:
        _, when = git(root, "show", "-s", "--format=%ci", shas[-1])
        stamp = when.strip()[:16] + " " + when.strip()[-5:]
        print(f"\n复盘写完后，把 LESSONS.md 第一行改为：<!-- reviewed-through: {shas[-1][:9]} ({stamp}) -->")
    return 0


if __name__ == "__main__":
    sys.exit(main())
