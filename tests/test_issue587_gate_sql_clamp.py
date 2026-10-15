# -*- coding: utf-8 -*-
"""test_issue587_gate_sql_clamp · #587 longterm_gate 裸 SQL 腿值域闸守卫
============================================================================
缺陷（本件守卫对象）：
    `LongTermMemoryGate.write_snapshot` 的「已存在 → 更新」分支与
    `promote_from_context` 都是**裸 SQL**（`UPDATE nodes SET importance=?`），
    不经 `STNode.to_row` 的 `_finite_clamp`（#185/#164 钳制只在 add_* 腿在场）
    ⇒ `importance_hint=1e6` / `inf` 原样落库：`inf × decay` 恒 inf（永生），
    且 `ORDER BY importance DESC` 检索被坏值恒占首位、挤占 LIMIT 窗口。
    正对照：同内容走 `add_perception` 新建腿时钳制正常（1e6 ⇒ 1.0）。

修法（值域闸单点收口）：裸 SQL 腿与门面腿走**同一** `_finite_clamp(x, 0, 1)`
（core.py #185 口径：有限值钳到 [0,1]，非有限回落区间下界）。阈值本体未改。

断言（三组）：
    ① 已存在腿：同内容二次 longterm_snapshot，hint=1e6 ⇒ 读回 DB importance==1.0；
    ② 非有限腿：hint=inf ⇒ 读回落库值 ≤1.0 且为有限；hint=NaN ⇒ 非 NULL、≤1.0；
    ③ 防过修正向：合法 hint（0.5/0.62）不改既有值语义（max 合并、四舍不动）。

变异自证（人工改码须触发）：
    · longterm_gate.py 该分支恢复 `max(node.importance, imp)` 裸落库 ⇒ ①②必红；
    · 把钳制放宽成 `min(imp, 1e9)` ⇒ ②必红。

运行：python -X utf8 -m pytest tests/test_issue587_gate_sql_clamp.py -q --no-header
"""
from __future__ import annotations

import math
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from lingshu.core.core import SpacetimeMemoryEngine  # noqa: E402

CONTENT = "同一内容双入口对照：六边形蜂窝网格的旋转等变上限记录"


def _imp(engine, node_id):
    return engine.store.conn.execute(
        "SELECT importance FROM nodes WHERE id=?", (node_id,)).fetchone()[0]


def _existing_engine_with_node():
    """先走新建腿落一个知识层节点（importance 0.5），返回 (engine, node_id)。"""
    e = SpacetimeMemoryEngine(":memory:")
    r = e.longterm_snapshot(CONTENT, importance_hint=0.5)
    nid = r.get("node_id")
    assert nid and _imp(e, nid) == 0.5, r
    return e, nid


def test_587_existing_branch_clamps_overflow_hint():
    """① 已存在分支裸 SQL 腿：hint=1e6 必须钳到 1.0（改前落库 1000000.0）。"""
    e, nid = _existing_engine_with_node()
    r2 = e.longterm_snapshot(CONTENT, importance_hint=1e6)
    assert r2.get("node_id") == nid, "同内容应命中已存在分支：" + str(r2)
    stored = _imp(e, nid)
    assert stored == 1.0, "裸 SQL 腿未钳制：importance_hint=1e6 ⇒ 落库 %r" % stored


def test_587_existing_branch_rejects_inf_and_nan():
    """② inf/NaN hint ⇒ 落库仍为有限值且 ≤1.0（改前 inf 原样入库存活）。"""
    e, nid = _existing_engine_with_node()
    e.longterm_snapshot(CONTENT, importance_hint=float("inf"))
    stored = _imp(e, nid)
    assert stored is not None and math.isfinite(stored) and stored <= 1.0, \
        "inf 未钳制：落库 %r" % stored
    e.longterm_snapshot(CONTENT, importance_hint=float("nan"))
    stored2 = _imp(e, nid)
    assert stored2 is not None, "NaN 落库存成 NULL（#185 后果面回归）"
    assert math.isfinite(stored2) and 0.0 <= stored2 <= 1.0, \
        "NaN 未钳制：落库 %r" % stored2


def test_587_valid_hints_unaffected():
    """③ 防过修正向：合法 hint 的 max 合并语义一字未动。"""
    e, nid = _existing_engine_with_node()
    r = e.longterm_snapshot(CONTENT, importance_hint=0.62)
    assert _imp(e, nid) == 0.62, "合法提升被误伤：" + str(_imp(e, nid))
    r2 = e.longterm_snapshot(CONTENT, importance_hint=0.31)
    assert _imp(e, nid) == 0.62, "低 hint 不应压低既有值：" + str(_imp(e, nid))


def test_587_negative_hint_clamped_to_floor():
    """④ 负 hint 钳到区间下界 0.0（`max` 合并下不改既有值，但入口必须归一）。"""
    e, nid = _existing_engine_with_node()
    e.longterm_snapshot(CONTENT, importance_hint=-5.0)
    stored = _imp(e, nid)
    assert stored == 0.5, "负 hint 未归一（既有 0.5 应保持不变）：%r" % stored


def test_587_legit_high_hint_still_protects():
    """⑤ 防过修正向：合法高 hint（0.9）仍按 long_term 决策并登记保护（判据未被改窄）。

    注：返回的 `layer` 是**读回 DB 的实写层**（新建腿经 `add_perception` 落知识层，
    见 `write_snapshot` docstring 的 #224 口径），决策层看 `evaluated_layer`。"""
    e = SpacetimeMemoryEngine(":memory:")
    r = e.longterm_snapshot(CONTENT, importance_hint=0.9)
    assert r.get("evaluated_layer") == "long_term", r
    assert r.get("protected") is True, "合法高 hint 的保护语义被误伤：" + str(r)
    assert _imp(e, r["node_id"]) == 0.9, _imp(e, r["node_id"])


def test_587_promote_from_context_bare_sql_clamped():
    """⑥ 同族腿：`promote_from_context` 亦为裸 SQL——库里既有越界行不得经
    `max(node.importance, ev["importance"])` 再次写回（改前会把 1e6 原样带回）。

    本组把 `evaluate` 钉成固定决策（`knowledge`/0.5），使判据只落在**值域闸**上：
    节点的库内值 1e6 与评估值 0.5 取 max 后必须仍 ≤1.0。"""
    e = SpacetimeMemoryEngine(":memory:")
    n = e.add_context("情境层里的一条待提升记录：蜂窝网格旋转等变上限。", importance=0.5)
    nid = n.id if hasattr(n, "id") else n
    # 直插越界行（模拟 #587 修复前落库的存量数据 / 别的旁路写入）
    e.store.conn.execute("UPDATE nodes SET importance=? WHERE id=?", (1e6, nid))
    e.store.conn.commit()
    assert _imp(e, nid) == 1e6, "前置：存量越界行未落库"

    gate = e._ensure_gate()
    gate.evaluate = lambda *a, **kw: {"layer": "knowledge", "importance": 0.5}
    promoted = gate.promote_from_context(limit=200)
    assert [p["node_id"] for p in promoted] == [nid], "前置：本节点未被提升：" + str(promoted)
    stored = _imp(e, nid)
    assert stored is not None and math.isfinite(stored) and stored <= 1.0, \
        "promote 腿把越界值写回：%r" % stored


def test_587_write_snapshot_existing_branch_clamps_stale_overflow_row():
    """⑦ #672 缺口：`write_snapshot` 的「已存在」腿——库内越界行 + **合法** hint。

    与⑥的区别：⑥ 走 `promote_from_context`，本组走 `write_snapshot`。⑥ 的入库值
    由 `max(1e6, 0.5)` 决定；本组的关键是 **hint 合法**（0.5）而库内是 1e6——
    正是 #587 存量复核帖要求的「双入口同值」对照：钳制前 `max(1e6, 0.5)`
    会把 1e6 原样写回，故本组在修前必红。
    """
    e = SpacetimeMemoryEngine(":memory:")
    gate = e._ensure_gate()
    content = CONTENT + "（⑦ 存量越界行）"
    first = gate.write_snapshot(content, source="snapshot", importance_hint=0.5)
    nid = first.get("node_id")
    assert nid, "前置：首写未返回 node_id：" + str(first)
    # 直插越界行（模拟 #587 修复前落库的存量数据 / 旁路写入）
    e.store.conn.execute("UPDATE nodes SET importance=? WHERE id=?", (1e6, nid))
    e.store.conn.commit()
    assert _imp(e, nid) == 1e6, "前置：存量越界行未落库"

    # 同内容二次写（命中 existing 分支）+ 合法 hint
    gate.write_snapshot(content, source="snapshot", importance_hint=0.5)
    stored = _imp(e, nid)
    assert stored is not None and math.isfinite(stored) and stored <= 1.0, \
        "write_snapshot 已存在腿把越界值写回：%r" % stored


if __name__ == "__main__":
    import subprocess
    import sys
    raise SystemExit(subprocess.call(
        [sys.executable, "-X", "utf8", "-m", "pytest", __file__, "-q", "--no-header"]))
