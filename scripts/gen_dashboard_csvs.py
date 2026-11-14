"""Generate small committed CSVs for the dashboard (T1-4 remediation).

Reads outputs/taobao_clean_sample.csv (local only, gitignored) and writes:
  - outputs/top_categories.csv       (Top 10 by PV; same numbers as sql Q6)
  - outputs/funnel_nonpath_split.csv (Q9b: 767 no-intent / 743 wrong-order)
  - outputs/funnel_nonpath_totals.csv (round-3 P0: buyer populations for
    the non-path share denominator)
  - outputs/funnel_cross_item.csv    (sequential-path buyers split by
    whether they bought an item they had shown intent for BEFORE buying
    it — time-aware, all window events, round-3 P1 + Codex review)
  - outputs/dataset_summary.csv      (dataset facts the dashboard renders:
    full-file counts, sample counts, window, R=5 share, risk users)
Run once after each recompute; the dashboard reads these files, never the
71MB sample, so it works on a fresh clone.
"""
import pandas as pd

SAMPLE = "outputs/taobao_clean_sample.csv"
# Full-file facts, verified in data/raw/DATA_VERIFICATION.md (Kaggle mirror
# of the Tianchi 649 file, 100,150,807 rows). Stable dataset constants.
FULL_ROWS = 100_150_807
FULL_USERS = 987_994
OBSERVE_DATE = pd.Timestamp("2017-12-03")


def compute_nonpath(df):
    """Q9b non-path split plus buyer-population totals (round-3 P0).

    Returns (split_df, totals, sub, sub2, seq_buyers). totals holds:
      total_buy_users (6,846), pv_buy_users (6,811), nonpath_total (1,510),
      nonpath_pv_users (1,475), nonpath_no_pv_users (35),
      nonpath_share_all (22.1%), nonpath_share_pv (21.7%).
    The dashboard share MUST use nonpath_share_all: the numerator counts
    all buyers, so the denominator must too.
    sub/sub2 are the path-filtered event sets (intent at/after first PV,
    buys at/after first intent) used ONLY for the user-level path.
    """
    pv_first = df[df.behavior_type == "pv"].groupby("user_id")["datetime"].min()
    sub = df[df.behavior_type.isin(["cart", "fav"])].merge(
        pv_first.rename("t_pv"), left_on="user_id", right_index=True)
    sub = sub[sub["datetime"] >= sub["t_pv"]]
    t_seq = sub.groupby("user_id")["datetime"].min()
    sub2 = df[df.behavior_type == "buy"].merge(
        t_seq.rename("t_seq"), left_on="user_id", right_index=True)
    sub2 = sub2[sub2["datetime"] >= sub2["t_seq"]]
    seq_buyers = set(sub2["user_id"])
    buyers = set(df[df.behavior_type == "buy"]["user_id"])
    pv_users = set(pv_first.index)
    non_path = buyers - seq_buyers
    intent_users = set(df[df.behavior_type.isin(["cart", "fav"])]["user_id"])
    no_intent = {x for x in non_path if x not in intent_users}
    split = pd.DataFrame([
        {"group_name": "no_intent_buyers", "users": len(no_intent),
         "definition": "窗口内无任何加购/收藏记录的购买用户"},
        {"group_name": "wrong_order_buyers",
         "users": len(non_path) - len(no_intent),
         "definition": "有意向记录但顺序不满足（首次购买在首次意向之前、"
                       "首次意向在首次浏览之前、或无浏览记录）"},
    ])
    totals = {
        "total_buy_users": len(buyers),
        "pv_buy_users": len(buyers & pv_users),
        "nonpath_total": len(non_path),
        "nonpath_pv_users": len(non_path & pv_users),
        "nonpath_no_pv_users": len(non_path - pv_users),
    }
    totals["nonpath_share_all"] = round(
        totals["nonpath_total"] / totals["total_buy_users"], 6)
    totals["nonpath_share_pv"] = round(
        totals["nonpath_pv_users"] / totals["pv_buy_users"], 6)
    return split, totals, sub, sub2, seq_buyers


def classify_cross_item(intent_events, buy_events, seq_buyers):
    """Time-aware cross-item classification (round-3 P1 + Codex review).

    intent_events: ALL cart/fav events in the window (columns user_id,
      item_id, datetime). buy_events: ALL buy events in the window.
    Event scope is deliberately the whole window: the question is whether
    a buyer bought an item they had shown intent for BEFORE buying it,
    and an intent that precedes the buy counts even if it precedes the
    user's first PV. (The user-level sequential path still requires
    pv -> intent -> buy; that restriction lives in compute_nonpath, not
    here. An earlier version wrongly reused the path-filtered sets and
    missed 32 users, e.g. a user who carted an item before their first
    PV and bought it days later.)
    A sequential-path buyer counts as bought_intended_item iff some item
    has min(intent time) < max(buy time) for that (user, item) — i.e.
    at least one cart/fav for the item strictly precedes at least one
    buy of the same item. Same-second intent/buy pairs are indeterminate
    and do NOT count as "before" (real sample: 0 such pairs).
    Returns (cross_df, diagnostics). Diagnostics:
      overlap_all_events: set-intersection count on all events (reference)
      same_second_pairs: distinct (user, item) pairs with >= 1 exact
        timestamp match between an intent and a buy event
      users_flipping_under_loose: users counted under a <= rule minus strict
    """
    intent_items = intent_events.groupby("user_id")["item_id"].apply(set)
    buy_items = buy_events.groupby("user_id")["item_id"].apply(set)
    overlap = sum(1 for x in seq_buyers
                  if intent_items.get(x, set()) & buy_items.get(x, set()))
    intent_min = (intent_events.groupby(["user_id", "item_id"])["datetime"]
                  .min().rename("t_intent_min"))
    buy_max = (buy_events.groupby(["user_id", "item_id"])["datetime"]
               .max().rename("t_buy_max"))
    m = intent_min.to_frame().join(buy_max, how="inner")
    m["strict_before"] = m["t_intent_min"] < m["t_buy_max"]
    m["loose_before"] = m["t_intent_min"] <= m["t_buy_max"]
    per_user_strict = m.groupby("user_id")["strict_before"].any()
    per_user_loose = m.groupby("user_id")["loose_before"].any()
    # Restrict to sequential-path buyers: the pair table also contains
    # non-path buyers (e.g. a buy before the first intent at user level
    # with a later intent before a second buy of the same item). Counting
    # them would overstate the intended group by 33 on this sample.
    new_same = int(sum(1 for x in seq_buyers
                       if per_user_strict.get(x, False)))
    loose_same = int(sum(1 for x in seq_buyers
                         if per_user_loose.get(x, False)))
    exact = intent_events.merge(
        buy_events, on=["user_id", "item_id", "datetime"],
        suffixes=("_intent", "_buy"))
    same_second_pairs = int(
        exact[["user_id", "item_id"]].drop_duplicates().shape[0])
    diagnostics = {
        "overlap_all_events": overlap,
        "same_second_pairs": same_second_pairs,
        "users_flipping_under_loose": loose_same - new_same,
    }
    cross = pd.DataFrame([
        {"group_name": "bought_intended_item", "users": new_same,
         "definition": "完整路径购买用户中，至少一件购买商品在购买前有同商品"
                       "加购/收藏记录（意向须严格早于购买，同秒不计入；"
                       "意向可在首次浏览之前）",
         "same_second_pairs": same_second_pairs,
         "users_flipping_under_loose": loose_same - new_same},
        {"group_name": "bought_only_unintended",
         "users": len(seq_buyers) - new_same,
         "definition": "完整路径购买用户中，所购商品在购买前均无同商品"
                       "加购/收藏记录（购买后才意向的商品不计入）",
         "same_second_pairs": same_second_pairs,
         "users_flipping_under_loose": loose_same - new_same},
    ])
    return cross, diagnostics


def compute_dataset_summary(df):
    """Dataset facts the dashboard renders (Codex review finding 2).

    Full-file counts are stable facts from data/raw/DATA_VERIFICATION.md;
    everything else is computed from the sample here. R bins and the risk
    rule replicate notebooks/02_funnel_rfm_analysis.py (observe date
    2017-12-03; R_score 5 <=> days_since_last_active <= 1; risk segment
    <=> buy_count >= 2 and R_score <= 3, i.e. days >= 4).
    Returns a metric/value/source DataFrame.
    """
    sample_users = int(df["user_id"].nunique())
    sample_rows = int(len(df))
    last = df.groupby("user_id")["datetime"].max()
    days = (OBSERVE_DATE.normalize() - last.dt.normalize()).dt.days.clip(lower=0)
    r5_share = round(float((days <= 1).mean()), 6)
    buys = (df[df.behavior_type == "buy"].groupby("user_id").size()
            .reindex(days.index, fill_value=0))
    risk_users = int(((buys >= 2) & (days >= 4)).sum())
    rows = [
        ("full_rows", FULL_ROWS, "data/raw/DATA_VERIFICATION.md"),
        ("full_users", FULL_USERS, "data/raw/DATA_VERIFICATION.md"),
        ("sample_users", sample_users, "computed from sample"),
        ("sample_rows", sample_rows, "computed from sample"),
        ("sample_share", round(sample_users / FULL_USERS, 6),
         "sample_users / full_users"),
        ("window_start", df["datetime"].min().date().isoformat(),
         "computed from sample"),
        ("window_end", df["datetime"].max().date().isoformat(),
         "computed from sample"),
        ("r5_share", r5_share,
         "notebooks/02 R bins, observe 2017-12-03"),
        ("risk_users", risk_users,
         "notebooks/02 risk rule: buys>=2 and R<=3"),
    ]
    return pd.DataFrame(rows, columns=["metric", "value", "source"])


def main():
    df = pd.read_csv(SAMPLE, usecols=["user_id", "item_id", "category_id",
                                       "behavior_type", "datetime"])
    df["datetime"] = pd.to_datetime(df["datetime"])

    # --- Top 10 categories (Q6) ---
    pv = df[df.behavior_type == "pv"].groupby("category_id").size().rename("pv")
    buy_users = (df[df.behavior_type == "buy"]
                 .groupby("category_id")["user_id"].nunique().rename("buy_users"))
    buy_beh = (df[df.behavior_type == "buy"]
               .groupby("category_id").size().rename("buy_behaviors"))
    top = pd.concat([pv, buy_users, buy_beh], axis=1).fillna(0)
    top["pv"] = top["pv"].astype(int)
    top["buy_users"] = top["buy_users"].astype(int)
    top["buy_behaviors"] = top["buy_behaviors"].astype(int)
    top["buy_rate_pv"] = (top["buy_behaviors"] / top["pv"]).round(4)
    top = top.sort_values("pv", ascending=False).head(10).reset_index()
    top["pv_rank"] = range(1, 11)
    top = top[["category_id", "pv", "buy_users", "buy_behaviors",
               "buy_rate_pv", "pv_rank"]]
    top.to_csv("outputs/top_categories.csv", index=False)
    print("top_categories.csv:"); print(top.to_string(index=False))

    # --- Non-path split (Q9b) + totals (round-3 P0) ---
    split, totals, sub, sub2, seq_buyers = compute_nonpath(df)
    split.to_csv("outputs/funnel_nonpath_split.csv", index=False)
    pd.DataFrame([totals]).to_csv("outputs/funnel_nonpath_totals.csv",
                                  index=False)
    print("\nfunnel_nonpath_split.csv:"); print(split.to_string(index=False))
    print("\nfunnel_nonpath_totals.csv:"); print(pd.DataFrame([totals])
                                                 .to_string(index=False))

    # --- Cross-item scope (round-3 P1, all window events) ---
    intent_all = df[df.behavior_type.isin(["cart", "fav"])]
    buy_all = df[df.behavior_type == "buy"]
    cross, diag = classify_cross_item(intent_all, buy_all, seq_buyers)
    cross.to_csv("outputs/funnel_cross_item.csv", index=False)
    print("\nfunnel_cross_item.csv:"); print(cross.to_string(index=False))
    print(f"\nsequential-path buyers: {len(seq_buyers)}")
    print(f"set-intersection on all events: {diag['overlap_all_events']}")
    print(f"same-second (user, item) pairs: {diag['same_second_pairs']}; "
          f"users flipping under <= rule: {diag['users_flipping_under_loose']}")

    # --- Dataset summary (Codex review finding 2) ---
    summary = compute_dataset_summary(df)
    summary.to_csv("outputs/dataset_summary.csv", index=False)
    print("\ndataset_summary.csv:"); print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
