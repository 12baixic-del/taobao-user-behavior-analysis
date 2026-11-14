-- 淘宝用户行为分析 v2：SQL 业务查询（T1-3）
-- 数据：outputs/taobao_clean_sample.csv（10,053 名用户整群抽样，总体 987,994 用户的 1.02%）
-- 表结构：sample(user_id, item_id, category_id, behavior_type, timestamp, datetime, date, hour)
-- 方言：SQLite（Python 标准库 sqlite3）。运行：python sql/run_sqlite.py
-- 口径说明：所有比例类指标基于样本；规模类数字（人数、行数）不得直接等同总体。
-- 交叉核对基准：outputs/funnel_summary.csv、funnel_sequential_summary.csv、
--   retention_summary.csv、rfm_summary.csv、daily_summary.csv（见 sql/README.md）。

-- Q1：每日 PV / UV / 购买人数与购买转化率
-- 业务问题：双 12 预热期 9 天内，每天的流量与购买转化如何变化？
SELECT
    date,
    SUM(CASE WHEN behavior_type = 'pv' THEN 1 ELSE 0 END) AS pv,
    COUNT(DISTINCT user_id) AS uv,
    COUNT(DISTINCT CASE WHEN behavior_type = 'buy' THEN user_id END) AS buy_users,
    SUM(CASE WHEN behavior_type = 'buy' THEN 1 ELSE 0 END) AS buy_behaviors,
    ROUND(1.0 * SUM(CASE WHEN behavior_type = 'buy' THEN 1 ELSE 0 END)
        / NULLIF(SUM(CASE WHEN behavior_type = 'pv' THEN 1 ELSE 0 END), 0), 4) AS buy_rate_pv,
    ROUND(1.0 * COUNT(DISTINCT CASE WHEN behavior_type = 'buy' THEN user_id END)
        / NULLIF(COUNT(DISTINCT user_id), 0), 4) AS buy_rate_uv
FROM sample
GROUP BY date
ORDER BY date;

-- Q2：覆盖漏斗各环节人数与转化率（口径 A）
-- 业务问题：各功能触点（浏览/加购/收藏/购买）分别覆盖了多少用户？
-- 口径：独立用户数，不要求行为先后顺序；cart 与 fav 并列展示，不设串联关系。
-- 口径规则（2026-11-14 复审整改）：users 与 user_coverage_vs_pv 只统计
--   浏览用户集合（发生过 pv 的用户）——分子分母同一集合；45 名无浏览记录
--   的用户（其中 35 人购买、42 人加购、24 人收藏）不计入覆盖人数。
--   behaviors 列仍为全样本事件次数（与 Q6/Q7 的事件转化率一致）。
WITH pv_users AS (
    SELECT DISTINCT user_id FROM sample WHERE behavior_type = 'pv'
),
pv_n AS (
    SELECT COUNT(*) AS n FROM pv_users
),
-- users：仅计浏览用户集合内的独立用户（分子分母同一集合）
u AS (
    SELECT 'pv' AS stage, COUNT(DISTINCT s.user_id) AS users
    FROM sample s JOIN pv_users p ON s.user_id = p.user_id WHERE s.behavior_type = 'pv'
    UNION ALL
    SELECT 'cart', COUNT(DISTINCT s.user_id)
    FROM sample s JOIN pv_users p ON s.user_id = p.user_id WHERE s.behavior_type = 'cart'
    UNION ALL
    SELECT 'fav', COUNT(DISTINCT s.user_id)
    FROM sample s JOIN pv_users p ON s.user_id = p.user_id WHERE s.behavior_type = 'fav'
    UNION ALL
    SELECT 'cart_or_fav', COUNT(DISTINCT s.user_id)
    FROM sample s JOIN pv_users p ON s.user_id = p.user_id WHERE s.behavior_type IN ('cart', 'fav')
    UNION ALL
    SELECT 'buy', COUNT(DISTINCT s.user_id)
    FROM sample s JOIN pv_users p ON s.user_id = p.user_id WHERE s.behavior_type = 'buy'
),
-- behaviors：全样本事件次数（与 Q6/Q7 的事件转化率一致，不按用户过滤）
b AS (
    SELECT 'pv' AS stage, COUNT(*) AS behaviors FROM sample WHERE behavior_type = 'pv'
    UNION ALL
    SELECT 'cart', COUNT(*) FROM sample WHERE behavior_type = 'cart'
    UNION ALL
    SELECT 'fav', COUNT(*) FROM sample WHERE behavior_type = 'fav'
    UNION ALL
    SELECT 'cart_or_fav', COUNT(*) FROM sample WHERE behavior_type IN ('cart', 'fav')
    UNION ALL
    SELECT 'buy', COUNT(*) FROM sample WHERE behavior_type = 'buy'
)
SELECT u.stage, u.users, b.behaviors,
       ROUND(1.0 * u.users / (SELECT n FROM pv_n), 4) AS user_coverage_vs_pv
FROM u JOIN b ON u.stage = b.stage;

-- Q3：时序漏斗 pv → 意向 → 购买（口径 B）
-- 业务问题：沿“浏览 → 产生意向 → 最终下单”主路径，转化效率如何？
-- 口径：严格按时间先后（首次 pv 之后的意向、首次意向之后的购买）。
--   1,510 名购买用户未满足该路径，不得统称为“直接购买”：767 人无任何
--   意向记录，743 人有意向但顺序不满足（见 Q9b 分流）。
--   同秒边界：意向与首次 pv 同秒、购买与首次意向同秒时，“>=”将其计入
--   路径内；真实样本中该情况为 0 例（见 tests/test_analysis.py）。
-- 漏斗口径：用户级、跨商品的行为顺序，不等于真实商品交易路径。
WITH first_pv AS (
    SELECT user_id, MIN(datetime) AS t_pv
    FROM sample WHERE behavior_type = 'pv' GROUP BY user_id
),
first_intent AS (
    SELECT s.user_id, MIN(s.datetime) AS t_intent
    FROM sample s JOIN first_pv p ON s.user_id = p.user_id
    WHERE s.behavior_type IN ('cart', 'fav') AND s.datetime >= p.t_pv
    GROUP BY s.user_id
),
buy_after_intent AS (
    SELECT DISTINCT s.user_id
    FROM sample s JOIN first_intent i ON s.user_id = i.user_id
    WHERE s.behavior_type = 'buy' AND s.datetime >= i.t_intent
)
SELECT 'pv' AS step, (SELECT COUNT(*) FROM first_pv) AS users,
       1.0 AS step_conv_from_prev, 1.0 AS overall_conv_from_pv
UNION ALL
SELECT 'pv_to_intent', (SELECT COUNT(*) FROM first_intent),
       ROUND(1.0 * (SELECT COUNT(*) FROM first_intent) / (SELECT COUNT(*) FROM first_pv), 4),
       ROUND(1.0 * (SELECT COUNT(*) FROM first_intent) / (SELECT COUNT(*) FROM first_pv), 4)
UNION ALL
SELECT 'intent_to_buy', (SELECT COUNT(*) FROM buy_after_intent),
       ROUND(1.0 * (SELECT COUNT(*) FROM buy_after_intent) / (SELECT COUNT(*) FROM first_intent), 4),
       ROUND(1.0 * (SELECT COUNT(*) FROM buy_after_intent) / (SELECT COUNT(*) FROM first_pv), 4);

-- Q4：次日留存率（按日期分组呈现，不得简称为工作日与周末）
-- 业务问题：用户第二天还回不回来？前 6 段与后 2 段有无差异？
-- 口径：留存率 = |当日活跃用户 ∩ 次日活跃用户| / |当日活跃用户|；12-03 无次日，自动排除。
WITH daily AS (
    SELECT date, user_id FROM sample GROUP BY date, user_id
)
SELECT a.date AS date,
       COUNT(DISTINCT a.user_id) AS active_users,
       COUNT(DISTINCT b.user_id) AS retained_next_day,
       ROUND(1.0 * COUNT(DISTINCT b.user_id) / COUNT(DISTINCT a.user_id), 4) AS retention_rate
FROM daily a
LEFT JOIN daily b
    ON b.user_id = a.user_id AND b.date = date(a.date, '+1 day')
WHERE EXISTS (SELECT 1 FROM daily d WHERE d.date = date(a.date, '+1 day'))
GROUP BY a.date
ORDER BY a.date;

-- Q5：R+F 用户分层人数（无 M）
-- 业务问题：按最近活跃（R）与购买频次（F），用户如何分层？
-- 口径：观察日 2017-12-03；R：0–1天=5、2–3天=4、4–5天=3、6–7天=2、≥8天=1；
--   F：0次=1、1次=2、2次=3、3–4次=4、≥5次=5。注意：R 维度在 9 天窗口内已退化
--   （99.86% 的用户 R=5），分层实际是单维 F；“流失风险的高价值用户”为 0 人，不会出现在结果中。
WITH user_agg AS (
    SELECT user_id,
           MAX(date) AS last_active_date,
           SUM(CASE WHEN behavior_type = 'buy' THEN 1 ELSE 0 END) AS buy_count
    FROM sample
    GROUP BY user_id
),
scored AS (
    SELECT user_id, buy_count,
           CAST(julianday('2017-12-03') - julianday(last_active_date) AS INTEGER) AS days_since,
           CASE WHEN CAST(julianday('2017-12-03') - julianday(last_active_date) AS INTEGER) <= 1 THEN 5
                WHEN CAST(julianday('2017-12-03') - julianday(last_active_date) AS INTEGER) <= 3 THEN 4
                WHEN CAST(julianday('2017-12-03') - julianday(last_active_date) AS INTEGER) <= 5 THEN 3
                WHEN CAST(julianday('2017-12-03') - julianday(last_active_date) AS INTEGER) <= 7 THEN 2
                ELSE 1 END AS r_score,
           CASE WHEN buy_count = 0 THEN 1 WHEN buy_count = 1 THEN 2
                WHEN buy_count = 2 THEN 3 WHEN buy_count <= 4 THEN 4 ELSE 5 END AS f_score
    FROM user_agg
)
SELECT
    CASE WHEN f_score >= 3 AND r_score >= 4 THEN '购买≥2次（原标签：重要价值用户）'
         WHEN f_score >= 3 AND r_score <= 3 THEN '流失风险的高价值用户'
         WHEN f_score = 2 AND r_score >= 4 THEN '潜力新客（首单待复购）'
         WHEN f_score = 1 AND r_score >= 4 THEN '一般活跃未购用户'
         ELSE '低频/沉睡用户' END AS segment,
    COUNT(*) AS users,
    ROUND(AVG(buy_count), 3) AS avg_buy_count,
    ROUND(AVG(days_since), 3) AS avg_days_since_active,
    ROUND(1.0 * COUNT(*) / (SELECT COUNT(*) FROM scored), 4) AS user_share
FROM scored
GROUP BY segment
ORDER BY users DESC;

-- Q6：Top 10 类目（按 PV 排序，窗口函数 RANK）
-- 业务问题：哪些类目流量最大、购买转化最高？
SELECT category_id,
       SUM(CASE WHEN behavior_type = 'pv' THEN 1 ELSE 0 END) AS pv,
       COUNT(DISTINCT CASE WHEN behavior_type = 'buy' THEN user_id END) AS buy_users,
       SUM(CASE WHEN behavior_type = 'buy' THEN 1 ELSE 0 END) AS buy_behaviors,
       ROUND(1.0 * SUM(CASE WHEN behavior_type = 'buy' THEN 1 ELSE 0 END)
           / NULLIF(SUM(CASE WHEN behavior_type = 'pv' THEN 1 ELSE 0 END), 0), 4) AS buy_rate_pv,
       RANK() OVER (ORDER BY SUM(CASE WHEN behavior_type = 'pv' THEN 1 ELSE 0 END) DESC) AS pv_rank
FROM sample
GROUP BY category_id
ORDER BY pv DESC
LIMIT 10;

-- Q7：分时段（hour 0–23）PV 与购买分布
-- 业务问题：一天中哪个时段流量最大、哪个时段购买转化最高？
SELECT hour,
       SUM(CASE WHEN behavior_type = 'pv' THEN 1 ELSE 0 END) AS pv,
       COUNT(DISTINCT user_id) AS uv,
       SUM(CASE WHEN behavior_type = 'buy' THEN 1 ELSE 0 END) AS buy_behaviors,
       ROUND(1.0 * SUM(CASE WHEN behavior_type = 'buy' THEN 1 ELSE 0 END)
           / NULLIF(SUM(CASE WHEN behavior_type = 'pv' THEN 1 ELSE 0 END), 0), 4) AS buy_rate_pv
FROM sample
GROUP BY hour
ORDER BY hour;

-- Q8：购买次数分布（0 次 / 恰好 1 次 / ≥2 次）
-- 业务问题：9 天窗口内，用户购买频次的分布形态如何？
WITH user_buy AS (
    SELECT user_id, SUM(CASE WHEN behavior_type = 'buy' THEN 1 ELSE 0 END) AS buy_count
    FROM sample
    GROUP BY user_id
)
SELECT CASE WHEN buy_count = 0 THEN '0次'
            WHEN buy_count = 1 THEN '恰好1次'
            ELSE '≥2次' END AS buy_group,
       COUNT(*) AS users,
       ROUND(1.0 * COUNT(*) / (SELECT COUNT(*) FROM user_buy), 4) AS user_share
FROM user_buy
GROUP BY buy_group
ORDER BY MIN(buy_count);

-- Q9：意向到购买——两种口径对照
-- 业务问题：有过加购/收藏的用户，最终有多少人买了？严格时序口径与宽松口径差多少？
-- 口径：(a) 宽松口径：有过任何意向行为的用户中，任意时间购买过的比例；
--   (b) 严格时序口径：首次 pv 之后的意向用户中，在首次意向之后购买的比例（= Q3 第二步→第三步）。
WITH first_pv AS (
    SELECT user_id, MIN(datetime) AS t_pv
    FROM sample WHERE behavior_type = 'pv' GROUP BY user_id
),
first_intent AS (
    SELECT s.user_id, MIN(s.datetime) AS t_intent
    FROM sample s JOIN first_pv p ON s.user_id = p.user_id
    WHERE s.behavior_type IN ('cart', 'fav') AND s.datetime >= p.t_pv
    GROUP BY s.user_id
),
strict_buyers AS (
    SELECT DISTINCT s.user_id
    FROM sample s JOIN first_intent i ON s.user_id = i.user_id
    WHERE s.behavior_type = 'buy' AND s.datetime >= i.t_intent
),
loose_intent AS (
    SELECT DISTINCT user_id FROM sample WHERE behavior_type IN ('cart', 'fav')
),
loose_buyers AS (
    SELECT DISTINCT user_id FROM sample
    WHERE behavior_type = 'buy' AND user_id IN (SELECT user_id FROM loose_intent)
)
SELECT 'strict_seq' AS caliber,
       (SELECT COUNT(*) FROM first_intent) AS intent_users,
       (SELECT COUNT(*) FROM strict_buyers) AS buyers,
       ROUND(1.0 * (SELECT COUNT(*) FROM strict_buyers) / (SELECT COUNT(*) FROM first_intent), 4) AS buy_rate
UNION ALL
SELECT 'any_intent',
       (SELECT COUNT(*) FROM loose_intent),
       (SELECT COUNT(*) FROM loose_buyers),
       ROUND(1.0 * (SELECT COUNT(*) FROM loose_buyers) / (SELECT COUNT(*) FROM loose_intent), 4);

-- Q9b：未满足完整时序路径的购买用户分流（1,510 人）
-- 业务问题：1,510 名未满足完整时序路径的购买用户由哪两类人构成？
-- 口径：无意向购买 = 窗口内无任何加购/收藏记录的购买用户（767 人）；
--   顺序不满足 = 有意向记录，但首次购买在首次意向之前、或首次意向在
--   首次浏览之前、或无浏览记录（743 人）。两类人不得混称为“直接购买”。
WITH seq_buyers AS (
    SELECT DISTINCT s.user_id
    FROM sample s
    JOIN (SELECT user_id, MIN(datetime) AS t_pv
          FROM sample WHERE behavior_type = 'pv' GROUP BY user_id) p
        ON s.user_id = p.user_id
    JOIN (SELECT s2.user_id, MIN(s2.datetime) AS t_intent
          FROM sample s2
          JOIN (SELECT user_id, MIN(datetime) AS t_pv
                FROM sample WHERE behavior_type = 'pv' GROUP BY user_id) p2
              ON s2.user_id = p2.user_id
          WHERE s2.behavior_type IN ('cart', 'fav') AND s2.datetime >= p2.t_pv
          GROUP BY s2.user_id) i ON s.user_id = i.user_id
    WHERE s.behavior_type = 'buy' AND s.datetime >= i.t_intent
),
non_path AS (
    SELECT DISTINCT user_id FROM sample WHERE behavior_type = 'buy'
    EXCEPT
    SELECT user_id FROM seq_buyers
),
no_intent AS (
    SELECT user_id FROM non_path
    WHERE user_id NOT IN (
        SELECT DISTINCT user_id FROM sample WHERE behavior_type IN ('cart', 'fav')
    )
)
SELECT 'no_intent_buyers' AS group_name, COUNT(*) AS users,
       '窗口内无任何加购/收藏记录的购买用户' AS definition
FROM no_intent
UNION ALL
SELECT 'wrong_order_buyers', COUNT(*),
       '有意向记录但顺序不满足（首次购买在首次意向之前、首次意向在首次浏览之前、或无浏览记录）'
FROM non_path WHERE user_id NOT IN (SELECT user_id FROM no_intent);

-- Q10：日环比——每日 PV 与购买人数的 LAG 日增长率（窗口函数）
-- 业务问题：流量与购买人数逐日如何波动？双 12 临近的周末跳升幅度多大？
WITH daily AS (
    SELECT date,
           SUM(CASE WHEN behavior_type = 'pv' THEN 1 ELSE 0 END) AS pv,
           COUNT(DISTINCT CASE WHEN behavior_type = 'buy' THEN user_id END) AS buy_users
    FROM sample
    GROUP BY date
)
SELECT date, pv, buy_users,
       LAG(pv) OVER (ORDER BY date) AS pv_prev_day,
       ROUND(1.0 * (pv - LAG(pv) OVER (ORDER BY date))
           / NULLIF(LAG(pv) OVER (ORDER BY date), 0), 4) AS pv_growth,
       LAG(buy_users) OVER (ORDER BY date) AS buy_users_prev_day,
       ROUND(1.0 * (buy_users - LAG(buy_users) OVER (ORDER BY date))
           / NULLIF(LAG(buy_users) OVER (ORDER BY date), 0), 4) AS buy_users_growth
FROM daily
ORDER BY date;
