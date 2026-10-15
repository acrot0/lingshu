# -*- coding: utf-8 -*-
"""test_issue657_rejected_path_null_guard · #657 被拒路径 NULL 兜底守卫
============================================================================
缺陷（本件守卫对象）：
    `find_rejected_paths()` 把 `description` 与 `reason` 直接相加送入
    `char_bigram_jaccard`：
        (p["description"] + p["reason"])
    存量行这两列可为 `NULL` ⇒ `TypeError: unsupported operand type(s) for
    +: 'NoneType' and 'NoneType'`（任一为 NULL 即崩）。
    连带面：`propose_promotion`（core.py:6155 附近）消费本函数 ⇒ 一条毒行
    同时打断「防重复失败」读面与「提议提升」写面。

修法（一行，语义不变）：
        (p["description"] or "") + (p["reason"] or "")
    `char_bigram_jaccard` 对空串返回 `0.0` ⇒ 该行相似度记 0，被既有
    `> 0.15` 阈值过滤掉——**不改变任何既有排序/阈值口径**，只堵 NULL。

断言组（回退即红）：
    ① `description=NULL, reason=NULL` ⇒ `find_rejected_paths()` 不抛，
       且该行**被排除**（相似度 0 < 0.15）；
    ② 仅 `description=NULL`（reason 有值）⇒ 不抛（只有 `+` 的左操作数坏）；
    ③ 仅 `reason=NULL`（description 有值）⇒ 不抛（右操作数坏）——
       与 ② 是**两条独立路径**，必须分别覆盖；
    ④ `propose_promotion()` 在上述毒行存在时**不抛**（连带面守卫）；
    ⑤ 正对照：两列都合法时，相似内容仍被命中（阈值语义未被顺手放宽）。

变异自证（人工改码须触发）：
    · 把 `or ""` 两处删回裸相加 ⇒ ①②③④必红。

运行：python -X utf8 -m pytest tests/test_issue657_rejected_path_null_guard.py -q
"""
from __future__ import annotations

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from lingshu.core.core import SpacetimeMemoryEngine  # noqa: E402


def _engine(tmp_path):
    return SpacetimeMemoryEngine(os.path.join(str(tmp_path), "657.db"))


def _insert_raw(eng, rid, description, reason, status="open"):
    """直插一行（绕过写入面）——毒行复现手法，与 #656 守卫同型。

    表 schema（core.py:1422）：id TEXT PK / path_type / description / reason /
    evidence / status / created_at / consumed_at。
    """
    conn = eng.store.conn
    conn.execute(
        "INSERT INTO rejected_paths (id, path_type, description, reason, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (rid, "dead_end", description, reason, status, 0.0))
    conn.commit()


def test_657_both_null_does_not_raise_and_excludes_row(tmp_path):
    """① 两列皆 NULL ⇒ 不抛且被阈值排除（修复前抛 TypeError）。"""
    eng = _engine(tmp_path)
    _insert_raw(eng, "R-BOTH-NULL", None, None)
    got = eng.find_rejected_paths("任意的检索串", limit=10)
    ids = [p["id"] for p in got]
    assert "R-BOTH-NULL" not in ids, \
        "NULL 行的相似度应为 0 被 >0.15 排除，却出现在结果里：%r" % got


def test_657_description_null_only(tmp_path):
    """② 仅 description=NULL ⇒ 不抛。"""
    eng = _engine(tmp_path)
    _insert_raw(eng, "R-DESC-NULL", None, "原因有值")
    eng.find_rejected_paths("原因", limit=10)


def test_657_reason_null_only(tmp_path):
    """③ 仅 reason=NULL ⇒ 不抛（与②独立的另一条路径）。"""
    eng = _engine(tmp_path)
    _insert_raw(eng, "R-REASON-NULL", "描述有值", None)
    eng.find_rejected_paths("描述", limit=10)


def test_657_propose_promotion_survives_null_row(tmp_path):
    """④ 连带面：毒行存在时 propose_promotion 不抛。"""
    eng = _engine(tmp_path)
    _insert_raw(eng, "R-CONSUMER", None, None)
    # propose_promotion 经 find_rejected_paths 读负记忆；毒行只须不使其抛
    # `TypeError`（NULL 相加）。它自己的 fail-closed `ValueError` 是**设计行为**，
    # 不算本项回归——故只断言错误类型不是 TypeError。
    try:
        eng.propose_promotion("n-test", "tester", "提议内容：蜂窝网格旋转等变上限")
    except TypeError as exc:  # pragma: no cover - 修前才会走到
        raise AssertionError("毒行让 propose_promotion 抛 TypeError：%r" % exc)
    except ValueError:
        pass  # 负记忆命中 → 设计内的拒绝


def test_657_valid_rows_still_matched(tmp_path):
    """⑤ 正对照：两列合法时相似内容仍被命中（阈值未被放宽）。"""
    eng = _engine(tmp_path)
    _insert_raw(eng, "R-VALID", "六边形蜂窝网格旋转等变上限记录", "重复失败")
    got = eng.find_rejected_paths("六边形蜂窝网格旋转等变上限记录", limit=10)
    assert [p["id"] for p in got] == ["R-VALID"], \
        "合法行的相似度语义被改动：%r" % got


if __name__ == "__main__":
    import subprocess
    import sys
    raise SystemExit(subprocess.call(
        [sys.executable, "-X", "utf8", "-m", "pytest", __file__, "-q", "--no-header"]))
