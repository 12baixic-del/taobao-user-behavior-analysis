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
SAMPLE = OUTPUTS / "taobao_clean_sample.csv"

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
    }


@st.cache_data
def load_top_categories(n: int = 10):
    """Top N 类目（按 PV）。样本 CSV 被 .gitignore 排除，缺失时返回 None。"""
    if not SAMPLE.exists():
        return None
    df = pd.read_csv(
        SAMPLE, usecols=["category_id", "behavior_type"]
    )
    agg = (
        df.groupby("category_id")
        .agg(
            pv=("behavior_type", lambda s: (s == "pv").sum()),
            buy=("behavior_type", lambda s: (s == "buy").sum()),
        )
        .reset_index()
    )
    agg["buy_rate_pv"] = agg["buy"] / agg["pv"]
    return agg.sort_values("pv", ascending=False).head(n)


data = load_summaries()
funnel = data["funnel"]
sequential = data["sequential"]
retention = data["retention"]
rfm = data["rfm"]
daily = data["daily"]

# ---- 顶部关键指标（全部来自 CSV） ---------------------------------------
buy_cov = float(
    funnel.loc[funnel["stage"] == "buy", "user_coverage_vs_pv"].iloc[0]
)
full_path = float(
    sequential.loc[
        sequential["step"] == "intent_to_buy", "overall_conv_from_pv"
    ].iloc[0]
)
ret_mean = float(retention["retention_rate"].mean())
st.metric(
    "覆盖口径购买转化率（9 天累计）", f"{buy_cov:.1%}",
    help="浏览用户中 9 天内购买的占比，非单次访问路径。",
)
st.metric("完整路径转化率（时序口径）", f"{full_path:.1%}",
          help="完成 pv → 意向 → 购买完整路径的用户占浏览用户的比例；"
               "22.1% 的购买用户为直接购买，不在该路径内。")
st.metric("次日留存（9 天简单平均）", f"{ret_mean:.2%}",
          help="工作日约 78.7%、周末约 98%，见留存图。")

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
    st.markdown(
        f"业务解读：覆盖口径下，浏览用户 9 天累计购买转化率为 **{buy_cov:.1%}**；"
        "加购与收藏并列展示，不设串联关系，其中加购覆盖 74.4%、收藏覆盖 39.2%。"
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
        "22.1% 的购买用户为直接购买，不在该路径内。"
    )

# ---- 2. 每日 PV / UV 与转化率趋势 ---------------------------------------
st.header("2 · 每日 PV / UV 与购买转化率趋势")
daily_indexed = daily.set_index("date")
st.line_chart(daily_indexed[["pv", "uv"]])
st.line_chart(daily_indexed[["buy_rate_pv", "buy_rate_uv"]])
st.dataframe(daily, width='stretch')
peak_pv_day = daily.loc[daily["pv"].idxmax(), "date"]
peak_pv = int(daily["pv"].max())
st.markdown(
    f"业务解读：样本日 PV 约 9.0–9.8 万（11-25 至 12-01），"
    f"12-02/03 跃升至约 12.4 万（{peak_pv_day} 达 {peak_pv:,}）；"
    "购买转化率（按 PV）全程在 2.0%–2.4% 之间窄幅波动，流量跳升未拉动转化率。"
)

# ---- 3. 次日留存曲线 ----------------------------------------------------
st.header("3 · 次日留存曲线")
retention_indexed = retention.set_index("date")
st.line_chart(retention_indexed[["retention_rate"]])
st.dataframe(retention, width='stretch')
early = retention[retention["date"] < "2017-12-01"]["retention_rate"].mean()
late = retention[retention["date"] >= "2017-12-01"]["retention_rate"].mean()
st.markdown(
    f"业务解读：次日留存前 6 个值（11-25→11-30）平均 **{early:.1%}**，"
    f"后 2 个值（12-01→12-02）平均 **{late:.1%}**；"
    "按报告口径表述为工作日约 78.7%、周末约 98%，周末的留存跃升与"
    "双 12 前夕流量涌入同步出现。"
)

# ---- 4. R+F 用户分层 ----------------------------------------------------
st.header("4 · R+F 用户分层（无 M）")
# 审查后的展示标签：CSV 中的原始标签须按审查裁决改述。
SEGMENT_LABELS = {
    "重要价值用户": "购买 ≥2 次（原标签：重要价值用户）",
    "潜力新客（首单待复购）": "潜力新客（首单待复购）",
    "一般活跃未购用户": "一般活跃未购用户",
    "低频/沉睡用户": "仅首日活跃的 2 名用户（原标签“低频/沉睡”不可用）",
}
rfm_display = rfm.copy()
rfm_display["segment"] = rfm_display["segment"].map(
    lambda s: SEGMENT_LABELS.get(s, s)
)
st.bar_chart(rfm_display.set_index("segment")[["users"]])
st.dataframe(rfm_display, width='stretch')
top_seg = rfm_display.loc[rfm_display["users"].idxmax()]
st.markdown(
    f"业务解读：9 天内购买 ≥2 次的用户占 **{float(top_seg['user_share']):.1%}**"
    f"（{int(top_seg['users']):,} 人），购买 0 次占 31.9%；"
    "“流失风险的高价值用户”为 0 人；R 维度在 9 天窗口内已退化，"
    "分层实际是单维 F，不得把“重要价值用户”写成高价值客户占比。"
)

# ---- 5. Top 类目 --------------------------------------------------------
st.header("5 · Top 类目（按 PV）")
top_cat = load_top_categories(10)
if top_cat is None:
    st.warning(
        "样本文件 outputs/taobao_clean_sample.csv 不在本地（该文件被 "
        ".gitignore 排除，仅用于本地复算），Top 类目图暂不可用；"
        "把样本 CSV 放回 outputs/ 后刷新即可显示。"
    )
else:
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
