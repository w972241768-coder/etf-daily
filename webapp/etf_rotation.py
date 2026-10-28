#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ETF 动横阶梯轮动 · 一键报告
================================================================
默认在线取【前复权】日线（腾讯财经，免费无密钥，支持盘中实时快照）
-> 复算动横阶梯 -> 生成 HTML -> 自动打开。
断网/限流/接口变更时自动回退本地通达信（不复权，会明确标注），不会中断。

为什么要前复权：本地通达信 .day 为不复权价，ETF 分红/拆分日会产生虚假跳空
（实测 588170 2026-07-06 每 1 拆 3，不复权动横20 = -52.62%，前复权 +42.07%）。

用法:
  python etf_rotation.py                    # 默认池, 在线前复权, 生成HTML并自动打开
  python etf_rotation.py --no-open          # 不自动打开 (后台/定时任务必须加)
  python etf_rotation.py --offline          # 强制只用本地通达信(不复权)
  python etf_rotation.py --date 2026-09-22  # 指定锚定日
  python etf_rotation.py --csv data.csv     # 改用长表CSV (绕过在线与本地TDX)
  python etf_rotation.py --feishu           # 额外投递飞书 (需已授权)
  python etf_rotation.py --sticky-lead 0.5  # 改粘性带阈值

依赖: pandas  (用 venv: C:/Users/王旭/.workbuddy/binaries/python/envs/default/Scripts/python.exe)
"""

import argparse
import csv
import html
import json
import os
import struct
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import roc_ladder as core  # noqa: E402  (动横/乖离/粘性带 唯一事实来源)
import etf_backtest_long as bl  # noqa: E402  (LIVE_COMPOSITE = 默认口径唯一事实源)

HERE = os.path.dirname(os.path.abspath(__file__))
# 池子 / 输出目录: 同时兼容「工作区根目录」与「skill/scripts」两种摆放
_POOL_CAND = [os.path.join(HERE, "etf_pool.csv"),
              os.path.join(os.path.dirname(HERE), "etf_pool.csv")]
DEFAULT_POOL = next((p for p in _POOL_CAND if os.path.exists(p)), _POOL_CAND[-1])
_OUT_CAND = [os.path.join(HERE, "out"), os.path.join(os.path.dirname(HERE), "out")]
DEFAULT_OUT_DIR = next((p for p in _OUT_CAND if os.path.isdir(p)), _OUT_CAND[-1])
DEFAULT_TDX_CANDIDATES = [r"E:/tdx", r"D:/tdx", r"C:/tdx", r"E:/new_tdx"]
FEISHU_USER = "ou_137e56eb64ada250b5f52fd691f8ec2e"

RED = "#d4380d"    # 涨 (中国习惯: 红涨)
GREEN = "#0f9d58"  # 跌 (绿跌)

# ---- 三条策略序列的统一配色（2026-10-25 改）----
# 旧版「红 / 紫 / 品红」三种色相太近（紫 #7b3fe4 与品红 #c2185b 几乎分不开），
# 改为「蓝 / 橙 / 玫红」——色相分别约 225° / 36° / 336°，两两间隔足够大。
# ⚠️ 年度柱状图、净值曲线、图例、注释文字必须全部引用这三个常量，不要再散写颜色。
CLR_STRAT = "#3d57e5"   # 基础轮动（沿用报告主色蓝）
CLR_D3 = "#e8890c"      # D3 单笔止损（橙）
CLR_P3 = "#c2185b"      # D3+P3 止盈（玫红）
CLR_BENCH_SH = "#9aa3b2"  # 上证指数（基准，灰）
CLR_BENCH_NDX = "#00897b"  # 纳斯达克综合指数（基准，青）

# ---- 策略二：512890 红利低波「回踩 MA200 买 / 超 +6% 卖」每日跟踪 ----
# 2026-10-21 用户指定。与 512040 价值引擎同一状态机，唯一差别是买线改成「回踩即买」：
#   512040: 收盘 < MA200×0.97 才买（要求跌破 3 个点，安全垫更厚）
#   512890: 收盘 < MA200×1.00 就买（低波品种偏离窄，跌破 −3% 的天数只占 4.1%，
#           用 −3% 买线会长期打不到；实测回踩买 + 6% 卖 5 年净值 2.1220 优于 1.7102）
VE_TRACK_CODE = "512890"
VE_TRACK_NAME = "红利低波ETF华泰柏瑞"
VE_TRACK_BUY = 1.00      # 回踩 MA200 即买
VE_TRACK_SELL = 1.06     # 超 MA200 +6% 卖（与 512040 一致）

# ---- ② 板块内「D3 单笔止损」参照卡（非默认口径，仅作对比）----
_D3_CSS = """
.d3card{margin:12px 0 0;background:#fbfcff;border:1px dashed #c3d0f0;border-radius:12px;
  padding:13px 15px;font-size:12.5px;color:#333a4d}
.d3hd{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin-bottom:7px}
.d3tag{background:#e8edfb;border:1px solid #c8d4f5;color:#2c4fb8;border-radius:6px;
  padding:1px 8px;font-size:11.5px;font-weight:700;white-space:nowrap}
.d3off{color:#6c717f;font-size:12px}
.d3rule{line-height:1.75;color:#3a4560;margin-bottom:8px}
/* 规则定义折叠（2026-10-26）：同一套 D3/P3 定义在页首 details 已写过，卡片里收起来 */
.d3rd{margin:0 0 7px}
.d3rd>summary{cursor:pointer;color:#3d57e5;font-size:11.5px;font-weight:600;
  list-style:none;display:inline-block}
.d3rd>summary::-webkit-details-marker{display:none}
.d3rd>summary::before{content:"▸ "}
.d3rd[open]>summary::before{content:"▾ "}
.d3rd .d3rule{margin:6px 0 0;padding:6px 9px;background:#f7f9fd;border-radius:7px;
  border:1px dashed #dbe3f5}
.d3state{background:#fff;border:1px solid #e2e5ea;border-radius:8px;padding:7px 11px;
  margin-bottom:9px;line-height:1.7}
.d3badge{display:inline-block;border-radius:5px;padding:0 7px;font-size:11px;
  font-weight:700;margin-right:7px;border:1px solid}
.d3badge.on{background:#fff3e8;border-color:#f3cfc6;color:#b3351a}
.d3badge.off{background:#eef7f1;border-color:#cfe8dc;color:#1a7a4a}
table.d3tbl{width:100%;border-collapse:collapse;background:#fff;border:1px solid #e2e5ea;
  border-radius:9px;overflow:hidden;font-size:12px}
table.d3tbl th,table.d3tbl td{padding:5px 9px;border-bottom:1px solid #eef0f3;
  text-align:left;white-space:nowrap}
table.d3tbl th{background:#f4f6fa;font-weight:600;color:#42474f;font-size:11.5px}
table.d3tbl tbody tr:last-child td{border-bottom:none}
table.d3tbl tr.hi td{background:#eef2fd}
table.d3tbl.sm{font-size:11.5px}
.d3card .num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.d3card .mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  font-size:11.5px;white-space:nowrap}
.d3card .dimtxt{color:#98a1ad;font-size:11.5px}
.d3meta{display:flex;flex-wrap:wrap;gap:4px 18px;color:#5b6470;font-size:11.8px;margin:8px 0 0}
.d3meta b{color:#202020}
.d3last{margin-top:9px}
.d3last .cap{color:#6c717f;font-size:11.8px;margin-bottom:5px;white-space:normal}
.d3sw{background:#fff;border-left:3px solid #3d57e5;border-radius:6px;padding:7px 11px;
  margin-top:9px;line-height:1.7}
.d3foot{color:#98a1ad;font-size:11.3px;margin-top:8px;line-height:1.6}
.d3card.p3{background:#fffafc;border-color:#f0c7d6}
.d3card.p3 .d3tag{background:#fceef4;border-color:#f2c8d9;color:#c2185b}
.d3card.p3 .d3sw{border-left-color:#c2185b}
"""


def _wstate_html(ws) -> str:
    """抄底锁定状态条（2026-10-28 用户需求：像 D3 一样显示「现在处在什么状态」）。

    设计要点（2026-10-28 二次改版）：
      · 两个离场出口量纲不同（时间 vs 价格），**分行并列**才读得出「哪个先到」；
      · 时间出口给进度条（2/10 天），价格出口给「还差多少 pp」；
      · 用橙色系但只做左侧细边，避免和红色告警抢注意力。
    数据来自 recent["p3"]["weak_state"]，不处于锁定期就返回空串。
    """
    if not ws:
        return ""
    days = int(ws.get("days") or 0)
    lim = int(ws.get("limit") or 0)
    remain = ws.get("remain")
    dev = ws.get("dev")
    pnl = ws.get("pnl")
    pct = (min(100.0, days / lim * 100) if lim else 0.0)
    pc = (RED if (pnl or 0) > 0 else (GREEN if (pnl or 0) < 0 else "#42474f"))

    rows = []
    # ① 时间出口
    if lim:
        rows.append(
            '<div class="wrow"><span class="wlab">时间出口 · 满 '
            f'{lim} 日</span>'
            f'<span class="wnum">{days} / {lim}</span>'
            f'<span class="wbar"><i style="width:{pct:.0f}%"></i></span>'
            f'<span class="wrest">还差 <b>{int(remain or 0)}</b> 个交易日</span></div>')
    # ② 价格出口
    if ws.get("use_ma20") and dev is not None:
        _far = "已回到 MA20 上方" if dev >= 0 else f"还差 <b>{abs(dev):.2f}</b>pp"
        rows.append(
            '<div class="wrow"><span class="wlab">价格出口 · 回 MA20</span>'
            f'<span class="wnum">{dev:+.2f}%</span>'
            '<span class="wbar flat"><i></i></span>'
            f'<span class="wrest">需回到 <b>0%</b> 上方（{_far}）</span></div>')
    # ③ 浮动盈亏
    _pnl_row = ""
    if pnl is not None and ws.get("entry_px"):
        _pnl_row = (f'<div class="wpnl">建仓 <b>{ws["entry_px"]:.3f}</b>'
                    f'（{html.escape(str(ws.get("entry_date") or ""))}）'
                    f' → 现价 <b>{(ws.get("px") or 0):.3f}</b> '
                    f'<b style="color:{pc}">{pnl:+.2f}%</b></div>')
    return (
        '<div class="wstate">'
        '<div class="whd"><span class="wbadge">🎯 抄底持仓中</span>'
        f'<b>{html.escape(str(ws.get("code") or ""))} '
        f'{html.escape(str(ws.get("name") or ""))}</b>'
        '<span class="wtip">锁定期内 D3 / P3 都不介入</span></div>'
        f'{"".join(rows)}'
        f'{_pnl_row}'
        '<div class="wnote">满足任一出口即换回当日第 1 名；'
        '这是 2026-10-28 起的新口径（旧口径为「亏 3% 由 D3 止损」）。</div>'
        '</div>')


def _combo_summary_html(recent):
    """【今日操作指引】顶部的一眼可见摘要：基础轮动 / D3 / D3+P3 止盈 三方案今日对照。

    用户 2026-10-25 要求：把叠加组合的简要信息提到操作指引里，不用往下翻到 ② 才看到。
    数据全部取自 `recent['d3']` / `recent['p3']`（同一次影子回测，不额外计算）。
    任何缺项都降级跳过，绝不影响操作指引主体。
    """
    try:
        if not recent or recent.get("error"):
            return ""
        d3 = recent.get("d3") or {}
        p3 = recent.get("p3") or {}
        if not d3.get("ok"):
            return ""
        base_pack = d3.get("base") or {}

        # ---- 口径变更提示（2026-10-28 E3 接入，过渡期 1 周内显示）----
        # 为什么必须提示：切到 E3 后「抄底持仓进锁定期、D3 不再介入」，
        # 10-27 那条「D3 止损→空仓」的指令被撤销，页面会显示「继续持 588170 / 无需动作」，
        # 但用户很可能已经按旧口径清仓了。不提示会让人以为不用买。
        _migrate_html = ""
        _asof_s = str(recent.get("asof") or "")
        if "2026-10-28" <= _asof_s <= "2026-11-05":
            _migrate_html = (
                '<div class="migr"><b>⚠ 口径已变更（2026-10-28）</b>：'
                '抄底持仓改为<b>锁定期</b> —— 期内 D3 / P3 不再介入，'
                '满足「<b>回 MA20 上方</b>」或「<b>满 10 个交易日</b>」任一条件'
                '才换回当日第 1 名。<br>'
                '若你在 10-27 已按旧口径（D3 止损）清仓，<b>今日应按新口径买回</b>；'
                '页面「当前持 X」是按模型口径推算的，与你的实际账户可能不同，'
                '<b>请以实际持仓为准</b>。</div>')

        def _hold_cell(name, is_cash):
            cls = "cash" if is_cash else "hold"
            return f'<td class="{cls}">{html.escape(str(name or "—"))}</td>'

        def _num(v, d=4):
            return f"{v:.{d}f}" if isinstance(v, (int, float)) else "—"

        rows = []
        if p3.get("ok"):
            rows.append(("D3+P3+抄底", p3.get("hold_name"), p3.get("hold") == "CASH",
                         dict(final=p3.get("final"), mdd=p3.get("mdd"),
                              calmar=p3.get("calmar")), True))
            # 2026-10-26：只保留「仅 P3」一个参照；基础策略与 D3 单列不再进正文表


        body = "".join(
            f'<tr{" class=\"hi\"" if (hi or i == 0) else ""}>'
            f'<td>{"<b>" + nm + "</b>" if (hi or i == 0) else nm}</td>'
            + _hold_cell(hn, cash)
            + f'<td class="num"{" style=\"font-weight:700\"" if (hi or i == 0) else ""}>{_num(pk.get("final"))}</td>'
            f'<td class="num">{_num(pk.get("mdd"), 2)}%</td>'
            f'<td class="num">{_num(pk.get("calmar"), 2)}</td></tr>'
            for i, (nm, hn, cash, pk, hi) in enumerate(rows))

        # ---- 今日动作结论（以 D3+P3 为默认口径，其余作参照）----
        p3_hold_nm = p3.get("hold_name") if p3.get("ok") else None
        base_hold_nm = d3.get("base_hold_name")
        d3_hold_nm = d3.get("hold_name")
        if not p3.get("ok"):
            concl = ("<b>D3+P3 本次未算出</b>，暂按基础轮动执行。"
                     "（详见下方 ② 的 D3 卡片）")
            tone = "warn"
        elif p3.get("hold") == d3.get("base_hold"):
            _same = ("空仓" if p3.get("hold") == "CASH"
                     else f"<b>{html.escape(str(p3_hold_nm))}</b>")
            concl = (f"今日持仓 <b>{_same}</b> → <b>无需任何操作</b>。")
            tone = "ok"
        elif p3.get("hold") == "CASH":
            concl = ("<b>今日空仓</b>（D3 止损 或 P3 追高触发）→ "
                     "<b>卖出清仓</b>。")
            tone = "diff"
        else:
            concl = (f"<b>今日调整为 {html.escape(str(p3_hold_nm))}</b>"
                     f"（D3 / P3 / 抄底 三者叠加后的结果）。")
            tone = "diff"

        # ---- 一行胶囊：打开页面第一眼只需要知道「今天持什么 · 要不要动手」----
        # ⚠️ 回测表本身**不删**（用户 2026-10-25 明确要求三口径对照），
        #    只是收进 <details>：一致时默认收起（多数日子），
        #    口径不一致时默认展开（那天必须看细节对比）。
        _h_p3, _h_base = p3.get("hold"), d3.get("base_hold")
        _h_disp = ("空仓持币" if _h_p3 == "CASH"
                   else html.escape(f"{_h_p3 or ''} {p3_hold_nm or ''}".strip()))
        _weak = bool(p3.get("weak_active"))
        if not p3.get("ok"):
            pill_cls = "warn"
            pill = "⚠ 默认口径本次未算出 · 暂按基础轮动执行"
        elif _h_p3 == _h_base:
            pill_cls = "ok"
            pill = f"✓ 今日持 <b>{_h_disp}</b> → <b>无需动作</b>"
        elif _weak:
            # 抄底触发：这不是「口径打架」，而是规则主动换持末位，要说得像件事
            pill_cls = "diff"
            pill = (f"🎯 <b>抄底触发</b>（主选池动横20 全部为负）→ "
                    f"<b>换持末位 {_h_disp}</b>")
        elif _h_p3 == "CASH":
            pill_cls = "diff"
            pill = "⚠ 今日<b>空仓持币</b>（D3 止损 或 P3 追高触发）"
        else:
            pill_cls = "diff"
            pill = f"⚠ <b>今日调整为 {_h_disp}</b>（D3 / P3 / 抄底 叠加）"
        _cal = (p3.get("calmar") if p3.get("ok") else None) or base_pack.get("calmar")
        if _cal:
            pill += (f'<span class="pillcal">　历史 Calmar <b>{_cal:.2f}</b></span>')
        _det_open = "" if pill_cls == "ok" else " open"

        _wstate_bar = _wstate_html(((recent.get("p3") or {}).get("weak_state")))
        return f"""<div class="cbsum {pill_cls}">
{_migrate_html}
<div class="cbpill">{pill}
<details class="cbdet"{_det_open}><summary>回测对比 · 规则</summary>
<div class="tblwrap"><table class="cbst"><thead><tr>
<th>方案</th><th>今日应持</th><th class="num">净值</th><th class="num">最大回撤</th><th class="num">Calmar</th>
</tr></thead><tbody>{body}</tbody></table></div>
<div class="cbsn">{concl}
<span class="dimtxt">　<b>默认口径＝第一行 D3+P3+抄底</b>：D3＝换出时亏≥3% 空仓 5 日；P3＝头名 ROC60 进自身历史 95 分位空仓 20 日；
抄底＝主选池动横20 全为负时换持末位{("、同标的 " + str(bl.LIVE_COMPOSITE["weak_cd"]) + " 日内不重复") if bl.LIVE_COMPOSITE.get("weak_cd", 0) else ""}（池内少于 {bl.LIVE_COMPOSITE.get("weak_min_pool", 0)} 只不做）。
三条独立判定、叠加使用。详细规则与触发明细见下方 ② 的卡片。</span></div>
</details>
</div>
{_wstate_bar}"""
    except Exception:                          # noqa: BLE001
        return ""


def _p3_card_html(p3, recent, cost):
    """渲染 ② 板块里的「D3+P3 止盈」参考卡（与 D3 卡并列）。

    **主口径 = D3 + P3 叠加**（2026-10-25 用户拍板的现行组合）。两条规则互相独立：
      · D3 单笔止损：某笔换出时亏 ≥3% → 空仓 5 日（看盈亏）
      · P3 追高止盈：头名 ROC60 进自身滚动 95 分位 → 空仓 20 日（不看盈亏）
    P3 本身**不是** D3 的延伸；卡片额外保留「仅 P3（不挂 D3）」一行作参照。
    本卡回答「要不要把基础轮动换成 D3+P3」，因此主对比基准是**基础轮动**。
    任何异常都降级成一行提示，绝不影响 ② 主体。
    """
    try:
        if not p3:
            return ""
        if not p3.get("ok"):
            return (f'<style>{_D3_CSS}</style><div class="d3card p3">'
                    f'<div class="d3hd"><span class="d3tag">D3+P3 止盈 · 参考</span></div>'
                    f'<div class="d3rule">本次未能计算：'
                    f'{html.escape(str(p3.get("error", "未知原因")))}</div></div>')

        def col(v):
            return RED if v > 0 else GREEN

        IMP, WOR = "#3d57e5", "#b3351a"
        base = p3.get("base") or {}
        d3 = p3.get("d3") or {}
        p3o = p3.get("p3only") or {}
        # 2026-10-26 用户要求：正文不再体现「基础策略（原始轮动）」——
        # 它只是底座，不是现在在用的东西。差值改为「叠加 D3 相对仅 P3 的增量」。
        up = p3["total"] - p3o.get("total", 0)
        um = p3["mdd"] - p3o.get("mdd", 0)        # 更接近 0 = 回撤更浅 = 改善
        # ---- 抄底「何时卖出」说明（2026-10-28 按口径自动切换）----
        # E3 接入后旧文案（隔夜短打 / 靠 D3 兜底）全部失效，必须跟着引擎常量走，
        # 避免以后改回旧口径时页面说错话。⚠️ HTML 属性一律用单引号，别用 \" 转义。
        _lc = bl.LIVE_COMPOSITE
        _lock_on = bool(_lc.get("weak_exit_ma20") or (_lc.get("weak_exit_days") or 0) > 0)
        if _lock_on:
            _ex_parts = []
            if _lc.get("weak_exit_ma20"):
                _ex_parts.append("收盘<b>回到自身 MA20 上方</b>"
                                 "（主要出口，实测 13/16 次）")
            if (_lc.get("weak_exit_days") or 0) > 0:
                _ex_parts.append(f"持满 <b>{int(_lc['weak_exit_days'])} 个交易日</b>"
                                 f"（兜底出口，实测 3/16 次）")
            if _lc.get("weak_exit_pos"):
                _ex_parts.append("池内出现动横20 ≥ 0 的标的（轮动转正）")
            _weak_exit_txt = (
                "<b>抄底持仓何时卖出</b>（锁定期规则，2026-10-28 起）："
                "抄底买入后进入锁定期，期内 <b>D3 / P3 都不介入</b>，"
                "满足下列任一条件即换回<b>当日第 1 名</b>：<br>"
                + "；<br>".join(f"{'①②③④'[i]} {t}" for i, t in enumerate(_ex_parts))
                + "。<br><span class='dimtxt'>实测：持有中位 <b>6 个交易日</b>、"
                  "单笔收益中位 <b>+2.52%</b>、胜率 <b>75%</b>、"
                  "最大单笔 <b>−3.83%</b>。"
                  "不再按「池内转正 / 末位换人」离场 —— 实测加「转正」出口"
                  "反而少 2.3 净值。代价：抄底那笔亏到 3% 也不止损了"
                  "（等回 MA20 或满 10 日）。</span><br>")
        else:
            _weak_exit_txt = (
                "<b>抄底持仓何时卖出</b>（每天收盘按顺序判断）："
                "① 池内<b>不再全负</b>（有标的动横20 ≥ 0）→ 次日换回<b>当日第 1 名</b>；"
                "② 仍全负但<b>末位换人</b> → 换到<b>新的末位</b>；"
                "③ 仍全负且末位没变 → <b>继续持有</b>，但受 D3 约束："
                "<b>亏 ≥3% 即止损空仓 5 日</b>。<br>")
        if p3["active"]:
            rem = (f"还剩 <b>{p3['remain']}</b> 个交易日"
                   if p3.get("remain") is not None else "")
            back = html.escape(str(p3.get("back") or "\u2014"))
            _csr = p3.get("cash_src")
            _cun = (p3.get("cash_until") or p3.get("cash_note") or "")
            if _csr == "D3":
                state = (f'<span class="d3badge on">空仓中 · D3 单笔止损触发</span>'
                         f'{html.escape(str(p3.get("cash_reason") or ""))} → '
                         f'<b>空仓 5 个交易日</b>，{_cun} 收盘回场')
                act = ('今天收盘卖出清仓；回场日以每日报告更新'
                       '（按当日排名第 1 名买回）。')
            elif _csr == "P3":
                state = (f'<span class="d3badge on">空仓中 · P3 追高止盈触发</span>'
                         f'{html.escape(str(p3.get("cash_reason") or ""))} → '
                         f'<b>空仓 20 个交易日</b>，{_cun} 收盘回场')
                act = ('今天收盘卖出清仓；回场日以每日报告更新'
                       '（按当日排名第 1 名买回）。')
            else:
                state = (f'<span class="d3badge on">空仓中</span>'
                         f'D3+P3 组合此刻要求<b>空仓</b>，{rem}，约 <b>{back}</b> 收盘回场')
                act = (f'若按 D3+P3 执行：<b>今天收盘应卖出、转入空仓</b>，'
                       f'到 <b>{back}</b> 收盘再按当日头名买回。')
        elif p3.get("same_base"):
            # 2026-10-28 用户要求：不再提「基础轮动」——既然只用这一套口径，
            # 拿基础策略做对照只会让人分心。
            state = (f'<span class="d3badge off">持仓已一致</span>'
                     f'今日持 <b>{html.escape(p3["hold_name"])}</b>，<b>无需任何动作</b>')
            act = ("今天<b>不用操作</b>。"
                   "D3 / P3 / 抄底 三条规则各自独立判定，谁触发谁生效。")
        else:
            state = (f'<span class="d3badge on">今日调整</span>'
                     f'按现行口径应持 <b>{html.escape(p3["hold_name"])}</b>')
            act = f'今天应调整为 <b>{html.escape(p3["hold_name"])}</b>。'

        def _n(v, unit="%", sign=True):
            """None/NaN 统一显示「—」，避免卡片出现 nan。"""
            if not isinstance(v, (int, float)) or v != v:
                return "—"
            return f"{v:+.2f}{unit}" if sign else f"{v:.2f}{unit}"

        rows_last = ""
        for x in (p3.get("last") or []):
            _p = x.get("pct")
            _pcell = (f'<span style="color:{CLR_P3};font-weight:700">{_p:.1f} 分位</span>'
                      if isinstance(_p, (int, float)) and _p == _p else "—")
            _head_cell = (f'{html.escape(str(x.get("head_name") or "—"))}'
                          f'<span class="dimtxt"> {html.escape(str(x.get("head") or ""))}</span>')
            _sell_cell = (f'{html.escape(str(x.get("name") or "—"))}'
                          f'<span class="dimtxt"> {html.escape(str(x.get("code") or ""))}</span>')
            if x.get("head") == x.get("code"):
                _sell_cell += '<span class="dimtxt">（同上）</span>'
            rows_last += (
                f'<tr><td class="mono">{html.escape(str(x["date"]))}</td>'
                f'<td>{_head_cell}</td>'
                f'<td class="num" style="color:{CLR_P3};font-weight:700">{_n(x.get("roc"))}</td>'
                f'<td class="num">{_pcell}</td>'
                f'<td>{_sell_cell}</td>'
                f'<td class="num">{x["days"]} 日</td>'
                f'<td class="mono">{html.escape(str(x["until"]))}</td></tr>')
        if not rows_last:
            rows_last = '<tr><td colspan="7" class="dimtxt">区间内没有触发</td></tr>'

        return f"""<style>{_D3_CSS}</style>
<div class="d3card p3">
  <div class="d3hd">
    <span class="d3tag">D3 + P3 + 抄底 · 默认口径</span>
    <span class="d3off">当前<b>默认执行组合</b> —— 今日操作与年度主柱均按此给出；下表按本口径回测（含换仓成本）</span>
  </div>
  <details class="d3rd"><summary>规则定义（D3 / P3 / 抄底）</summary>
  <div class="d3rule">主口径 = <b>三条独立规则</b>（各判各的、独立计时，
  目标持仓优先级 D3 &gt; P3 &gt; 抄底；<b>抄底锁定期内 D3 / P3 让位</b>）：<br>
  ① <b>D3 单笔止损</b>（看盈亏）：某笔在换出时相对建仓价亏损 ≥3% ⇒ 空仓 5 个交易日；<br>
  ② <b>P3 追高止盈</b>（不看盈亏）：<b>头名的 60 日动量（ROC60）冲进它自身历史的 95 分位</b>
  ——即已涨到「历史极高位」、动量透支 ⇒ 空仓 20 个交易日；<br>
  ③ <b>抄底</b>（零参数）：主选池 <b>全部</b> 标的动横20 都为负 ⇒ 当日换持<b>排名最后一名</b>
  （不是倒数第二）；{("同一标的 " + str(bl.LIVE_COMPOSITE["weak_cd"]) + " 个交易日内不重复抄底；") if bl.LIVE_COMPOSITE.get("weak_cd", 0) else ""}
  池内可用标的少于 {bl.LIVE_COMPOSITE.get('weak_min_pool', 0)} 只时不做（小池子最弱恒为同一只，会连买暴跌品）。<br>
  {_weak_exit_txt}
  到期都按当日排名第 1 名恢复；只改「多久之后回来」，<b>不改卖出条件、不改选股</b>。
  <span class="dimtxt">三条规则互不替代；下表即为现行口径在该窗口的历史表现。</span></div>
  </details>
  <div class="d3state">{state}</div>
  <div class="tblwrap"><table class="d3tbl"><thead><tr>
    <th>同一窗口（{recent.get('base', '')} → {html.escape(str(recent.get('asof', '')))}）</th>
    <th class="num">净值</th><th class="num">区间收益</th>
    <th class="num">最大回撤</th><th class="num">夏普</th><th class="num">Calmar</th></tr></thead><tbody>
    <tr class="hi"><td><b>D3+P3+抄底（现行口径）</b></td>
      <td class="num">{p3['final']:.4f}</td>
      <td class="num" style="color:{col(p3['total'])}">{p3['total']:+.2f}%</td>
      <td class="num" style="color:#42474f">{p3['mdd']:.2f}%</td>
      <td class="num">{p3.get('sharpe') or 0:.2f}</td>
      <td class="num">{p3.get('calmar') or 0:.2f}</td></tr>
  </tbody></table></div>
  <div class="d3meta">
    <span>P3 触发 <b>{p3['roc_n']}</b> 次</span>
    <span>P3 累计空仓 <b>{p3['roc_days_total']}</b> 个交易日</span>
    <span>换仓 <b>{p3['trades_n']}</b> 笔</span>
    <span>口径 <b>D3 亏≥{int(p3.get('thr', 0.03) * 100)}% 空仓 5 日 + ROC{p3['n']} 滚动 {p3['q']*100:.0f} 分位 空仓 {p3['days']} 日</b></span>
  </div>
  <div class="d3last">
    <div class="cap">最近 {len(p3.get('last') or [])} 次追高止盈事件</div>
    <div class="tblwrap"><table class="d3tbl sm"><thead><tr>
      <th>触发日（＝清仓日）</th>
      <th title="当日动横20 排名第 1 名；P3 看的是它的 ROC60">触发依据 · 当日头名</th>
      <th class="num" title="当日头名的 60 日动量">该头名 ROC60</th>
      <th class="num" title="该 ROC60 在自身扩张历史中的百分位；触发线 95，故必然 ≥95">自身历史分位</th>
      <th>当日卖出（当时持仓）</th>
      <th class="num">空仓</th><th>回场日</th></tr></thead>
      <tbody>{rows_last}</tbody></table></div>
    <div class="sub" style="margin-top:6px;font-size:11px;line-height:1.6">
    <b>三件事别混</b>：①「触发依据 · 当日头名」= 当天动横20 排名第 1 名，P3 判定的是<b>它的</b> ROC60 有没有进自身历史 95 分位；
    ②「当日卖出」= 你那时<b>实际持有的</b>那只 —— 因为冷却/未换仓，它可能不是当日头名，两者不同是正常的；
    ③ <b>触发日＝清仓日</b>：当天收盘判定、当天收盘清仓，次日起空仓；<b>回场日</b> = 空仓满 {p3['days']} 个交易日后那天，按当日动横20 第 1 名买回。
    <br>「自身历史分位」= 该值在该标的自身扩张历史里排第几（0~100），触发线固定 95，所以必然 ≥95；越接近 100 说明当时涨得越极端。</div>
  </div>
  <div class="d3sw">{act}</div>
  <div class="d3foot">数据与本节同源：同一次回测、同基点、同双边成本 {cost*100:.1f}%，
  只多打开「亏≥3% 空仓 5 日」+「头名 ROC60 进 95 分位空仓 20 日」两条。回测口径，非实盘成交。<br>
  信号实测：ROC60 触发后头名未来 20 日中位 <b>−4.11%</b>、胜率仅 <b>37%</b>（动量透支）。
  <span class="dimtxt">口径已内置于引擎（etf_backtest_long.LIVE_COMPOSITE），
  改规则只改那一处。</span></div>
</div>
"""
    except Exception as e:                     # noqa: BLE001
        return (f'<style>{_D3_CSS}</style><div class="d3card p3">'
                f'<div class="d3hd"><span class="d3tag">D3+P3 止盈 · 参考</span></div>'
                f'<div class="d3rule">渲染失败：{html.escape(f"{type(e).__name__}: {e}")}</div></div>')


def _d3_card_html(d3, recent, cost):
    """渲染 ② 板块里的「D3 单笔止损」参考卡。任何异常都降级成一行提示，绝不影响 ② 主体。"""
    try:
        if not d3:
            return ""
        if not d3.get("ok"):
            return (f'<style>{_D3_CSS}</style><div class="d3card">'
                    f'<div class="d3hd"><span class="d3tag">D3 单笔止损 · 参考</span></div>'
                    f'<div class="d3rule">本次未能计算：'
                    f'{html.escape(str(d3.get("error", "未知原因")))}</div></div>')

        def col(v):
            return RED if v > 0 else GREEN

        IMP, WOR = "#3d57e5", "#b3351a"        # 改善 / 恶化
        up = d3["total"] - d3["base"]["total"]
        um = d3["mdd"] - d3["base"]["mdd"]     # 更接近 0 = 回撤更浅 = 改善
        base_txt = "\u2014"
        if d3["active"]:
            rem = (f"还剩 <b>{d3['remain']}</b> 个交易日"
                   if d3.get("remain") is not None else "")
            back = html.escape(str(d3.get("back") or "\u2014"))
            state = (f'<span class="d3badge on">冷却中</span>'
                     f'D3 此刻要求<b>空仓</b>，{rem}，约 <b>{back}</b> 收盘回场')
            act = (f'若按 D3 执行：<b>今天收盘应卖出、转入空仓</b>，'
                   f'到 <b>{back}</b> 收盘再按当日头名买回。')
        elif d3["same"]:
            state = (f'<span class="d3badge off">与现方案一致</span>'
                     f'今日两者都持 <b>{html.escape(d3["hold_name"])}</b>，<b>无需任何动作</b>')
            act = ("若按 D3 执行：今天<b>不用操作</b>。"
                   "区别只在某笔亏损 ≥3% 换出时，多空仓 5 个交易日再回来。")
        else:
            state = (f'<span class="d3badge on">持仓不同</span>'
                     f'D3 持 <b>{html.escape(d3["hold_name"])}</b> ／ '
                     f'现方案持 <b>{html.escape(d3["base_hold_name"])}</b>')
            act = f'若按 D3 执行：今天应调整为 <b>{html.escape(d3["hold_name"])}</b>。'

        rows_last = "".join(
            f'<tr><td class="mono">{html.escape(str(x["date"]))}</td>'
            f'<td>{html.escape(str(x["name"]))}</td>'
            f'<td class="num" style="color:{col(x["ret"])}">{x["ret"]:+.2f}%</td>'
            f'<td class="num">{x["days"]} 日</td>'
            f'<td class="mono">{html.escape(str(x["until"]))}</td></tr>'
            for x in d3.get("last") or [])
        if not rows_last:
            rows_last = '<tr><td colspan="5" class="dimtxt">区间内没有触发</td></tr>'

        return f"""<style>{_D3_CSS}</style>
<div class="d3card">
  <div class="d3hd">
    <span class="d3tag">D3 单笔止损 · 参照</span>
    <span class="d3off">当前<b>参照组合</b> —— 非默认口径，仅用于对比 D3 单独生效的效果</span>
  </div>
  <details class="d3rd"><summary>规则定义（D3）</summary>
  <div class="d3rule">规则：轮动期内，任一笔在<b>换出时</b>相对建仓价亏损 ≥{d3['thr']*100:.0f}%，
  就不立刻买入新头名，改为<b>空仓 {d3['days']} 个交易日</b>，之后按当日排名第 1 名恢复。
  只改「卖出后多久回来」，<b>不改卖出条件、不改选股</b>。</div>
  </details>
  <div class="d3state">{state}</div>
  <div class="tblwrap"><table class="d3tbl"><thead><tr>
    <th>同一窗口（{recent.get('base', '')} → {html.escape(str(recent.get('asof', '')))}）</th>
    <th class="num">净值</th><th class="num">区间收益</th>
    <th class="num">最大回撤</th><th class="num">夏普</th></tr></thead><tbody>
    <tr><td>现方案（正在用）</td>
      <td class="num">{d3['base']['final']:.4f}</td>
      <td class="num" style="color:{col(d3['base']['total'])}">{d3['base']['total']:+.2f}%</td>
      <td class="num" style="color:#42474f">{d3['base']['mdd']:.2f}%</td>
      <td class="num">{d3['base'].get('sharpe') or 0:.2f}</td></tr>
    <tr class="hi"><td><b>D3 单笔止损（若切换）</b></td>
      <td class="num">{d3['final']:.4f}</td>
      <td class="num" style="color:{col(d3['total'])}">{d3['total']:+.2f}%</td>
      <td class="num" style="color:#42474f">{d3['mdd']:.2f}%</td>
      <td class="num">{d3.get('sharpe') or 0:.2f}</td></tr>
    <tr><td class="dimtxt">差（D3 − 现方案）</td>
      <td class="num dimtxt">{d3['final']-d3['base']['final']:+.4f}</td>
      <td class="num" style="color:{IMP if up > 0 else WOR};font-weight:700">{up:+.2f}pp</td>
      <td class="num" style="color:{IMP if um > 0 else WOR};font-weight:700">{um:+.2f}pp</td>
      <td class="num dimtxt">—</td></tr>
  </tbody></table></div>
  <div class="d3meta">
    <span>累计触发 <b>{d3['cool_n']}</b> 次</span>
    <span>累计空仓 <b>{d3['cool_days']}</b> 个交易日（占 {d3['cash_pct']:.1f}%）</span>
    <span>换仓 <b>{d3['trades_n']}</b> 笔</span>
  </div>
  <div class="d3last">
    <div class="cap">最近 {len(d3.get('last') or [])} 次冷却事件</div>
    <div class="tblwrap"><table class="d3tbl sm"><thead><tr><th>触发日</th><th>被换出标的</th>
      <th class="num">该笔亏损</th><th class="num">空仓</th><th>回场日</th></tr></thead>
      <tbody>{rows_last}</tbody></table></div>
  </div>
  <div class="d3sw">{act}</div>
  <div class="d3foot">数据与本节同源：同一次回测、同基点、同双边成本 {cost*100:.1f}%，
  只是把「亏损 ≥3% 就空仓 5 日」这一条打开。回测口径，非实盘成交。<br>
  想启用时：照上面「若按 D3 执行」那一句做即可，<b>不需要改代码</b>；不启用就忽略本卡。</div>
</div>
"""
    except Exception as e:                     # noqa: BLE001
        return (f'<style>{_D3_CSS}</style><div class="d3card">'
                f'<div class="d3hd"><span class="d3tag">D3 单笔止损 · 参考</span></div>'
                f'<div class="d3rule">渲染失败：{html.escape(f"{type(e).__name__}: {e}")}</div></div>')



# ---------------------------------------------------------------- 数据读取
def price_divisor(code: str) -> float:
    """通达信 .day 价格整数还原除数: 基金/ETF 为 x1000, 其余(股票)为 x100。"""
    return 1000.0 if code[:1] in ("1", "5") else 100.0


def find_tdx_root(user_root=None):
    if user_root:
        return user_root if os.path.isdir(user_root) else None
    env = os.environ.get("TDX_ROOT")
    if env and os.path.isdir(env):
        return env
    for c in DEFAULT_TDX_CANDIDATES:
        if os.path.isdir(os.path.join(c, "vipdoc")):
            return c
    return None


def day_file_path(tdx_root, code):
    mkt = "sz" if code[:1] in ("0", "1", "2", "3") else "sh"
    order = [mkt, "sh" if mkt == "sz" else "sz"]
    for m in order:
        p = os.path.join(tdx_root, "vipdoc", m, "lday", f"{m}{code}.day")
        if os.path.exists(p):
            return p
    return None


def read_day(path, divisor=1000.0, want=120):
    """读取 .day (32字节/条) 末尾 want 条, 返回 [(date_str, close), ...] 升序。"""
    size = os.path.getsize(path)
    n = size // 32
    if n == 0:
        return []
    take = min(want, n)
    with open(path, "rb") as f:
        f.seek((n - take) * 32)
        buf = f.read(take * 32)
    out = []
    for i in range(take):
        rec = buf[i * 32:(i + 1) * 32]
        d, o, h, l, c, amt, vol, _ = struct.unpack("<5if2i", rec)
        y, mo, dy = d // 10000, (d // 100) % 100, d % 100
        out.append((f"{y:04d}-{mo:02d}-{dy:02d}", c / divisor))
    return out


def load_pool(path):
    pool = []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            code = (row.get("code") or "").strip()
            if not code:
                continue
            pool.append((code,
                         (row.get("name") or "").strip(),
                         (row.get("role") or "").strip().lower()))
    return pool


def build_panel_from_tdx(pool, tdx_root, want, want_by_code=None):
    """返回 (DataFrame[date,code,close,name,role], info_log, info_by_code)。

    want_by_code: {code: n} 可为个别标的指定更多历史 (如防守位算 MA200 需 200+ 根)。
    """
    want_by_code = want_by_code or {}
    recs, info, info_by_code = [], [], {}
    for code, name, role in pool:
        p = day_file_path(tdx_root, code)
        if not p:
            line = f"[MISSING] {code} {name}: 通达信里找不到 .day 文件"
            info.append(line)
            info_by_code[code] = line
            continue
        rows = read_day(p, price_divisor(code), want_by_code.get(code, want))
        if not rows:
            line = f"[EMPTY] {code} {name}: .day 无数据"
            info.append(line)
            info_by_code[code] = line
            continue
        for d, c in rows:
            recs.append(dict(date=d, code=code, close=c, name=name, role=role))
        line = f"[OK] {code} {name}: {len(rows)} 条, 最新 {rows[-1][0]} 收 {rows[-1][1]:.3f}"
        info.append(line)
        info_by_code[code] = line
    df = pd.DataFrame(recs)
    df["date"] = pd.to_datetime(df["date"])
    return df, info, info_by_code


# ---------------------------------------------------------------- HTML
def _fmt(v, pct=True, sign=True, nd=2):
    if v is None or (isinstance(v, float) and v != v):
        return "-"
    s = f"{v:+.{nd}f}" if sign else f"{v:.{nd}f}"
    return s + ("%" if pct else "")


def _color(v):
    if v is None or v != v:
        return "#666"
    return RED if v > 0 else (GREEN if v < 0 else "#666")


def _bg(v, vmax):
    """阶梯单元格热力底色 (红正绿负, 淡色)。"""
    if v is None or v != v or vmax <= 0:
        return "#fff"
    a = min(abs(v) / vmax, 1.0) * 0.55
    if v > 0:
        return f"rgba(212,56,13,{a:.3f})"
    if v < 0:
        return f"rgba(15,157,88,{a:.3f})"
    return "#fff"


# ------------------------------------------------- 盘中实时（联网，可选）
EM_URL = "https://push2.eastmoney.com/api/qt/ulist.np/get"
EM_FIELDS = "f2,f3,f12,f14,f18,f124"   # 最新价/涨跌幅/代码/名称/昨收/行情时间戳


def bj_now(now=None):
    """取【北京时间】(A股作息基准)。本机可能在南非 UTC+2，必须显式转换。"""
    base = now or datetime.now()
    if base.tzinfo is None:
        base = base.astimezone()          # naive 视为本机时间
    return base.astimezone(timezone(timedelta(hours=8)))


def _em_secid(code: str) -> str:
    """东财 secid: 沪市(5/6/9开头)=1.xxx, 深市(0/1/3开头)=0.xxx"""
    return ("1." if code[:1] in ("5", "6", "9") else "0.") + code


def _pfx(code: str) -> str:
    """腾讯/新浪前缀: sh510300 / sz159967"""
    return ("sh" if code[:1] in ("5", "6", "9") else "sz") + code


def _http_get(url, timeout, referer=None):
    import ssl as _ssl
    import urllib.request as _ur
    h = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    if referer:
        h["Referer"] = referer
    ctx = _ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = _ssl.CERT_NONE
    with _ur.urlopen(_ur.Request(url, headers=h), timeout=timeout, context=ctx) as f:
        return f.read()


def _fetch_em(pool, timeout):
    """东方财富批量（JSON，含昨收/涨跌幅/行情时间戳）。"""
    import json as _json
    url = (f"{EM_URL}?secids={','.join(_em_secid(c) for c, _n, _r in pool)}"
           f"&fields={EM_FIELDS}&fltt=2&invt=2")
    obj = _json.loads(_http_get(url, timeout, "https://quote.eastmoney.com/")
                      .decode("utf-8"))
    diff = (obj.get("data") or {}).get("diff") or []
    out = {}
    for it in diff:
        code = str(it.get("f12") or "")
        px = it.get("f2")
        if not code or not isinstance(px, (int, float)) or px <= 0:
            continue
        prev, chg, ts = it.get("f18"), it.get("f3"), it.get("f124")
        out[code] = dict(price=float(px),
                         prev=float(prev) if isinstance(prev, (int, float)) else None,
                         chg=float(chg) if isinstance(chg, (int, float)) else None,
                         name=str(it.get("f14") or ""),
                         ts=int(ts) if isinstance(ts, (int, float)) else None)
    if not out:
        raise RuntimeError("东财返回空数据")
    return out


def _fetch_tx(pool, timeout):
    """腾讯行情（GBK，`~` 分隔；[3]最新 [4]昨收 [30]时间戳）。"""
    txt = _http_get("https://qt.gtimg.cn/q=" + ",".join(_pfx(c) for c, _n, _r in pool),
                    timeout).decode("gbk", "replace")
    out = {}
    for line in txt.split(";"):
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        k = key.strip()                        # v_sh510300
        if len(k) < 9:
            continue
        code = k[2:][2:]                       # -> 510300
        p = val.strip().strip('"').split("~")
        if len(p) < 6:
            continue
        try:
            px, prev = float(p[3]), float(p[4])
        except ValueError:
            continue
        if px <= 0:
            continue
        ts = None
        if len(p) > 30 and len(p[30]) >= 14:
            try:
                ts = int(datetime.strptime(p[30][:14], "%Y%m%d%H%M%S")
                         .replace(tzinfo=timezone(timedelta(hours=8))).timestamp())
            except ValueError:
                pass
        out[code] = dict(price=px, prev=prev or None,
                         chg=round((px / prev - 1) * 100, 2) if prev else None,
                         name=p[1], ts=ts)
    if not out:
        raise RuntimeError("腾讯返回空数据")
    return out


def _fetch_sina(pool, timeout):
    """新浪行情（GBK，逗号分隔；[2]昨收 [3]最新 [30][31]日期时间）。"""
    txt = _http_get("https://hq.sinajs.cn/list=" + ",".join(_pfx(c) for c, _n, _r in pool),
                    timeout, "https://finance.sina.com.cn").decode("gbk", "replace")
    out = {}
    for line in txt.split("\n"):
        if "=" not in line or '"' not in line:
            continue
        key, val = line.split("=", 1)
        code = key.strip().split("_")[-1]       # hq_str_sh510300
        if len(code) < 8:
            continue
        code = code[2:]                         # -> 510300
        p = val.strip().strip(";").strip('"').split(",")
        if len(p) < 6:
            continue
        try:
            prev, px = float(p[2]), float(p[3])
        except ValueError:
            continue
        if px <= 0:
            continue
        ts = None
        if len(p) > 31 and p[30] and p[31]:
            try:
                ts = int(datetime.strptime(p[30] + " " + p[31], "%Y-%m-%d %H:%M:%S")
                         .replace(tzinfo=timezone(timedelta(hours=8))).timestamp())
            except ValueError:
                pass
        out[code] = dict(price=px, prev=prev or None,
                         chg=round((px / prev - 1) * 100, 2) if prev else None,
                         name=p[0], ts=ts)
    if not out:
        raise RuntimeError("新浪返回空数据")
    return out


LIVE_SOURCES = [("东财", _fetch_em), ("腾讯", _fetch_tx), ("新浪", _fetch_sina)]


def _online(timeout=2.5):
    """快速探测外网可达性：任一行情域名可建立 TCP 连接即视为在线。

    目的：完全断网时不要在 3 个源上挨个等超时，直接走离线数据。
    """
    import socket as _s
    for host in ("push2.eastmoney.com", "qt.gtimg.cn", "hq.sinajs.cn"):
        try:
            _s.create_connection((host, 443), timeout=timeout).close()
            return True
        except Exception:                               # noqa: BLE001
            continue
    return False


def fetch_live_quotes(pool, timeout=5.0, retries=1):
    """多源实时行情（含盘中），任一源成功即返回。

    顺序 东财 → 腾讯 → 新浪，每源重试 retries 次（间隔递增）。
    实测东财批量偶发 HTTP 502，故必须多源容错。
    返回 (quotes, 源名)；全失败抛 RuntimeError（消息含各源错误）。
    """
    import time as _t
    errs = []
    for name, fn in LIVE_SOURCES:
        for k in range(retries):
            try:
                q = fn(pool, timeout)
                return q, name
            except Exception as e:                      # noqa: BLE001
                errs.append(f"{name}#{k + 1}:{type(e).__name__} {e}")
                _t.sleep(0.6 * (k + 1))
    raise RuntimeError(" ｜ ".join(errs[-4:]))


def apply_live(df, pool, quotes):
    """把实时价并入长表：本地缺当日 -> 追加；已有当日 -> 用实时价覆盖。"""
    today = pd.Timestamp(bj_now().strftime("%Y-%m-%d"))
    rows, added, updated, skipped = [], [], [], []
    for code, name, role in pool:
        q = quotes.get(code)
        if not q:
            skipped.append(code)
            continue
        sub = df[df["code"] == code]
        if not len(sub):
            skipped.append(code)
            continue
        last = sub["date"].max()
        if last < today:
            rows.append(dict(date=today, code=code, close=q["price"],
                             name=name, role=role))
            added.append(code)
        elif last == today:
            df.loc[(df["code"] == code) & (df["date"] == today), "close"] = q["price"]
            updated.append(code)
        else:
            skipped.append(code)
    if rows:
        df = pd.concat([df, pd.DataFrame(rows)], ignore_index=True)
        df["date"] = pd.to_datetime(df["date"])
    return df, dict(added=added, updated=updated, skipped=skipped, today=str(today.date()))


def data_freshness(pivot, now=None):
    """本地数据新鲜度提示（离线判断，不联网）。

    本脚本是【离线模式】——只读本地通达信 vipdoc，不会去网上取数。
    而通达信日线（.day）是「盘后数据下载」才落盘的，所以：
      · 今天还没收盘（<15:00）时，本地最新交易日必然是"上一个交易日"；
      · 今天已收盘但没有在通达信里执行下载，本地也不会自动更新。
    这里只做"看起来落后了"的温和提示，不做交易日历推断（避免节假日误报）。
    ⚠️ 时区: 判断必须用【北京时间】(A股 15:00 收盘), 本机可能在南非(UTC+2)等地。
    返回 (is_stale, text)。
    """
    bj = bj_now(now)
    lag = (bj.date() - datetime.strptime(pivot, "%Y-%m-%d").date()).days
    wd = "一二三四五六日"[bj.weekday()]
    stale = (bj.weekday() < 5 and bj.hour >= 16 and lag >= 1) or lag >= 5
    if stale:
        tip = ("⚠️ <b>本地数据可能不是最新</b>（若今天为交易日且已收盘，通常需要重新下载）："
               "请在通达信中执行：<b>系统 → 盘后数据下载 → 勾选「沪深日线」→ 开始下载</b>，"
               "完成后重新运行本脚本即可更新。")
    else:
        tip = ("本脚本为<b>离线模式</b>（只读本地通达信文件、不联网）。"
               "要拿到当日数据，须先在通达信中执行「盘后数据下载」"
               "（当天收盘 15:00 之后），再重新运行本脚本。")
    return stale, (f"本地数据最新交易日 <b>{pivot}</b>（距今 {lag} 天）｜ "
                   f"北京时间 {bj:%Y-%m-%d %H:%M} 周{wd}。{tip}")


# ------------------------------------------------- 近 5 年 / 近 2 月净值曲线（内联 SVG + 可移动光标）
ZOOM_DAYS = 62            # 「近 2 个月」放大窗口（自然日）

CURVE_SERIES = [
    ("nv",  "策略净值（规则复算）",  CLR_STRAT, 2.4),
    ("d3",  "D3 单笔止损（若切换）", CLR_D3, 1.9),
    ("p3",  "D3+P3+抄底（默认口径）", CLR_P3, 1.9),
    ("sh",  "上证指数",              CLR_BENCH_SH, 1.4),
    ("ndx", "纳斯达克综合指数",      CLR_BENCH_NDX, 1.4),
]
CURVE_LEGEND = ("nv", "d3", "p3", "sh", "ndx")          # 图例/光标顺序
# 末端标注用短名（全名在图例里，末端太长会压住折线）
CURVE_SHORT = {"nv": "策略", "d3": "D3", "p3": "主口径", "sh": "上证", "ndx": "纳指"}
CURVE_Z = {"sh": 0, "ndx": 1, "nv": 2, "d3": 3, "p3": 4}   # 绘制叠放顺序（数值大者画在上层）

_CV_CSS = """
.cvbox{position:relative;width:100%;min-width:0}
.cvhint{color:#98a1ad;font-size:11.5px;margin-top:3px;line-height:1.6}
.cvbox.cvlk .cvhint{color:#3d57e5;font-weight:600}
.cvtip{position:fixed;z-index:99;pointer-events:none;display:none;background:#fff;
  border:1px solid #dfe3e8;border-radius:8px;box-shadow:0 6px 18px rgba(16,24,40,.16);
  padding:8px 11px;font-size:12px;line-height:1.8;color:#202020;white-space:nowrap}
.cvtip .d{color:#6c717f;font-size:11.5px;margin-bottom:2px;font-weight:600}
.cvtip i{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px}
"""

_CV_JS = """
<script>
(function(){
  function fmt(v){ return (v>=0?'+':'')+v.toFixed(2)+'%'; }
  function col(v){ return v>0.005?'#d4380d':(v<-0.005?'#0f9d58':'#42474f'); }
  function setup(box){
    var id=box.getAttribute('data-cv');
    var svg=box.querySelector('svg');
    var raw=document.getElementById(id+'-data');
    if(!svg||!raw) return;
    var d=JSON.parse(raw.textContent);
    if(!d||!d.ser) return;
    var g=document.getElementById(id+'-x');
    var vl=document.getElementById(id+'-vl');
    if(!g||!vl) return;
    var dots=[],s;
    for(s=0;s<d.ser.length;s++){ dots.push(document.getElementById(id+'-d'+s)); }
    var tip=document.createElement('div');
    tip.className='cvtip'; document.body.appendChild(tip);
    var locked=false;
    function pt(e){
      return (e.touches && e.touches.length) ? e.touches[0] : e;
    }
    function move(ev){
      var e=pt(ev);
      if(!e) return;
      var r=svg.getBoundingClientRect();
      if(!r.width) return;
      var vx=(e.clientX-r.left)/r.width*d.w;
      var i=Math.round((vx-d.pl)/d.iw*(d.n-1));
      i=Math.max(0,Math.min(d.n-1,i));
      var x=d.pl+d.iw*i/(d.n-1);
      vl.setAttribute('x1',x); vl.setAttribute('x2',x);
      var h=['<div class="d">'+d.dates[i]+'</div>'];
      for(s=0;s<d.ser.length;s++){
        var k=d.ser[s][0], arr=d.v[k];
        if(!arr||arr[i]===null||!dots[s]) continue;
        var y=d.pt+d.ih*(1-(arr[i]-d.lo)/d.span);
        dots[s].setAttribute('cx',x); dots[s].setAttribute('cy',y);
        dots[s].style.opacity=1;
        h.push('<div><i style="background:'+d.ser[s][2]+'"></i>'+d.ser[s][1]+
               ' <b style="color:'+col(arr[i])+'">'+fmt(arr[i])+'</b></div>');
      }
      g.style.opacity=1;
      tip.innerHTML=h.join('');
      tip.style.display='block';
      var tw=tip.offsetWidth, th=tip.offsetHeight;
      var lx=e.clientX+16, ly=e.clientY-th/2;
      if(lx+tw>window.innerWidth-8){ lx=e.clientX-tw-16; }
      if(lx<8){ lx=8; }
      if(ly<8){ ly=8; }
      if(ly+th>window.innerHeight-8){ ly=window.innerHeight-th-8; }
      tip.style.left=lx+'px'; tip.style.top=ly+'px';
    }
    function leave(){
      if(locked) return;                 // 单击锁定后移出也不消失
      g.style.opacity=0; tip.style.display='none';
      for(var k=0;k<dots.length;k++){ if(dots[k]) dots[k].style.opacity=0; }
    }
    svg.addEventListener('mousemove',move);
    svg.addEventListener('mouseleave',leave);
    svg.addEventListener('touchstart',function(e){ move(e); },{passive:true});
    svg.addEventListener('touchmove',function(e){ move(e); },{passive:true});
    svg.addEventListener('click',function(e){
      locked=!locked; box.classList.toggle('cvlk',locked); move(e);
    });
  }
  function boot(){
    var boxes=document.querySelectorAll('.cvbox');
    for(var i=0;i<boxes.length;i++){ setup(boxes[i]); }
  }
  if(document.readyState==='loading'){
    document.addEventListener('DOMContentLoaded',boot);
  } else { boot(); }
})();
</script>
"""


def _json_dump(v):
    import json as _j
    return _j.dumps(v, ensure_ascii=False, separators=(",", ":"))


def _pct_series(series, n):
    """把「首日 = 1.0 的归一化序列」转成「自首日起累计涨跌 %」。"""
    out = {}
    for k, arr in series.items():
        a = np.asarray(arr, dtype=float)
        if a.size != n:
            continue
        out[k] = (a - 1.0) * 100.0
    return out


def _rebase_pct(pct, i0):
    """把累计涨跌再以第 i0 日为基准重定基（近 2 个月放大图用）。"""
    out = {}
    for k, a in pct.items():
        if i0 >= len(a):
            continue
        base = 1.0 + float(a[i0]) / 100.0
        if not np.isfinite(base) or base <= 0:
            continue
        out[k] = ((1.0 + a[i0:] / 100.0) / base - 1.0) * 100.0
    return out


def _svg_chart(dates, pct, switches=None, *, W=920.0, H=300.0, uid="cv1"):
    """单张内联 SVG 折线图 + 光标数据；失败返回 None。

    静态部分（网格/折线/末端标注/切换点）全在 SVG 里，打印与禁用 JS 时仍正确；
    光标是叠在上面的一层：鼠标移动只改一条竖线与几个高亮点的坐标，不重建 DOM。
    """
    n = len(dates)
    if n < 4 or "nv" not in pct:
        return None
    vis = []
    for k, label, color, lw in CURVE_SERIES:
        a = pct.get(k)
        if a is None or len(a) != n or not np.all(np.isfinite(a)):
            continue
        vis.append((k, label, color, lw, a))
    if not vis:
        return None

    pl, pr, pt, pb = 62.0, 16.0, 16.0, 28.0
    iw, ih = W - pl - pr, H - pt - pb

    vals = np.concatenate([v[4] for v in vis])
    vals = vals[np.isfinite(vals)]
    if not len(vals):
        return None
    lo, hi = float(vals.min()), float(vals.max())
    span0 = max(hi - lo, 1.0)
    lo -= span0 * 0.12
    hi += span0 * 0.12
    lo = min(lo, 0.0)
    hi = max(hi, 0.0)
    span = max(hi - lo, 1e-9)

    def ypx(v):
        return pt + ih * (1.0 - (v - lo) / span)

    def xpx(i):
        return pl + iw * (i / (n - 1)) if n > 1 else pl + iw / 2.0

    def _glab(v, st):
        """刻度文案：整数步长不显示小数（+400% 而非 +400.0%）；零线写 0%。"""
        if abs(v) < 1e-9:
            return "0%"
        if st >= 1.0 and abs(st - round(st)) < 1e-9:
            return f"{v:+.0f}%"
        return f"{v:+.1f}%"

    def _nice_step(raw):
        for s in (0.2, 0.5, 1.0, 2.0, 2.5, 5.0, 10.0, 20.0, 25.0, 50.0,
                  100.0, 150.0, 200.0, 300.0, 400.0):
            if raw / s <= 6:
                return s
        return raw / 5.0

    step = _nice_step(span)
    grids = []
    gv = np.ceil(lo / step) * step
    while gv <= hi + step * 0.5:
        if abs(gv) < step * 1e-9:
            gv = 0.0
        yy = ypx(float(gv))
        cls = ' class="cz"' if abs(gv) < 1e-9 else ""
        grids.append(
            f'<line x1="{pl:.1f}" y1="{yy:.1f}" x2="{W-pr:.1f}" y2="{yy:.1f}"'
            f' stroke="#dfe3e8" stroke-width="1"{cls}/>'
            f'<text x="{pl-8:.1f}" y="{yy+4:.1f}" class="cg">{_glab(gv, step)}</text>')
        gv += step
    grid_svg = "".join(grids)

    nticks = 5 if n > 200 else 6
    want = [round(i * (n - 1) / (nticks - 1)) for i in range(nticks)]
    xt, seen = [], set()
    for i in want:
        if i in seen:
            continue
        seen.add(i)
        d = dates[i]
        lab = d[:7] if n > 200 else f"{d[5:7]}-{d[8:10]}"
        anchor = ("start" if i == 0 else ("end" if i == n - 1 else "middle"))
        xt.append(f'<text x="{xpx(i):.1f}" y="{H-8:.1f}" class="cx" '
                  f'text-anchor="{anchor}">{lab}</text>')
    xt_svg = "".join(xt)

    # 折线：按叠放层次先画基准、再画策略、最后 D3（细线压在最上层）
    lines = []
    for k, label, color, lw, a in sorted(vis, key=lambda t: CURVE_Z.get(t[0], 9)):
        pts = " ".join(f"{xpx(i):.1f},{ypx(float(a[i])):.1f}" for i in range(n))
        lines.append(f'<polyline points="{pts}" fill="none" stroke="{color}" '
                     f'stroke-width="{lw}" stroke-linejoin="round" stroke-linecap="round"/>')

    # 末端标注：按终点涨跌自上而下排；重叠下移，整体越界再上推
    fin = sorted([(float(a[-1]), color, label, k) for k, label, color, _lw, a in vis],
                 key=lambda t: -t[0])
    ys = []
    for v, color, label, _k in fin:
        y = ypx(v)
        for prev in ys:
            if abs(y - prev) < 12.5:
                y = prev + 12.5
        ys.append(max(pt + 5.0, y))
    if ys and ys[-1] > H - pb + 8:
        dsh = ys[-1] - (H - pb + 8)
        ys = [y - dsh for y in ys]
    ends = []
    for (v, color, label, k), yy in zip(fin, ys):
        ends.append(
            f'<circle cx="{xpx(n-1):.1f}" cy="{ypx(v):.1f}" r="3" fill="{color}"/>'
            f'<text x="{W-pr-9:.1f}" y="{yy+4:.1f}" class="ce" text-anchor="end" '
            f'stroke="#fff" stroke-width="3" paint-order="stroke" fill="{color}">'
            f'{CURVE_SHORT.get(k, label)} {v:+.2f}%</text>')
    ends_svg = "".join(ends)

    # 状态机切换点（红=切回轮动 / 绿=切防守）
    mark = []
    nv_arr = pct["nv"]
    if switches:
        d2i = {d: i for i, d in enumerate(dates)}
        for sw in switches:
            i = d2i.get(str(sw.get("date", "")))
            if i is None:
                continue
            colr = RED if sw.get("state") == "ROT" else GREEN
            tip = "正向轮动切换" if sw.get("state") == "ROT" else "防守切换"
            mark.append(
                f'<circle cx="{xpx(i):.1f}" cy="{ypx(float(nv_arr[i])):.1f}" r="3.4" '
                f'fill="{colr}" stroke="#fff" stroke-width="1.3" opacity="0.95">'
                f'<title>{sw["date"]} {tip}</title></circle>')
    mark_svg = "".join(mark)

    # 光标层（初始透明）
    cur = [f'<line id="{uid}-vl" x1="{pl:.1f}" y1="{pt:.1f}" x2="{pl:.1f}" '
           f'y2="{pt+ih:.1f}" stroke="#5b6470" stroke-width="1" stroke-dasharray="3 3"/>']
    for s, (k, _l, color, _w, _a) in enumerate(vis):
        cur.append(f'<circle id="{uid}-d{s}" r="4" cx="{pl:.1f}" cy="{pt:.1f}" '
                   f'fill="#fff" stroke="{color}" stroke-width="2" opacity="0"/>')
    cursor_svg = (f'<g id="{uid}-x" opacity="0" pointer-events="none">'
                  + "".join(cur) + "</g>")

    svg = (f'<div class="cvbox" data-cv="{uid}">'
           f'<div class="tblwrap"><svg id="{uid}-svg" viewBox="0 0 {W:.0f} {H:.0f}" '
           f'class="csvg" role="img" aria-label="净值曲线 {uid}">'
           f'{grid_svg}{xt_svg}{"".join(lines)}{ends_svg}{mark_svg}{cursor_svg}'
           f'</svg></div>'
           f'<div class="cvhint">鼠标在图上移动 → 竖线与高亮点跟随，浮窗显示当日各序列的累计涨跌；'
           f'<b>单击可锁定/解锁</b>，方便对着某一天慢慢看（触屏可拖动）。</div>'
           f'</div>')

    data = dict(w=W, pl=pl, iw=iw, pt=pt, ih=ih, lo=lo, span=span, n=n,
                dates=list(dates),
                ser=[[k, label, color] for k, label, color, _w, _a in vis],
                v={k: [None if not np.isfinite(x) else round(float(x), 2) for x in a]
                   for k, _l, _c, _w, a in vis})
    return dict(html=svg, data=data,
                vis=[(k, label, color) for k, label, color, _w, _a in vis],
                last={k: float(a[-1]) for k, _l, _c, _w, a in vis})


def _health_html(m) -> str:
    """近 5 年「策略体检」横条（2026-10-27 用户需求，10-27 晚按「单段波段」重写）。

    用户明确要求：看的是**单段波段**（波谷→波峰 / 波峰→波谷），
    不是「从起点或最近低点的累计涨幅」。故改用 ZigZag（5% 阈值）识别波段：
      · 近5年最大波段涨幅 = max(各上涨段 谷→峰)
      · 近5年最大波段回撤 = min(各下跌段 峰→谷)
      · 当前波段 = 现在所处那一段的方向与幅度（起点日期一并给出）
    另保留「当前持仓段收益 + 其在所有持仓段中的百分位」。
    """
    if not m:
        return ""
    def _pc(v, d=1):
        return f"{v:+.{d}f}%" if isinstance(v, (int, float)) else "—"
    up = m.get("seg_max_up")
    dn = m.get("seg_max_dn")
    cdd = m.get("cur_dd") or 0.0
    cup = m.get("cur_up") or 0.0
    hr = m.get("hold_ret")
    hp = m.get("hold_pct")
    # 当前波段：谁非零就是谁（两者互斥）
    if cdd < -0.05:
        cur_v = f'<b style="color:{GREEN}">↓ 下跌中 {_pc(cdd)}</b>'
        cur_n = f'<i>自 {html.escape(str(m.get("peak_date") or ""))} 的峰起</i>'
    elif cup > 0.05:
        cur_v = f'<b style="color:{RED}">↑ 上涨中 {_pc(cup)}</b>'
        cur_n = f'<i>自 {html.escape(str(m.get("trough_date") or ""))} 的谷起</i>'
    else:
        cur_v = '<b style="color:#42474f">刚触新低/新高</b>'
        cur_n = ""
    hcol = (RED if (hr or 0) > 0 else (GREEN if (hr or 0) < 0 else "#42474f"))
    pct_txt = ""
    if isinstance(hp, (int, float)):
        _n = int(m.get("seg_n") or 0)
        if hp <= 5:
            pct_txt = (f'<i><b style="color:{GREEN}">近 5 年最差区间</b>'
                       f'（第 {hp:.0f} 百分位 / 共 {_n} 段）</i>')
        elif hp >= 95:
            pct_txt = (f'<i><b style="color:{RED}">近 5 年最佳区间</b>'
                       f'（第 {hp:.0f} 百分位 / 共 {_n} 段）</i>')
        else:
            pct_txt = f'<i>第 <b>{hp:.0f}</b> 百分位（共 {_n} 段，越高越好）</i>'
    cells = [
        ("近 5 年最大波段涨幅",
         f'<b style="color:{RED}">{_pc(up)}</b><i>单段 谷→峰</i>'),
        ("近 5 年最大波段回撤",
         f'<b style="color:{GREEN}">{_pc(dn)}</b><i>单段 峰→谷</i>'),
        ("当前波段", cur_v + cur_n),
        ("当前持仓段收益",
         f'<b style="color:{hcol}">{_pc(hr, 2)}</b>{pct_txt}'),
    ]
    body = "".join(f'<div class="hc"><span class="hk">{k}</span>{v}</div>'
                   for k, v in cells)
    return (f'<section class="hsec">'
            f'<div class="hhd"><h2 class="hh2">📊 近 5 年策略体检</h2>'
            f'<span class="htip">{html.escape(str(m.get("since") or ""))} 起 · '
            f'波段按 5% 阈值切分（{int(m.get("piv_n") or 0)} 个拐点：'
            f'{int(m.get("up_n") or 0)} 涨 / {int(m.get("dn_n") or 0)} 跌）</span></div>'
            f'<div class="hrow">{body}</div></section>')


def _mine_block_html(mine) -> str:
    """「我的实盘跟踪」区块（2026-10-26 用户需求）。

    数据来自 my_since.json 的 since/capital，由 recent_rotations 按当前生产口径
    （D3+P3+抄底）从启用日起理论复算。没配置 / 出错时返回空串，不占版面。
    """
    if not mine or mine.get("error"):
        return ""
    def _p(v, d=2):
        return f"{v:+.{d}f}%" if isinstance(v, (int, float)) else "—"
    up = float(mine.get("total") or 0)
    col = RED if up > 0 else (GREEN if up < 0 else "#42474f")
    cap = mine.get("capital")
    money = ""
    if isinstance(cap, (int, float)) and cap > 0:
        now = cap * float(mine.get("final") or 1.0)
        money = (f'<div class="msub">起始资金 <b>{cap:,.0f}</b> 元 → '
                 f'现在 <b>{now:,.0f}</b> 元'
                 f'（<b style="color:{col}">{now-cap:+,.0f}</b> 元）'
                 f'　｜　当前持 <b>{html.escape(str(mine.get("hold_name") or "—"))}</b>'
                 f'</div>')
    else:
        money = (f'<div class="msub">当前持 '
                 f'<b>{html.escape(str(mine.get("hold_name") or "—"))}</b>'
                 f'　｜　填入起始资金可换算成金额（见 my_since.json）</div>')
    _ann = mine.get("ann")
    _mdd = mine.get("mdd")
    cells = [
        ("起始日", f'<b>{html.escape(str(mine.get("since") or ""))}</b>', ""),
        ("已运行", f'<b>{int(mine.get("days_td") or 0)}</b> 个交易日', ""),
        ("累计收益", f'<b style="color:{col}">{_p(up)}</b>', ""),
        ("年化", f'<b style="color:{RED if (_ann or 0) > 0 else "#42474f"}">{_p(_ann)}</b>', ""),
        ("最大回撤", f'<b style="color:#42474f">{_mdd:.2f}%</b>' if isinstance(_mdd, (int, float)) else "<b>—</b>", ""),
        ("换仓 / 抄底", f'<b>{int(mine.get("trades_n") or 0)}</b> 笔 ／ '
                        f'<b>{int(mine.get("weak_n") or 0)}</b> 次', ""),
    ]
    body = "".join(f'<div class="mcell"><span class="mk">{k}</span>{v}</div>'
                   for k, v, _ in cells)
    return (f'<section class="minesec">'
            f'<div class="mhd"><h2 class="mh2">📌 我的实盘跟踪</h2>'
            f'<span class="mtip">自你启用日起，按现行口径理论复算</span></div>'
            f'<div class="mrow">{body}</div>{money}'
            f'<div class="mnote">理论值：假设每次严格按信号以收盘价成交，'
            f'不含滑点与手动干预，与真实成交可能有差。改 my_since.json 的 since '
            f'即可调整起点。</div></section>')


def _curve_block(curve):
    """把 recent_rotations._build_curve 的结果渲染成两张内联 SVG（近 5 年 + 近 2 个月放大）。

    序列：策略净值 / D3 单笔止损影子组合 / P3 追高止盈影子组合 / 上证指数 / 纳斯达克综合指数。
    两张图都带可移动光标（纯 JS、无外部依赖）；禁用 JS 时静态图仍完整。
    """
    if not curve:
        return ""
    dates = curve["dates"]
    n = len(dates)
    if n < 8 or "nv" not in (curve.get("series") or {}):
        return ""
    pct = _pct_series(curve["series"], n)
    if "nv" not in pct:
        return ""
    switches = curve.get("switches") or []

    big = _svg_chart(dates, pct, switches, W=920.0, H=300.0, uid="cv1")
    if not big:
        return ""

    # —— 近 2 个月放大：同一批序列，基准重置到该段首日 ——
    small, zoom_from = None, ""
    i0 = None
    try:
        z0 = pd.Timestamp(dates[-1]) - pd.Timedelta(days=ZOOM_DAYS - 1)
        for i, d in enumerate(dates):
            if pd.Timestamp(d) >= z0:
                i0 = i
                break
    except Exception:                              # noqa: BLE001
        i0 = None
    if i0 is not None and n - i0 >= 8:
        zpct = _rebase_pct(pct, i0)
        zsw = [s for s in switches if str(s.get("date", "")) >= dates[i0]]
        small = _svg_chart(dates[i0:], zpct, zsw, W=920.0, H=232.0, uid="cv2")
        if small:
            zoom_from = dates[i0]

    def _chips(pairs):
        out = []
        for k, label, color in pairs:
            v = pairs_v.get(k)
            if v is None:
                continue
            out.append(f'<span class="chip"><i style="background:{color}"></i>'
                       f'{label} <b style="color:{_color(v)}">{_fmt(v)}</b></span>')
        return "".join(out)

    pairs_v = {k: (float(v[-1]) if len(v) else None) for k, v in pct.items()}
    pairs = [(k, lb, c) for k, lb, c, _lw in CURVE_SERIES if k in pct]
    pairs.sort(key=lambda t: CURVE_LEGEND.index(t[0]) if t[0] in CURVE_LEGEND else 9)
    chips_html = _chips(pairs)

    zoom_block = ""
    if small:
        pairs_v = dict(small["last"])
        zpairs = list(small["vis"])
        zpairs.sort(key=lambda t: CURVE_LEGEND.index(t[0]) if t[0] in CURVE_LEGEND else 9)
        zchips_html = _chips(zpairs)
        zoom_block = f"""
<h3 class="sph3" style="margin-top:18px">🔍 近 2 个月放大（{zoom_from} 起 = 1.0）</h3>
<div class="note" style="margin-bottom:8px;padding:9px 12px">
{zchips_html}
<span class="dimtxt">　把上面同一批序列截到最近 {ZOOM_DAYS} 个自然日，并把<b>区间首日重定为 1.0</b>，
便于看清最近两个月的相对强弱；纵轴仍是自该日以来的累计涨跌，光标用法相同。</span>
</div>
{small["html"]}
"""

    d3_note = ("；<b style=\"color:" + CLR_D3 + "\">D3 单笔止损</b>是参照组合"
               "（任一笔换出时亏 ≥3% 就空仓 5 日，不改卖出条件），与「② 近期换仓复盘」"
               "里的 D3 卡片同源，<b>仅作参照</b>，非默认口径") \
        if "d3" in pct else ""
    p3_note = ("；<b style=\"color:#c2185b\">D3+P3 止盈</b>是<b>当前的默认执行组合</b>"
               "（D3「换出时亏≥3% 空仓 5 日」+ P3「头名 ROC60 进自身 95 分位空仓 20 日」"
               "两条独立规则叠加），与 ② 里的 D3+P3 卡片同源") \
        if "p3" in pct else ""

    return f"""<style>{_CV_CSS}</style>
<h3 class="sph3" style="margin-top:20px">📈 近 5 年净值曲线（策略 / D3 单笔止损 / D3+P3 止盈 vs 上证指数 · 纳斯达克综合指数）</h3>
<div class="note" style="margin-bottom:8px;padding:9px 12px">
{chips_html}
<span class="chip"><i style="background:{RED};width:8px;height:8px;border-radius:50%"></i>正向轮动切换</span>
<span class="chip"><i style="background:{GREEN};width:8px;height:8px;border-radius:50%"></i>防守切换</span>
<span class="dimtxt">　{html.escape(curve.get("info", ""))}。全部序列以窗口首个交易日 = 1.0 归一化，
纵轴为自该日起的累计涨跌；策略净值与「② 近期换仓复盘」同源同口径（前复权长历史 + 同一套规则复算）{d3_note}{p3_note}。</span>
</div>
{big["html"]}
{zoom_block}
<div class="sub" style="margin-top:8px">注：纳斯达克综合指数为美股日线，按公历日与 A 股交易日对齐（休市日沿用前值），
仅作全球风险偏好对比，不参与任何信号计算。曲线上每个圆点为一次系统换仓：
<b style="color:{RED}">● 红 = 正向轮动切换</b>（切到动量头名）、
<b style="color:{GREEN}">● 绿 = 防守切换</b>（因过热熔断切防守位或持币）。
两张图都可移动光标逐日查看；D3 曲线只回答「若启用会走成什么样」，不进入任何信号。</div>
<div hidden><script type="application/json" id="cv1-data">{_json_dump(big["data"])}</script>
<script type="application/json" id="cv2-data">{_json_dump(small["data"]) if small else "{}"}</script></div>
{_CV_JS}
"""


# ---------------------------------------------------------------- 近 1 年偏离区间
def _dev_win_stats(s, days: int, buy_line=None):
    """单窗口内 MA200 偏离的极值 / 当前分位 / 买线下方天数（s 已 dropna 升序）。

    窗口优先按**日历**切（「近 1 年」= 最近 365 天）；index 解析不了日期时
    按 244 日/年 折算成 bar 数兜底。样本 < 20 根返回 None。
    """
    if s is None or len(s) == 0:
        return None
    w = None
    try:
        t = pd.to_datetime(s.index)
        # ⚠️ `DatetimeIndex >= Timestamp` 返回 **numpy.ndarray** 而非 pandas 数组，
        #    早先多写一个 `.values` 直接 AttributeError → 整段静默退化成 tail()，
        #    窗口天数与声称的「近 1 年」不符（实测少 18 天）。用 timedelta 而非
        #    pd.Timedelta，避免 pandas 2.3 + numpy 2.x 的 generic-unit 弃用警告刷屏。
        w = s[t >= (t[-1] - timedelta(days=int(days)))]
    except Exception:                                  # noqa: BLE001
        w = None
    if w is None or len(w) < 20:
        w = s.tail(max(20, int(round(days * 244 / 365.0))))
    if len(w) < 20:
        return None

    def _d(x):
        try:
            return pd.Timestamp(x).strftime("%Y-%m-%d")
        except Exception:                              # noqa: BLE001
            return str(x)[:10]

    lo, hi = float(w.min()), float(w.max())
    cur = float(s.iloc[-1])
    pos = 50.0 if hi - lo < 1e-9 else (cur - lo) / (hi - lo) * 100.0
    nb = nbp = None
    if buy_line is not None:
        nb = int((w <= float(buy_line)).sum())
        nbp = nb / len(w) * 100.0
    return dict(
        lo=lo, lo_date=_d(w.idxmin()), hi=hi, hi_date=_d(w.idxmax()),
        cur=cur, pos=max(0.0, min(100.0, pos)), n=int(len(w)),
        start=_d(w.index[0]), end=_d(w.index[-1]),
        n_below=nb, below_pct=nbp,
    )


def _ma_dev_span(dev, *, days: int = 365, buy_line=None, extra_years=(3, 5)):
    """「近 N 年 MA200 偏离」的极值区间 —— 给当前偏离找一个历史坐标。

    dev      : 偏离序列（百分数，index 为交易日；可含 NaN）
    buy_line : 买线偏离（%）。给了就统计「窗口内有多少交易日低于买线」，
               用来回答「这种入场机会一年能遇到几次 / 有多稀缺」
    extra_years : 额外给这些年的**同一统计**（只取极值与当前分位）。

    ⚠️ 为什么必须给长窗口：只看 1 年会严重误判。实测 512040 近 1 年最深仅
       −5.03%、当前 3 分位（看着像一年最便宜），但近 3 年最深 −8.66%、近 5 年
       −12.76%，当前在 5 年区间只是 26 分位 ⇒ 单窗口会把「年内相对位置」误读成
       「历史低位」。长窗口就是用来防这个的。

    返回 dict(lo, lo_date, hi, hi_date, cur, pos, n, start, end, n_below,
              below_pct, extra=[{years, lo, lo_date, hi, pos, n}])；数据不足返回 None。
    """
    if dev is None:
        return None
    try:
        s = pd.Series(dev).astype(float).dropna()
    except Exception:                                  # noqa: BLE001
        return None
    if len(s) < 30:
        return None

    base = _dev_win_stats(s, days, buy_line)
    if base is None:
        return None
    extra = []
    for yr in extra_years or ():
        st = _dev_win_stats(s, 365 * int(yr), buy_line)
        if st is None or st["n"] < 244:                # 不足约 1 年样本就不硬凑
            continue
        extra.append(dict(years=int(yr), lo=st["lo"], lo_date=st["lo_date"],
                          hi=st["hi"], pos=st["pos"], n=st["n"]))
    base["extra"] = extra
    return base


def _span_word(pos: float) -> str:
    """把分位翻成一句人话（低 = 便宜，因为偏离越低越接近/跌破 MA200）。"""
    if pos <= 5.0:
        return "≈近 1 年最便宜"
    if pos <= 33.0:
        return "近 1 年偏便宜区"
    if pos <= 67.0:
        return "近 1 年中性区"
    if pos <= 95.0:
        return "近 1 年偏贵区"
    return "≈近 1 年最贵"


def _hspan_html(span, *, label: str = "近 1 年 MA200 偏离区间",
                buy_label: str = "", alt=None, alt_label: str = "指数口径") -> str:
    """渲染「历史参照」条带：近 1 年偏离极值 + 当前所处位置 + 买线下方天数 + 长窗口参照。

    与 512040 的暖色 action 条（.gidxbar）、512890 的冷色 action 条（.tvbar）
    用中性灰虚线区分 —— 这是**判贵贱的参照数据**，不是当日动作信号。
    alt(可选) = 另一口径的同一统计，只在末尾并排给极值，省宽度。
    """
    if not span:
        return ""
    RED_, GRN_ = RED, GREEN

    def _c(v):
        return RED_ if v > 0 else GRN_

    # ★ 2026-10-25 移动端改造：常显只留「窗口 + 当前值与分位」（判贵贱的核心结论），
    #   极值/买线下方天数/长窗口/口径/起止全部收进 <details> —— 手机首屏不再被
    #   十几行 span 淹没，且**无需 JS**；桌面端点一下 summary 即可展开。
    # ⚠️ 但「只报近 1 年分位会说反」是硬坑（512040 近1年 3 分位 vs 近5年 26 分位），
    #   所以把**最长的那个窗口分位**提到常显，折叠区只放极值等细节。
    _long = None
    for _e in (span.get("extra") or []):
        if _e.get("years") == 5:
            _long = _e
    if _long is None and span.get("extra"):
        _long = span["extra"][-1]
    _long_txt = (f'<span class="v dimtxt">｜近 {_long["years"]} 年仍 {_long["pos"]:.0f} 分位</span>'
                 if _long else "")
    head = [
        f'<span class="k">{html.escape(label)}（{span["n"]} 日）</span>',
        f'<span class="v">当前 <b style="color:{_c(span["cur"])}">{span["cur"]:+.2f}%</b>'
        f'位于 <b>{span["pos"]:.0f} 分位</b><i>{_span_word(span["pos"])}</i></span>',
        _long_txt,
    ]

    body = [
        f'<span class="v">最深回踩 <b style="color:{_c(span["lo"])}">{span["lo"]:+.2f}%</b>'
        f'<i>{html.escape(span["lo_date"])}</i></span>',
        f'<span class="v">最高冲高 <b style="color:{_c(span["hi"])}">{span["hi"]:+.2f}%</b>'
        f'<i>{html.escape(span["hi_date"])}</i></span>',
    ]
    if span.get("n_below") is not None:
        _bl = html.escape(buy_label) if buy_label else "买线"
        body.append(
            f'<span class="v">{_bl}下方 <b>{span["n_below"]}/{span["n"]}</b> 日'
            f'（{span["below_pct"]:.1f}%）</span>')
    if span.get("extra"):
        # 每个长窗口单独一格（不合并成一句）：单格文字短，flex-wrap 才能在各宽度下
        # 自然换行、不会撑出横向滚动条；合并成一长句在 600px 屏会超出容器。
        for e in span["extra"]:
            body.append(
                f'<span class="v">更长期 · {e["years"]} 年 <b>{e["lo"]:+.2f}%</b>'
                f'<i>当前 {e["pos"]:.0f} 分位</i></span>')
    if alt:
        body.append(
            f'<span class="v dimtxt">{html.escape(alt_label)} '
            f'{alt["lo"]:+.2f}% ~ {alt["hi"]:+.2f}%</span>')
    body.append(f'<span class="v dimtxt">窗口 {span["start"]} ~ {span["end"]}</span>')

    return ('<div class="hspan">' + "".join(head)
            + '<details class="hdet"><summary>极值 · 长窗口 · 买点稀缺度</summary>'
            + '<div class="hdetb">' + "".join(body) + "</div></details></div>")


# ---------------------------------------------------------------- 指数口径对照
# 512040 价值ETF富国（场内简称 价值100ETF）跟踪【中证国信价值指数 931052.CSI】。
IDX_OF_DEF = {"512040": ("931052", "中证国信价值")}


def build_index_cmp(df, def_code, ma=200, index_code=None, lookback=500):
    """基金口径 vs 其跟踪指数口径 的 MA200 偏离对照（防守位区域渲染用）。

    两者口径不同，偏离天然不等：
      · 基金 = **前复权价**（分红已加回）→ 历史价被压低、MA200 偏平 → 偏离系统性偏高；
      · 指数 = **价格指数**（分红不加回，成分股除息直接扣点位）→ 偏离系统性偏低。
    实测近 500 个交易日口径差（基金 − 指数）均值约 +1.6~2.1pp，
    因此同一条 −3% 买线对两者含义不同，不能直接互换阈值。

    返回 dict；取数失败/样本不足返回 None（报告退化为只显示基金口径）。
    """
    info = IDX_OF_DEF.get(def_code) if index_code is None else (index_code, "")
    if df is None or not info:
        return None
    index_code, index_name = info
    try:
        # ⚠️ 必须用「长历史 CSV 打底 + 在线补尾」的合并序列，不能只用在线：
        #    512040 在 2023-10-11 有份额折算，新浪日线（不复权）留一根 −50.1% 假跳空，
        #    单靠常数复权因子修不掉。近 1 年窗口虽不含它，但「近 3/5 年参照」和
        #    MA200 的早期回溯段会中招 ⇒ 用已修正的长历史（整段按复权比重定基）。
        sf, _vnotes = _ve_merged_series(df, def_code, ma=ma)
        if sf is None or len(sf) < ma + 5:
            return None
        maf = sf.rolling(ma).mean()
        devf = (sf / maf - 1.0) * 100.0

        import online_data as od
        di = od.fetch_csindex_daily(index_code, beg="2019-01-01")
        if di is None or len(di) < ma + 5:
            return None
        si = di.set_index("date")["close"].astype(float).sort_index()
        mai = si.rolling(ma).mean()
        devi = (si / mai - 1.0) * 100.0

        cmp = pd.DataFrame({"f": devf, "i": devi}).dropna()
        if cmp.empty:
            return None
        cmp["gap"] = cmp["f"] - cmp["i"]
        rec = cmp.tail(lookback)
        both = int(((rec["i"] <= -3.0) & (rec["f"] > -3.0)).sum())
        return dict(
            code=index_code, name=index_name,
            f_date=sf.index[-1].strftime("%Y-%m-%d"), f_close=float(sf.iloc[-1]),
            f_ma=float(maf.iloc[-1]), f_dev=float(devf.iloc[-1]),
            i_date=si.index[-1].strftime("%Y-%m-%d"), i_close=float(si.iloc[-1]),
            i_ma=float(mai.iloc[-1]), i_dev=float(devi.iloc[-1]),
            gap=float(cmp["gap"].iloc[-1]),
            gap_mean=float(rec["gap"].mean()),
            gap_p10=float(rec["gap"].quantile(0.10)),
            gap_p90=float(rec["gap"].quantile(0.90)),
            n_gap_days=both, n_days=int(len(rec)),
            ohlc_bars=int(len(si)),
            # 近 1 年偏离区间（2026-10-25 用户要求）：给「当前偏离算不算便宜」一个历史坐标。
            # 两口径都算 —— 基金口径用于实盘判断，指数口径只作交叉参照。
            f_span=_ma_dev_span(devf, buy_line=-3.0),
            i_span=_ma_dev_span(devi, buy_line=-3.0),
        )
    except Exception:                                  # noqa: BLE001
        return None


# ------------------------------------------------- 防守位策略净值曲线（价值引擎回测）
def _ve_merged_series(df, code, ma=200):
    """取标的的「前复权长序列」：长历史 CSV 打底 + 本次运行在线数据补尾部。

    返回 (Series 按日期升序, notes 列表)。数据不足或没有该标的返回 (None, notes)。

    ⚠️ 为什么不能直接用在线序列：新浪日线不回溯复权，份额拆分日会留一根假跳空
    （512040 在 2023-10-11 有 −50.1%、512890 在 2021-10-25 有 −51.1%），
    而 `fetch_online` 报的 factors 是单常数因子，修不掉序列**中段**事件。
    """
    notes = []
    if df is None or not code:
        return None, notes
    g = df[df["code"].astype(str).str.zfill(6) == code]
    if g.empty:
        return None, notes
    onl = (g.sort_values("date").set_index("date")["close"].astype(float)
           .pipe(lambda s: s[~s.index.duplicated(keep="last")]).sort_index())
    if onl.empty:
        return None, notes

    s = None
    try:
        import online_data as od
        path = od.find_adj_csv()[0]
        hist = pd.read_csv(path, parse_dates=["date"])
        h = hist[hist["code"].astype(str).str.zfill(6) == code]
        if not h.empty:
            hs = (h.sort_values("date").set_index("date")["close"].astype(float)
                  .pipe(lambda x: x[~x.index.duplicated(keep="last")]).sort_index())
            common = hs.index.intersection(onl.index)
            if len(common) >= 20:
                # 把历史段整体换算到「当前前复权基准」：分红/拆分后比值会 ≠1，
                # 只缩放尾部会造成接缝跳空，必须整段乘。
                ratio = float(onl.loc[common[-1]]) / float(hs.loc[common[-1]])
                if abs(ratio - 1.0) > 1e-4:
                    notes.append(f"历史段按时点复权比 {ratio:.4f} 整体重定基")
                hs = hs * ratio
                extra = onl[sorted(set(onl.index) - set(hs.index))]
                s = pd.concat([hs, extra]).sort_index()
                s = s[~s.index.duplicated(keep="last")]
                notes.append(f"长历史 CSV（{hs.index[0]:%Y-%m-%d} 起）"
                             + (f" + 在线补 {len(extra)} 日" if len(extra) else ""))
    except Exception as e:                             # noqa: BLE001
        notes.append(f"长历史不可用（{type(e).__name__}）→ 仅用在线序列")

    if s is None or len(s) < ma + 30:
        s = onl
        notes.append("⚠ 仅用在线序列，历史可能不足")
    return s, notes


def build_ve_track(df, code, *, ma=200, buy=1.00, sell=1.06, name=""):
    """单标的「价值引擎」每日跟踪快照（给 512890 这类第二条策略用）。

    规则与 `build_ve_curve` 同一状态机，但只取**最新一根**的状态与操作提示：
      · 收盘 < MA200×buy  → 买点（未持有则买入，已持有则继续持有）
      · 收盘 > MA200×sell → 卖点（已持有则卖出，已持币则继续持币）
      · 中间区间           → 维持上一状态（不给动作）
    返回 dict；数据不足返回 None。
    """
    s, notes = _ve_merged_series(df, code, ma=ma)
    if s is None or len(s) < ma:
        return None

    mav = s.rolling(ma).mean()
    state, hold = [], False
    for t in s.index:
        px = float(s.loc[t])
        mv = float(mav.loc[t]) if mav.loc[t] == mav.loc[t] else None
        if mv:
            if px < mv * buy:
                hold = True
            elif hold and px > mv * sell:
                hold = False
        state.append(hold)
    st = pd.Series(state, index=s.index, dtype=bool)

    last = s.index[-1]
    px = float(s.loc[last])
    mv = float(mav.loc[last])
    dev = (px / mv - 1.0) * 100.0
    holding = bool(st.iloc[-1])

    # 距两条线的位置（pp）
    to_buy = dev - (buy - 1.0) * 100.0       # >0 = 高于买线（还差多少才能买）
    to_sell = (sell - 1.0) * 100.0 - dev     # >0 = 距卖线还有多少
    if dev < (buy - 1.0) * 100.0:
        zone, act = "buy", ("买入" if not holding else "继续持有")
    elif dev > (sell - 1.0) * 100.0:
        zone, act = "sell", ("卖出" if holding else "保持持币")
    else:
        zone, act = "mid", ("继续持有" if holding else "继续持币（不追）")

    # 上一次状态切换日（买点/卖点发生的日期）
    sw = st.astype(int).diff().fillna(0)
    last_buy = s.index[sw == 1]
    last_sell = s.index[sw == -1]
    return dict(
        code=code, name=name, ma=ma, buy=buy, sell=sell,
        date=last, close=px, ma200=mv, dev=dev,
        buy_line=mv * buy, sell_line=mv * sell,
        holding=holding, zone=zone, action=act,
        to_buy=to_buy, to_sell=to_sell,
        last_buy=(last_buy[-1] if len(last_buy) else None),
        last_sell=(last_sell[-1] if len(last_sell) else None),
        n=len(s), d0=s.index[0], notes=notes,
        # 近 1 年偏离区间（2026-10-25）：买线偏离 = (buy-1)*100，
        # 例 512890 回踩即买 → 0.0；512040 跌 3% 买 → −3.0。
        span=_ma_dev_span((s / mav - 1.0) * 100.0,
                          buy_line=(buy - 1.0) * 100.0),
    )


def build_ve_curve(df, def_code, *, ma=200, buy=0.97, sell=1.06, years=5):
    """512040 价值引擎「买入 / 持有 / 卖出」状态机的近 N 年回测净值。

    规则与报告里「防守位操作指南」表格完全一致：
      · 收盘 < MA200×0.97  → 买入（进入持有状态）
      · 收盘 > MA200×1.06  → 卖出（转为持币）
      · 中间区间            → 维持上一状态
      · 未满足买点          → 持币（不追高）
    口径：**前复权**、**零手续费**、初始净值 = 1。

    收益归属：用 **k-1 日收盘后的状态** 决定 k-1→k 的收益（信号当日成交、次日才吃涨跌），
    与 `etf_backtest_long.backtest()` 同口径，不含未来函数。

    数据：长历史前复权 CSV（已核验）打底 + 本次运行的前复权在线数据补尾部。
    ⚠️ 不能直接用在线序列做 5 年窗口：新浪日线在 2023-10-11 有一根 −50.1% 的
    **拆分未复权假跳空**（512040 份额折算），单靠常数因子修不掉；长历史 CSV 是干净的。

    返回 dict（净值为 list，供内联 SVG 渲染）；数据不足返回 None。
    """
    if df is None or not def_code:
        return None
    s, notes = _ve_merged_series(df, def_code, ma=ma)
    if s is None or len(s) < ma + 30:
        return None
    onl = (df[df["code"].astype(str).str.zfill(6) == def_code]
           .sort_values("date").set_index("date")["close"].astype(float)
           .pipe(lambda x: x[~x.index.duplicated(keep="last")]).sort_index())

    mav = s.rolling(ma).mean()
    state, hold = [], False
    for t in s.index:
        px, mv = float(s.loc[t]), float(mav.loc[t]) if mav.loc[t] == mav.loc[t] else None
        if mv:
            if px < mv * buy:
                hold = True
            elif hold and px > mv * sell:
                hold = False
        state.append(hold)
    st = pd.Series(state, index=s.index, dtype=bool)

    cut = s.index[-1] - pd.DateOffset(years=years)
    w, sw = s[s.index >= cut], st[s.index >= cut]
    if len(w) < 60:
        return None

    nav, navs, events = 1.0, [1.0], []
    if bool(sw.iloc[0]):
        events.append(dict(date=w.index[0], act="HOLD",
                           note="窗口首日已处于持有状态（买点发生在窗口之前）"))
    for i in range(1, len(w)):
        if bool(sw.iloc[i - 1]):                       # k-1 持仓 → 吃 k-1→k 的涨跌
            nav *= float(w.iloc[i]) / float(w.iloc[i - 1])
        navs.append(nav)
        if bool(sw.iloc[i]) != bool(sw.iloc[i - 1]):
            events.append(dict(date=w.index[i], act="BUY" if bool(sw.iloc[i]) else "SELL",
                               note=""))
    navs = pd.Series(navs, index=w.index)
    bh = w / float(w.iloc[0])

    yrs = (w.index[-1] - w.index[0]).days / 365.25
    mdd = float((navs / navs.cummax() - 1).min() * 100)
    return dict(
        code=def_code, ma=ma, buy=buy, sell=sell, years=years,
        d0=w.index[0], d1=w.index[-1], n=len(w), yrs=yrs,
        dates=[d.strftime("%Y-%m-%d") for d in w.index],
        nav=[float(v) for v in navs.values],
        bh=[float(v) for v in bh.values],
        hold=[bool(v) for v in sw.values],
        events=events,
        final=float(navs.iloc[-1]), ret=(float(navs.iloc[-1]) - 1) * 100,
        cagr=(float(navs.iloc[-1]) ** (1 / yrs) - 1) * 100 if yrs > 0 else 0.0,
        mdd=mdd,
        hold_rate=float(sw.mean() * 100),
        n_buy=sum(1 for e in events if e["act"] == "BUY"),
        n_sell=sum(1 for e in events if e["act"] == "SELL"),
        bh_final=float(bh.iloc[-1]), bh_ret=(float(bh.iloc[-1]) - 1) * 100,
        bh_mdd=float((bh / bh.cummax() - 1).min() * 100),
        holding_now=bool(sw.iloc[-1]),
        last_buy=next((e["date"] for e in reversed(events) if e["act"] == "BUY"), None),
        notes=notes,
    )


def _ve_curve_block(vc):
    """把 build_ve_curve 的结果渲染成内联 SVG（净值曲线 + 持仓色带 + 买卖点）。"""
    if not vc:
        return ""
    nav = vc["nav"]
    bh = vc["bh"]
    hold = vc["hold"]
    n = len(nav)
    if n < 30:
        return ""

    W_, H_ = 980.0, 300.0
    pl, pr, pt = 62.0, 96.0, 16.0
    pb = 46.0                                   # 底部留给日期刻度 + 持仓色带
    iw, ih = W_ - pl - pr, H_ - pt - pb

    vals = np.asarray(nav + bh, dtype=float)
    lo, hi = float(vals.min()), float(vals.max())
    span = max(hi - lo, 1e-6)
    lo, hi = lo - span * 0.10, hi + span * 0.10
    span = hi - lo

    def ypx(v):
        return pt + ih * (1.0 - (v - lo) / span)

    def xpx(i):
        return pl + iw * (i / (n - 1))

    # 纵向网格 + 刻度（净值，保留 2 位）
    step = next((s for s in (0.05, 0.1, 0.2, 0.25, 0.5, 1.0) if span / s <= 6), span / 5)
    g0 = np.ceil(lo / step) * step            # ⚠️ 用 ceil：floor 会画到绘图区外（如 0.50 落到轴下）
    grid = []
    for gv in np.arange(g0, hi + step / 2, step):
        yy = ypx(float(gv))
        grid.append(f'<line x1="{pl:.1f}" y1="{yy:.1f}" x2="{W_ - pr:.1f}" y2="{yy:.1f}"'
                    f' stroke="#dfe3e8" stroke-width="1"/>'
                    f'<text x="{pl - 8:.1f}" y="{yy + 4:.1f}" class="cg">{gv:.2f}</text>')
    grid_svg = "".join(grid)

    # 持仓色带（底部）：蓝=持有 512040，灰=持币
    rb_y, rb_h = H_ - pb + 12, 9.0
    bands, i = [], 0
    while i < n:
        j = i
        while j + 1 < n and hold[j + 1] == hold[i]:
            j += 1
        x0, x1 = xpx(i), xpx(min(j + 1, n - 1))
        if hold[i]:
            bands.append(f'<rect x="{x0:.1f}" y="{rb_y:.1f}" width="{max(x1 - x0, 1.2):.1f}"'
                         f' height="{rb_h:.1f}" fill="#3d57e5" opacity="0.42"/>')
        i = j + 1
    bands_svg = "".join(bands)
    band_frame = (f'<rect x="{pl:.1f}" y="{rb_y:.1f}" width="{iw:.1f}" height="{rb_h:.1f}"'
                  f' fill="#eef0f3" rx="2"/>')

    # 折线
    bh_pts = " ".join(f"{xpx(i):.1f},{ypx(bh[i]):.1f}" for i in range(n))
    nav_pts = " ".join(f"{xpx(i):.1f},{ypx(nav[i]):.1f}" for i in range(n))
    lines = (f'<polyline points="{bh_pts}" fill="none" stroke="#98a1ad" stroke-width="1.4"'
             f' stroke-dasharray="5 4" stroke-linejoin="round"/>'
             f'<polyline points="{nav_pts}" fill="none" stroke="#3d57e5" stroke-width="2.2"'
             f' stroke-linejoin="round" stroke-linecap="round"/>')

    # 买卖点
    d2i = {d: k for k, d in enumerate(vc["dates"])}
    marks = []
    for ev in vc["events"]:
        # events 里的 date 是 Timestamp，d2i 的键是 "YYYY-MM-DD" 字符串，必须转换后查
        k = d2i.get(pd.Timestamp(ev["date"]).strftime("%Y-%m-%d"))
        if k is None:
            continue
        x, y = xpx(k), ypx(nav[k])
        if ev["act"] == "BUY":
            marks.append(f'<path d="M {x:.1f} {y - 11:.1f} L {x - 4.4:.1f} {y - 3.6:.1f}'
                         f' L {x + 4.4:.1f} {y - 3.6:.1f} Z" fill="{RED}">'
                         f'<title>{ev["date"]} 买入（净值 {nav[k]:.3f}）</title></path>')
        elif ev["act"] == "SELL":
            marks.append(f'<path d="M {x:.1f} {y + 11:.1f} L {x - 4.4:.1f} {y + 3.6:.1f}'
                         f' L {x + 4.4:.1f} {y + 3.6:.1f} Z" fill="{GREEN}">'
                         f'<title>{ev["date"]} 卖出（净值 {nav[k]:.3f}）</title></path>')
        else:                                          # 窗口首日已持仓
            marks.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.4" fill="#3d57e5"'
                         f' stroke="#fff" stroke-width="1.4">'
                         f'<title>{ev["date"]} {ev["note"]}</title></circle>')
    marks_svg = "".join(marks)

    # 末端标注（两条线）
    ends = []
    for v, color, label in ((nav[-1], "#3d57e5", "策略"),
                            (bh[-1], "#8a929c", "买入持有")):
        yy = ypx(v)
        ends.append(f'<circle cx="{xpx(n - 1):.1f}" cy="{yy:.1f}" r="3.2" fill="{color}"/>'
                    f'<text x="{W_ - pr + 6:.1f}" y="{yy + 4:.1f}" class="ce" fill="{color}">'
                    f'{label} {v:.2f}</text>')
    ends_svg = "".join(ends)

    # 日期刻度
    xt = []
    want = [0, n // 4, n // 2, 3 * n // 4, n - 1]
    for k in sorted(set(want)):
        xt.append(f'<text x="{xpx(k):.1f}" y="{H_ - 4:.1f}" class="cx">'
                  f'{vc["dates"][k][:7]}</text>')
    xt_svg = "".join(xt)

    # 顶部信息条
    def _cc(v):
        return RED if v >= 0 else GREEN

    chips = (
        f'<span class="chip"><i style="background:#3d57e5"></i>价值引擎策略 '
        f'<b style="color:{_cc(vc["ret"])}">{vc["ret"]:+.2f}%</b></span>'
        f'<span class="chip"><i style="background:#98a1ad"></i>同期买入持有 '
        f'<b style="color:{_cc(vc["bh_ret"])}">{vc["bh_ret"]:+.2f}%</b></span>'
        f'<span class="chip">最大回撤 <b style="color:{GREEN}">{vc["mdd"]:.2f}%</b>'
        f'（买入持有 {vc["bh_mdd"]:.2f}%）</span>'
        f'<span class="chip">年化 <b style="color:{_cc(vc["cagr"])}">{vc["cagr"]:+.2f}%</b></span>'
        f'<span class="chip">持仓 <b>{vc["hold_rate"]:.1f}%</b> 交易日</span>'
        f'<span class="chip">买卖 <b>{vc["n_buy"]}</b> 买 / <b>{vc["n_sell"]}</b> 卖</span>'
        f'<span class="chip">当前 <b>{"持仓中" if vc["holding_now"] else "持币中"}</b>'
        + (f'（{vc["last_buy"]:%m-%d} 买入）'
           if vc["holding_now"] and vc["last_buy"] is not None else "")
        + '</span>'
    )

    notes = "".join(f"<br>· {html.escape(x)}" for x in (vc.get("notes") or []))

    return f"""
<h3 class="sph3" style="margin-top:22px">📉 {vc['code']} 价值引擎 · 近 {vc['years']} 年回测净值曲线</h3>
<div class="note" style="margin-bottom:8px;padding:9px 12px">
{chips}
<div class="dimtxt" style="margin-top:6px;line-height:1.75">
口径：<b>前复权</b>收盘价 · <b>零手续费</b> · 初始净值 <b>1.00</b> ·
窗口 {vc['d0']:%Y-%m-%d} ~ {vc['d1']:%Y-%m-%d}（{vc['n']} 个交易日 / {vc['yrs']:.2f} 年）。
规则：收盘 &lt; MA{vc['ma']}×{vc['buy']:.2f} 买入 → 收盘 &gt; MA{vc['ma']}×{vc['sell']:.2f} 卖出 →
中间区间维持上一状态，未达买点持币。收益按「前一日收盘后的状态」结算，信号当日成交、次日才计入涨跌，无未来函数。
<b>▲ 红 = 买入　▼ 绿 = 卖出</b>，底部色带 = 持有 512040 的区间（空白即持币）。
{notes}
</div>
</div>
<div class="tblwrap"><svg viewBox="0 0 {W_:.0f} {H_:.0f}" class="csvg" role="img"
  aria-label="{vc['code']} 价值引擎近 {vc['years']} 年回测净值：策略 {vc['ret']:+.2f}%，同期买入持有 {vc['bh_ret']:+.2f}%，最大回撤 {vc['mdd']:.2f}%">
{grid_svg}{band_frame}{bands_svg}{lines}{ends_svg}{marks_svg}{xt_svg}
</svg></div>
"""


def render_html(*, pivot, window, sticky, defensive, snap, ranked, cols, steps,
                swap_days, tie_days, head1, head2, lead, overheated,
                overheat_dev, overheat_lead, info, source, now,
                watch=(), info_watch=(), freshness=(False, ""), live=(False, ""),
                recent=None, adj_note="", yearly=None, idx=None, ve_curve=None,
                ve_track=None, ve2_curve=None):
    cells = [s.momentum for s in snap]
    vmax = max((abs(v) for m in cells for v in m.values() if v == v), default=1.0)

    def mcell(v, is_head):
        style = f"background:{_bg(v, vmax)};color:{_color(v)};font-weight:600"
        mark = " ★" if is_head else ""
        return f'<td class="num" style="{style}">{_fmt(v, pct=False, nd=1)}{mark}</td>'

    # 各列头名集合 (粘性带后)
    eff_heads = {st["eff_head"] for st in steps}

    # ---- 防守位 / 价值引擎判定（排名表防守行、③ 操作指引、防守位指南三处共用同一结论）----
    _def = next((r for r in snap if r.role == "defensive"), None)
    def_code = _def.code if _def else "防守位"
    def_nm = _def.name if _def else ""
    def_close = _def.close if _def else float("nan")
    ve_state = _def.ve_state if _def else "不适用"
    ve_ratio = _def.ve_ratio if _def else float("nan")
    ve_ma200 = _def.ma200 if _def else float("nan")
    ve_ok = ve_state in ("可买入", "应持有")     # 防守期该不该持有防守位
    # 只有真需要关注防守位（头名过热熔断）时, 才在防守位上标注"应该持有/应该持币"
    def_now_txt = ("现在应该持有" if ve_ok else "现在应该持币")

    # 排名表（末列 = MA20，由 乖离 = P/MA20−1 反推：MA20 = P / (1 + 乖离%)）
    def _ma20(r):
        if r.dev == r.dev and r.close > 0:
            return r.close / (1.0 + r.dev / 100.0)
        return float("nan")

    # 「切换至今」：该标的最近一次被系统买入 → 至今（或已换出）的价格涨跌。
    # 数据来自「② 近期换仓复盘」的同一次长历史回测（recent["since"]），保证两处数字一致。
    _since = (recent or {}).get("since") or {}
    _asof = (recent or {}).get("asof") or pivot

    def _since_cell(code):
        """返回 (显示文本, 颜色, 悬停明细, 是否陈旧, 移动端副文本)。

        ⚠️ 陈旧标记：若「买入日」距锚定日超过 120 个**日历日**（约半年），数字虽对
        但代表的是很久以前那一段行情（例 510300 最近一次被买入是 2023-07-31，
        收益率是三年前的），直接摆在「今日涨跌」旁边极易被误读为近期表现
        ⇒ 加淡色 + 虚线下划线。注意用「距今」而不是「该段持仓天数」——
        后者只描述那一段有多长，510300 那一段本身只有 15 个交易日。

        ★ 第 5 项「移动端副文本」（2026-10-25）：手机没有 hover，title 完全看不到，
          把「持仓/已换出 + 天数」直接渲染成 <small> 挂在单元格内，桌面端 CSS 隐藏。
        """
        d = _since.get(code)
        if not d:
            return "—", "#98a1ad", "回测区间内从未被系统买入", False, ""
        r_ = d.get("ret")
        if r_ is None or r_ != r_:
            return "—", "#98a1ad", "缺价，无法计算", False, ""
        v = r_ * 100.0
        _days = d.get("days") or 0
        tip = (f"{d['start']} 买入 → {d['end']}"
               f"（{'持有中' if d['holding'] else '已换出'}，持仓 {_days} 个交易日）")
        mob = f"{'持仓' if d['holding'] else '已换出'} {_days} 日"
        try:
            _gap = (pd.Timestamp(_asof) - pd.Timestamp(d["start"])).days
        except Exception:                                  # noqa: BLE001
            _gap = 0
        stale = bool(_gap > 120 and not d["holding"])
        if stale:
            tip += f"　⚠ 该买入日距 {_asof} 已 {_gap} 天，非近期表现"
            return f'<span class="stale">{v:+.2f}%</span>', _color(v), tip, True, mob
        return f"{v:+.2f}%", _color(v), tip, False, mob

    rank_rows = []
    # 「② 近期换仓复盘」同口径复算出的当前真实持仓（用于排名表里标出「你现在拿着哪只」）
    _held_row = None
    if recent and not recent.get("error"):
        try:
            _held_row = (recent["rows"][-1] or {}).get("to")
        except Exception:                              # noqa: BLE001
            _held_row = None
    for i, r in enumerate(ranked, 1):
        _sc, _scl, _stip, _scl2, _smob = _since_cell(r.code)
        _rcls = []
        if i == 1:
            _rcls.append("row-top1")
        if _held_row and r.code == _held_row:
            _rcls.append("row-held")
        _tr = ' class="%s"' % " ".join(_rcls) if _rcls else ""
        rank_rows.append(
            f'<tr{_tr}>'
            f'<td class="num c-idx">{i}</td>'
            f'<td class="mono c-code">{r.code}</td>'
            f'<td class="c-nm">{html.escape(r.name)}'
            f'<span class="code-sub">{r.code}</span></td>'
            f'<td class="num c-m20" style="color:{_color(r.momentum[window])};font-weight:600">'
            f'{_fmt(r.momentum[window])}</td>'
            f'<td class="num c-dev" style="color:{_color(r.dev)}">{_fmt(r.dev)}</td>'
            f'<td class="num c-since" title="{html.escape(_stip)}" '
            f'style="color:{_scl};font-weight:600">{_sc}'
            f'<small class="since-sub">{html.escape(_smob)}</small></td>'
            f'<td class="num c-px">{r.close:.3f}</td>'
            f'<td class="num c-chg" style="color:{_color(r.chg1)};font-weight:600">{_fmt(r.chg1)}</td>'
            f'<td class="num c-ma20">{_ma20(r):.3f}</td>'
            "</tr>")
    for r in snap:
        if r.role != "defensive":
            continue
        # ⚠️ 只有真进入防守期（头名过热熔断）才在防守行上打"应该持有/应该持币"标记,
        #    否则正常轮动时该行只作参考, 避免用户误以为要卖出手上的头名。
        if overheated:
            _mark = (f'<span class="tag defhot">{def_now_txt} {r.code}</span>'
                     if ve_ok else
                     f'<span class="tag defcash">现在应该持币（不买 {r.code}）</span>')
        else:
            _mark = '<span class="dim2">正常轮动中 · 本轮无需关注</span>'
        # 指数口径徽标：把「基金 MA200 偏离」与「跟踪指数 MA200 偏离」并排展示。
        # ⚠️ 2026-10-20 排版改造：徽标原是塞进「名称」单元格里的 nowrap 内联块（宽 347px），
        #    它把名称列顶到 359px、整表 697px，导致右侧排名卡出现横向滚动条。
        #    现改为**独占一行**（colspan 跨 8 列的子行），文字可自然换行，表格最小宽度回落到内容本身。
        _ibadge = ""
        if idx:
            _fdv, _idv = float(idx["f_dev"]), float(idx["i_dev"])
            _cfd = RED if _fdv > 0 else GREEN
            _cid = RED if _idv > 0 else GREEN
            _ibadge = (f'<span class="idxbadge">MA200 偏离 ｜ 基金 '
                       f'<b style="color:{_cfd}">{_fdv:+.2f}%</b> ｜ '
                       f'指数{idx["code"]} <b style="color:{_cid}">{_idv:+.2f}%</b>'
                       f'（差 {idx["gap"]:+.2f}pp）</span>')
        _sc2, _scl2, _stip2, _sstale2, _smob2 = _since_cell(r.code)
        rank_rows.append(
            "<tr class='def'>"
            f'<td class="num c-idx">—</td>'
            f'<td class="mono c-code">{r.code}</td>'
            f'<td class="c-nm">{html.escape(r.name)} (防守)'
            f'<span class="code-sub">{r.code}</span></td>'
            f'<td class="num c-m20" style="color:{_color(r.momentum[window])}">{_fmt(r.momentum[window])}</td>'
            f'<td class="num c-dev" style="color:{_color(r.dev)}">{_fmt(r.dev)}</td>'
            f'<td class="num c-since" title="{html.escape(_stip2)}" style="color:{_scl2}">{_sc2}'
            f'<small class="since-sub">{html.escape(_smob2)}</small></td>'
            f'<td class="num c-px">{r.close:.3f}</td>'
            f'<td class="num c-chg" style="color:{_color(r.chg1)}">{_fmt(r.chg1)}</td>'
            f'<td class="num c-ma20">{_ma20(r):.3f}</td>'
            "</tr>")
        rank_rows.append(
            "<tr class='defsub'><td colspan='9'>"
            f'<div class="defbar">{_ibadge}{_mark}</div>'
            "</td></tr>")

    # ---- 年度表现（近 6 年，每年三根竖柱：基础轮动 / D3 单笔止损 / P3 追高止盈）----
    # yearly: [{year, ret, mdd, ...}] 来自长历史回测，ret/mdd 均为小数(0.5=50%)。
    # D3 / P3 的逐年数据从 recent['d3']['yearly'] / recent['p3']['yearly'] 取（同一次影子回测，不重算）。
    _yl = [d for d in (yearly or []) if d.get("year")]
    _yl = sorted(_yl, key=lambda d: str(d["year"]))[-6:]     # 近 6 年
    for _d in _yl:                            # 统一转成百分数
        if _d.get("ret") is not None:
            _d["ret"] = float(_d["ret"]) * 100.0
        if _d.get("mdd") is not None:
            _d["mdd"] = float(_d["mdd"]) * 100.0

    def _yy(src):
        """影子组合的逐年收益 → {year_str: {ret, mdd}}（百分数）"""
        out = {}
        for _d in (src or []):
            _r, _m = _d.get("ret"), _d.get("mdd")
            out[str(_d.get("year"))] = dict(
                ret=(float(_r) * 100.0 if _r is not None and _r == _r else None),
                mdd=(float(_m) * 100.0 if _m is not None and _m == _m else None))
        return out

    _d3y = _yy(((recent or {}).get("d3") or {}).get("yearly"))
    _p3y = _yy(((recent or {}).get("p3") or {}).get("yearly"))

    # 三条序列：[(键, 显示名, 颜色, {year: ret}, {year: mdd})]
    # ★ 第一根 = D3+P3 止盈（默认执行组合，2026-10-25 用户拍板升级为主口径）；
    #   后两根「基础轮动 / D3 止损」降为参照。
    _yseries = [
        ("p3", "D3+P3+抄底（默认）", CLR_P3, {k: v["ret"] for k, v in _p3y.items()},
         {k: v["mdd"] for k, v in _p3y.items()}),
        ("nv", "基础轮动", CLR_STRAT, {str(d["year"]): d.get("ret") for d in _yl},
         {str(d["year"]): d.get("mdd") for d in _yl}),
        ("d3", "D3 止损",  CLR_D3, {k: v["ret"] for k, v in _d3y.items()},
         {k: v["mdd"] for k, v in _d3y.items()}),
    ]
    _yv = [v for _k, _n, _c, rr, _m in _yseries for v in rr.values()
           if v is not None and v == v]
    _vmax = max([0.0] + _yv)
    _vmin = min([0.0] + _yv)
    _ytop = _vmax + max(7.0, abs(_vmax) * 0.24)     # 柱顶留标签空间
    _ybot = _vmin - max(5.0, abs(_vmin) * 0.40)     # 柱底留标签空间
    _yspan = (_ytop - _ybot) or 1.0

    # ---- SVG 竖柱分组图（viewBox 定幅，宽度 100% 自适应）----
    _VW, _VH = 300.0, 224.0
    _PX0, _PX1 = 7.0, 293.0                        # 绘图区左右
    _PYT, _PYB = 30.0, 178.0                       # 绘图区上下（150pt 高）
    _PH = _PYB - _PYT
    _ZY = _PYB - (0.0 - _ybot) / _yspan * _PH      # 零轴线 y
    _nyr = max(1, len(_yl))
    _nser = len(_yseries)
    _bw = 11.0                                    # 柱宽
    _dgap = 9.0                                   # 组间距（保证跨组柱心距 ≥ 标签宽，永不相撞）
    # 组内柱心距 s：_nyr*((_nser-1)*s + bw) + (_nyr-1)*dgap = 绘图区宽
    _s = (((_PX1 - _PX0) - (_nyr - 1) * _dgap - _nyr * _bw)
          / max(1.0, _nyr * (_nser - 1)))
    _grp_w = (_nser - 1) * _s + _bw               # 单组宽度
    _gstep = _grp_w + _dgap                       # 组步进

    def _sy(v):
        return _PYB - (v - _ybot) / _yspan * _PH

    _yparts = [
        # 图例
        f'<text x="{_PX0}" y="12" font-size="8.6" fill="#5b6472">'
        + "".join(
            f'<tspan fill="{c}" font-weight="700">■</tspan>'
            f'<tspan dx="2" fill="#5b6472">{n}</tspan>'
            + ('<tspan dx="9">&#8203;</tspan>' if i < 2 else "")
            for i, (_k, n, c, _r, _m) in enumerate(_yseries))
        + "</text>",
        # 零轴线
        f'<line x1="{_PX0}" y1="{_ZY:.1f}" x2="{_PX1}" y2="{_ZY:.1f}" '
        f'stroke="#c9cfd8" stroke-width="1"/>',
    ]
    for _gi, _d in enumerate(_yl):
        _yr = str(_d.get("year", ""))
        _gx = _PX0 + _gi * _gstep + (_PX1 - _PX0 - (_nyr * _grp_w + (_nyr - 1) * _dgap)) / 2.0
        # 组分隔虚线
        if _gi:
            _sx = _gx - _dgap / 2.0
            _yparts.append(f'<line x1="{_sx:.1f}" y1="{_PYT-4}" x2="{_sx:.1f}" '
                           f'y2="{_PYB+4}" stroke="#eceef2" stroke-width="1" '
                           f'stroke-dasharray="2 2"/>')
        _gbars, _glabs = [], []          # 本年的柱与数值标签（标签最后统一发，避免互相压字）
        for _ki, (_k, _n, _c, _rr, _mm) in enumerate(_yseries):
            _v = _rr.get(_yr)
            _bx = _gx + _ki * _s
            if _v is None or _v != _v:
                _gbars.append(
                    f'<rect x="{_bx:.1f}" y="{_ZY-1:.1f}" width="{_bw:.1f}" '
                    f'height="2" fill="#e3e6ea"><title>{_yr} {_n}: 无数据</title></rect>')
                continue
            _h = abs(_v) / _yspan * _PH
            _by = _ZY - _h if _v >= 0 else _ZY
            _md = _mm.get(_yr)
            _tip = f"{_yr} {_n}: {_v:+.1f}%"
            if _md is not None and _md == _md:
                _tip += f" · 最大回撤 {_md:.1f}%"
            _gbars.append(
                f'<rect x="{_bx:.1f}" y="{_by:.1f}" width="{_bw:.1f}" '
                f'height="{max(_h, 0.8):.1f}" fill="{_c}" rx="1.5" '
                f'opacity="{1.0 if _k == "nv" else 0.82}">'
                f'<title>{html.escape(_tip)}</title></rect>')
            # 数值标签基准位：正值在柱上方、负值在柱下方（离零轴更远那侧）
            _ly0 = (_by - 3.0) if _v >= 0 else (_by + _h + 7.5)
            _glabs.append([_bx + _bw / 2.0, _ly0, _c, _v, _ki])
        _yparts.extend(_gbars)
        # 标签避撞：三根柱的数值接近时（如 2021 年 D3 与 P3 相等）标签会压字。
        # 左右两根相距 2*(bw+gap)≈34pt，与中间那根只隔 17pt，而标签宽约 18pt
        # ⇒ 只需把中间那根（D3）逐步推远；推不开就放到同侧三标签的最外侧。
        if len(_glabs) == 3:
            _mid = _glabs[1]
            _side = [_glabs[0], _glabs[2]]
            _hw, _hh = 9.2, 3.4                      # 标签半宽 / 半高（保守估值）

            def _hit(a, b):
                return (abs(a[0] - b[0]) < 2 * _hw and abs(a[1] - b[1]) < 2 * _hh)

            for _ in range(10):
                if not any(_hit(_mid, s) for s in _side):
                    break
                _mid[1] += -2.0 if _mid[3] >= 0 else 2.0    # 正值上推、负值下推
            if any(_hit(_mid, s) for s in _side):
                _oth = [s[1] for s in _side]
                if _mid[3] >= 0:
                    _mid[1] = max(_PYT + 2.0, min(_oth) - 8.0)   # 正值：抬到最高那个之上
                else:
                    _mid[1] = min(_PYB + 7.0, max(_oth) + 8.0)   # 负值：压到最低那个之下
        for _lx, _ly, _lc, _lv, _ki in _glabs:
            _yparts.append(
                f'<text x="{_lx:.1f}" y="{_ly:.1f}" font-size="6.4" '
                f'text-anchor="middle" fill="{_lc}" font-weight="600">'
                f'{_lv:+.1f}</text>')
        # 年份标签
        _yparts.append(f'<text x="{_gx + _grp_w / 2:.1f}" y="{_PYB + 18:.1f}" '
                       f'font-size="8.6" text-anchor="middle" fill="#5b6472">'
                       f'{_yr}</text>')
    # 顶部/底部刻度参考
    _yparts.append(f'<text x="{_PX1}" y="{_PYT - 6:.1f}" font-size="6.4" '
                   f'text-anchor="end" fill="#b6bdc6">{_ytop:.0f}%</text>')
    _yparts.append(f'<text x="{_PX1}" y="{_PYB + 11:.1f}" font-size="6.4" '
                   f'text-anchor="end" fill="#b6bdc6">{_ybot:.0f}%</text>')
    ytd_svg = (f'<svg viewBox="0 0 {_VW:.0f} {_VH:.0f}" width="100%" '
               f'preserveAspectRatio="xMidYMid meet" role="img" '
               f'aria-label="年度表现分组柱状图">{"".join(_yparts)}</svg>')

    n_yr = len(_yl)
    # ★ 2026-10-25：手机触屏无 hover，SVG 里 <title> 的年度回撤根本点不出来。
    #   这里把「默认口径 D3+P3」的逐年最大回撤压成一行小字直接印在脚注里，
    #   桌面端仍保留柱子悬停（信息重复但无害），手机端则始终可读。
    _mdd_line = " · ".join(
        f"{str(y)[-2:]}′ {(_p3y.get(str(y)) or {}).get('mdd'):.1f}%"
        for y in (str(d["year"]) for d in _yl)
        if (_p3y.get(str(y)) or {}).get("mdd") is not None)
    ytd_block = f"""
<div class="ytdchart">
<div class="ytdh">年度表现<span class="sphs">（近 {n_yr} 年 · 三柱对比）</span></div>
<div class="ytdbody">
{ytd_svg}
</div>
<div class="ytdfoot">每年收益（含换仓成本，单位 %）· <b>柱子颜色区分策略</b>：<span
style="color:{CLR_P3};font-weight:700">D3+P3+抄底（默认口径）</span> / <span
style="color:{CLR_STRAT};font-weight:700">基础轮动</span> / <span
style="color:{CLR_D3};font-weight:700">D3 单笔止损</span>（后两者<b>仅作参照</b>）·
柱体<b>向上=盈利、向下=亏损</b>；柱上数字沿用 <span style="color:{RED}">红涨</span>
<span style="color:{GREEN}">绿跌</span> · 同一刻度 · 悬停看当年最大回撤 · 前复权长历史回测
<br><span class="ytdmdd">各年最大回撤（默认口径 D3+P3）：{_mdd_line}</span></div>
</div>"""

    # 阶梯表
    lad_rows = []
    for r in snap:
        tds = "".join(mcell(r.momentum.get(n, float("nan")), r.code in eff_heads)
                      for n in cols)
        cls = " class='def'" if r.role == "defensive" else ""
        lad_rows.append(
            f"<tr{cls}><td class='mono'>{r.code}</td><td>{html.escape(r.name)}</td>"
            f"{tds}"
            f'<td class="num">{r.close:.3f}</td>'
            f'<td class="num" style="color:{_color(r.chg1)};font-weight:600">{_fmt(r.chg1)}</td>'
            "</tr>")

    # 逐列头名
    step_rows = []
    nm_by_code = {r.code: r.name for r in snap}
    for st in steps:
        h, s = st["head"], st["second"]
        if st["is_swap"]:
            tag = "<span class='tag swap'>换仓日</span>"
        elif st["held"]:
            tag = (f"<span class='tag tie'>粘性带保留 {st['eff_head']}</span>")
        elif st["tied"]:
            tag = "<span class='tag tie'>平手带内</span>"
        else:
            tag = ""
        st_lead = st["lead"]          # 注意: 不能覆盖外层的 lead 参数
        gap = max(0.0, sticky - st_lead) if st_lead == st_lead else float("nan")
        if gap != gap:
            gap_txt = "-"
        elif gap <= 1e-9:
            gap_txt = "<span class='good'>已达标</span>"
        else:
            gap_txt = f"差 {gap:.2f}pp"
        eff = st["eff_head"]
        eff_txt = ((f"<span class='mono'>{eff}</span> "
                    f"{html.escape(nm_by_code.get(eff, ''))}") if eff else "-")
        step_rows.append(
            f"<tr><td class='num'>{st['idx']}</td><td class='num'>横{st['n']}</td>"
            f"<td class='mono'>{h.code}</td><td>{html.escape(h.name)}</td>"
            f"<td class='num' style='color:{_color(h.momentum[st['n']])}'>"
            f"{_fmt(h.momentum[st['n']])}</td>"
            f"<td class='mono'>{(s.code if s else '-')}</td>"
            f"<td class='num'>{_fmt(st_lead, pct=False, sign=True)}pp</td>"
            f"<td class='num'>{gap_txt}</td>"
            f"<td>{eff_txt}</td><td>{tag}</td></tr>")

    # ---- 今日操作指引（第 1 列 = 今日；叠加过热熔断后的最终建议）----
    t0 = steps[0]
    eff0 = t0["eff_head"]
    eff0_nm = nm_by_code.get(eff0, "")
    # 「② 近期换仓复盘」与本报告同规则同口径复算出的「当前应持仓」，
    # 用于粘性带平手时判定"保留谁"——阶梯推演只知窗口内的列链，不知道真实上一持仓。
    _prev_held = None
    if recent and not recent.get("error"):
        try:
            _prev_held = (recent["rows"][-1] or {}).get("to")
        except Exception:                              # noqa: BLE001
            _prev_held = None
    # def_code / def_nm / ve_state / ve_ok / def_now_txt 已在上方（排名表前）算好，此处直接复用。
    if overheated:
        if ve_state == "数据不足":
            final_code, final_nm = "CASH", "持币"
            final_note = ("头名已触发过热熔断，但防守位 200 日线数据不足，"
                          "价值引擎无法判定 → 持币")
        elif ve_ok:
            final_code, final_nm = def_code, def_nm
            final_note = (f"头名已触发过热熔断（乖离 {head1.dev:+.2f}% 或 领先 {lead:+.2f}pp 触线）；"
                          f"价值引擎对防守位判定为「{ve_state}」"
                          f"（偏离 MA200 {ve_ratio:+.2f}%）→ 建议切到防守位")
        else:
            final_code, final_nm = "CASH", "持币"
            final_note = (f"头名已触发过热熔断（乖离 {head1.dev:+.2f}% 或 领先 {lead:+.2f}pp 触线）；"
                          f"但价值引擎对防守位判定为「{ve_state}」"
                          f"（偏离 MA200 {ve_ratio:+.2f}%，已涨破 +6% 卖出线）→ 防守期持币，不买防守位")
    else:
        final_code, final_nm = eff0, eff0_nm
        _held_nm = nm_by_code.get(_prev_held or "", "")
        if _prev_held and _held_nm and _prev_held != eff0 and lead < sticky:
            # 粘性带内平手 → 保留「② 近期换仓复盘」同口径复算的真实上一持仓，
            # 而非阶梯推演自己算出来的列链头名（两者可能不同，阶梯不知真实持仓）
            final_code, final_nm = _prev_held, _held_nm
            final_note = (f"今日头名 {head1.code} 领先次名仅 {lead:+.2f}pp，"
                          f"落在粘性带（&lt;{sticky:.1f}pp）内视为平手 → "
                          f"<b>保留上一持仓 {final_code} {html.escape(final_nm)}</b>"
                          f"（阶梯推演的有效头名为 {eff0} {html.escape(eff0_nm)}，"
                          f"不换仓、不产生成本）")
        else:
            final_note = ("头名未过热，按「粘性带后有效头名」持仓"
                          if not (t0["held"] or t0["tied"]) else
                          f"今日领先 {t0['lead']:+.2f}pp &lt; {sticky:.1f}pp，视为平手 → 保留原持仓")
    # ---- 主口径覆盖（2026-10-26 接入抄底后必须补）----
    # 上面的 final_code 只由「当日阶梯 + 粘性带 + 过热」决定，**不知道** D3/P3 冷却
    # 与抄底规则；而生产默认口径 = LIVE_COMPOSITE（D3+P3+抄底）。
    # 不对齐会出现「顶部胶囊说 588170、下方操作表说 513100」的自相矛盾。
    # 故以 recent["p3"]（同一次回测算出的真实持仓）为准覆盖。
    _p3v = ((recent or {}).get("p3") or {})
    _weak_active = bool(_p3v.get("weak_active"))
    if _p3v.get("ok") and _p3v.get("hold") and _p3v["hold"] != final_code:
        _ph, _ph_nm = _p3v["hold"], (_p3v.get("hold_name") or "")
        if _ph == "CASH":
            # 2026-10-28 用户需求：空仓期要直观显示「已经空仓几天」
            _cd = _p3v.get("cash_days")
            _cd_txt = (f"，<b>已空仓 {int(_cd)} 个交易日</b>"
                       if isinstance(_cd, (int, float)) and _cd > 0 else "")
            final_note = (f"主口径处于空仓冷却期{_cd_txt} → <b>今日空仓</b>")
        elif _weak_active:
            # 不再重复「排名末位 XXX」——「今日建议持仓 = XXX」与胶囊已说过（2026-10-28 去重）
            final_note = "依据：主选池动横20 <b>全部为负</b>，抄底规则取末位"
        else:
            final_note = (f"主口径调整为 <b>{_ph} {html.escape(_ph_nm)}</b>")
        final_code, final_nm = _ph, _ph_nm

    _g1 = "✓" if head1.dev < 9.0 else "✗"
    _g2 = "✓" if lead < 10.0 else "✗"

    # ---- 指数口径条带（跨 4 列独占一行，跟在「防守位」行下面，就地可见不用往下翻）----
    # 用户 2026-10-19 要求：基金口径与「跟踪指数」口径的 MA200 偏离各是多少、差多少。
    # 两个口径的偏离天然不等（基金=前复权含分红回补，指数=价格指数除息扣点位），
    # 所以这里只做「并列展示 + 距 −3% 买线还差多少」，不改判定规则。
    _grow = ""
    if idx:
        _fdv, _idv = float(idx["f_dev"]), float(idx["i_dev"])
        _cfd = RED if _fdv > 0 else GREEN
        _cid = RED if _idv > 0 else GREEN

        def _dist(v):
            """到 −3% 买线的距离：v 高于 −3% → 还差多少 pp；v 低于 −3% → 已破多少 pp。"""
            if v <= -3.0:
                return f"已破 {-3.0 - v:.2f}pp"
            return f"还差 {v + 3.0:.2f}pp"
        if _idv <= -3.0 and _fdv > -3.0:
            _gtip = ("⚑ 指数已先破 −3%，基金还差 "
                     f"<b>{_fdv + 3.0:.2f}pp</b>：可先备资金，"
                     "但按回测所用基金口径<b>尚未触发</b>（改规则未验证）")
            _gtip_cls = "hot"
        elif _fdv <= -3.0 and _idv <= -3.0:
            _gtip = "✅ 两个口径都已跌破 −3% → 按价值引擎<b>该建仓 / 补仓</b>防守位"
            _gtip_cls = "pos"
        elif _fdv <= -3.0:
            _gtip = "基金已破 −3%、指数未破 → 以基金口径为准，<b>可建仓 / 补仓</b>"
            _gtip_cls = "pos"
        else:
            _gtip = ""
            _gtip_cls = ""
        # 2026-10-20 排版改造：原来是两段文字硬塞进「依据」窄列（约 190px），被迫折成 4~5 行，
        # 而左边「今日动作」列下方大片留白 → 改成**跨 4 列独占一行**的条带，
        # 三个数值做成并列 chip（自动换行），结论单独一行，文字横向铺开、不再挤压。
        _grow_cls = "hotx" if overheated else "dimx"
        _grow = (
            f"<tr class='defx {_grow_cls}'><td colspan='4'>"
            '<div class="gidxbar">'
            '<span class="k">MA200 偏离（买线 −3%）</span>'
            f'<span class="v">基金 {def_code} <b style="color:{_cfd}">{_fdv:+.2f}%</b>'
            f'<i>{_dist(_fdv)}</i></span>'
            f'<span class="v">指数 {idx["code"]} <b style="color:{_cid}">{_idv:+.2f}%</b>'
            f'<i>{_dist(_idv)}</i></span>'
            f'<span class="v">口径差 <b>{idx["gap"]:+.2f}pp</b></span>'
            '</div>'
            # 近 1 年偏离区间：判「今天 −4.38% 到底算便宜还是还在半山腰」的参照
            + _hspan_html(idx.get("f_span"), buy_label="−3% 买线",
                          alt=idx.get("i_span"), alt_label="指数口径")
            + (f'<div class="gidxnote {_gtip_cls}">{_gtip}</div>' if _gtip else "")
            + "</td></tr>")
    else:
        _grow = ""

    # ---- 512890 红利低波「回踩 MA200 买 / 超 +6% 卖」每日跟踪（2026-10-21 新增）----
    # 用户要求：在 512040 防守位下面增加一条同规格的每日跟踪，用于提醒当日操作。
    # 规则与 512040 的写法完全一致（同一状态机），只是买线不同：
    #   512040 = 低于 MA200 3 个点买（×0.97）；512890 = 回踩 MA200 即买（×1.00）。
    # ⚠️ 这是**独立策略**，不参与轮动排名、不受过热熔断控制、也不替代防守位。
    _trow = ""
    if ve_track:
        _t = ve_track
        _tdev = float(_t["dev"])
        _tcls = RED if _tdev > 0 else GREEN
        if _t["zone"] == "buy":
            _tact = f"<b class='good'>{_t['action']} {_t['code']}</b>"
            _tact_cls = "tvbuy"
        elif _t["zone"] == "sell":
            _tact = f"<b class='warn'>{_t['action']}</b>"
            _tact_cls = "tvsell"
        else:
            _tact = f"<b>{_t['action']}</b>"
            _tact_cls = "tvmid"
        # 距两条线的距离（只在有意义时给出提示方向）
        if _tdev < 0:
            _tbuy_txt = f"已破 <b>{-_tdev:.2f}pp</b>"
        else:
            _tbuy_txt = f"还差 <b>{_tdev:.2f}pp</b>"
        _tsell_txt = f"还差 <b>{_t['to_sell']:.2f}pp</b>"
        if _t["zone"] == "mid":
            # ★ 2026-10-26：mid 区间原本还有一句「介于买入线~卖出线之间 → 继续持有，
            #   距卖线还差 X pp」，但「偏离/距买线/距卖线/状态」在上面的 .tvbar 里已给全，
            #   「继续持有」也在那儿 —— 三行里同一个事实说了三遍，故 mid 不再输出本句。
            #   buy / sell 两个区间是**强指令**（该买 / 该卖），继续保留。
            _tnote = ""
            _tnote_cls = ""
        elif _t["zone"] == "buy":
            if _t["holding"]:
                _tnote = (f"收盘已跌破买线（回踩 MA200），本就持有 → <b>继续持有 / 可补仓</b>。")
            else:
                _tnote = (f"✅ 收盘跌破买线（回踩 MA200）→ <b>按规则该买入 {_t['code']}</b>。")
            _tnote_cls = "pos"
        else:
            if _t["holding"]:
                _tnote = (f"⚠️ 收盘已涨破 +6% 卖线 → <b>按规则该卖出</b>，卖出后需重新回踩才买回。")
            else:
                _tnote = f"已涨破卖线但当前空仓 → <b>保持持币，不追高</b>。"
            _tnote_cls = "hot"
        _lb = (f"最近买入 {_t['last_buy']:%Y-%m-%d}" if _t["last_buy"] is not None
               else "窗口内无买入信号")
        _ls = (f"最近卖出 {_t['last_sell']:%Y-%m-%d}" if _t["last_sell"] is not None
               else "窗口内无卖出信号")
        _tstate = "持有中" if _t["holding"] else "持币中"
        _trow = (
            f"<tr class='tvmk {_tact_cls}'><td class='mono'>{_t['code']}</td>"
            f"<td class='nm2'>{html.escape(_t['name'] or '')} "
            f"<span class='tag tvt'>策略二 · 每日跟踪</span></td>"
            f"<td>{_tact}</td>"
            # ★ 2026-10-26：这里原本还写「偏离 +2.42%；当前状态持有中」，
            #   与下一行进度条（.tvbar）里的「偏离 / 状态」完全重复。
            #   依据列只留下进度条没有的信息：最近买卖日 + 收盘/MA200 原始价。
            f"<td class='rs'>收盘 <b>{_t['close']:.3f}</b> / MA200 "
            f"<b>{_t['ma200']:.4f}</b>；{_lb}、{_ls}</td></tr>")
        _trow += (
            f"<tr class='tvx'><td colspan='4'>"
            f'<div class="tvbar">'
            f'<span class="k">规则：回踩 MA200 买 / 超 +6% 卖</span>'
            f'<span class="v">买线 <b>{_t["buy_line"]:.4f}</b>'
            f'<i>{_tbuy_txt}</i></span>'
            f'<span class="v">卖线 <b>{_t["sell_line"]:.4f}</b>'
            f'<i>{_tsell_txt}</i></span>'
            f'<span class="v">偏离 <b style="color:{_tcls}">{_tdev:+.2f}%</b></span>'
            f'<span class="v">状态 <b>{_tstate}</b></span>'
            f'</div>'
            # 近 1 年偏离区间：判「现在 +2.42% 该不该等回踩」的参照
            + _hspan_html(_t.get("span"), buy_label="MA200 买线")
            + (f'<div class="gidxnote {_tnote_cls}">{_tnote}</div>' if _tnote else "")
            + "</td></tr>")

    # 首行的「动作」不能再写死「无需动作」：抄底换持 / 主口径空仓都会真的产生交易。
    # 以「主口径持仓 vs 当前真实持仓」判定是否换仓。
    # ★ 2026-10-27：「当前持 X」必须用**主口径**的昨日持仓（prev_hold）——
    #   主口径昨天可能刚抄底换过仓（如 588170），与基础轮动的持仓（513100）不同；
    #   用基础轮动的持仓会显示成「当前持 513100」，与实盘不符。
    _real_prev = ((_p3v.get("prev_hold") if _p3v.get("ok") else None)
                  or _prev_held)
    _need_switch = bool(_real_prev and final_code != _real_prev)
    _held_disp = _real_prev or "—"
    _held_disp_nm = ((_p3v.get("prev_hold_name") if _p3v.get("ok") else None)
                     or nm_by_code.get(_held_disp, _held_disp))
    if _need_switch:
        _act_txt = (f"<b class='good'>买入 {final_code}</b>"
                    if final_code != "CASH" else "<b class='warn'>卖出清仓</b>")
        _tag = ("<span class='tag weakb'>抄底买入</span>" if _weak_active
                else "<span class='tag rot'>今日建议持仓</span>")
        _why_txt = (f"当前持 {_held_disp} {html.escape(str(_held_disp_nm or ''))}"
                    f" → 主口径换持 {final_code}"
                    + ("（抄底规则）" if _weak_active else "") + "，换仓成本约双边 0.2%")
        if live[0] and "盘中" in str(live[1] or ""):
            # ★ 2026-10-27：盘中实时价会产生「盘中看似触发、收盘可能收回」的换仓指令
            #   （今天 D3 空仓就是 13:38 盘中价触发的，14:10 价格回升后指令消失），
            #   必须显式警示，不能只靠页头小字。已收盘时（live_txt 无「盘中」）不显示。
            _why_txt += ("<br><b style='color:#b45309'>⚠ 当前为盘中实时价，"
                         "换仓判定以收盘为准 —— 收盘前该指令可能消失，勿提前操作</b>")
    else:
        _act_txt = "<b>无需动作</b>"
        _tag = "<span class='tag rot'>今日建议持仓</span>"
        _why_txt = ("防守期内" if overheated else "未触发过热熔断") + \
                   "，持仓已一致 · 无交易"

    guide_rows = [
        f"<tr><td class='mono'>{final_code}</td>"
        f"<td class='nm2'>{html.escape(final_nm)} {_tag}</td>"
        f"<td>{_act_txt}</td>"
        f"<td class='rs'>{_why_txt}</td></tr>",
    ]
    # ---- 防守位行：只有真进入防守期（过热熔断）才给出"应该持有/应该持币"的强指令 ----
    if overheated:
        if ve_ok:
            _act = f"<b class='good'>现在应该继续持有</b>"
            _why = (f"头名仍处过热熔断中，尚未满足切回三条件；价值引擎判定「{ve_state}」"
                    f"（偏离 MA200 {ve_ratio:+.2f}%，未涨破 +6% 卖出线）→ 持有防守位")
        else:
            _act = f"<b class='warn'>现在应该持币</b>"
            _why = (f"头名仍处过热熔断中；价值引擎判定「{ve_state}」"
                    f"（偏离 MA200 {ve_ratio:+.2f}%，已涨破 +6% 卖出线）→ <b>不买入</b>防守位")
        guide_rows.append(
            f"<tr class='defhot defmain'><td class='mono'>{def_code}</td>"
            f"<td class='nm2'>{html.escape(def_nm)} "
            f"<span class='tag def'>防守位 · 已进入防守期</span></td>"
            f"<td>{_act}</td><td class='rs'>{_why}</td></tr>")
        if _grow:
            guide_rows.append(_grow)
        if not ve_ok:
            guide_rows.append(
                f"<tr class='defhot'><td class='mono'>CASH</td>"
                f"<td class='nm2'>持币（空仓） <span class='tag def'>防守期</span></td>"
                f"<td><b class='warn'>维持持币</b></td>"
                f"<td class='rs'>价值引擎已给出卖出信号，防守期不买防守位 → 持币</td></tr>")
    else:
        # 正常轮动：防守位只作一行灰色参考，不给动作指令，避免误以为要卖出手上的头名
        guide_rows.append(
            f"<tr class='defdim defmain'><td class='mono'>{def_code}</td>"
            f"<td class='nm2'>{html.escape(def_nm)} "
            f"<span class='tag def'>防守位</span></td>"
            f"<td class='dimtxt'>本轮无需关注</td>"
            f"<td class='rs dimtxt'>仅当头名乖离 &gt; {overheat_dev:.0f}% "
            f"或领先 &gt; {overheat_lead:.0f}pp（过热熔断）时才启用；"
            f"届时按下方「防守位操作指南」执行</td></tr>")
        if _grow:
            guide_rows.append(_grow)
    if overheated and ve_ok:
        # 防守期但价值引擎允许持有 → 若当前是持币状态, 需要建仓防守位
        guide_rows.append(
            f"<tr class='defhot'><td class='mono'>CASH</td>"
            f"<td class='nm2'>持币（空仓） <span class='tag def'>防守期</span></td>"
            f"<td><b class='good'>买入 {def_code}</b></td>"
            f"<td class='rs'>价值引擎判定「{ve_state}」（偏离 MA200 {ve_ratio:+.2f}%），"
            f"防守期应持有防守位而非持币</td></tr>")
    # ★ 512890 策略二：紧跟在 512040 防守位之后（用户要求「在 512040 策略的下面」）
    if _trow:
        guide_rows.append(_trow)
    guide_rows.append(
        f"<tr><td>其他</td><td class='nm2'>既非今日建议持仓、也非防守位</td>"
        f"<td>换到 <span class='mono'>{final_code}</span> {html.escape(final_nm)}</td>"
        f"<td class='rs'>换仓成本按双边约 0.2% 估算</td></tr>")
    # ★ 2026-10-26：正常轮动日这段文字是**第三遍重复**（状态条一遍、这里一遍、
    #   操作表首行「依据」列一遍），三句在说同一件事：「持 513100、无需动作、未过热」。
    #   故只在「非正常轮动」（过热熔断 / 粘性带保留旧仓 / 空仓）时才保留文字解释——
    #   那几种情形下原因需要展开，且表格依据列写不下。
    _guide_normal = (not overheated) and final_code == eff0 and final_nm == eff0_nm
    if _guide_normal:
        guide_note = ""
    else:
        guide_note = f"今日建议持仓 = <b>{final_code} {html.escape(final_nm)}</b>。{final_note}。"
        if not overheated:
            # ★ 2026-10-28：空仓（D3/P3 冷却）时不能说「你现在持有的是排名第一的轮动 ETF」
            #   —— 那时手里是现金，这句话与「今日空仓」直接矛盾。按是否空仓分两种措辞。
            if final_code == "CASH":
                guide_note += ("<br><span class='dimtxt'>ℹ️ 头名未过热，"
                               "<b>本轮无需关注防守位</b>；防守位仅在过热熔断时才启用，"
                               "下方操作指南已列出备用。</span>")
            elif _weak_active:
                # ★ 2026-10-28：抄底时手里是**末位**，不是「排名第一」——
                #   旧文案在这里直接说错话（用户截图里就撞上了）。
                guide_note += ("<br><span class='dimtxt'>ℹ️ 头名未过热，"
                               "你现在持有的是<b>抄底选出的末位标的</b>（不是头名），"
                               "<b>本轮无需关注防守位</b>；防守位仅在过热熔断时才启用，"
                               "下方操作指南已列出备用。</span>")
            else:
                guide_note += ("<br><span class='dimtxt'>ℹ️ 头名未过热、"
                               "你现在持有的是排名第一的轮动 ETF，"
                               "<b>本轮无需关注防守位</b>；防守位仅在过热熔断时才启用，"
                               "下方操作指南已列出备用。</span>")

    # ---- 防守位操作指南（规则常驻报告，不必记；强度随是否进入防守期切换）----
    if ve_state == "数据不足":
        ve_state_txt = "<b>MA200 数据不足</b>（历史不足 200 根，暂无法判定）"
    else:
        ve_state_txt = (f"当前偏离 MA200 <b>{_fmt(ve_ratio, pct=False, sign=True)}%</b>"
                        f"（现价 {def_close:.3f} vs MA200 {ve_ma200:.3f}）→ 判定「{ve_state}」")

    # ---- 基金口径 vs 跟踪指数口径（用户 2026-10-19 要求：直观看该不该补仓/建仓）----
    idx_cmp_html = ""
    if idx:
        _fd, _id = float(idx["f_dev"]), float(idx["i_dev"])
        _gap, _gm = float(idx["gap"]), float(idx["gap_mean"])
        _need = _fd - (-3.0)                    # >0 = 基金口径距 −3% 买线还差几个点
        _fg = RED if _fd > 0 else GREEN
        _ig = RED if _id > 0 else GREEN
        _ftxt = ("<b>已跌破</b> −3% 建仓线" if _fd <= -3.0
                 else f"距 −3% 建仓线<b>还差 {_need:.2f}pp</b>")
        _itxt = ("<b>已跌破</b> −3% 建仓线" if _id <= -3.0
                 else f"距 −3% 建仓线还差 {(-3.0 - _id):.2f}pp")
        if _id <= -3.0 and _fd > -3.0:
            _vcls = "hot"
            _eq = -3.0 + _gm                          # 指数 -3% 时，基金口径的等效位置
            _d = _fd - _eq
            _rel = (f'比等效位置<b>高 {_d:.2f}pp</b>，说明这一段基金相对指数更强'
                    if _d >= 0 else
                    f'比等效位置<b>低 {abs(_d):.2f}pp</b>，说明这一段基金相对指数更弱')
            _verdict = (f'⚑ <b>这正是你说的情况：指数已破 −3%，基金还没破。</b>'
                        f'按长期平均口径差折算，指数在 −3% 时基金等效约 {_eq:+.2f}%，'
                        f'而基金此刻实际 {_fd:+.2f}%——{_rel}'
                        f'（跟踪误差 + 分红节奏差异叠加）。<br>'
                        f'所以「基金没破线」不是错觉，也<b>不是指数信号失效</b>，'
                        f'而是 <b>{abs(_gap):.2f}pp 的系统性口径偏移</b>。'
                        f'按回测所用的基金口径，<b>当前仍不满足建仓条件</b>（还差 {_need:.2f}pp）；'
                        f'若改成指数 −3% 就买，等于把买点整体上移约 {_gm:.1f}pp，属<b>改动规则、回测未验证</b>，'
                        f'请勿直接照做。（近 {idx["n_days"]} 个交易日里，这种「指数破、基金未破」的分歧出现 '
                        f'{idx["n_gap_days"]} 天。）')
        elif _fd <= -3.0 and _id <= -3.0:
            _vcls = "hot"
            _verdict = (f'✅ <b>两个口径都已在 −3% 建仓线下方</b>（基金 {_fd:+.2f}% / '
                        f'指数 {_id:+.2f}%）→ 按价值引擎规则，<b>现在该建仓 / 补仓 {def_code}</b>，'
                        f'并进入「持有」状态直到偏离涨过 +6%。')
        elif _fd <= -3.0:
            _vcls = "hot"
            _verdict = (f'✅ <b>基金口径已跌破 −3% 建仓线，指数口径还没破</b>'
                        f'（基金 {_fd:+.2f}% / 指数 {_id:+.2f}%）→ 按回测所用的基金口径，'
                        f'<b>现在该建仓 / 补仓 {def_code}</b>。'
                        f'两个口径方向相反时<b>以基金口径为准</b>（回测就是按它做的），'
                        f'指数没破说明基金这一段相对指数更弱（分红节奏 / 跟踪误差）。')
        else:
            _vcls = ""
            _verdict = (f'两边都未到 −3% 建仓线，<b>无需动作</b>。'
                        f'注意指数口径系统性低约 {abs(_gm):.2f}pp，'
                        f'所以「指数 −3%」大约只相当于基金 {_gm - 3.0:+.2f}% 的位置，'
                        f'别拿同一条线去套两个口径。')
        idx_cmp_html = f"""
  <div class="dgcmp">
    <div class="cmpc fund">
      <div class="cmpl"><b>基金 {def_code}</b> {html.escape(def_nm)} · 前复权口径
        <span class="dimtxt">（策略回测实际使用的口径）</span></div>
      <div class="cmpv" style="color:{_fg}">{_fd:+.2f}%<small>偏离 MA200</small></div>
      <div class="cmps">{idx['f_close']:.3f} vs MA200 {idx['f_ma']:.4f}（{idx['f_date']}）→ {_ftxt}</div>
    </div>
    <div class="cmpc idx">
      <div class="cmpl"><b>指数 {idx['code']}</b> {html.escape(idx['name'])} · 价格指数口径
        <span class="dimtxt">（你看到的「指数已破 3%」口径）</span></div>
      <div class="cmpv" style="color:{_ig}">{_id:+.2f}%<small>偏离 MA200</small></div>
      <div class="cmps">{idx['i_close']:.2f} vs MA200 {idx['i_ma']:.2f}（{idx['i_date']}）→ {_itxt}</div>
    </div>
    <div class="cmpgap {_vcls}">
      <b>口径差 {_gap:+.2f}pp</b>（基金 − 指数；近 {idx['n_days']} 个交易日
      {idx['gap_p10']:+.2f} ~ {idx['gap_p90']:+.2f}pp、均值 {_gm:+.2f}pp）。这不是数据错误，是
      <b>系统性偏移</b>：基金用前复权价（分红已加回 → 历史价被压低、MA200 更平 → 偏离天然偏高），
      指数是价格指数（成分股除息直接扣点位 → 偏离天然偏低）。<b>同一条 −3% 线对两者不是同一件事。</b><br>
      {_verdict}
    </div>
  </div>"""
    if overheated:
        head_act = (f'<div class="dghdr on">🔥 现在处于<b>防守期</b>：头名已触发过热熔断 '
                    f'（乖离 {head1.dev:+.2f}% / 领先 {lead:+.2f}pp，触线 '
                    f'{overheat_dev:.0f}% / {overheat_lead:.0f}pp）。'
                    f'请按下面第 ①–③ 步执行。</div>')
        dcls, dstate_cls = "dcard on", ("hold" if ve_ok else "cash")
    else:
        head_act = ('<div class="dghdr off">😌 现在<b>不在防守期</b>：'
                    '你持有的是排名第一的轮动 ETF，<b>无需任何防守位操作</b>。'
                    '以下规则仅作备用，进入防守期时照做即可。</div>')
        dcls, dstate_cls = "dcard off", "idle"
    def_guide_card = f"""
<h2>🛡 防守位操作指南 · {def_code} {html.escape(def_nm)}（价值引擎）</h2>
<div class="{dcls}">
  {head_act}
  <div class="dgstate {dstate_cls}">当前价值引擎状态：{ve_state_txt}</div>
{idx_cmp_html}
  <table class="dgtbl">
    <tr><th style="width:190px">触发条件（收盘价 vs MA200）</th><th style="width:150px">动作</th><th>说明</th></tr>
    <tr><td>收盘 <b>&lt; MA200×0.97</b><br><span class="dimtxt">低于 200 日线 3 个点</span></td>
        <td class="good"><b>干 → 买入持有 {def_code}</b></td>
        <td>逻辑：把 MA200 当作合理的中长期均价。标的本身股息率约 5%，
            再加"低于均价 3%"的破位幅度 ≈ 8% 的安全垫，大资金在这个位置愿意买。
            <b>买入后进入持有状态。</b></td></tr>
    <tr><td>收盘 <b>&gt; MA200×1.06</b><br><span class="dimtxt">高于 200 日线 6 个点</span></td>
        <td class="warn"><b>跑 → 卖出 / 转为持币</b></td>
        <td>已涨过合理均价 6 个点，8% 安全垫已被吃掉，继续持有性价比下降 → 离场。
            <b>卖出后不会自动买回</b>，必须重新跌破 0.97 才可再买。</td></tr>
    <tr><td>收盘 介于 <b>0.97×MA200 ~ 1.06×MA200</b><br><span class="dimtxt">中间区间</span></td>
        <td><b>维持上一状态</b></td>
        <td>状态机逻辑：不涨破卖线就不卖、不跌破买线就不买，避免在区间里被反复摩擦。
            所以判定是"记忆性"的，取决于之前是买还是卖。</td></tr>
    <tr><td>防守期到来时，<b>尚未跌破 0.97</b></td>
        <td class="warn"><b>持币（空仓）</b></td>
        <td>没有达到买点就不买防守位，<b>宁可空仓也不追高</b>。这是新策略的持币来源之一
            （打破策略B 原"始终满仓"设计）。</td></tr>
  </table>
  <div class="dgnote">
    <b>切回轮动的三个条件（同时满足才换回头名，与价值引擎独立判断）</b>：
    ① 头名乖离 &lt; 9%（当前 {head1.dev:+.2f}% {_g1}）　
    ② 头名领先 &lt; 10pp（当前 {lead:+.2f}pp {_g2}）　
    ③ 已防守满 5 个交易日（需自行确认）。<br>
    <b>当前是否已触发过热熔断</b>：乖离 {head1.dev:+.2f}%（线 {overheat_dev:.0f}%）
    或 领先 {lead:+.2f}pp（线 {overheat_lead:.0f}pp）→
    {'<b class="warn">已触发 → 防守位生效</b>' if overheated else '<b class="good">未触发 → 防守位不启用</b>'}。
  </div>
</div>
{_ve_curve_block(ve_curve)}
"""

    # ---------- 策略二卡片：512890 红利低波「回踩 MA200 买 / 超 +6% 卖」 ----------
    # 与 512040 防守位同规格：规则表 + 今日状态 + 近 5 年回测曲线。
    # 但**独立成卡**，因为两者是两条互不替代的策略（买线不同、标的属性不同）。
    _ve2_card = ""
    if ve_track or ve2_curve:
        if ve_track:
            _t = ve_track
            _tdc = RED if _t["dev"] > 0 else GREEN
            _tst = "持有中" if _t["holding"] else "持币中"
            if _t["zone"] == "buy":
                _z = (f'<b class="good">已跌破买线（回踩 MA200）→ 该买入</b>'
                      if not _t["holding"] else
                      f'<b class="good">已跌破买线 → 继续持有 / 可补仓</b>')
                _zcls = "pos"
            elif _t["zone"] == "sell":
                _z = (f'<b class="warn">已涨破 +6% 卖线 → 该卖出</b>'
                      if _t["holding"] else
                      f'<b class="warn">已涨破卖线但空仓 → 保持持币，不追高</b>')
                _zcls = "hot"
            else:
                _z = (f'介于两条线之间 → 沿用状态机记忆：<b>继续持有</b>，不动作'
                      if _t["holding"] else
                      f'介于两条线之间且当前空仓 → <b>不追高</b>，等回踩 MA200 才买')
                _zcls = ""
            _today = f"""
  <div class="dgstate {'hold' if _t['holding'] else 'cash'}">
    当前状态：<b>{_tst}</b>　｜　收盘 <b>{_t['close']:.3f}</b> vs MA200
    <b>{_t['ma200']:.4f}</b> → 偏离
    <b style="color:{_tdc}">{_t['dev']:+.2f}%</b>　｜　
    买线 <b>{_t['buy_line']:.4f}</b>（{_t['dev']:.2f}% vs 1.00 基准）
    卖线 <b>{_t['sell_line']:.4f}</b>
  </div>
  <div class="gidxnote {_zcls}" style="margin-bottom:10px">{_z}</div>"""
        else:
            _today = ('<div class="dgstate idle">MA200 数据不足，'
                      '暂时无法给出今日判定</div>')
        _ve2_card = f"""
<h2>📈 策略二 · {VE_TRACK_CODE} {html.escape(VE_TRACK_NAME)}（回踩买 / +6% 卖）</h2>
<div class="dcard off">
{_today}
  <table class="dgtbl">
    <tr><th style="width:190px">触发条件（收盘价 vs MA200）</th><th style="width:150px">动作</th><th>说明</th></tr>
    <tr><td>收盘 <b>&lt; MA200×1.00</b><br><span class="dimtxt">回踩 200 日线</span></td>
        <td class="good"><b>干 → 买入 {VE_TRACK_CODE}</b></td>
        <td>与 512040 唯一的差别就在这条买线：<b>不要求再跌破 3 个点，回踩到 MA200 就买</b>。
            原因：{VE_TRACK_CODE} 是低波品种，相对 MA200 的偏离中枢偏高（近 5 年均值约 +3.5%），
            跌破 −3% 的天数只占约 4%，用 −3% 买线会长期打不到、大段时间空仓。
            <b>买入后进入持有状态。</b></td></tr>
    <tr><td>收盘 <b>&gt; MA200×1.06</b><br><span class="dimtxt">高于 200 日线 6 个点</span></td>
        <td class="warn"><b>跑 → 卖出 / 转为持币</b></td>
        <td>与 512040 完全一致。<b>卖出后不会自动买回</b>，必须重新回踩到 MA200 才可再买。</td></tr>
    <tr><td>收盘 介于 <b>MA200 ~ 1.06×MA200</b></td>
        <td><b>维持上一状态</b></td>
        <td>状态机逻辑，判定是"记忆性"的，取决于之前是买还是卖。</td></tr>
    <tr><td>未回踩到 MA200</td>
        <td class="warn"><b>持币（空仓）</b></td>
        <td>没到买点就不买，<b>宁可空仓也不追高</b>。</td></tr>
  </table>
  <div class="dgnote">
    <b>这条策略和 512040 是什么关系</b>：<b>互不替代、可并行</b>。
    512040 是轮动策略的<b>防守位</b>（只在头名过热熔断时启用）；
    {VE_TRACK_CODE} 是<b>独立策略二</b>，不参与轮动排名、不受过热熔断控制，
    每天都在跑，按自己的 MA200 偏离决定买卖。<br>
    <b>买线为什么定 1.00 而不是 0.97</b>：这是实测结论 —— 同 5 年窗口、
    卖出线同为 +6% 的条件下，回踩买（×1.00）净值 2.1220、跑赢 −3% 买（×0.97）的 1.7102
    约 41pp，也跑赢 512040 同期的 2.0122。若把卖出线收紧到 +3%，净值反而掉到 1.6628
    （交易翻倍但每笔只赚 3~6%）→ <b>买入线该放宽，卖出线不该收紧</b>。
  </div>
</div>
{_ve_curve_block(ve2_curve)}
"""

    # ---------- 备选观察池（独立分组，渲染在报告最后，不干扰主选池筛选） ----------
    watch = list(watch or [])
    watch_block = ""
    if watch:
        wvmax = max((abs(v) for r in watch for v in r.momentum.values() if v == v),
                    default=1.0)

        def wmcell(v):
            style = f"background:{_bg(v, wvmax)};color:{_color(v)};font-weight:600"
            return f'<td class="num" style="{style}">{_fmt(v, pct=False, nd=1)}</td>'

        wrank_rows = []
        for i, r in enumerate(watch, 1):
            wrank_rows.append(
                "<tr>"
                f'<td class="num">{i}</td>'
                f'<td class="mono">{r.code}</td><td>{html.escape(r.name)}</td>'
                f'<td class="num" style="color:{_color(r.momentum[window])};font-weight:600">'
                f'{_fmt(r.momentum[window])}</td>'
                f'<td class="num" style="color:{_color(r.dev)}">{_fmt(r.dev)}</td>'
                f'<td class="num">{r.close:.3f}</td>'
                f'<td class="num" style="color:{_color(r.chg1)};font-weight:600">{_fmt(r.chg1)}</td>'
                "</tr>")
        wlad_rows = []
        for r in watch:
            tds = "".join(wmcell(r.momentum.get(n, float("nan"))) for n in cols)
            wlad_rows.append(
                f"<tr><td class='mono'>{r.code}</td><td>{html.escape(r.name)}</td>"
                f"{tds}"
                f'<td class="num">{r.close:.3f}</td>'
                f'<td class="num" style="color:{_color(r.chg1)}">{_fmt(r.chg1)}</td>'
                "</tr>")
        wh1 = watch[0]
        wh2 = watch[1] if len(watch) > 1 else None
        wlead = (wh1.momentum[window] - wh2.momentum[window]) if wh2 else float("nan")
        wgap = wh1.momentum[window] - head1.momentum[window]
        wh2_txt = (f"{wh2.code} {html.escape(wh2.name)} {_fmt(wh2.momentum[window])}"
                   if wh2 else "-")
        wgap_txt = (f'<b style="color:{RED}">高出 {abs(wgap):.2f}pp ★ 出现更替候选</b>'
                    if wgap > 0 else f'低于 {abs(wgap):.2f}pp（主选池仍更强）')
        wcol_txt = "、".join(
            f"横{n}:{sorted(watch, key=lambda r: r.momentum.get(n, -9e9), reverse=True)[0].code}"
            for n in cols)
        winfo_txt = "<br>".join(html.escape(x) for x in (info_watch or [])) or "（无）"
        watch_block = f"""
<div class="sep"></div>
<h2 class="wh2">⑥ 备选观察池（{len(watch)} 只 · 不参与调仓，仅供"更替候选"参考）</h2>
<div class="note" style="margin-bottom:10px">
<b>本节与上面的主选池完全独立</b>：备选标的<b>不进入排名、不参与任何换仓判定</b>，
只用来看它们的动量水位，判断有没有出现值得纳入主选池的标的。<br>
· 备选头名 <b>{wh1.code} {html.escape(wh1.name)}</b>：动横{window} <b>{_fmt(wh1.momentum[window])}</b>
　次名 {wh2_txt}　领先 <b>{_fmt(wlead, pct=False, sign=True)}pp</b><br>
· 对照主选池头名 <b>{head1.code} {html.escape(head1.name)} {_fmt(head1.momentum[window])}</b>
→ 备选头名 {wgap_txt}<br>
· 备选池各列头名（仅列示）：<span class="mono">{wcol_txt}</span><br>
· 说明：博主原面板的 19 只中，若某标的已纳入主选池（或作为防守位），本节不再重复列入。
</div>
<div class="tblwrap"><table><thead><tr><th>#</th><th>代码</th><th>名称</th><th>动横{window}</th><th>乖离</th>
<th>现价</th><th>今日涨跌</th></tr></thead><tbody>
{''.join(wrank_rows)}
</tbody></table></div>
<h2 class="wh2">备选观察池 · 动横阶梯（第 n 列 = 冻结价格下未来第 {window}-n 个交易日的动横{window}）</h2>
<div class="tblwrap"><table class="lad"><thead><tr><th>代码</th><th>名称</th>
{''.join(f'<th>横{n}</th>' for n in cols)}
<th>现价</th><th>今日涨跌</th></tr></thead><tbody>
{''.join(wlad_rows)}
</tbody></table></div>
<h2 class="wh2">备选观察池 · 数据体检</h2>
<div class="note">{winfo_txt}</div>
"""

    # ---- 近期换仓复盘（recent_rotations 模块，失败时渲染降级提示）----
    rr_rows = []
    rr_block = ""
    if recent:
        if recent.get("error"):
            rr_block = f"""
<h2>② 近期换仓复盘 · 近 {recent.get('n', 3)} 次建议换仓（vs 同期上证指数）</h2>
<div class="note" style="color:#8a6d3b">
<b>本节暂不可用</b>：{html.escape(recent['error'])}。
近期换仓盈亏可改用 <span class="mono">etf_backtest_long.py</span> 的长周期回测结果查看。
</div>
"""
        else:
            _n = recent["n"]
            # ★ 2026-10-28：改用**主口径**（D3+P3+抄底）的换仓记录 —— 原来用的是基础策略，
            #   所以抄底买入（10-23 买 588170）和 D3 止损空仓（10-27 → CASH）都看不到，
            #   用户以为这张表「不更新」。现按主口径复算，与今日操作同一口径。
            _rows_src = ((recent.get("p3") or {}).get("rows") or recent["rows"])
            for rw in _rows_src:
                # 模块返回的是小数（-0.024 = -2.4%），×100 后交给 _fmt 显示
                _r = None if rw["ret"] is None else rw["ret"] * 100.0
                _b = None if rw["bench"] is None else rw["bench"] * 100.0
                _a = None if rw["alpha"] is None else rw["alpha"] * 100.0
                _closed = rw.get("closed", not rw.get("open", True))
                _tag = ("已平仓" if _closed
                        else ('<span class="tag tie">今日刚换仓 · 尚无收益</span>'
                              if rw["end_date"] == rw["date"]
                              else '<span class="tag tie">持仓中 · 未扣卖出成本</span>'))
                _days = rw.get("days", 0) or 0
                _dtxt = (f"{_days} 日" if _days
                         else '<span class="dimtxt">0 日</span>')
                # ★ 2026-10-28：空仓段（换到 CASH）不参与指数比较 ——
                #   现金 vs 指数会显示成「跑输大盘」，属误导，直接标「空仓」。
                _cash_row = bool(rw.get("cash"))
                _bench_cell = ('<span class="dimtxt">空仓</span>' if _cash_row
                               else _fmt(_b))
                _alpha_cell = ('<span class="dimtxt">—</span>' if (_cash_row or _a is None)
                               else _fmt(_a, pct=False, nd=2) + "pp")
                rr_rows.append(
                    "<tr>"
                    f'<td class="num">{rw["idx"]}</td>'
                    f'<td class="mono">{rw["date"]}</td>'
                    f'<td><span class="mono">{rw["to"]}</span> {html.escape(rw["to_name"])}'
                    f' <span class="tag tie">自 {rw["frm_name"]}</span></td>'
                    f'<td class="mono">{rw["end_date"]}<br><span class="dimtxt">{_tag}</span></td>'
                    f'<td class="num" style="font-weight:600">{_dtxt}</td>'
                    f'<td class="num" style="color:{_color(_r)};font-weight:600">'
                    f'{_fmt(_r)}</td>'
                    f'<td class="num" style="color:{_color(_b)}">{_bench_cell}</td>'
                    f'<td class="num" style="color:{_color(_a)};font-weight:600">'
                    f'{_alpha_cell}</td>'
                    f'<td class="rs" style="min-width:150px">{html.escape(rw["reason"])}</td>'
                    "</tr>")
            # ★ 2026-10-28：Σ 行必须与**上面这张表**同源（明细已改主口径），
            #   否则「与上表严格自洽」是假话。这里按 _rows_src 自行连乘。
            _pr, _pb = 1.0, 1.0
            _n_r = _n_b = 0
            for _rw in _rows_src:
                if _rw.get("ret") is not None:
                    _pr *= (1.0 + _rw["ret"]); _n_r += 1
                if _rw.get("bench") is not None:
                    _pb *= (1.0 + _rw["bench"]); _n_b += 1
            # 末段若是「今日刚换仓」，同期上证可能无数据（_n_b < _n_r），
            # 此时用**可算的段**连乘，并在说明里标注，避免整列显示「-」
            _ok_b = (_n_b > 0 and _n_b == _n_r)
            _ok_b_part = (_n_b > 0 and _n_b < _n_r)
            _c = dict(
                start=(_rows_src[0]["date"] if _rows_src else recent["cum"]["start"]),
                end=recent["cum"]["end"], trades=len(_rows_src),
                ret=(_pr - 1.0) if _n_r else None,
                bench=(_pb - 1.0) if (_ok_b or _ok_b_part) else None,
                alpha=((_pr - _pb)) if _ok_b else None,
                bench_partial=_ok_b_part)
            _cr = None if _c["ret"] is None else _c["ret"] * 100.0
            _cb = None if _c["bench"] is None else _c["bench"] * 100.0
            _ca = None if _c["alpha"] is None else _c["alpha"] * 100.0
            _cum_days = sum((rw.get("days") or 0) for rw in _rows_src)
            rr_rows.append(
                '<tr class="def">'
                f'<td class="num">Σ</td>'
                f'<td class="mono">{_c["start"]}</td>'
                f'<td><b>累计 {_c["trades"]} 次换仓连乘</b></td>'
                f'<td class="mono">{_c["end"]}</td>'
                f'<td class="num" style="font-weight:700">{_cum_days} 日</td>'
                f'<td class="num" style="color:{_color(_cr)};font-weight:700">'
                f'{_fmt(_cr)}</td>'
                f'<td class="num" style="color:{_color(_cb)}">{_fmt(_cb)}</td>'
                f'<td class="num" style="color:{_color(_ca)};font-weight:700">'
                f'{"<span class=\"dimtxt\">—</span>" if _ca is None else _fmt(_ca, pct=False, nd=2) + "pp"}</td>'
                f'<td class="rs" style="min-width:150px">分段 (1+r) 连乘，与上表严格自洽'
                f'{"；同期上证只算持仓段（空仓段/末段不计），故不列超额" if _c.get("bench_partial") else ""}'
                f'</td>'
                '</tr>')
            _ext_txt = ""
            if recent.get("extended"):
                _ext_txt = (f"；长历史止于 <b>{recent['hist_end']:%Y-%m-%d}</b>，"
                            f"其后 {html.escape(str(recent.get('ext_note') or ''))}"
                            f" <b>→ 已补至 {recent['asof']}，故「至今」= 锚定日</b>")
            rr_src = (f"数据源：{html.escape(recent['info'])}；"
                      f"「至今」= <b>{recent['asof']}</b>"
                      + _ext_txt
                      + ("" if recent.get("extended") or not recent.get("data_end")
                         or str(recent["data_end"]) >= pivot.replace("-", "")
                         else f" ｜ ⚠ 长历史（{recent.get('data_end','')}）早于锚定日 {pivot}，"
                              f"且在线补数失败，以长历史截止日为准"))
            rr_note = ("最后一段为当前持仓、未平仓，不计卖出成本；其余各段已扣双边成本。"
                       "<b>持仓天数</b>按交易日计（非自然日），今日刚换仓记 0 日。"
                       "本节为事后规则复算（模型输出），非实盘成交记录。")
            rr_block = f"""
<h2>② 近期换仓复盘 · 近 {_n} 次建议换仓（vs 同期上证指数）</h2>
<div class="note" style="margin-bottom:10px">
已复算最近 {_n} 次建议换仓的收益、持仓天数与同期上证对比。
<details class="lgd"><summary>复算规则</summary>
<div class="lgdb">按<b>同一套策略规则</b>（动横{window} 排名 + 粘性带 {sticky:.1f}pp + 过热熔断 + 价值引擎）在长历史前复权数据上复算，
取最近 {_n} 次系统建议换仓，计算「该次换仓日起到下一次换仓（或至今）」的净值收益与<b>持仓天数</b>，
并与<b>同期上证指数</b>对比。{rr_src}</div></details>
</div>
<div class="tblwrap"><table class="rtbl"><thead><tr><th>#</th><th>建议换仓日</th><th>买入</th><th>持仓至</th>
<th title="该段实际持仓的交易日根数">持仓天数</th>
<th>策略收益</th><th>同期上证</th><th>超额</th><th>触发依据</th></tr></thead><tbody>
{''.join(rr_rows)}
</tbody></table></div>
<div class="sub" style="margin-top:8px">{rr_note}</div>
{_p3_card_html(recent.get("p3") or {}, recent, recent.get("cost", 0.002))}
{_health_html((recent.get("p3") or {}).get("metrics"))}
<!-- 2026-10-26 用户要求：正文不再单列「D3 单笔止损 · 参照」卡片（现在没人只看这一条）。
     recent["d3"] 仍照常计算并保留在净值曲线里，只是不单独成卡。 -->
"""

    info_txt = "<br>".join(html.escape(x) for x in info)
    _fs_stale, _fs_txt = freshness
    fresh_cls = "fresh stale" if _fs_stale else "fresh"
    _lv_on, _lv_txt = live
    # ★ 2026-10-25：数据校验条平时只占**一行小徽章**（正常 = 不需要每天读两行说明），
    #   只有抓取失败 / 数据非最新时才退回整宽醒目横幅。详细来源收进 <details>。
    # ★ 2026-10-26：数据正常时收成**页头内的一行小徽章**（占用最小）；
    #   异常（抓取失败 / 非最新交易日）时改为 hero 之外的整宽醒目横幅 —— 异常必须刺眼。
    _fresh_ok = (_lv_on and not _fs_stale
                 and "⚠" not in _lv_txt and "失败" not in _lv_txt)
    if _fresh_ok:
        fresh_inline = (
            f'<div class="fdot"><span class="fdot-i"></span>数据正常 · 截至 '
            f'<b>{html.escape(str(pivot))}</b>'
            f'<details class="fdet"><summary>来源</summary>'
            f'<div class="fdetb">{_lv_txt}</div></details></div>')
        fresh_alert = ""
    else:
        fresh_inline = ""
        fresh_alert = (f'<div class="fresh live">{_lv_txt}</div>' if _lv_on
                       else f'<div class="{fresh_cls}">{_fs_txt}</div>')

    # ---- 粘性带安全垫（2026-10-25）：一眼回答「今天收盘前会不会换仓」----
    # 用户原来要自己心算「第1名 − 第2名 − 粘性带」，现在直接给结果。
    # 距换仓 = lead − sticky：次名相对头名追近这么多 pp，才会触发换仓判定。
    _sr = []
    if overheated:
        _sr.append(f'<span class="sr-t">🔥 头名已过热熔断</span>')
        _sr.append(f'<span>乖离 <b>{head1.dev:+.2f}%</b> 或 领先 <b>{lead:+.2f}pp</b> 触线 → '
                   f'<b>今日切防守/持币</b>（见上方今日操作）</span>')
        _saferow_html = '<div class="saferow hot">' + "".join(_sr) + "</div>"
    else:
        _sr.append(f'<span class="sr-t">🥇 {html.escape(head1.code)} '
                   f'{html.escape(head1.name)}</span>')
        if head2 is not None and lead == lead:
            _sr.append(f'<span>领先次名 <b>{lead:+.2f}pp</b></span>')
            _pad = lead - sticky
            if _pad > 0:
                _sr.append(f'<span>距换仓 <b>{_pad:.2f}pp</b>'
                           f'<i>次名需再追近这么多</i></span>')
            else:
                _sr.append('<span style="color:#d4380d;font-weight:700">'
                           '已在粘性带内 · 头名一变就可能换仓</span>')
            _sr.append(f'<span class="dimtxt2">粘性带 {sticky:.1f}pp</span>')
        _cands = []
        if head1.dev == head1.dev:
            _cands.append((overheat_dev - head1.dev, "乖离"))
        if lead == lead:
            _cands.append((overheat_lead - lead, "领先"))
        if _cands:
            _m, _w = min(_cands, key=lambda x: x[0])
            if _m > 0:
                _sr.append(f'<span>距过热 <b>{_m:.2f}pp</b>'
                           f'<i>（{_w}口径）</i></span>')
        _saferow_html = '<div class="saferow">' + "".join(_sr) + "</div>"

    # ---- 抄底影子信号（2026-10-26）：**不改变默认口径**，只在全池转负时给参照 ----
    # 研究结论：主选池 9 只动横20 全为负 → 买「排名最后一名」，主窗 11.0196→13.6360
    # （+2.62），最大回撤不变（−15.12%），分年度 7/8 年跑赢、无一年为负，且零阈值参数。
    # ⚠️ 但它不降低回撤、且属反直觉操作，故只作提示，不写进「今日应持」。
    _all_neg = bool(ranked) and all(r.momentum[window] < 0 for r in ranked)
    _weak_html = ""
    if _all_neg and len(ranked) >= 2:
        _wr = ranked[-1]
        _wv = _wr.momentum[window]
        _n_neg = sum(1 for r in ranked if r.momentum[window] < 0)
        if _wr.code == final_code:
            _wtxt = (f'当前持有<b>{html.escape(_wr.code)} {html.escape(_wr.name)}</b>'
                     f'（正是最弱那只），与抄底信号方向一致 → 无需额外动作')
            _wcls = "ok"
        else:
            _wtxt = (f'历史规律指向买最弱：<b>{html.escape(_wr.code)} '
                     f'{html.escape(_wr.name)}</b>（动横{window} {_wv:+.2f}%，'
                     f'排名 {len(ranked)}/{len(ranked)}）'
                     f'，与上方默认口径的 {html.escape(str(final_code))} 不同')
            _wcls = "diff"
        _weak_html = f"""<div class="weaksig {_wcls}">
<div class="wkh">🔍 抄底影子信号 · 全池转负 <span class="sphs">（参照，不改默认口径）</span></div>
<div class="wkb">主选池 <b>{_n_neg}/{len(ranked)}</b> 只动横{window} 为负（最高 {head1.momentum[window]:+.2f}%，最弱 {_wv:+.2f}%）。{_wtxt}。</div>
<div class="wkf">依据：2019-2026 主选池回测，此规则净值 <b>11.0196 → 13.6360</b>（+2.62）、
最大回撤 <b>不变 −15.12%</b>、分年度 <b>7/8 年跑赢且无一年为负</b>、无阈值参数。
<span class="dimtxt">⚠️ 属「增收益」而非「降回撤」，且为反直觉操作；是否执行由你决定。</span></div>
</div>"""

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<!-- PWA：手机「添加到主屏幕」后全屏运行，无地址栏 / 底部工具栏 -->
<link rel="manifest" href="manifest.json">
<meta name="theme-color" content="#eef0f3">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<meta name="apple-mobile-web-app-title" content="ETF日报">
<link rel="apple-touch-icon" href="apple-touch-icon.png">
<link rel="icon" type="image/png" sizes="64x64" href="favicon-64.png">
<title>ETF 动横阶梯轮动报告 · {pivot}</title>
<style>
  :root{{--bd:#e2e5ea;--mut:#6c717f;--fg:#202020;}}
  *{{box-sizing:border-box}}
  body{{margin:0;padding:24px;background:#eef0f3;color:var(--fg);
    font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}}
  .wrap{{max-width:1500px;margin:0 auto}}
  /* 顶部蓝色渐变标题带 */
  .hero{{border-radius:16px;padding:22px 26px;margin:0 0 18px;color:#fff;
    background:linear-gradient(120deg,#3d57e5 0%,#4a64e8 55%,#566de7 100%);
    box-shadow:0 6px 18px rgba(61,87,229,.25)}}
  .hero h1{{font-size:22px;margin:0;letter-spacing:.5px}}
  .hero .sub{{color:rgba(255,255,255,.86);font-size:12.5px;margin:6px 0 0}}
  .hero .sub b{{color:#fff}}
  h2{{font-size:15px;margin:28px 0 10px;padding-left:10px;color:#202020;
    border-left:4px solid;border-image:linear-gradient(180deg,#3d57e5,#566de7) 1}}
  .sub{{color:var(--mut);font-size:12.5px;margin-bottom:14px}}
  table{{width:100%;border-collapse:collapse;background:#fff;border:1px solid var(--bd);
    border-radius:10px;overflow:hidden;font-size:13px;box-shadow:0 1px 3px rgba(16,24,40,.05)}}
  th,td{{padding:7px 9px;border-bottom:1px solid #f0f2f5;text-align:left;white-space:nowrap}}
  th{{background:#f4f6fa;font-weight:600;font-size:12.5px;color:#2a3140}}
  tr:last-child td{{border-bottom:none}}
  tr.def td{{background:#fafbfc;color:#555}}
  .num{{text-align:right;font-variant-numeric:tabular-nums}}
  .mono{{font-family:Consolas,Menlo,monospace}}
  .tag{{display:inline-block;padding:1px 7px;border-radius:9px;font-size:11.5px}}
  .tag.swap{{background:#fff1e8;color:{RED};border:1px solid #ffd0b8}}
  .tag.tie{{background:#f2f4f7;color:#5b6472;border:1px solid #e0e4ea}}
  .tag.def{{background:#f2f4f7;color:#5b6472;border:1px solid #e0e4ea}}
  .tag.rot{{background:#eef1fe;color:#3d57e5;border:1px solid #cdd8fb}}
  /* 抄底买入标记（2026-10-26）：与常规轮动区分，一眼看出这笔是「全池转弱→买末位」 */
  .tag.weakb{{background:#fff4e8;color:#b45309;border:1px solid #fcd9a8}}
  .tag.defhot{{background:#fff1e8;color:{RED};border:1px solid #ffcbb0;margin-top:3px}}
  .tag.defcash{{background:#f2f4f7;color:#5b6472;border:1px solid #e0e4ea;margin-top:3px}}
  tr.defhot td{{background:#fff8f3}}
  tr.defdim td{{background:#fbfcfd}}
  .dimtxt{{color:#98a1ad;font-size:12px}}
  /* 防守位操作指南 */
  .dcard{{border-radius:12px;padding:14px 16px;margin:0 0 8px;border:1px solid var(--bd);
    background:#fff;box-shadow:0 1px 3px rgba(16,24,40,.05)}}
  .dcard.on{{border-color:#ffcbb0;background:#fffaf6;box-shadow:0 2px 8px rgba(212,56,13,.10)}}
  .dcard.off{{border-color:#e2e5ea;background:#fcfcfd}}
  .dghdr{{font-size:14px;line-height:1.75;padding:9px 12px;border-radius:9px;margin-bottom:10px}}
  .dghdr.on{{background:#fff1e8;color:#8a2c08;border:1px solid #ffd7c2}}
  .dghdr.off{{background:#f2f4f7;color:#5b6472;border:1px solid #e4e8ed}}
  .dgstate{{font-size:13.5px;padding:8px 12px;border-radius:9px;margin-bottom:10px;border:1px solid}}
  .dgstate.hold{{background:#eef1fe;border-color:#cdd8fb;color:#2c4fb8}}
  .dgstate.cash{{background:#f2f4f7;border-color:#e0e4ea;color:#5b6472}}
  .dgstate.idle{{background:#fafbfc;border-color:#e9ecef;color:#6c717f}}
  /* 防守位：基金口径 vs 跟踪指数口径（MA200 偏离对照） */
  .dgcmp{{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px}}
  .cmpc{{border:1px solid var(--bd);border-radius:10px;padding:10px 13px;background:#fff;
    box-shadow:0 1px 2px rgba(16,24,40,.04);min-width:0}}
  .cmpc.fund{{border-color:#ccd7fb;background:#f8faff}}
  .cmpc.idx{{border-color:#ffe1c4;background:#fffbf5}}
  .cmpl{{font-size:11.5px;color:var(--mut);margin-bottom:3px;line-height:1.5}}
  .cmpl b{{color:var(--fg);font-weight:600}}
  .cmpv{{font-size:22px;font-weight:700;line-height:1.15;letter-spacing:-.5px}}
  .cmpv small{{font-size:11.5px;font-weight:500;color:var(--mut);letter-spacing:0;margin-left:5px}}
  .cmps{{font-size:11.5px;color:var(--mut);margin-top:5px;line-height:1.65}}
  .cmpgap{{grid-column:1/-1;font-size:12.5px;line-height:1.8;padding:9px 12px;
    border-radius:9px;border:1px dashed #d8dee6;background:#f7f9fb;color:#374151}}
  .cmpgap.hot{{background:#fff4ec;border-color:#ffd0b0;color:#8a2c08}}
  .cmpgap.pos{{background:#f2fbf6;border-color:#c9ecd9;color:#0b6b3a}}
  @media(max-width:700px){{.dgcmp{{grid-template-columns:1fr}}}}
  .idxbadge{{display:inline-block;margin-top:4px;font-size:11px;line-height:1.6;
    padding:2px 7px;border-radius:6px;background:#fff7ef;border:1px solid #ffe0c4;
    color:#7a4a20;font-weight:400}}
  .idxbadge b{{font-weight:700}}
  /* 排名表防守位：徽标下沉为 colspan 子行（不再撑宽「名称」列 → 消灭横向滚动条） */
  tr.def td{{border-bottom:none}}
  tr.defsub td{{background:#fafbfc;color:#555;padding:0 6px 7px}}
  .defbar{{display:flex;flex-wrap:wrap;align-items:center;gap:4px 8px;min-width:0}}
  .defbar .idxbadge{{margin-top:0;white-space:normal;line-height:1.55}}
  .defbar .dim2{{color:#6c717f;font-size:11px;white-space:nowrap}}
  /* 今日操作指引 · 防守位口径条带：跨 4 列独占一行，数值并列 chip + 结论单行 */
  table.gtbl tr.defmain td{{border-bottom:none}}
  table.gtbl tr.defx td{{padding:0 9px 8px}}
  tr.defx.dimx td{{background:#fbfcfd}}
  tr.defx.hotx td{{background:#fff8f3}}
  .gidxbar{{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 14px;
    padding:7px 10px;border:1px solid #ffe3c8;border-radius:8px;background:#fffaf4;
    font-size:11.5px;line-height:1.65;color:#6b4320;min-width:0}}
  .gidxbar .k{{font-weight:600;color:#8a5a2b;white-space:nowrap}}
  .gidxbar .v{{white-space:nowrap}}
  .gidxbar .v i{{font-style:normal;color:#8a6a45;margin-left:4px;font-size:11px}}
  .gidxnote{{margin-top:5px;padding:6px 10px;border-radius:8px;font-size:11.5px;
    line-height:1.65;background:#f7f9fb;border:1px dashed #d8dee6;color:#4b5563;
    white-space:normal}}
  .gidxnote.hot{{background:#fff4ec;border-color:#ffd0b0;color:#8a2c08}}
  .gidxnote.pos{{background:#f2fbf6;border-color:#c9ecd9;color:#0b6b3a}}
  /* 「近 1 年偏离区间」= 历史参照条：中性灰虚线，刻意弱于上面的 action 条（暖/冷实线），
     避免被误读成「今天要做的事」 */
  .hspan{{display:flex;flex-wrap:wrap;align-items:baseline;gap:3px 14px;margin-top:5px;
    padding:6px 10px;border:1px dashed #d8dee6;border-radius:8px;background:#fafbfc;
    font-size:11.5px;line-height:1.65;color:#4b5563;min-width:0}}
  .hspan .k{{font-weight:600;color:#5b6472;white-space:nowrap}}
  .hspan .v{{white-space:nowrap}}
  .hspan .v i{{font-style:normal;color:#8b93a1;margin-left:4px;font-size:11px}}
  /* MA200 条带的「极值 · 长窗口」折叠区（2026-10-25 手机端改造，纯 HTML 无需 JS） */
  .hspan .hdet{{flex:0 0 100%;margin:1px 0 0}}
  .hdet>summary{{cursor:pointer;color:#3d57e5;font-size:11px;font-weight:600;
    list-style:none;display:inline-flex;align-items:center;gap:4px;outline:none;
    padding:1px 0}}
  .hdet>summary::-webkit-details-marker{{display:none}}
  .hdet>summary::before{{content:"▸";font-size:10px;color:#3d57e5}}
  .hdet[open]>summary::before{{content:"▾"}}
  .hdetb{{display:flex;flex-wrap:wrap;align-items:baseline;gap:3px 14px;
    margin-top:3px;padding-top:4px;border-top:1px dashed #e4e8ee}}
  .hdetb .v{{white-space:nowrap}}   /* 手机端改为 normal（见媒体查询） */
  /* 策略二（512890）：每日跟踪行 */
  table.gtbl tr.tvmk td{{border-bottom:none}}
  table.gtbl tr.tvx td{{padding:0 9px 8px}}
  /* 「今日执行口径」胶囊条：一行给结论，回测表收进 details（不一致时自动展开） */
  .cbsum{{background:#fbfcfe;border:1px solid #e2e5ea;border-left:3px solid #2e9e5b;
    border-radius:8px;padding:7px 10px;margin:0 0 8px}}
  .cbsum.diff{{border-left-color:#d4380d;background:#fff8f6}}
  .cbsum.warn{{border-left-color:#d4a017;background:#fffdf6}}
  /* 胶囊放大（2026-10-26 用户要求「第一时间看到」）：12.5px → 15px，结论加粗 */
  /* 抄底锁定状态条（2026-10-28 二次改版）：左侧细边 + 双出口分行，避免与红色告警抢注意力 */
  .wstate{{margin:9px 0 0;padding:9px 12px 8px;background:#fffdfa;
    border:1px solid #eee6dc;border-left:3px solid #d99a4e;border-radius:9px;
    line-height:1.65;white-space:normal}}
  .whd{{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 8px;margin-bottom:7px}}
  .wbadge{{display:inline-block;background:#fdf1e2;border:1px solid #f0d9bd;color:#8a5312;
    border-radius:5px;padding:0 7px;font-size:11px;font-weight:700}}
  .wtip{{font-size:10.5px;color:#9aa3ae;margin-left:auto}}
  /* 双出口：label / 数值 / 条 / 剩余，四列对齐 */
  .wrow{{display:grid;grid-template-columns:96px 62px 1fr auto;align-items:center;
    gap:8px;padding:2px 0}}
  .wlab{{font-size:11.5px;color:#6c717f;white-space:nowrap}}
  .wnum{{font-size:13px;font-weight:800;font-variant-numeric:tabular-nums;color:#202020;
    text-align:right;white-space:nowrap}}
  .wbar{{height:5px;background:#f2ece4;border-radius:3px;overflow:hidden;min-width:50px}}
  .wbar i{{display:block;height:100%;background:linear-gradient(90deg,#e8b978,#c98336);
    border-radius:3px}}
  .wbar.flat{{background:transparent}}
  .wrest{{font-size:11.5px;color:#5b6472;white-space:nowrap}}
  .wpnl{{margin-top:6px;padding-top:6px;border-top:1px dashed #ece4d9;font-size:12px;
    color:#42474f}}
  .wnote{{margin-top:4px;font-size:10.5px;color:#9aa3ae}}
  @media(max-width:640px){{.wrow{{grid-template-columns:1fr auto;gap:2px 8px}}
    .wbar{{grid-column:1 / -1}} .wrest{{grid-column:1 / -1;font-size:11px}}}}
  /* 口径变更提示：一次性通知而非风险告警，用中性灰蓝，别抢红色警报的注意力 */
  .migr{{margin:0 0 9px;padding:8px 12px;background:#f7f9fb;border:1px solid #e2e8ef;
    border-left:3px solid #7b8ea8;border-radius:9px;font-size:11.5px;line-height:1.7;
    color:#4b5563;white-space:normal}}
  .pillcal{{font-size:11px;color:#8b93a1;font-weight:400}}
  .cbpill{{font-size:15px;line-height:1.58;color:#202020;white-space:normal;font-weight:500}}
  .cbpill b{{font-weight:800}}
  .cbsum.diff .cbpill{{font-size:15px}}
  .cbdet{{display:inline}}
  .cbdet>summary{{cursor:pointer;color:#3d57e5;font-size:11.5px;font-weight:600;
    list-style:none;display:inline-block;margin-left:8px}}
  .cbdet>summary::-webkit-details-marker{{display:none}}
  .cbdet>summary::before{{content:"▸ "}}
  .cbdet[open]>summary::before{{content:"▾ "}}
  .cbdet .tblwrap{{margin-top:7px}}
  .cbsh{{font-size:12px;font-weight:600;color:#202020;margin-bottom:5px}}
  .cbsh span{{font-weight:400;color:#6c717f;font-size:11px}}
  /* 抄底影子信号（2026-10-26）：仅在「主选池动横20 全为负」当天出现，独立于默认口径 */
  .weaksig{{margin:9px 0 0;border:1px solid #dbe6fb;border-left:3px solid #6d8bd8;
    border-radius:9px;padding:8px 11px;background:#f7faff;font-size:11.5px;
    line-height:1.68;color:#3b4a63;white-space:normal}}
  .weaksig.diff{{border-left-color:#d4380d;background:#fff8f6}}
  .weaksig.ok{{border-left-color:#2e9e5b;background:#f5fbf7}}
  .wkh{{font-weight:700;color:#2c4fb8;margin-bottom:3px}}
  .weaksig.diff .wkh{{color:#b3351a}}
  .weaksig.ok .wkh{{color:#1f7a48}}
  .wkb b{{color:#111}}
  .wkf{{margin-top:4px;color:#6c717f;font-size:11px}}
  .wkf b{{color:#42474f}}
  table.cbst{{font-size:11.5px;margin:0;width:100%}}
  table.cbst th,table.cbst td{{padding:3px 5px}}
  table.cbst tr.hi td{{background:#fdf2f7}}
  table.cbst td.hold{{font-weight:600}}
  table.cbst td.cash{{color:#b3351a;font-weight:700}}
  .cbsn{{font-size:11.5px;color:#42474f;line-height:1.6;margin-top:5px;
    white-space:normal}}
  .cbsn b{{color:#111}}
  tr.tvmk.tvbuy td{{background:#f4fbf7}}
  tr.tvmk.tvsell td{{background:#fff8f3}}
  tr.tvmk.tvmid td{{background:#fbfcfd}}
  .tvbar{{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 14px;
    padding:7px 10px;border:1px solid #d3dced;border-radius:8px;background:#f7f9fe;
    font-size:11.5px;line-height:1.65;color:#3a4560;min-width:0}}
  .tvbar .k{{font-weight:600;color:#33447a;white-space:nowrap}}
  .tvbar .v{{white-space:nowrap}}
  .tvbar .v i{{font-style:normal;color:#5d6a86;margin-left:4px;font-size:11px}}
  .tag.tvt{{background:#e8edfb;border:1px solid #c8d4f5;color:#2c4fb8}}
  table.dgtbl{{font-size:12.5px;margin-bottom:10px}}
  table.dgtbl th,table.dgtbl td{{white-space:normal;vertical-align:top;line-height:1.7}}
  .dgnote{{font-size:12.5px;line-height:1.85;color:#374151;background:#f7f9fb;
    border:1px dashed #d8dee6;border-radius:9px;padding:10px 12px}}
  .rs{{color:#5b6470;font-size:12.5px;white-space:normal;min-width:260px}}
  h3{{font-size:14px;font-weight:600;margin:24px 0 8px;color:#202020}}
  .good{{color:{GREEN};font-weight:600}} .warn{{color:#b26a00;font-weight:600}}
  .bad{{color:{RED};font-weight:700}}
  .note{{background:#fff;border:1px solid var(--bd);border-radius:12px;padding:12px 14px;
    color:#374151;font-size:12.5px;box-shadow:0 1px 3px rgba(16,24,40,.05)}}
  .sep{{margin:36px 0 0;border-top:3px double #d9dee5}}
  h2.wh2{{border-image:linear-gradient(180deg,#9aa3ae,#b6bdc6) 1;color:#5b6472}}
  .fresh{{border-radius:10px;padding:10px 14px;font-size:12.5px;line-height:1.75;
    margin:12px 0 4px;border:1px solid #d8dee6;background:#f7f9fb;color:#4b5563}}
  .fresh.stale{{background:#fffbe6;border-color:#ffe58f;color:#614700}}
  .fresh.live{{background:#eef1fe;border-color:#cdd8fb;color:#2c4fb8}}
  /* 数据正常 = 一行小徽章（异常才用上面的整宽横幅） */
  .fdot{{display:flex;align-items:center;gap:7px;flex-wrap:wrap;margin:8px 0 2px;
    font-size:11.5px;color:#5b6472}}
  .fdot-i{{width:7px;height:7px;border-radius:50%;background:#2e9e5b;flex:0 0 auto;
    box-shadow:0 0 0 3px rgba(46,158,91,.14)}}
  .fdet{{display:inline}}
  .fdet>summary{{cursor:pointer;color:#3d57e5;font-weight:600;list-style:none;
    display:inline;font-size:11.5px}}
  .fdet>summary::-webkit-details-marker{{display:none}}
  .fdet>summary::before{{content:"· "}}
  .fdetb{{flex:0 0 100%;margin-top:3px;padding:5px 8px;background:#f7f9fb;
    border:1px solid #e6eaf0;border-radius:7px;color:#4b5563;font-size:11.5px;
    line-height:1.6;white-space:normal}}
  /* 数据徽章嵌进蓝色 hero 后的反白配色（2026-10-26） */
  .hero .fdot{{margin:7px 0 0;color:rgba(255,255,255,.9);font-size:11.5px}}
  .hero .fdot b{{color:#fff}}
  .hero .fdot .fdot-i{{background:#7fe3a9;box-shadow:0 0 0 3px rgba(127,227,169,.22)}}
  .hero .fdet>summary{{color:#fff;text-decoration:underline;text-underline-offset:2px}}
  .hero .fdetb{{background:rgba(255,255,255,.14);border-color:rgba(255,255,255,.28);
    color:#fff}}
  .foot{{margin-top:24px;padding-top:14px;border-top:1px solid #dfe3e8;
    color:var(--mut);font-size:11.5px;line-height:1.8}}
  .lead{{margin:14px 0 0;color:#5b6472;font-size:13px}}
  /* 图例 / 复算规则折叠：每天一字不差的说明书式段落默认收起（2026-10-25） */
  .lgd{{display:inline}}
  .lgd>summary{{cursor:pointer;color:#3d57e5;font-size:12px;font-weight:600;
    list-style:none;display:inline-block;margin-left:6px}}
  .lgd>summary::-webkit-details-marker{{display:none}}
  .lgd>summary::before{{content:"▸ "}}
  .lgd[open]>summary::before{{content:"▾ "}}
  .lgdb{{margin-top:5px;padding:6px 9px;background:#f9fafc;border:1px dashed #dfe4ea;
    border-radius:7px;line-height:1.72;font-size:12px;color:#5b6472;
    white-space:normal}}
  /* 近 5 年策略体检横条（2026-10-27）：跟在 D3+P3 卡片后面 */
  .hsec{{margin:12px 0 0;background:#fbfcfe;border:1px solid #e2e5ea;border-radius:10px;
    padding:11px 13px}}
  .hhd{{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 10px;margin-bottom:8px}}
  .hh2{{font-size:14px;margin:0;color:#202020;font-weight:700}}
  .htip{{font-size:11px;color:#6c717f}}
  .hrow{{display:flex;flex-wrap:wrap;gap:9px 0}}
  .hc{{flex:1 1 19%;min-width:132px;padding:4px 10px 4px 0;display:flex;
    flex-direction:column;gap:2px}}
  .hc .hk{{font-size:11px;color:#6c717f}}
  .hc b{{font-size:16px;font-weight:800;font-variant-numeric:tabular-nums}}
  .hc i{{font-style:normal;font-size:10.5px;color:#8b93a1;line-height:1.5}}
  @media(max-width:600px){{.hc{{flex:1 1 46%}}}}
  /* 「我的实盘跟踪」区块（2026-10-26）：放在 ③ 之后，不挤占首屏 */
  .minesec{{margin:26px 0 0;background:#fbfcfe;border:1px solid #e2e5ea;
    border-left:3px solid #3d57e5;border-radius:10px;padding:12px 14px}}
  .mhd{{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 10px;margin-bottom:9px}}
  .mh2{{font-size:15px;margin:0;color:#202020;font-weight:700}}
  .mtip{{font-size:11.5px;color:#6c717f}}
  .mrow{{display:flex;flex-wrap:wrap;gap:8px 0}}
  .mcell{{flex:1 1 30%;min-width:104px;padding:6px 10px 6px 0;
    display:flex;flex-direction:column;gap:2px}}
  .mcell .mk{{font-size:11px;color:#6c717f}}
  .mcell b{{font-size:16px;font-weight:800;color:#202020;font-variant-numeric:tabular-nums}}
  .msub{{margin-top:8px;padding-top:8px;border-top:1px dashed #e2e5ea;
    font-size:12px;color:#42474f;line-height:1.7}}
  .mnote{{margin-top:6px;font-size:11px;color:#8b93a1;line-height:1.6}}
  /* ===== 今日操作（2026-10-25 重排：置顶 + 独占整宽）=====
     旧版是「左 42% 放 4 列操作表 | 右放排名表+年度卡」，4 列被挤在 42% 里文字
     严重折行、整页显乱。现在操作表拿到整宽（表格 auto 布局，依据列自然变宽，
     不需要 table-layout:fixed——那样反而会留大片空白），并把排名表与年度卡
     降到它下面并排。 */
  .gsec{{background:#fff;border:1px solid var(--bd);border-radius:12px;
    padding:14px 16px 12px;margin:16px 0 0;box-shadow:0 1px 3px rgba(16,24,40,.05)}}
  .ghead{{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin:0 0 10px}}
  .gh2{{font-size:16px;font-weight:700;margin:0;color:#202020;
    padding-left:10px;border-left:4px solid;
    border-image:linear-gradient(180deg,#3d57e5,#566de7) 1}}
  .gh2 .gtip{{font-size:12px;font-weight:400;color:var(--mut);margin-left:8px}}
  .gdate{{margin-left:auto;font-size:12px;color:var(--mut);white-space:nowrap}}
  .gsec table.gtbl{{font-size:12.5px;width:100%;table-layout:fixed;
    border:none;border-radius:0;box-shadow:none;background:transparent}}
  /* ⚠️ 全局 table 自带 border+圆角+阴影（卡片样式）；放进 .gsec 白卡片里会变成
     「卡片套卡片」的双层边框与双层圆角 —— 这是之前显乱的主要原因之一，故清零。 */
  /* fixed 布局：前三列定宽，第 4 列（依据）吃掉剩余，避免 auto 布局下
     「依据」列被拉到近千 px、文字只占左侧一小段的空旷感 */
  .gsec table.gtbl th:nth-child(1){{width:104px}}
  .gsec table.gtbl th:nth-child(2){{width:296px}}
  .gsec table.gtbl th:nth-child(3){{width:214px}}
  .gsec table.gtbl th{{background:#f4f6fa;font-weight:600;color:#4b5563;
    padding:7px 9px;border-bottom:1px solid var(--bd)}}
  .gsec table.gtbl td{{padding:7px 9px;border-bottom:1px solid #eef0f3}}
  .gsec table.gtbl tbody tr:last-child td{{border-bottom:none}}
  /* 白卡片里的说明块改成浅灰底，避免「白上白」看不出层次 */
  .gsec .note{{background:#f7f9fb;box-shadow:none;border-color:#e2e5ea}}
  /* 排名表 + 年度卡：并排、等高（年度卡被拉伸到排名表高度，底边齐平） */
  .split2{{display:flex;gap:14px;align-items:stretch;margin:14px 0 0}}
  .s2l{{flex:1 1 auto;min-width:0;display:flex;flex-direction:column;min-height:0}}
  /* 年度卡 400px（原 300）：给图更多横向空间，同时把排名表自然压窄、
     列间距收紧，不再出现「表格被拉得很散」的观感 */
  .s2r{{flex:0 0 400px;min-width:0;display:flex;flex-direction:column;min-height:0}}
  /* min-height 防「排名表很矮时年度卡被压扁到看不见图」；两者底边仍由 stretch 齐平 */
  .s2r .ytdchart{{flex:1 1 auto;min-height:250px}}
  /* ===== ① 今日排名卡片（2026-10-25 补）=====
     此前 h2 写在卡片**外面**、表格自带边框当「框」，而右边年度卡把标题包在**框内**，
     于是左框整体下移约 30px、两框底边永远对不上。现在左边也包成同规格卡片、
     标题收进框内，两框的顶边与底边同时对齐。 */
  .pcard{{display:flex;flex-direction:column;min-height:0;flex:1 1 auto;
    background:#fff;border:1px solid var(--bd);border-radius:10px;
    padding:10px 12px;box-shadow:0 1px 3px rgba(16,24,40,.05)}}
  .pcard h2.sph2{{margin:0 0 8px;font-size:15px;padding-left:9px;
    border-left-width:3px;display:flex;align-items:baseline;gap:2px;flex-wrap:wrap}}
  .pcard .rtblwrap2{{flex:1 1 auto;min-height:0;overflow-x:auto}}
  /* 全局 table 自带 border+radius+shadow（卡片样式）→ 内层清零，避免「卡片套卡片」 */
  .pcard table.rtbl{{border:none;border-radius:0;box-shadow:none;background:transparent}}
  /* 旧布局遗留（.split/.rwrap）保留但已不在主流程使用 */
  /* 操作指引 与 ① 今日排名 并排（左右等高） */
  .split{{display:flex;gap:18px;align-items:stretch;margin:20px 0 6px}}
  .split .l{{flex:0 0 42%;min-width:0}}
  .split .r{{flex:1;min-width:0;display:flex;flex-direction:column;min-height:0}}
  /* ① 今日排名表 + 年度表现柱状图 并排（等高，表内溢出不外泄到页面）
     ⚠️ 对齐约束（2026-10-25）：右列整体撑满 .split 行高 → .rwrap 撑满右列 →
     年度卡再撑满 .rwrap，SVG 用 height:100% 等比缩放填充剩余空间。
     这样「左列底边 = 排名表底边 = 年度卡底边」三条线天然重合，不会出现台阶。 */
  .rwrap{{display:flex;gap:12px;align-items:stretch;flex:1 1 auto;min-height:0}}
  .rtblwrap{{flex:1 1 auto;min-width:0;overflow-x:auto;min-height:0}}
  .ytdchart{{flex:0 0 268px;min-width:0;display:flex;flex-direction:column;
    min-height:0;overflow:hidden;
    background:#fff;border:1px solid var(--bd);border-radius:10px;padding:10px 12px;
    box-shadow:0 1px 3px rgba(16,24,40,.05)}}
  /* 与 .pcard 的标题同规格（字号/缩进/左边框/下间距一致）⇒ 两框内部内容起点也齐平 */
  .ytdh{{font-size:15px;font-weight:600;color:#202020;margin:0 0 8px;
    padding-left:9px;border-left:3px solid #3d57e5;
    display:flex;align-items:baseline;gap:2px;flex-wrap:wrap}}
  /* 年度表现 = SVG 分组竖柱（每年三根：基础轮动 / D3 / D3+P3） */
  .ytdbody{{flex:1 1 0;min-height:0;display:flex;align-items:center;justify-content:center}}
  .ytdbody svg{{display:block;width:100%;height:auto}}
  @media (min-width:761px){{
    /* 并排时让 SVG 吃满卡片剩余高度，卡片底边不被内容顶出去 */
    .ytdbody svg{{height:100%;max-height:100%}}
  }}
  .ytdfoot{{margin-top:4px;font-size:9.5px;color:#98a1ad;line-height:1.5;
    white-space:normal;flex:0 0 auto}}
  .ytdfoot .ytdmdd{{color:#6c717f;font-variant-numeric:tabular-nums}}
  .split .l .rs{{min-width:0}}
  .sphs{{font-size:12px;font-weight:400;color:var(--mut)}}
  h2.sph2{{margin:0 0 8px}}
  h3.sph3{{margin:0 0 10px;font-size:15px;font-weight:600;color:#202020;
    padding-left:10px;border-left:4px solid;border-image:linear-gradient(180deg,#3d57e5,#566de7) 1}}
  table.rtbl{{font-size:12px}}
  /* ① 排名表：粘性带安全垫横条 + 第1名 / 当前持仓行标记（2026-10-25） */
  .saferow{{display:flex;flex-wrap:wrap;align-items:baseline;gap:3px 12px;
    margin:0 0 7px;padding:6px 9px;background:#f4f8ff;border:1px solid #dbe6fb;
    border-radius:8px;font-size:11.5px;line-height:1.6;color:#3b4a63}}
  .saferow.hot{{background:#fff6f3;border-color:#ffd6c8;color:#8a3a1c}}
  .saferow .sr-t{{font-weight:700;color:#3d57e5;white-space:nowrap}}
  .saferow.hot .sr-t{{color:#c2410c}}
  .saferow b{{color:#202020}}
  .saferow i{{font-style:normal;color:#8b93a1;margin-left:3px;font-size:10.5px}}
  .saferow .dimtxt2{{color:#8b93a1}}
  table.rtbl tr.row-top1 td:first-child{{border-left:3px solid #3d57e5}}
  table.rtbl tr.row-held td{{background:#f1faf4}}
  table.rtbl tr.row-held td:first-child{{border-left:3px solid #2e9e5b}}
  table.rtbl th,table.rtbl td{{padding:6px 6px}}
  table.rtbl td.vcell{{white-space:normal;min-width:130px}}
  /* ①「切换至今」列：买入日距今 >120 交易日 → 数字淡色 + 虚线下划线，
     提醒这是很久以前那一段的收益，不是近期表现（悬停看起止日） */
  .stale{{opacity:.62;text-decoration:underline dotted #b6bcc7;
    text-underline-offset:2px;cursor:help}}
  /* 左列操作指引：缩小字号、允许换行，确保不出现横向滑块 */
  table.gtbl{{font-size:12px;width:100%}}
  table.gtbl th,table.gtbl td{{padding:6px 7px;vertical-align:top}}
  table.gtbl td.rs{{white-space:normal;min-width:0}}
  table.gtbl td.nm2{{white-space:normal}}
  .split .l .note{{font-size:12px;line-height:1.7}}
  /* ≤1320px：年度表现柱状图移到排名表下方，把整宽让给 8 列排名表 → 不再出现横向滚动条 */
  /* 旧布局（.split / .rwrap）遗留规则保留，但已不在主流程使用 */
  @media (max-width:1320px){{
    .rwrap{{flex-direction:column}}
  }}
  /* 中等窗口：年度卡收窄到 300px（400px 在 ≤1050 太占宽），把宽度让给 9 列排名表 */
  @media (max-width:1050px){{
    .s2r{{flex:0 0 300px}}
  }}
  /* ===== 并排断点 760px（2026-10-25 修）=====
     ⚠️ 原来断点是 1320px，导致「内嵌预览面板 / 未最大化窗口」等实际渲染宽度
     <1320 的环境一律退化成上下堆叠 —— 用户看到的正是堆叠而非并排。
     现降到 760：≥761 一律并排（左栏 flex:1 自动占剩余宽度；排名表放不下时由
     .rtblwrap2 内部横向滚动兜底，绝不撑破页面）；≤760（手机/极窄）才堆叠。 */
  @media (max-width:760px){{
    .split2{{flex-direction:column}}
    .s2r{{flex:0 0 auto}}
    .s2r .ytdchart{{flex:0 0 auto;overflow:visible}}
    .ytdchart{{flex:0 0 auto;overflow:visible}}
    /* 堆叠后容器变全宽，svg width:100% 会把图等比放大到极高 → 按高度上限缩放并居中 */
    .ytdbody{{flex:0 0 auto;min-height:150px;display:block}}
    .ytdbody svg{{width:auto;max-width:100%;height:auto;max-height:330px;margin:0 auto}}
  }}
  @media (max-width:1100px){{
    .split{{flex-direction:column}}
    .split .l,.split .r{{flex:1 1 auto;align-self:stretch}}
    .rwrap{{flex-direction:column}}
  }}
  /* 窄屏：定宽列会把内容挤爆 → 退回 auto 布局，让内容自己决定宽度 */
  @media (max-width:900px){{
    .gsec table.gtbl{{table-layout:auto}}
    .gsec table.gtbl th:nth-child(1),
    .gsec table.gtbl th:nth-child(2),
    .gsec table.gtbl th:nth-child(3){{width:auto}}
    .ghead{{gap:6px}}
    .gdate{{margin-left:0;width:100%}}
  }}

  /* ===== 手机竖屏（≤640px）排名表瘦身（2026-10-25）=====
     问题：9 列在 375px 宽下每列仅约 41px，字小如蚂蚁；
           且「切换至今」的持仓信息只写在 title 里，触屏无 hover 完全看不到。
     方案：① 代码并入名称列（名称在上、代码灰色小字在下）
           ② 隐藏 现价 / MA20 —— 乖离 = P/MA20−1，已完整包含这两列的信息
           ③ 「切换至今」把持仓状态+天数渲染成单元格内小字
           ④ 条带内 nowrap 改 normal，避免长文本撑出横向滚动
     结果：9 列 → 6 列，375px 下每列约 62px，无需左右拖动。 */
  .code-sub,.since-sub{{display:none}}
  @media (max-width:640px){{
    table.rtbl{{font-size:11.5px}}
    table.rtbl th,table.rtbl td{{padding:5px 4px}}
    table.rtbl .c-code,table.rtbl .c-px,table.rtbl .c-ma20{{display:none}}
    table.rtbl .c-nm .code-sub{{display:block;font-size:10px;color:#98a1ad;
      font-weight:400;line-height:1.2;margin-top:1px}}
    table.rtbl .c-since .since-sub{{display:block;font-size:10px;color:#8b93a1;
      font-weight:400;line-height:1.2;margin-top:1px}}
    .hspan{{font-size:11px;padding:5px 8px;gap:2px 10px}}
    .hdetb .v,.hspan .v{{white-space:normal}}
    .hdet>summary{{font-size:10.5px}}
    /* 今日操作明细表：手机上允许换行，避免长名称撑宽 */
    .gsec table.gtbl{{font-size:11.5px}}
  }}

  /* 净值曲线 SVG */
  .csvg{{display:block;width:100%;height:auto;min-width:560px;background:#fff;
    border:1px solid var(--bd);border-radius:10px;box-shadow:0 1px 3px rgba(16,24,40,.05)}}
  .csvg .cg{{font-size:10.5px;fill:#98a1ad;text-anchor:end;font-family:inherit}}
  .csvg .cx{{font-size:10.5px;fill:#98a1ad;text-anchor:middle;font-family:inherit}}
  .csvg .cz{{stroke:#b9c0ca;stroke-dasharray:5 4}}
  .csvg .ce{{font-size:11px;font-weight:700;font-family:inherit}}
  .chip{{display:inline-flex;align-items:center;gap:5px;margin:0 12px 0 0;
    font-size:12px;color:#374151;white-space:nowrap}}
  .chip i{{display:inline-block;width:14px;height:3px;border-radius:2px}}
  /* 阶梯表压缩：单位省略 %、小字、紧凑内边距 */
  table.lad{{font-size:11.5px}}
  table.lad th,table.lad td{{padding:4px 4px}}
  table.lad td.mono{{font-size:11.5px}}
  .tblwrap{{overflow-x:auto}}
</style></head><body><div class="wrap">
<div class="hero">
<h1>ETF 动横阶梯轮动报告</h1>
<div class="sub"><b>动横{window}</b> · 粘性带 <b>{sticky:.1f}pp</b> · 主选 <b>{len(ranked)}+{len([r for r in snap if r.role == 'defensive'])}</b> 只
 ｜ 生成 {now}</div>
{fresh_inline}
</div>
{fresh_alert}

<section class="gsec">
<div class="ghead">
  <h2 class="gh2">🎯 今日操作<span class="gtip">按你当前持仓对号入座</span></h2>
  <span class="gdate">锚定日 <b>{pivot}</b> ｜ 数字随盘中波动，以<b>收盘价</b>为准</span>
</div>
{_combo_summary_html(recent)}
{('<div class="note" style="margin-bottom:8px">' + guide_note + '</div>') if guide_note else ''}
<table class="gtbl"><thead><tr><th>代码</th><th>标的</th><th>今日动作</th><th>依据</th></tr></thead>
<tbody>{''.join(guide_rows)}</tbody></table>
{_weak_html}
</section>

<div class="lead">以下为主选池结论（第 ① ~ ⑤ 节），备选观察池在报告末尾第 ⑥ 节、与主选池完全独立。
<details class="lgd"><summary>📖 表格字段与图例说明</summary>
<div class="lgdb"><span class="dimtxt">① 「切换至今」= 该标的最近一次被系统买入 → 至今（已换出）的
<span style="color:{RED}">价格</span>涨跌，与 ② 节同源同口径，悬停看起止日与持仓天数；
<span class="stale">淡色虚线下划线</span> = 买入日距今已超 120 个交易日（半年前的老仓位，非近期表现）；
「—」= 回测区间内从未被买入。</span></div>
</details></div>

<div class="split2">
<div class="s2l">
<div class="pcard">
<h2 class="sph2">① 今日排名 · 主选池<span class="sphs">（按 动横{window} 降序）</span></h2>
{_saferow_html}
<div class="rtblwrap2">
<table class="rtbl"><thead><tr><th class="c-idx">#</th><th class="c-code">代码</th>
<th class="c-nm">名称</th><th class="c-m20">动横{window}</th><th class="c-dev">乖离</th>
<th class="c-since" title="该标的最近一次被系统买入 → 至今（或已换出）的价格涨跌，口径与 ② 节一致">切换至今</th>
<th class="c-px">现价</th><th class="c-chg">今日涨跌</th><th class="c-ma20">MA20</th></tr></thead><tbody>
{''.join(rank_rows)}
</tbody></table>
</div>
</div>
</div>
<div class="s2r">
{ytd_block}
</div>
</div>

{rr_block}

{_curve_block(recent.get("curve") if recent else "")}

<h2>③ 动横阶梯全表 · 主选池（第 n 列 = 冻结价格下未来第 {window}-n 个交易日的动横{window}）</h2>
<div class="tblwrap"><table class="lad"><thead><tr><th>代码</th><th>名称</th>
{''.join(f'<th>横{n}</th>' for n in cols)}
<th>现价</th><th>今日涨跌</th></tr></thead><tbody>
{''.join(lad_rows)}
</tbody></table></div>
<div class="sub" style="margin-top:8px">★ = 该列为头名（已含粘性带判定）。数值单位为 <b>%</b>（省略百分号），
保留 1 位小数。底色深浅表示幅度，<span style="color:{RED}">红</span>为涨、
<span style="color:{GREEN}">绿</span>为跌。阶梯值为「价格冻结」推演，非预测。</div>

{_mine_block_html(recent.get("mine") if recent else None)}

<h2>④ 逐列头名与换仓日 · 主选池</h2>
<div class="tblwrap"><table><thead><tr><th>第几日</th><th>列</th><th>头名</th><th>名称</th><th>动横值</th>
<th>次名</th><th>领先</th><th>距换仓</th><th>建议持仓</th><th>状态</th></tr></thead><tbody>
{''.join(step_rows)}
</tbody></table></div>
<div class="sub" style="margin-top:8px">
「距换仓」= 该列还需再拉开多少领先度才够 <b>{sticky:.1f}pp</b> 门槛（<span class="good">已达标</span> = 该列可直接换）；
「建议持仓」= 该列经粘性带过滤后的有效头名（<b>未叠加过热熔断</b>，最终建议见上方"今日操作指引"）。</div>

{def_guide_card}
{_ve2_card}

<h2>⑤ 数据体检 · 主选池</h2>
<div class="note">{info_txt}</div>
{watch_block}

<div class="foot">
口径: 动横(n)=P_t/P_(t-n)−1 ｜ 乖离=P_t/MA20−1 ｜ 阶梯=假设未来价格冻结、窗口向历史滑移 ｜
今日涨跌=P_t/P_(t-1)−1 ｜ 过热熔断: 乖离&gt;{overheat_dev:.0f}% 或 领先&gt;{overheat_lead:.0f}pp<br>
防守位 = 512040 价值ETF富国（原 512880 证券ETF国泰，2026-09-24 替换）｜ 防守期叠加价值引擎:
收盘 &lt; MA200×0.97 买入持有、&gt; MA200×1.06 卖出、中间区间维持上一状态；未触发买入条件时持币。
<br>{adj_note}<br>
本报告仅供研究参考，不构成个人投资建议。
</div>
</div></body></html>"""


# ---------------------------------------------------------------- main
def _fmt_factors(res):
    """把 fetch_online 返回的复权因子格式化为报告用的提示串（全为 1 时返回空串）。"""
    try:
        f = res.get("factors") or {}
        ev = {c: v for c, v in f.items() if abs(v - 1.0) > 1e-6}
        return ", ".join(f"{c} F={v:.4f}" for c, v in sorted(ev.items())) if ev else ""
    except Exception:                                # noqa: BLE001
        return ""


# 手机端「一键刷新」浮窗：点击触发 GitHub Actions 工作流重算日报。
# 关键安全设计：GitHub Token 仅由用户在本机浏览器 localStorage 保存，
# 不进入仓库源码、不发送给任何第三方，只发往 api.github.com。
# 注意：report 主体是一个 f-string（其中 { } 会被当格式字段），本常量必须是
# 普通字符串（非 f-string），并在写文件时用 .replace 注入到 </body> 前，
# 以避免 JS 里的 { }（如 JSON.stringify({ref:...})）被 f-string 误解析。
# PWA：注册 Service Worker（注入到 </body> 前）。
# ⚠️ 用普通字符串、不用 f-string —— JS 的花括号在 f-string 里要写成双括号，极易出错。
# sw.js 是 network-first：保证「添加到主屏幕」后每天看到的是最新持仓，不是首次缓存。
PWA_TAIL = """
<script>
if ('serviceWorker' in navigator) {
  window.addEventListener('load', function () {
    navigator.serviceWorker.register('sw.js').catch(function () {});
  });
}
</script>
"""

REFRESH_WIDGET = """
<style>
#rfFab{position:fixed;right:14px;bottom:14px;z-index:9999;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}
#rfBtn{background:#3d57e5;color:#fff;border:none;border-radius:24px;padding:12px 18px;font-size:15px;font-weight:600;box-shadow:0 4px 14px rgba(61,87,229,.45);cursor:pointer}
#rfPanel{display:none;position:fixed;right:14px;bottom:66px;z-index:9999;width:min(88vw,320px);background:#fff;border:1px solid #e2e5ea;border-radius:12px;padding:14px;box-shadow:0 8px 28px rgba(0,0,0,.18);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;font-size:13px;color:#202020;box-sizing:border-box}
#rfPanel h4{margin:0 0 8px;font-size:14px;color:#3d57e5}
#rfPanel p{margin:0 0 8px;line-height:1.5;color:#42474f}
#rfToken{width:100%;box-sizing:border-box;padding:8px;border:1px solid #e2e5ea;border-radius:8px;font-size:13px;margin-bottom:8px}
#rfPanel button{background:#3d57e5;color:#fff;border:none;border-radius:8px;padding:8px 12px;font-size:13px;font-weight:600;cursor:pointer;margin:0 6px 0 0}
#rfPanel a{color:#3d57e5}
#rfStatus{margin-top:8px;line-height:1.5;color:#42474f}
#rfGen{margin:0 0 8px;color:#6c717f;font-size:12px}
</style>
<div id="rfFab"><button id="rfBtn" onclick="rfToggle()">🔄 刷新日报</button></div>
<div id="rfPanel">
  <h4>一键刷新（触发云端重算）</h4>
  <div id="rfGen">本页生成于：__RF_GEN__</div>
  <p>首次使用粘贴一次 <b>GitHub Token</b>（仅需 repo 权限）。<b>仅存你本机浏览器</b>，不进仓库、不发给任何第三方，只发往 api.github.com。</p>
  <input id="rfToken" type="password" placeholder="ghp_... 或 github_pat_..." />
  <button onclick="rfSaveRun()">保存并刷新</button>
  <button onclick="rfRun()">直接刷新</button>
  <div id="rfStatus"></div>
</div>
<script>
(function(){
  var REPO="w972241768-coder/etf-daily", WF="daily.yml", REF="main";
  function el(id){return document.getElementById(id);}
  window.rfToggle=function(){var p=el("rfPanel");p.style.display=p.style.display==="none"?"block":"none";};
  var saved=localStorage.getItem("rfGhToken");
  if(saved){el("rfToken").value=saved;el("rfToken").placeholder="已保存（可留空直接刷新）";}
  function run(){
    var tok=el("rfToken").value.trim()||localStorage.getItem("rfGhToken")||"";
    var st=el("rfStatus");
    if(!tok){st.textContent="请先粘贴 GitHub Token。";return;}
    st.textContent="正在触发刷新，GitHub 云端约 2~3 分钟后重算完成，届时刷新本页即可。";
    fetch("https://api.github.com/repos/"+REPO+"/actions/workflows/"+WF+"/dispatches",{
      method:"POST",
      headers:{"Authorization":"token "+tok,"Accept":"application/vnd.github+json","Content-Type":"application/json"},
      body:JSON.stringify({ref:REF})
    }).then(function(r){
      if(r.status===204){st.innerHTML="✅ 已触发！<b>约 2~3 分钟后</b>本页会自动刷新（或手动刷新）。";setTimeout(function(){location.reload();},180000);}
      else if(r.status===401||r.status===403){st.textContent="❌ Token 无效或无权限（需 repo 权限）。";}
      else{st.textContent="⚠️ 返回状态 "+r.status+"，可能已触发或受限，请稍后看本页是否更新。";}
    }).catch(function(e){
      var url="https://github.com/"+REPO+"/actions/workflows/"+WF;
      st.innerHTML="❌ 浏览器跨域(CORS)受限，已自动为你打开 <a href='"+url+"' target='_blank'>Actions 手动触发页</a>，点右上 Run workflow 即可。";
      try{window.open(url,"_blank");}catch(_){}
    });
  }
  window.rfRun=run;
  window.rfSaveRun=function(){localStorage.setItem("rfGhToken",el("rfToken").value.trim());run();};
})();
</script>
"""


def main():
    ap = argparse.ArgumentParser(description="ETF 动横阶梯轮动 · 一键报告（在线前复权，可回退本地）")
    ap.add_argument("--pool", default=None, help="标的池 CSV (code,name,role)")
    ap.add_argument("--tdx-root", default=None, help="通达信安装根目录 (含 vipdoc)")
    ap.add_argument("--csv", default=None, help="改用长表 CSV (绕过本地通达信)")
    ap.add_argument("--date", default=None, help="锚定日 YYYY-MM-DD, 默认取共有最新交易日")
    ap.add_argument("--window", type=int, default=20)
    ap.add_argument("--defensive", default=None, help="防守标的(逗号分隔); 默认取池内 role=defensive")
    ap.add_argument("--overheat-dev", type=float, default=12.0)
    ap.add_argument("--overheat-lead", type=float, default=15.0)
    ap.add_argument("--sticky-lead", type=float, default=1.0)
    ap.add_argument("--recent-n", type=int, default=3,
                    help="「近期换仓复盘」显示最近 N 次建议换仓 (默认 3)")
    ap.add_argument("--recent-cost", type=float, default=0.002,
                    help="复盘每次换仓按双边计的成本 (默认 0.002 = 0.2%%)")
    ap.add_argument("--no-recent", action="store_true",
                    help="跳过「近期换仓复盘」板块（长历史 CSV 缺失时可用）")
    ap.add_argument("--out", default=None, help="HTML 输出路径")
    ap.add_argument("--dump-csv", default=None,
                    help="把本次读取的长表(date,code,close,name,role)另存为 CSV")
    ap.add_argument("--no-open", action="store_true", help="不自动打开 HTML (后台必加)")
    ap.add_argument("--offline", action="store_true",
                    help="强制离线: 只读本地通达信不复权数据（默认为在线前复权，"
                         "断网/限流时自动回退本地，不会中断）")
    ap.add_argument("--live", action="store_true",
                    help="（已废弃，保留兼容）默认即在线模式，自动支持盘中实时；"
                         "失败自动回退本地离线数据，不会中断")
    ap.add_argument("--feishu", action="store_true", help="额外投递飞书单聊")
    ap.add_argument("--state-out", default=None,
                    help="同时输出状态快照 JSON（供「仅换仓/触发日提醒」比对用）")
    a = ap.parse_args()

    pool_path = a.pool or (DEFAULT_POOL if os.path.exists(DEFAULT_POOL) else None)
    info = []
    info_by_code = {}
    live_on, live_txt, live_err = False, "", ""
    adj_note = ("数据为通达信本地 vipdoc 不复权价；ETF 分红日会有约 0.6~0.8pp 的假跳空，"
                "拆分日（如 588170 于 2026-07-06 每 1 拆 3）更会产生高达 90+pp 的虚假动横跳变 —— "
                "⚠ 请恢复联网后用在线前复权数据重跑。")
    if a.csv:
        df = core.load_panel(a.csv)
        source = f"CSV {os.path.basename(a.csv)}"
        if not a.defensive:
            raise SystemExit("--csv 模式请显式指定 --defensive")
        defensive = set(x for x in a.defensive.split(",") if x)
        info.append(f"[SRC] 长表 CSV: {a.csv}")
        adj_note = "数据为长表 CSV，复权口径以该文件本身为准。"
        if a.live:
            info.append("[LIVE] --csv 模式不支持在线取数（请用默认池模式），已忽略")
    else:
        if not pool_path or not os.path.exists(pool_path):
            raise SystemExit(f"找不到标的池文件: {pool_path}")
        pool = load_pool(pool_path)
        if a.defensive:
            defensive = set(x for x in a.defensive.split(",") if x)
        else:
            defensive = {c for c, _, r in pool if r == "defensive"}
        _def_codes = {c for c, _n, r in pool if r == "defensive"}
        n_watch = sum(1 for _c, _n, r in pool if r == "watch")

        df = None
        source = None
        # ---------- 默认：在线（新浪不复权 × 前复权因子校正，免费无密钥，支持盘中实时） ----------
        if a.live:
            info.append("[LIVE] --live 已废弃（默认即在线模式，自动支持盘中实时），本次按在线模式运行")
        if not a.offline:
            try:
                import online_data as od
                res = od.fetch_online([c for c, _n, _r in pool],
                                      cache_tag=(a.date or None))
                ok_n = int((res["ok"] or "0/0").split("/")[0])
                codes_ok = set()
                if res["df"] is not None:
                    codes_ok = set(res["df"]["code"].unique())
                def_ok = (not _def_codes) or _def_codes <= codes_ok
                if res["df"] is not None and ok_n >= max(2, int(0.9 * len(pool))) and def_ok:
                    meta = pd.DataFrame(
                        [(c, n, r) for c, n, r in pool],
                        columns=["code", "name", "role"])
                    df = res["df"].merge(meta, on="code", how="left")
                    df = df[["date", "code", "close", "name", "role"]]
                    source = res["src"]
                    live_on = True
                    if res["live"]:
                        live_txt = (
                            f"⚡ <b>盘中实时数据</b>（未收盘）　数据源 <b>{source}</b>"
                            f"　｜　最后一根 = <b>{res['lastdate']}</b> 盘中快照"
                            f"　｜　成功 {res['ok']} 只"
                            f"。⚠ <b>价格为盘中快照、非收盘价</b>，动横与信号会随盘中波动；"
                            f"收盘后请再跑一次以收盘价复算。")
                    else:
                        live_txt = (
                            f"✓ <b>在线前复权数据</b>　数据源 <b>{source}</b>"
                            f"　｜　数据截至 <b>{res['lastdate']}</b>（已收盘/非交易时段）"
                            f"　｜　成功 {res['ok']} 只。分红与拆分已按前复权还原，无除权假跳空。"
                            + (f"　｜　近期事件校正: {_adj_ev}" if (_adj_ev := _fmt_factors(res)) else ""))
                    info.extend(res["info"])
                    _sub = res["df"].groupby("code")
                    for c, n, r in pool:
                        if c not in codes_ok:
                            info_by_code[c] = f"[FAIL] {c} {n}: 在线取数失败"
                            continue
                        g = _sub.get_group(c).sort_values("date")
                        info_by_code[c] = (f"[OK] {c} {n}: {len(g)} 根, "
                                           f"最新 {g['date'].max():%Y-%m-%d} "
                                           f"收 {g['close'].iloc[-1]:.3f} (前复权)")
                    info.insert(0, f"[SRC] {source} ｜ 池: {os.path.basename(pool_path)} "
                                   f"({len(pool)} 只 = 主选池 {len(pool) - n_watch} 只 + "
                                   f"备选观察池 {n_watch} 只)")
                    adj_note = ("数据为新浪财经在线日线：原始为不复权价，已用已核验长历史（tdx_adj_long）"
                                "的复权因子 F = 不复权(T0)/前复权(T0) 整体校正到前复权口径；"
                                "ETF 分红与拆分均已还原，不存在除权假跳空。"
                                "只要 T0（长历史最新日）之后没有新的分红/拆分，结果与严格前复权一致；"
                                "有新事件时脚本会明确提示。")
                else:
                    why = (f"在线取数不完整（{res['ok']}），"
                           + (f"失败: {','.join(res['fails'])}" if res["fails"] else "数据为空")
                           + ("；且防守位未取到" if not def_ok else ""))
                    info.append(f"[ONLINE] {why} → 回退本地通达信（不复权）")
                    live_err = f"在线前复权取数不完整（{res['ok']}）→ 已回退本地通达信不复权数据"
            except Exception as e:                     # noqa: BLE001
                info.append(f"[ONLINE] 在线取数异常（{type(e).__name__}: {e}）"
                            f" → 回退本地通达信（不复权）")
                live_err = f"在线取数异常（{type(e).__name__}: {e}）→ 已回退本地通达信不复权数据"

        # ---------- 回退：本地通达信（不复权） ----------
        if df is None:
            tdx_root = find_tdx_root(a.tdx_root)
            if not tdx_root:
                # 云端 / 无通达信环境兜底：直接用仓库已提交的长表 CSV（前复权口径），
                # 避免 workflow 因找不到 TDX 根目录而崩溃退出。
                # 数据为最近一次本地推送的快照，可能不是最新交易日（页面会明确标注）。
                csv_fb = os.path.join(HERE, "tdx_adj_long_2019_2026.csv")
                if os.path.exists(csv_fb):
                    df = core.load_panel(csv_fb)
                    source = (f"仓库历史CSV {os.path.basename(csv_fb)}"
                              "（在线/通达信均不可用时的兜底，口径为前复权）")
                    info.insert(0, f"[SRC] ⚠ {source}（在线前复权失败后的兜底，"
                                   f"数据可能滞后于最新交易日）"
                                   f" ｜ 池: {os.path.basename(pool_path)} "
                                   f"({len(pool)} 只 = 主选池 {len(pool) - n_watch} 只 + "
                                   f"备选观察池 {n_watch} 只)")
                    live_err = ("在线与本地通达信均不可用，已回退到仓库历史数据"
                                "(可能滞后于最新交易日)")
                else:
                    raise SystemExit("在线取数失败且找不到通达信根目录(含 vipdoc)。"
                                     "请用 --tdx-root 指定，或检查网络后重试。")
            else:
                # 防守位需要 MA200 价值引擎, 必须多取历史; 其余标的 window+8 根够算动横即可。
                want_def = max(a.window + 8, 260)      # 260 = 200(MA) + 60 余量
                df, _info2, info_by_code = build_panel_from_tdx(
                    pool, tdx_root, want=a.window + 8,
                    want_by_code={c: want_def for c in _def_codes})
                info.extend(_info2)
                source = f"本地通达信 {tdx_root}/vipdoc (不复权)"
                info.insert(0, f"[SRC] ⚠ {source}（在线前复权失败后的回退，口径为不复权）"
                               f" ｜ 池: {os.path.basename(pool_path)} "
                               f"({len(pool)} 只 = 主选池 {len(pool) - n_watch} 只 + "
                               f"备选观察池 {n_watch} 只)")

    if df.empty:
        raise SystemExit("没有任何可用数据, 无法生成报告。")

    if a.dump_csv:
        dump = df.sort_values(["code", "date"]).copy()
        dump["date"] = dump["date"].dt.strftime("%Y-%m-%d")
        dump = dump[dump["date"] <= (a.date or df["date"].max().strftime("%Y-%m-%d"))]
        dump.to_csv(a.dump_csv, index=False, encoding="utf-8-sig")
        print(f"长表已导出: {a.dump_csv} ({len(dump)} 行)")

    # 锚定日: 默认 = 所有标的都能取到的「共有最新交易日」
    if a.date:
        pivot = a.date
    else:
        last = df.groupby("code")["date"].max()
        pivot = last.min().strftime("%Y-%m-%d")
        if last.max() != last.min():
            info.append(f"[ANCHOR] 各标的最新日不一致(最新 {last.max():%Y-%m-%d}), "
                        f"取共有最新日 {pivot} 以保证同窗口可比")

    W = a.window
    is_stale, fresh_txt = data_freshness(pivot)
    snap = core.build_snapshot(df, pivot, W, defensive)
    # ★ 主选池 / 备选观察池 分组（唯一入口 core.split_pools）
    ranked, defs, watched = core.split_pools(snap)
    if not ranked:
        raise SystemExit("主选池排名为空, 请检查标的池与 --defensive 设置。")
    snap_main = ranked + defs
    head1, head2 = ranked[0], (ranked[1] if len(ranked) > 1 else None)
    lead = (head1.momentum[W] - head2.momentum[W]) if head2 else float("nan")
    overheated = (head1.dev > a.overheat_dev) or (lead > a.overheat_lead)
    cols, steps, swap_days, tie_days = core.analyze_ladder(ranked, W, a.sticky_lead)

    recent = None
    # 「② 近期换仓复盘」的「至今」必须真到今天：长历史 CSV 的 T0（2026-09-24）通常比
    # 锚定日晚一个月，只喂长历史会把「本次换仓截止目前」算成一个月前。
    # 故把本次运行的前复权数据（df）作为 live_df 传下去，由 recent_rotations 负责拼接。
    # ⚠️ 上证指数必须单独按 sh000001 取：`fetch_online` 的 _market() 会把 "000001"
    #    判成 sz000001（平安银行），再除以前复权因子后点位看着像大盘、实则完全错。
    _bench_live = None
    try:
        import online_data as od
        _bdf = od.fetch_sina_kline_sh000001(datalen=90)
        if _bdf is not None and len(_bdf):
            _bench_live = _bdf
    except Exception as e:                     # noqa: BLE001
        info.append(f"[BENCH] 上证指数在线补数失败（{type(e).__name__}: {e}），"
                    f"② 的「同期上证」将只算到长历史末日")
    if not a.no_recent:
        try:
            import recent_rotations as rr_mod
            _live = df if (df is not None and not df.empty) else None
            if _live is not None and _bench_live is not None:
                _live = pd.concat([_live[["date", "code", "close"]], _bench_live],
                                  ignore_index=True)
            # 「我的实盘跟踪」：读 my_since.json（没有该文件 / since 为空则不显示）
            _m_since, _m_cap = None, None
            try:
                _mp = os.path.join(HERE, "my_since.json")
                if os.path.exists(_mp):
                    with open(_mp, encoding="utf-8") as _mf:
                        _mc = json.load(_mf)
                    _m_since = str(_mc.get("since") or "").strip() or None
                    _m_cap = _mc.get("capital") or None
            except Exception as _e:                 # noqa: BLE001
                info.append(f"[MINE] my_since.json 读取失败: {type(_e).__name__}: {_e}")
            recent = rr_mod.recent_rotation_pnl(
                pool=[r.code for r in ranked], defensive=next(iter(defensive), None),
                n=a.recent_n, pivot=pivot, cost=a.recent_cost,
                W=W, MA=W, sticky=a.sticky_lead,
                enter_dev=a.overheat_dev, enter_lead=a.overheat_lead,
                value_engine=True, live_df=_live,
                mine_since=_m_since, mine_capital=_m_cap)
        except Exception as e:                 # noqa: BLE001
            recent = dict(error=f"复算异常（{type(e).__name__}: {e}）", n=a.recent_n)
            info.append(f"[RECENT] 近期换仓复盘失败: {e}")

    # ---- 年度表现：策略近 6 年每年收益，取自「② 近期换仓复盘」同一次长历史回测 ----
    yearly = None
    if recent and not recent.get("error"):
        yearly = recent.get("yearly")

    # 数据体检拆成 主选池 / 备选池 两段
    wcodes = [r.code for r in watched]
    info_watch = [info_by_code[c] for c in wcodes if c in info_by_code]
    _wset = set(info_watch)
    info_main = [x for x in info if x not in _wset]

    # ---- 指数口径对照（防守位 512040 跟踪 中证国信价值 931052）----
    idx = None
    try:
        idx = build_index_cmp(df, defs[0].code if defs else None)
    except Exception as e:                      # noqa: BLE001
        info_main.append(f"[IDX] 指数口径对照计算异常: {type(e).__name__}: {e}")
    if idx is None:
        info_main.append("[IDX] 指数口径数据不可用（中证指数官网/东财均取数失败，"
                         "且无 out/csindex_931052.csv 本地缓存）→ 防守位仅显示基金口径")
    else:
        print(f"[指数口径] {idx['code']} {idx['name']} {idx['i_date']} 收 "
              f"{idx['i_close']:.2f} vs MA200 {idx['i_ma']:.2f} → 偏离 {idx['i_dev']:+.2f}%")
        print(f"           基金 {defs[0].code} {idx['f_date']} 收 {idx['f_close']:.3f} vs "
              f"MA200 {idx['f_ma']:.4f} → 偏离 {idx['f_dev']:+.2f}% ｜ "
              f"口径差(基金−指数) {idx['gap']:+.2f}pp，"
              f"近 {idx['n_days']} 日均值 {idx['gap_mean']:+.2f}pp")

    # ---- 防守位策略净值曲线（价值引擎近 5 年回测，前复权 + 零手续费）----
    ve_curve = None
    try:
        ve_curve = build_ve_curve(df, defs[0].code if defs else None, years=5)
    except Exception as e:                      # noqa: BLE001
        info_main.append(f"[VE] 防守位净值曲线计算异常: {type(e).__name__}: {e}")
    if ve_curve:
        print(f"[防守位回测] {ve_curve['code']} {ve_curve['d0']:%Y-%m-%d}~"
              f"{ve_curve['d1']:%Y-%m-%d}（{ve_curve['n']} 日）净值 "
              f"{ve_curve['final']:.4f}（{ve_curve['ret']:+.2f}%）｜"
              f"最大回撤 {ve_curve['mdd']:.2f}% ｜ 持仓 {ve_curve['hold_rate']:.1f}% ｜"
              f"买 {ve_curve['n_buy']} 卖 {ve_curve['n_sell']} ｜"
              f"同期买入持有 {ve_curve['bh_final']:.4f}（{ve_curve['bh_ret']:+.2f}%）")
    else:
        info_main.append("[VE] 防守位净值曲线不可用（历史数据不足）")

    # ---- 512890 红利低波「回踩 MA200 买 / 超 +6% 卖」每日跟踪（策略二）----
    # 用户 2026-10-21 指定：与 512040 同规格，放在防守位下面，用于提醒当日操作。
    # 买线不同：512890 用「回踩 MA200 即买（×1.00）」，卖出同样 +6%。
    # 该标的在 etf_pool.csv 里是 watch 角色（不参与轮动排名），这是刻意的：
    # 它是独立策略而非轮动候选，所以不能改成 defensive 或去掉 role。
    ve_track = None
    try:
        ve_track = build_ve_track(
            df, VE_TRACK_CODE,
            buy=VE_TRACK_BUY, sell=VE_TRACK_SELL, name=VE_TRACK_NAME)
    except Exception as e:                      # noqa: BLE001
        info_main.append(f"[VE2] {VE_TRACK_CODE} 每日跟踪计算异常: "
                         f"{type(e).__name__}: {e}")
    if ve_track:
        _tt = ve_track
        print(f"[策略二跟踪] {_tt['code']} {_tt['date']:%Y-%m-%d} 收 {_tt['close']:.3f} vs "
              f"MA200 {_tt['ma200']:.4f} → 偏离 {_tt['dev']:+.2f}% ｜ "
              f"买线 {_tt['buy_line']:.4f} / 卖线 {_tt['sell_line']:.4f} ｜ "
              f"状态 {'持有中' if _tt['holding'] else '持币中'} ｜ 动作 {_tt['action']}")
    else:
        info_main.append(f"[VE2] {VE_TRACK_CODE} 每日跟踪不可用（历史数据不足或标的缺失）")

    # ---- 策略二 512890 的近 5 年回测曲线（与 512040 同规格，便于并排比较）----
    ve2_curve = None
    try:
        ve2_curve = build_ve_curve(
            df, VE_TRACK_CODE, buy=VE_TRACK_BUY, sell=VE_TRACK_SELL, years=5)
    except Exception as e:                      # noqa: BLE001
        info_main.append(f"[VE2] {VE_TRACK_CODE} 净值曲线计算异常: "
                         f"{type(e).__name__}: {e}")
    if ve2_curve:
        print(f"[策略二回测] {ve2_curve['code']} {ve2_curve['d0']:%Y-%m-%d}~"
              f"{ve2_curve['d1']:%Y-%m-%d}（{ve2_curve['n']} 日）净值 "
              f"{ve2_curve['final']:.4f}（{ve2_curve['ret']:+.2f}%）｜"
              f"最大回撤 {ve2_curve['mdd']:.2f}% ｜ 持仓 {ve2_curve['hold_rate']:.1f}% ｜"
              f"买 {ve2_curve['n_buy']} 卖 {ve2_curve['n_sell']} ｜"
              f"同期买入持有 {ve2_curve['bh_final']:.4f}（{ve2_curve['bh_ret']:+.2f}%）")
    else:
        info_main.append(f"[VE2] {VE_TRACK_CODE} 净值曲线不可用（历史数据不足）")

    gen_now = datetime.now().strftime("%Y-%m-%d %H:%M")
    html_doc = render_html(
        pivot=pivot, window=W, sticky=a.sticky_lead, defensive=defensive, snap=snap_main,
        ranked=ranked, cols=cols, steps=steps, swap_days=swap_days, tie_days=tie_days,
        head1=head1, head2=head2, lead=lead, overheated=overheated,
        overheat_dev=a.overheat_dev, overheat_lead=a.overheat_lead,
        info=info_main, source=source, now=gen_now,
        watch=watched, info_watch=info_watch,
        freshness=(is_stale, fresh_txt), live=(live_on, live_txt),
        recent=recent, adj_note=adj_note, yearly=yearly, idx=idx, ve_curve=ve_curve,
        ve_track=ve_track, ve2_curve=ve2_curve)

    out_dir = a.out and os.path.dirname(os.path.abspath(a.out)) or DEFAULT_OUT_DIR
    os.makedirs(out_dir, exist_ok=True)
    out_path = a.out or os.path.join(out_dir, f"etf_rotation_{pivot}.html")
    html_doc = html_doc.replace(
        "</body>",
        REFRESH_WIDGET.replace("__RF_GEN__", gen_now) + PWA_TAIL + "\n</body>", 1)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(html_doc)

    print("=" * 70)
    print(f"锚定日 {pivot} ｜ 动横{W} ｜ 粘性带<{a.sticky_lead:.1f}pp ｜ "
          f"主选池 {len(ranked)} 只轮动 + {len(defs)} 只防守 ｜ "
          f"备选观察池 {len(watched)} 只(不参与调仓)")
    import re as _re
    if live_on:
        print("[实时] " + _re.sub(r"<[^>]+>", "", live_txt))
    elif live_err:
        print("⚠ " + live_err)
    elif is_stale:
        print("⚠ 数据可能不是最新: " + _re.sub(r"<[^>]+>", "", fresh_txt))
    else:
        print(f"[数据] {source} ｜ 最新交易日 {pivot}")
    print(f"今日目标: {head1.code} {head1.name}  现价 {head1.close:.3f} "
          f"(今日 {head1.chg1:+.2f}%)  动横{W} {head1.momentum[W]:+.2f}%")
    print(f"次名 {head2.code if head2 else '-'}  领先 {lead:+.2f}pp ｜ "
          f"乖离 {head1.dev:+.2f}% ｜ {'★过热熔断' if overheated else '未过热'}")
    print(f"换仓: {len(swap_days)} 次 (粘性带抹平 {tie_days} 列)")
    if watched:
        _wg = watched[0].momentum[W] - head1.momentum[W]
        print(f"备选池头名(仅参考): {watched[0].code} {watched[0].name} "
              f"{watched[0].momentum[W]:+.2f}%  ->  比主选池头名"
              f"{'高' if _wg > 0 else '低'} {abs(_wg):.2f}pp"
              + ("  ★ 出现更替候选" if _wg > 0 else ""))
    print(f"报告: {out_path}")
    print("=" * 70)

    # ---- 状态快照（2026-10-25）：供「只在换仓/触发日提醒」比对用 ----
    # 由 notify.py 读取，与仓库里上一版 docs/state.json 逐字段比对；
    # 无变化则静默，有变化才推送。字段名一旦改动，要与 notify.py 的事件规则同步。
    if a.state_out:
        _p3 = ((recent or {}).get("p3") or {})
        _ve2 = ve_track or {}
        _snap_state = dict(
            asof=str(pivot),
            gen=gen_now,
            # 执行口径：默认 = D3+P3；算不出时退化为基础策略头名
            hold_default=(_p3.get("hold") if _p3.get("ok") else None) or head1.code,
            hold_default_name=(_p3.get("hold_name") if _p3.get("ok") else None)
            or head1.name,
            hold_base=head1.code,
            hold_base_name=head1.name,
            overheat=bool(overheated),
            p3_active=bool(_p3.get("active")),
            p3_remain=_p3.get("remain"),
            p3_back=_p3.get("back"),
            # 抄底模块：weak_active=今日持仓由抄底产生；weak_n=窗口内触发总次数
            weak_active=bool(_p3.get("weak_active")),
            weak_n=_p3.get("weak_n"),
            nav_default=_p3.get("final"),
            calmar_default=_p3.get("calmar"),
            # 防守位（512040）MA200 偏离与买线（%，买线 = (VE_BUY−1)×100 = −3.0）
            def_code=(defs[0].code if defs else None),
            def_dev=(float(idx["f_dev"]) if idx else None),
            def_buy_pct=-3.0,
            # 策略二（512890）当日位置与动作
            ve2_code=VE_TRACK_CODE,
            ve2_dev=_ve2.get("dev"),
            ve2_zone=_ve2.get("zone"),
            ve2_action=_ve2.get("action"),
            ve2_holding=_ve2.get("holding"),
        )
        try:
            _sd = os.path.dirname(os.path.abspath(a.state_out))
            if _sd:
                os.makedirs(_sd, exist_ok=True)
            with open(a.state_out, "w", encoding="utf-8", newline="\n") as _f:
                json.dump(_snap_state, _f, ensure_ascii=False, indent=1,
                          sort_keys=True, default=str)
            print(f"状态快照: {a.state_out}")
        except Exception as _e:                 # noqa: BLE001
            print(f"⚠ 状态快照写入失败（不影响日报）: {type(_e).__name__}: {_e}")

    if a.feishu:
        md = (f"**ETF 动横阶梯轮动**（锚定 {pivot}，{source}）\n\n"
              f"- 今日目标：**{head1.code} {head1.name}** 现价 {head1.close:.3f}"
              f"（今日 {head1.chg1:+.2f}%）动横{W} {head1.momentum[W]:+.2f}%\n"
              f"- 次名 {head2.code if head2 else '-'} {head2.name if head2 else ''}"
              f" {head2.momentum[W]:+.2f}%，领先 {lead:+.2f}pp\n"
              f"- 头名乖离 {head1.dev:+.2f}%（过热线 {a.overheat_dev:.0f}%）\n"
              f"- 换仓 {len(swap_days)} 次，粘性带抹平 {tie_days} 列\n")
        try:
            r = subprocess.run(["lark-cli", "im", "+messages-send", "--user-id", FEISHU_USER,
                                "--as", "user", "--markdown", md],
                               capture_output=True, text=True, timeout=60)
            print("飞书投递:", "OK" if r.returncode == 0 else f"FAIL {r.stdout[:200]}{r.stderr[:200]}")
        except Exception as e:
            print("飞书投递失败(忽略):", e)

    if not a.no_open:
        _open_report(out_path)


def _open_report(path: str):
    """自动打开 HTML 报告。

    ⚠ 不用 os.startfile(): 若系统 .html 默认关联是 IE（常见旧配置），IE 已被禁用会一闪而过，
    用户看不到报告。改为显式用已在运行的浏览器（Chrome 优先，--new-window 强制新窗口到前台），
    都没有时才回退 startfile。
    """
    import subprocess as _sp
    cands = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    try:
        for exe in cands:
            if os.path.exists(exe):
                _sp.Popen([exe, "--new-window", os.path.abspath(path)], close_fds=True)
                print(f"[打开] {exe} --new-window")
                return
        os.startfile(path)                       # noqa: S606  (回退)
        print("[打开] os.startfile(回退)")
    except Exception as e:
        print("自动打开失败:", e)
        print("报告路径:", os.path.abspath(path))


if __name__ == "__main__":
    main()
