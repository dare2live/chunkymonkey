-- P2: fact_model_train_log — 写 LightGBM retrain 时 IS RankIC + train metrics
-- 解锁 phase4 gate IS-OOS 真接 (commit 8005a849 之前是 split-half proxy)

CREATE TABLE IF NOT EXISTS fact_model_train_log (
    model_id VARCHAR NOT NULL,
    run_id VARCHAR NOT NULL,
    model_version VARCHAR,
    feature_version VARCHAR,
    label_version VARCHAR,
    train_start DATE,
    train_end DATE,
    n_train_rows INTEGER,
    n_features INTEGER,
    is_rank_ic DOUBLE,          -- in-sample Spearman RankIC (train period)
    is_rank_ic_ir DOUBLE,       -- IS RankIC IR (mean / std × sqrt(n))
    is_ndcg5 DOUBLE,
    is_ndcg10 DOUBLE,
    is_ndcg20 DOUBLE,
    oos_rank_ic_avg DOUBLE,     -- OOS mean RankIC (跨 walk-forward windows)
    oos_rank_ic_ir DOUBLE,
    seed INTEGER,
    n_trials INTEGER,
    n_windows INTEGER,
    optuna_best_value DOUBLE,
    walk_forward_mode VARCHAR,
    metrics_json VARCHAR,        -- additional metrics (NDCG / Sharpe / etc.)
    built_at VARCHAR NOT NULL,
    PRIMARY KEY (model_id, run_id)
);

COMMENT ON TABLE fact_model_train_log IS
    'P2 (Phase 5): LightGBM/LambdaMART retrain log 含 IS RankIC + OOS RankIC + Optuna metrics. 解锁 phase4 gate IS-OOS 真接.';
