"""Generate small committed CSVs for the dashboard (T1-4 remediation).

Reads outputs/taobao_clean_sample.csv (local only, gitignored) and writes:
  - outputs/top_categories.csv       (Top 10 by PV; same numbers as sql Q6)
  - outputs/funnel_nonpath_split.csv (Q9b: 767 no-intent / 743 wrong-order)
  - outputs/funnel_cross_item.csv    (sequential-path buyers split by
                                      whether they bought an intended item)
Run once after each recompute; the dashboard reads these files, never the
71MB sample, so it works on a fresh clone.
"""
import pandas as pd

SAMPLE = "outputs/taobao_clean_sample.csv"

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

# --- Non-path split (Q9b) ---
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
split.to_csv("outputs/funnel_nonpath_split.csv", index=False)
print("\nfunnel_nonpath_split.csv:"); print(split.to_string(index=False))

# --- Cross-item scope ---
intent_items = sub.groupby("user_id")["item_id"].apply(set)
buy_items = sub2.groupby("user_id")["item_id"].apply(set)
same = sum(1 for x in seq_buyers if intent_items[x] & buy_items[x])
cross = pd.DataFrame([
    {"group_name": "bought_intended_item", "users": same,
     "definition": "完整路径购买用户中，至少购买过一件意向商品"},
    {"group_name": "bought_only_unintended", "users": len(seq_buyers) - same,
     "definition": "完整路径购买用户中，购买的全部是未意向过的商品"},
])
cross.to_csv("outputs/funnel_cross_item.csv", index=False)
print("\nfunnel_cross_item.csv:"); print(cross.to_string(index=False))
print("\nsequential-path buyers:", len(seq_buyers))
