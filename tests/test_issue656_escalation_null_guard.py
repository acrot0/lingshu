# -*- coding: utf-8 -*-
"""test_issue656_escalation_null_guard · #656 升级点 NULL 兜底守卫
============================================================================
缺陷（本件守卫对象）：
    `list_escalation_points()` 原样回填 `trigger` / `condition` 两列 ⇒ 存量
    行该列为 `NULL` 时返回 `None`；`check_escalation()` 随后对其做 `in`
    （子串匹配）⇒ `TypeError: argument of type 'NoneType' is not iterable`。
    `add_escalation_point` 不校验入参，NULL 可达 ⇒ 一条毒行令**整个公开读面
    崩掉**。

修法（唯一读取点归一）：
    `list_escalation_points` 返回体 `"trigger": r[2] or ""`,
    `"condition": r[3] or ""`——与同行 `enabled: bool(r[6])` **同处、同手法**，
    缺口最小：只堵 NULL，不动任何既有匹配口径。

断言组（回退即红）：
    ① `condition=NULL` ⇒ `list_escalation_points()` 不抛，该键为 `""`；
    ② `trigger=NULL` ⇒ 同上（issue 自述只实测了 condition，trigger 是
       判据式顺序推出的第二条路径，**须独立覆盖**）；
    ③ `check_escalation(signal_type)` 对上述两行均**不抛**（修复前任一即崩）；
    ④ 正对照：合法值语义逐位不变（非空 trigger/condition 原样返回，
       `check_escalation` 仍按子串命中）。

变异自证（人工改码须触发）：
    · 把 `r[2] or ""` / `r[3] or ""` 改回 `r[2]` / `r[3]` ⇒ ①②③必红。

运行：python -X utf8 -m pytest tests/test_issue656_escalation_null_guard.py -q
"""
from __future__ import annotations

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from lingshu.core.core import SpacetimeMemoryEngine  # noqa: E402


def _engine(tmp_path):
    return SpacetimeMemoryEngine(os.path.join(str(tmp_path), "656.db"))


def _insert_raw(eng, code, trigger, condition, enabled=1):
    """直插一行（绕过 add_escalation_point 的写入面）——毒行复现手法。"""
    conn = eng.store.conn
    conn.execute(
        "INSERT INTO escalation_points (code, trigger, condition, action, "
        "severity, enabled, created_at) VALUES (?,?,?,?,?,?,?)",
        (code, trigger, condition, "noop", "medium", enabled, 0.0))
    conn.commit()


def test_null_condition_and_trigger_do_not_raise(tmp_path):
    """①②③：两条 NULL 路径都不抛，且归一为 ""。"""
    eng = _engine(tmp_path)
    _insert_raw(eng, "E-NULLCOND", "some_trigger", None)
    _insert_raw(eng, "E-NULLTRIG", None, "signal_x")

    # ①② 读面不抛 + 归一
    rows = {r["code"]: r for r in eng.list_escalation_points()}
    assert rows["E-NULLCOND"]["condition"] == "", rows["E-NULLCOND"]
    assert rows["E-NULLCOND"]["trigger"] == "some_trigger"
    assert rows["E-NULLTRIG"]["trigger"] == "", rows["E-NULLTRIG"]
    assert rows["E-NULLTRIG"]["condition"] == "signal_x"

    # ③ 消费面不抛（修复前任一即 TypeError）
    eng.check_escalation("some_trigger")
    eng.check_escalation("signal_x")
    eng.check_escalation("no_such_signal")


def test_valid_values_unchanged(tmp_path):
    """④ 正对照：合法值语义不变。"""
    eng = _engine(tmp_path)
    _insert_raw(eng, "E-OK", "ok_trigger", "ok_condition")
    rows = {r["code"]: r for r in eng.list_escalation_points()}
    assert rows["E-OK"]["trigger"] == "ok_trigger"
    assert rows["E-OK"]["condition"] == "ok_condition"
    # 子串匹配口径不动：仍能命中
    hits = eng.check_escalation("ok_trigger")
    assert any(h["code"] == "E-OK" for h in hits), hits
