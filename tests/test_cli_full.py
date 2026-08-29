"""FULL CLI 冒烟：--data FULL 的三条 E0a 命令返回 0 且关键文案正确。"""

from __future__ import annotations

import sys

import environment.__main__ as main_mod


def test_data_report_full_cli(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["environment", "--data-report", "--data", "FULL"])
    assert main_mod.main() == 0
    out = capsys.readouterr().out
    assert "技能 553 条" in out
    assert "精灵 593 只" in out
    assert "家族 178 个" in out and "首领 61 只" in out
    assert "系别 18 种" in out
    assert "技能引用缺漏 0 ✅" in out


def test_team_report_full_cli(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv",
                        ["environment", "--team-report", "--data", "FULL",
                         "--spirit", "迪莫", "--spirit", "喵喵", "--spirit", "火花"])
    assert main_mod.main() == 0
    out = capsys.readouterr().out
    assert "队伍 FULL" in out and "校验：队伍合法 ✅" in out
    assert "迪莫" in out and "喵喵" in out and "火花" in out


def test_team_report_full_invalid(monkeypatch, capsys) -> None:
    """首领 + 同族 → 报错返回 1，不打印阵容。"""
    monkeypatch.setattr(sys, "argv",
                        ["environment", "--team-report", "--data", "FULL",
                         "--spirit", "迪莫", "--spirit", "圣光迪莫"])
    assert main_mod.main() == 1
    out = capsys.readouterr().out
    assert "首领形态不可入队" in out
    assert "同一家族只能入队一只" in out


def test_probe_errors_full_cli(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["environment", "--probe-errors", "--data", "FULL"])
    assert main_mod.main() == 0
    out = capsys.readouterr().out
    assert "首领形态不可入队" in out
    assert "同一家族只能入队一只" in out
    assert "需要先选择血脉系别" in out
    assert "共 8 条错误" in out


def test_default_cli_is_full(monkeypatch, capsys) -> None:
    """不传 --data（默认 FULL）行为：FULL 数据自检。"""
    monkeypatch.setattr(sys, "argv", ["environment", "--data-report"])
    assert main_mod.main() == 0
    assert "FULL 数据自检" in capsys.readouterr().out
