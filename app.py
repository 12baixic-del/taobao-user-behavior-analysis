"""淘宝用户行为分析 v2：Streamlit 可交互数据看板（T1-4）。

运行（仓库根目录）：
    python -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt
    streamlit run app.py

所有业务数字均在运行时从 outputs/*.csv 读取，代码中不硬编码任何业务数字。
样本口径：10,053 名用户的整群抽样（总体 987,994 用户的 1.02%），
时间窗口为北京时间 2017-11-25 至 2017-12-03（双 12 预热期 9 天）。
"""
from pathlib import Path

import pandas as pd
import streamlit as st

BASE = Path(__file__).resolve().parent
OUTPUTS = BASE / "outputs"

st.set_page_config(
    page_title="淘宝用户行为分析 | 双 12 预热期", layout="wide"
)

st.title("淘宝用户行为分析 · 可交互看板")

st.markdown(
    """
**数据来源**：阿里云天池 UserBehavior 数据集（649），与 Kaggle 公开镜像
同源验证通过（100,150,807 行，987,994 用户）。
**样本**：按 `user_id` 哈希做用户级整群抽样，保留 10,053 名用户的
1,009,774 条完整行为记录（总体 987,994 用户的 1.02%）。
**窗口**：北京时间 2017-11-25 至 2017-12-03（双 12 预热期 9 天）。

**局限声明**：原始数据无金额字段，只做最近活跃（R）与行为频次（F）分析，
不涉及金额维度与 GMV；R+F 分层无 M；9 天窗口仅反映双 12 预热期；
R 维度在 9 天窗口内已退化（99.86% 的用户 R=5），分层实际是单维 F；
比例类指标可推广，规模类数字不得直接等同总体规模。
"""
)


@st.cache_data
def load_summaries():
    return {
        "funnel": pd.read_csv(OUTPUTS / "funnel_summary.csv"),
        "sequential": pd.read_csv(OUTPUTS / "funnel_sequential_summary.csv"),
        "retention": pd.read_csv(OUTPUTS / "retention_summary.csv"),
        "rfm": pd.read_csv(OUTPUTS / "rfm_summary.csv"),
        "daily": pd.read_csv(OUTPUTS / "daily_summary.csv"),
        # 小表由 scripts/gen_dashboard_csvs.py 从样本生成后提交入库，
        # 看板只读这些小表，不读 71MB 原始样本（见第 5 节）。
        "top_categories": pd.read_csv(OUTPUTS / "top_categories.csv"),
        "nonpath_split": pd.read_csv(OUTPUTS / "funnel_nonpath_split.csv"),
        "nonpath_totals": pd.read_csv(OUTPUTS / "funnel_nonpath_totals.csv"),
        "cross_item": pd.read_csv(OUTPUTS / "funnel_cross_item.csv"),
    }


data = load_summaries()
funnel = data["funnel"]
sequential = data["sequential"]
retention = data["retention"]
rfm = data["rfm"]
daily = data["daily"]
top_categories = data["top_categories"]
nonpath_split = data["nonpath_split"]
nonpath_totals = data["nonpath_totals"]
cross_item = data["cross_item"]

# ---- 顶部关键指标（全部从 CSV 运行时计算，无硬编码业务数字） -----------
buy_cov = float(
    funnel.loc[funnel["stage"] == "buy", "user_coverage_vs_pv"].iloc[0]
)
full_path = float(
    sequential.loc[
        sequential["step"] == "intent_to_buy", "overall_conv_from_pv"
    ].iloc[0]
)
ret_mean = float(retention["retention_rate"].mean())
nonpath_total = int(nonpath_split["users"].sum())
# P0（第三轮）：分子是非路径购买用户（全部购买用户口径，1,510 人），
# 分母必须用全部购买用户 6,846，不得用仅有 PV 的购买用户 6,811。
# 旧代码用 1,510 / 6,811 = 22.2%，与文档/简历的 22.1% 不一致。
total_buy_users = int(nonpath_totals["total_buy_users"].iloc[0])
nonpath_share = nonpath_total / total_buy_users
no_intent_n = int(
    nonpath_split.loc[
        nonpath_split["group_name"] == "no_intent_buyers", "users"
    ].iloc[0]
)
wrong_order_n = int(
    nonpath_split.loc[
        nonpath_split["group_name"] == "wrong_order_buyers", "users"
    ].iloc[0]
)
ret_early = float(
    retention[retention["date"] < "2017-12-01"]["retention_rate"].mean()
)
ret_late = float(
    retention[retention["date"] >= "2017-12-01"]["retention_rate"].mean()
)
st.metric(
    "覆盖口径购买转化率（9 天累计）", f"{buy_cov:.1%}",
    help="浏览用户中 9 天内购买的占比，非单次访问路径。",
)
st.metric("用户级时序路径完成率（时序口径）", f"{full_path:.1%}",
          help="完成 pv → 意向 → 购买完整路径的用户占浏览用户的比例（用户级跨商品行为顺序，不等于真实商品交易路径）；"
               f"另有 {nonpath_share:.1%} 的购买用户（{nonpath_total:,}/{total_buy_users:,} 人，"
               f"占全部购买用户）未满足该路径，其中无意向记录 {no_intent_n:,} 人、"
               f"顺序不满足 {wrong_order_n:,} 人。")
st.metric("次日留存（9 天简单平均）", f"{ret_mean:.2%}",
          help=f"前 6 个值平均 {ret_early:.1%}，后 2 个值平均 {ret_late:.1%}，见留存图。")

# ---- 1. 漏斗：双口径切换 -----------------------------------------------
st.header("1 · 转化漏斗（双口径切换）")
caliber = st.radio(
    "漏斗口径",
    ["覆盖口径（各触点独立覆盖）", "时序口径（pv → 意向 → 购买）"],
    horizontal=True,
)
if caliber.startswith("覆盖口径"):
    chart_df = funnel.set_index("stage")[["users"]]
    st.bar_chart(chart_df)
    st.dataframe(funnel, width='stretch')
    cart_cov = float(
        funnel.loc[funnel["stage"] == "cart", "user_coverage_vs_pv"].iloc[0]
    )
    fav_cov = float(
        funnel.loc[funnel["stage"] == "fav", "user_coverage_vs_pv"].iloc[0]
    )
    st.markdown(
        f"业务解读：覆盖口径下，浏览用户 9 天累计购买转化率为 **{buy_cov:.1%}**；"
        f"加购覆盖 {cart_cov:.1%}、收藏覆盖 {fav_cov:.1%}，两者并列展示，"
        "不设串联关系。"
    )
else:
    chart_df = sequential.set_index("step")[["users"]]
    st.bar_chart(chart_df)
    st.dataframe(sequential, width='stretch')
    intent_conv = float(
        sequential.loc[
            sequential["step"].str.startswith("pv_to_intent"),
            "step_conv_from_prev",
        ].iloc[0]
    )
    intent_buy = float(
        sequential.loc[
            sequential["step"] == "intent_to_buy", "step_conv_from_prev"
        ].iloc[0]
    )
    st.markdown(
        f"业务解读：时序口径下，浏览→意向 **{intent_conv:.1%}**、"
        f"意向→购买 **{intent_buy:.1%}**，完整路径占浏览用户 **{full_path:.1%}**；"
        f"另有 {nonpath_share:.1%} 的购买用户（{nonpath_total:,}/{total_buy_users:,} 人，"
        f"占全部购买用户）未满足该路径，"
        f"其中无意向记录 {no_intent_n:,} 人、顺序不满足 {wrong_order_n:,} 人。"
    )

# 漏斗口径与三率区分（数字全部从 CSV 计算）
_cross = cross_item.set_index("group_name")["users"]
_bought_intended = int(_cross.loc["bought_intended_item"])
_bought_only_unintended = int(_cross.loc["bought_only_unintended"])
_path_buyers = _bought_intended + _bought_only_unintended
_pv_behaviors = int(funnel.loc[funnel["stage"] == "pv", "behaviors"].iloc[0])
_buy_behaviors = int(funnel.loc[funnel["stage"] == "buy", "behaviors"].iloc[0])
_event_ratio = _buy_behaviors / _pv_behaviors
st.markdown(
    "漏斗口径：当前漏斗是用户级、跨商品的行为顺序（同一用户在不同商品上的"
    "浏览/意向/购买按时间排序），不等于真实商品交易路径："
    f"完整路径购买用户共 {_path_buyers:,} 人，其中 {_bought_intended:,} 人"
    f"（{_bought_intended / _path_buyers:.1%}）至少购买过一件意向商品，"
    f"{_bought_only_unintended:,} 人购买的全部是未意向过的商品。"
    "商品级核对（与用户级路径不同）：同一商品的加购/收藏须严格早于对该商品"
    "的购买，才算“购买过先前意向商品”（同秒不计入；真实样本中同秒配对为 0）。"
    "用户级完整时序路径只要求行为顺序，不要求同一商品。"
)
st.markdown(
    "三率区分：事件次数比（购买次数 / 浏览次数，全样本 "
    f"{_event_ratio:.2%}）≠ 用户覆盖率（发生过某行为的用户 / 浏览用户，"
    f"如购买覆盖率 {buy_cov:.1%}）≠ 用户级时序路径完成率（完成完整时序路径的"
    f"用户 / 浏览用户，{full_path:.1%}）。三者口径不同，不得混用。"
)

# ---- 2. 每日 PV / UV 与转化率趋势 ---------------------------------------
st.header("2 · 每日 PV / UV 与购买转化率趋势")
daily_indexed = daily.set_index("date")
st.line_chart(daily_indexed[["pv", "uv"]])
st.line_chart(daily_indexed[["buy_rate_pv", "buy_rate_uv"]])
st.dataframe(daily, width='stretch')
peak_pv_day = daily.loc[daily["pv"].idxmax(), "date"]
peak_pv = int(daily["pv"].max())
_early_daily = daily[daily["date"] < "2017-12-02"]
_pv_lo = round(float(_early_daily["pv"].min()) / 10000, 1)
_pv_hi = round(float(_early_daily["pv"].max()) / 10000, 1)
_late_pv = round(float(daily[daily["date"] >= "2017-12-02"]["pv"].max()) / 10000, 1)
_rate_lo = float(daily["buy_rate_pv"].min()) * 100
_rate_hi = float(daily["buy_rate_pv"].max()) * 100
st.markdown(
    f"业务解读：样本日 PV 约 {_pv_lo}–{_pv_hi} 万（11-25 至 12-01），"
    f"12-02/03 跃升至约 {_late_pv} 万（{peak_pv_day} 达 {peak_pv:,}）；"
    f"购买转化率（按 PV）全程在 {_rate_lo:.1f}%–{_rate_hi:.1f}% 之间窄幅波动，"
    "流量跳升未拉动转化率。"
)

# ---- 3. 次日留存曲线 ----------------------------------------------------
st.header("3 · 次日留存曲线")
retention_indexed = retention.set_index("date")
st.line_chart(retention_indexed[["retention_rate"]])
st.dataframe(retention, width='stretch')
st.markdown(
    f"业务解读：次日留存前 6 个值（11-25→11-30）平均 **{ret_early:.1%}**，"
    f"后 2 个值（12-01→12-02）平均 **{ret_late:.1%}**；分组依据是日期段，"
    "不得简称为工作日与周末（前 6 段起始日含周六、周日）。后段的留存跃升"
    "与双 12 前夕流量涌入同步出现。"
)

# ---- 4. R+F 用户分层 ----------------------------------------------------
st.header("4 · R+F 用户分层（无 M）")
# 审查后的展示标签：CSV 中的原始标签须按审查裁决改述；人数从 CSV 实时取数。
_dormant_n = int(
    rfm.loc[rfm["segment"] == "低频/沉睡用户", "users"].iloc[0]
    if (rfm["segment"] == "低频/沉睡用户").any() else 0
)
SEGMENT_LABELS = {
    "重要价值用户": "购买 ≥2 次（原标签：重要价值用户）",
    "潜力新客（首单待复购）": "潜力新客（首单待复购）",
    "一般活跃未购用户": "一般活跃未购用户",
    "低频/沉睡用户": f"仅首日活跃的 {_dormant_n} 名用户（原标签“低频/沉睡”不可用）",
}
rfm_display = rfm.copy()
rfm_display["segment"] = rfm_display["segment"].map(
    lambda s: SEGMENT_LABELS.get(s, s)
)
st.bar_chart(rfm_display.set_index("segment")[["users"]])
st.dataframe(rfm_display, width='stretch')
top_seg = rfm_display.loc[rfm_display["users"].idxmax()]
_zero_n = int(rfm.loc[rfm["avg_buy_count"] == 0, "users"].sum())
_total_users = int(rfm["users"].sum())
st.markdown(
    f"业务解读：9 天内购买 ≥2 次的用户占 **{float(top_seg['user_share']):.1%}**"
    f"（{int(top_seg['users']):,} 人），购买 0 次共 {_zero_n:,} 人"
    f"（{_zero_n / _total_users:.1%}）；"
    "“流失风险的高价值用户”为 0 人；R 维度在 9 天窗口内已退化，"
    "分层实际是单维 F，不得把“重要价值用户”写成高价值客户占比。"
)

# ---- 5. Top 类目 --------------------------------------------------------
st.header("5 · Top 类目（按 PV）")
# Top 类目来自已提交的 outputs/top_categories.csv（由
# scripts/gen_dashboard_csvs.py 从样本生成）；看板不读 71MB 原始样本，
# 因此在无原始样本的机器上也能完整展示。
top_cat = top_categories
st.bar_chart(top_cat.set_index("category_id")[["pv"]])
st.dataframe(top_cat, width='stretch')
best = top_cat.loc[top_cat["buy_rate_pv"].idxmax()]
st.markdown(
    f"业务解读：流量最大的类目是 {int(top_cat.iloc[0]['category_id'])}"
    f"（PV {int(top_cat.iloc[0]['pv']):,}），但 Top 10 中购买转化率最高的是"
    f" {int(best['category_id'])}（{float(best['buy_rate_pv']):.2%}）；"
    "流量最大的类目不等于转化最高的类目；类目无中文名，仅以 "
    "category_id 标识。"
)

st.divider()
st.markdown(
    "仓库：github.com/12baixic-del/taobao-user-behavior-analysis · "
    "报告见 REPORT.md，SQL 查询见 sql/，复算方法见 notebooks/01 与 02。"
)
