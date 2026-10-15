# -*- coding: utf-8 -*-
"""test_issue655_context_max_overflow_guard · #655 context_max 溢出兜底守卫
============================================================================
缺陷（本件守卫对象）：
    `SpacetimeMemoryEngine._load_context_max` / `set_context_cap` 用的
    `int(float(x))` 其 `except` 只捕 `(TypeError, ValueError)`，**超大数值**
    走的是 `OverflowError`：
      · `'1e400'` / `'inf'` → `float()` 得 `inf` → `int(inf)` 抛 OverflowError；
      · `10**400` / `'9'*400` → 在 `float()` 一步就抛 OverflowError。
    后果（严重性高于「一个 API 抛错」）：`_load_context_max` 在
    `__init__` 期执行 ⇒ 盘面上一行坏 `engine_meta['context_max']` 让
    **整库构造失败**（进程起不来）。且 `set_context_cap` 会把坏值写进盘面，
    使**下一次构造**同样失败（自锁死）。

修法（单点 helper 收口，两处共用）：
    `_to_int_or(x, default, lo)`：`except` 扩到 `(TypeError, ValueError,
    OverflowError)`；坏值**归一**为 `default`（fail-closed），与 `_finite_clamp`
    （#185）的「坏值归一而非抛错」口径一致。

断言（四组）：
    ① 盘上 `'1e400'` ⇒ 构造**不抛**且回退 DEFAULT_CONTEXT_MAX；
    ② 盘上 `'inf'` / `10**400` / `'9'*400` 同样不抛且回退默认；
    ③ `set_context_cap(inf)` 不抛，且落到合法上界（== DEFAULT_CONTEXT_MAX）；
    ④ 正对照：合法值语义逐位不变（3.0 仍归一到 3，'200' 仍为 200）。

变异自证（人工改码须触发）：
    · `_to_int_or` 的 except 去掉 `OverflowError` ⇒ ①②③必红；
    · `set_context_cap` 改回 `max(1, int(float(max_size)))` ⇒ ③必红。

运行：python -X utf8 -m pytest tests/test_issue655_context_max_overflow_guard.py -q
"""
from __future__ import annotations

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from lingshu.core.core import SpacetimeMemoryEngine  # noqa: E402

DEFAULT = SpacetimeMemoryEngine.DEFAULT_CONTEXT_MAX


def _engine_with_raw_meta(raw):
    """造一个盘上 context_max=raw 的库，然后返回**重新构造**的 engine。

    用文件库（而非 :memory:）是因为要验证「落盘 → 下次构造」这条路径。
    """
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    try:
        e = SpacetimeMemoryEngine(path)
        e.store.set_meta("context_max", raw)
        e.close()
        e2 = SpacetimeMemoryEngine(path)          # 这一步改前会抛 OverflowError
        return e2, path
    except Exception:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise


def test_655_1e400_on_disk_does_not_break_construction():
    """① 盘上 '1e400' ⇒ 构造不抛，回退 DEFAULT_CONTEXT_MAX（改前整库起不来）。"""
    e, path = _engine_with_raw_meta("1e400")
    try:
        assert e._context_max == DEFAULT, e._context_max
    finally:
        e.close()
        os.unlink(path)


def test_655_other_overflow_shapes_on_disk():
    """② 'inf' / '10**400'(写为字符串) / '9'*400 三条路径都要兜住。

    注意两条**不同**的异常路径：`'inf'` 是 int(inf)；`'9'*400` 是 float() 就抛。
    """
    for raw in ("inf", "9" * 400):
        e, path = _engine_with_raw_meta(raw)
        try:
            assert e._context_max == DEFAULT, (raw[:12], e._context_max)
        finally:
            e.close()
            os.unlink(path)


def test_655_set_context_cap_non_finite_is_fail_closed():
    """③ set_context_cap(inf) 不抛，且落到合法上界（改前抛 OverflowError）。"""
    e = SpacetimeMemoryEngine(":memory:")
    try:
        e.set_context_cap(float("inf"))          # 改前：OverflowError
        assert e._context_max == DEFAULT, e._context_max
        e.set_context_cap(10 ** 400)             # 改前：OverflowError
        assert e._context_max == DEFAULT, e._context_max
    finally:
        e.close()


def test_655_positive_control_legal_values_unchanged():
    """④ 正对照：合法入参语义逐位不变（防过修）。"""
    e = SpacetimeMemoryEngine(":memory:")
    try:
        e.set_context_cap(3.0)
        assert e._context_max == 3, e._context_max
        e.set_context_cap("200")
        assert e._context_max == 200, e._context_max
        e.set_context_cap(0)                     # 下界仍是 1（lo=1）
        assert e._context_max == 1, e._context_max
    finally:
        e.close()
