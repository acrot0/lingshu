"""#665 守卫：嵌套写事务必须可整体回滚（内层不得提前提交外层）。

缺陷（修复前实测）：`@_transactional` 只做 `with self._tx()`，而 `_tx` 无嵌套深度概念，
一律 `conn.commit()`。于是任何「外层事务里调用另一个 @_transactional 方法」的组合——
本仓真实存在（`ingest_frame` → `add_node` 等）——内层退出时就把整条事务提交掉，
外层随后抛异常 rollback 时**写已经落了盘**，撤不回来。

修复后语义：
  depth==1  → BEGIN，退出时 commit / 异常 rollback（与修复前一致，单层行为不变）
  depth>1   → SAVEPOINT，退出时 RELEASE（正常）/ ROLLBACK TO（异常）；
              且内层方法体内的 `self._commit_locked()` 不得终结外层事务。

本文件是「回退即红」对照：把 core.py 的 `_tx` / `_commit_locked` 改回旧实现，
A/B/C 三条会失败。
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lingshu.core.core import LayeredStore  # noqa: E402


@pytest.fixture()
def store():
    """独立临时库的 LayeredStore（不碰任何真实数据）。"""
    tmpdir = tempfile.mkdtemp(prefix="lingshu665_")
    st = LayeredStore(os.path.join(tmpdir, "t665.sqlite3"))
    yield st
    try:
        st.conn.close()
    except Exception:
        pass


def _count(st, table="nodes"):
    cur = st.conn.cursor()
    cur.execute("SELECT COUNT(*) FROM %s" % table)
    return cur.fetchone()[0]


def _seed(st, tag):
    """在外层事务之外写一行，作为断言基准。"""
    with st._tx():
        st.conn.cursor().execute(
            "INSERT OR REPLACE INTO engine_meta (key, value) VALUES (?, ?)",
            ("seed_%s" % tag, "1"),
        )


# ---------------------------------------------------------------- A 嵌套回滚

def test_a_outer_rollback_discards_inner_write(store):
    """A：外层回滚后，内层 @_transactional 的写必须一起消失（#665 主判据）。"""
    def inner_write():
        with store._tx():
            store.conn.cursor().execute(
                "INSERT OR REPLACE INTO engine_meta (key, value) VALUES (?, ?)",
                ("inner_A", "1"),
            )

    before = _count(store, "engine_meta")
    with pytest.raises(RuntimeError):
        with store._tx():
            inner_write()
            raise RuntimeError("外层失败")
    after = _count(store, "engine_meta")

    assert after == before, (
        "外层回滚后内层写仍然存在（%d -> %d）：内层提交泄漏——#665 未修复" % (before, after)
    )


def test_a2_outer_commit_keeps_inner_write(store):
    """A2 阳性对照：外层正常提交时，内层写必须留下（防「一律不写」的假修复）。"""
    tag = "inner_A2"
    with store._tx():
        with store._tx():
            store.conn.cursor().execute(
                "INSERT OR REPLACE INTO engine_meta (key, value) VALUES (?, ?)", (tag, "1"))
    cur = store.conn.cursor()
    cur.execute("SELECT value FROM engine_meta WHERE key = ?", (tag,))
    assert cur.fetchone() is not None, "外层提交后内层写丢了——提交语义被改坏"


# ------------------------------------------------- B 内层方法体内的显式提交

def test_b_inner_explicit_commit_does_not_end_outer_tx(store):
    """B：内层方法体内的 `_commit_locked()` 不得终结外层事务。

    这是 #665 的第二半：光有 SAVEPOINT 还不够——@_transactional 方法体内原本写着
    `self.conn.commit()`，执行到那里 SAVEPOINT 就被销毁，外层 rollback 变成
    「no such savepoint」。本用例锁定「显式提交也必须推迟到最外层」。
    """
    tag = "inner_B"
    before = _count(store, "engine_meta")
    with pytest.raises(RuntimeError):
        with store._tx():
            with store._tx():
                store.conn.cursor().execute(
                    "INSERT OR REPLACE INTO engine_meta (key, value) VALUES (?, ?)",
                    (tag, "1"))
                store._commit_locked()  # 模拟 @_transactional 方法体内的显式提交
            raise RuntimeError("外层失败")
    after = _count(store, "engine_meta")

    assert after == before, (
        "内层显式提交把外层事务提前提交了（%d -> %d）" % (before, after)
    )


# ----------------------------------------------------------------- C 深度归零

def test_c_depth_returns_to_zero_after_exception(store):
    """C：异常路径也必须把嵌套深度归零，否则后续写会退化成 savepoint。"""
    try:
        with store._tx():
            with store._tx():
                raise RuntimeError("boom")
    except RuntimeError:
        pass

    assert getattr(store, "_tx_depth", 0) == 0, "异常后 _tx_depth 未归零"

    # 归零判据：下一笔独立事务必须仍能提交（若深度残留，写会在无 savepoint 时炸）
    with store._tx():
        store.conn.cursor().execute(
            "INSERT OR REPLACE INTO engine_meta (key, value) VALUES (?, ?)",
            ("after_C", "1"))
    cur = store.conn.cursor()
    cur.execute("SELECT value FROM engine_meta WHERE key = ?", ("after_C",))
    assert cur.fetchone() is not None, "深度残留导致后续事务无法提交"


# ----------------------------------------------- D 单层行为不变 / 三层嵌套

def test_d_single_level_behaviour_unchanged(store):
    """D：单层（depth==1）行为必须与修复前完全一致——提交落盘、异常回滚。"""
    with store._tx():
        store.conn.cursor().execute(
            "INSERT OR REPLACE INTO engine_meta (key, value) VALUES (?, ?)", ("solo_D", "1"))
    cur = store.conn.cursor()
    cur.execute("SELECT value FROM engine_meta WHERE key = ?", ("solo_D",))
    assert cur.fetchone() is not None, "单层正常提交丢数据"

    before = _count(store, "engine_meta")
    with pytest.raises(RuntimeError):
        with store._tx():
            store.conn.cursor().execute(
                "INSERT OR REPLACE INTO engine_meta (key, value) VALUES (?, ?)",
                ("solo_D2", "1"))
            raise RuntimeError("单层失败")
    assert _count(store, "engine_meta") == before, "单层异常未回滚"


def test_d2_three_level_nesting(store):
    """D2：三层嵌套——中间层正常退出，最外层回滚，全部写都必须消失。"""
    before = _count(store, "engine_meta")
    with pytest.raises(RuntimeError):
        with store._tx():
            with store._tx():
                store.conn.cursor().execute(
                    "INSERT OR REPLACE INTO engine_meta (key, value) VALUES (?, ?)",
                    ("lvl2", "1"))
                with store._tx():
                    store.conn.cursor().execute(
                        "INSERT OR REPLACE INTO engine_meta (key, value) VALUES (?, ?)",
                        ("lvl3", "1"))
            raise RuntimeError("最外层失败")
    assert _count(store, "engine_meta") == before, "三层嵌套下外层回滚不干净"
