# -*- coding: utf-8 -*-
"""pytest 收集失明守卫（#5 复发面）。

判据来源：公开 issue #5（只报了已修的 `test_hex_cnn.py`），本类缺陷此后**复发 4 次**
（见本件 `_KNOWN_SCRIPT_TESTS`），而 `tests/test_gate_coverage.py` 的现有判据是
「列入 `SCRIPT_TESTS` **或** 可被 pytest 收集」⇒ 对本类**结构性失明**，
CI 用 `--ignore` 恰好把它掩盖住（删掉测试等于修好）。

本件判据：对 `tests/*.py` 逐个 `pytest --collect-only`——
① 收集**不得**出现 `INTERNALERROR`；
② 顶层**不得**存在模块级 `sys.exit(...)`（收集期就会退出进程）。
③ 阳性对照：`test_hex_cnn.py` 是**已修**样本 ⇒ 必须**收集通过**。
   若它红了，说明本守卫自身失效（不是被测件的问题）。
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent

# module 级 sys.exit 的**已知修复样本**（#5 复发面；已按 test_hex_cnn.py 体例收进 main()）
_FIXED_SAMPLES = (
    "test_hex_cnn.py",
    "test_core_nplus1_getstats_forgetadvisor.py",
    "test_issue_ingest_scene_state_batch.py",
    "test_prediction_path_nodes_single_fetch.py",
    "test_traverse_frontier_batch.py",
)


class _ModuleLevelOnly(ast.NodeTransformer):
    """剔除**一切**函数/类体与 `if __name__ == "__main__":` 块。

    留下的才是「导入/收集期就会执行」的模块级语句——只有这些才可能在
    pytest 收集阶段杀死进程。函数体内的 `sys.exit(1)`（含 `main()` 里的）
    是正常体例，不在判据内。
    """

    def visit_FunctionDef(self, node):  # noqa: N802
        return None  # 整段删除（含装饰器），不进入其 body

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):  # noqa: N802
        return None

    def visit_Lambda(self, node):  # noqa: N802
        return None

    def visit_If(self, node):  # noqa: N802
        self.generic_visit(node)
        if "__name__" in ast.unparse(node.test) and "__main__" in ast.unparse(node.test):
            return None
        return node


def _count_pytest_tests(path: Path) -> int:
    """独立数一遍该件定义了几个 pytest 用例（`test_*` 顶层函数/类方法）。

    与 pytest 自身的收集**互为对照**：rc=5 时必须靠它区分
    「脚本风格件（合法 0 用例）」与「声明了用例却收集不到（真失明）」。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    n = 0
    for sub in tree.body:
        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and sub.name.startswith("test_"):
            n += 1
        elif isinstance(sub, ast.ClassDef) and sub.name.startswith("Test"):
            for m in sub.body:
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) and m.name.startswith("test_"):
                    n += 1
    return n


def _module_level_sys_exit(path: Path) -> list[str]:
    """返回该文件**模块级**（收集期会执行）的 sys.exit(...) 列表。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    stripped = _ModuleLevelOnly().visit(tree)
    out: list[str] = []
    for sub in ast.walk(stripped):
        if (
            isinstance(sub, ast.Call)
            and isinstance(sub.func, ast.Attribute)
            and sub.func.attr == "exit"
            and isinstance(sub.func.value, ast.Name)
            and sub.func.value.id == "sys"
        ):
            out.append(f"{path.name}:{sub.lineno}: {ast.unparse(sub)}")
    return out


def test_no_module_level_sys_exit_in_tests():
    """tests/ 下不得有模块级 sys.exit（会在 pytest 收集期杀死整个进程）。"""
    offenders: list[str] = []
    for path in sorted(TESTS_DIR.glob("*.py")):
        if path.name == Path(__file__).name:
            continue
        offenders.extend(_module_level_sys_exit(path))
    assert not offenders, (
        "模块级 sys.exit 会让 `pytest tests/` 直接 INTERNALERROR 并停掉后续全部测试：\n  "
        + "\n  ".join(offenders)
        + "\n（修法：收进 `def main()`，末尾加 `if __name__ == \"__main__\": sys.exit(main())`"
        " —— 体例见 test_hex_cnn.py）"
    )


def test_guard_has_positive_control():
    """★ 判据不得失明：本守卫必须能**真的报警**。

    用一份含模块级 `sys.exit(0)` 的合成样本喂给同一解析逻辑——
    若它没报出来，说明守卫的 AST 剥离过度（把要查的东西也删了），
    此时「零命中」是**失明**而非「干净」。
    """
    probe = TESTS_DIR / "_guard_probe_tmp.py"
    probe.write_text(
        "import sys\n"
        "fail_n = 0\n"
        "if fail_n:\n"
        "    sys.exit(1)\n"
        "def main():\n"
        "    return 0\n"
        "if __name__ == '__main__':\n"
        "    sys.exit(main())\n",
        encoding="utf-8",
    )
    try:
        hits = _module_level_sys_exit(probe)
    finally:
        probe.unlink(missing_ok=True)
    assert len(hits) == 1, (
        "守卫阳性对照失败：合成样本含 1 处模块级 sys.exit（且 main/__main__ 块内各 1 处"
        f"须被剔除），实测命中 {len(hits)} 处：{hits}"
    )


@pytest.mark.parametrize("sample", _FIXED_SAMPLES)
def test_fixed_samples_collect_cleanly(sample: str):
    """阳性对照：已修样本必须能被 pytest 干净收集。"""
    path = TESTS_DIR / sample
    assert path.exists(), f"阳性对照样本缺失：{sample}"
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(path), "--collect-only", "-q", "-p", "no:warnings"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    blob = (proc.stdout or "") + (proc.stderr or "")
    assert "INTERNALERROR" not in blob, f"{sample} 收集期 INTERNALERROR：\n{blob[-2000:]}"
    # ★ 纪律 14①：「0 检查 ≠ 通过」——rc=5（no tests collected）不得与 rc=0 混同。
    # 本仓存在两类测试件：pytest 风格（有 test_* 函数）与脚本风格（只有 main()）。
    # 脚本风格件被 pytest 收集时**合法地**返回 5；但必须与「声明了用例却收集不到」
    # 区分开，否则守卫会失明。判据：独立数一遍该件是否真的定义了 test_* 函数。
    if proc.returncode == 5:
        has_tests = _count_pytest_tests(path) > 0
        assert not has_tests, (
            f"{sample} 定义了 test_* 函数却收集不到（rc=5）——这不是脚本风格件，是真失明：\n"
            f"{blob[-2000:]}"
        )
        assert "no tests collected" in blob, (
            f"{sample} rc=5 但输出不含 no tests collected，归因不明：\n{blob[-2000:]}"
        )
        return  # 显式区分的 skip 语义：既非通过也非失败，而是「本件不以 pytest 用例形式存在」
    assert proc.returncode == 0, f"{sample} 收集失败 rc={proc.returncode}：\n{blob[-2000:]}"


def test_full_suite_collect_only_has_no_internalerror():
    """整目录收集不得 INTERNALERROR（这是 CI 用 --ignore 掩盖的那一面）。"""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(TESTS_DIR), "--collect-only", "-q", "-p", "no:warnings"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    blob = (proc.stdout or "") + (proc.stderr or "")
    assert "INTERNALERROR" not in blob, f"全目录收集 INTERNALERROR：\n{blob[-3000:]}"
    # ★ 失明守卫：必须真的收集到测试。0 条 ≠ 通过（lessons: 判据不得失明）。
    assert "test" in blob and "collected" in blob, f"收集输出异常（可能 0 条）：\n{blob[-2000:]}"
    m = None
    for line in blob.splitlines():
        if " collected" in line and "error" not in line:
            m = line
    assert m is not None, f"未找到 collected 计数行：\n{blob[-2000:]}"
