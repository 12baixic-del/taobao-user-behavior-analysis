"""Generate small committed CSVs for the dashboard (T1-4 remediation).

Reads outputs/taobao_clean_sample.csv (local only, gitignored) and writes:
  - outputs/top_categories.csv       (Top 10 by PV; same numbers as sql Q6)
  - outputs/funnel_nonpath_split.csv (Q9b: 767 no-intent / 743 wrong-order)
  - outputs/funnel_nonpath_totals.csv (round-3 P0: buyer populations for
    the non-path share denominator — see compute_nonpath)
  - outputs/funnel_cross_item.csv    (sequential-path buyers split by
    whether they bought an item they had shown intent for BEFORE buying
    it — time-aware, round-3 P1; same-second pairs do not count)
Run once after each recompute; the dashboard reads these files, never the
71MB sample, so it works on a fresh clone.
"""
import pandas as pd

SAMPLE = "outputs/taobao_clean_sample.csv"


def compute_nonpath(df):
    """Q9b non-path split plus buyer-population totals (round-3 P0).

    Returns (split_df, totals) where totals holds:
      total_buy_users: all buyers in the window (6,846)
      pv_buy_users: buyers with at least one PV record (6,811)
      nonpath_total: buyers not on the sequential path (1,510)
      nonpath_pv_users: non-path buyers WITH a PV record (1,475)
      nonpath_no_pv_users: non-path buyers WITHOUT a PV record (35)
      nonpath_share_all: nonpath_total / total_buy_users (22.1%)
      nonpath_share_pv: nonpath_pv_users / pv_buy_users (21.7%)
    The dashboard share MUST use nonpath_share_all. The historical bug
    divided nonpath_total (all buyers) by pv_buy_users (PV buyers only),
    mixing two populations (22.2% instead of 22.1%). The alternative
    PV-only share is reported for reference only.
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
    """Time-aware cross-item classification (round-3 P1).

    intent_events: cart/fav events at/after the user's first PV
      (columns user_id, item_id, datetime).
    buy_events: buy events at/after the user's first intent
      (same columns).
    A sequential-path buyer counts as bought_intended_item iff some item
    has min(intent time) < max(buy time) for that (user, item) — i.e.
    at least one cart/fav for the item strictly precedes at least one
    buy of the same item. The old rule used only set intersection
    (intent_items & buy_items) and counted users whose intent came
    AFTER their only buy of that item. Same-second intent/buy pairs are
    indeterminate and do NOT count as "before" (real sample: 0 such
    pairs, so the choice does not change real-data numbers).
    Returns (cross_df, diagnostics) with diagnostics holding old_same
    (set-intersection count, for the log), same_second_pairs and
    users_flipping_under_loose (count under a <= rule minus strict).
    """
    intent_items = intent_events.groupby("user_id")["item_id"].apply(set)
    buy_items = buy_events.groupby("user_id")["item_id"].apply(set)
    old_same = sum(1 for x in seq_buyers
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
    new_same = int(per_user_strict.sum())
    loose_same = int(per_user_loose.sum())
    diagnostics = {
        "old_same": old_same,
        "same_second_pairs": int((m["t_intent_min"] == m["t_buy_max"]).sum()),
        "users_flipping_under_loose": loose_same - new_same,
    }
    cross = pd.DataFrame([
        {"group_name": "bought_intended_item", "users": new_same,
         "definition": "完整路径购买用户中，至少一件购买商品在购买前有同商品"
                       "加购/收藏记录（意向须严格早于购买，同秒不计入）"},
        {"group_name": "bought_only_unintended",
         "users": len(seq_buyers) - new_same,
         "definition": "完整路径购买用户中，没有任何一件购买商品在购买前有"
                       "同商品加购/收藏记录"},
    ])
    return cross, diagnostics


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

    # --- Cross-item scope (round-3 P1: time-aware) ---
    cross, diag = classify_cross_item(sub, sub2, seq_buyers)
    cross.to_csv("outputs/funnel_cross_item.csv", index=False)
    print("\nfunnel_cross_item.csv:"); print(cross.to_string(index=False))
    print(f"\nsequential-path buyers: {len(seq_buyers)}")
    print(f"old set-intersection count: {diag['old_same']} "
          f"(overcounted by {diag['old_same'] - int(cross.loc[0, 'users'])})")
    print(f"same-second (user, item) pairs: {diag['same_second_pairs']}; "
          f"users flipping under <= rule: {diag['users_flipping_under_loose']}")


if __name__ == "__main__":
    main()
