import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import duck_mem
from scripts import train_tdx_keep_challenger_model as subject
from services.model_feature_schema import (
    BASE_FEATURE_COLS,
    DENSE_V2_FEATURE_COLS,
    TDX_KEEP_FEATURE_COLS,
)


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _all_feature_cols() -> list[str]:
    cols = []
    for col in [*BASE_FEATURE_COLS, *DENSE_V2_FEATURE_COLS, *TDX_KEEP_FEATURE_COLS]:
        if col not in cols:
            cols.append(col)
    return cols


def test_tdx_keep_load_panel_records_and_resolve_features():
    conn = duck_mem()
    feature_cols = _all_feature_cols()
    try:
        conn.execute(
            f"""
            CREATE TABLE fact_feature_panel_tdx_keep_challenger (
                feature_set_id TEXT,
                stock_code TEXT,
                date TEXT,
                forward_ret_20d DOUBLE,
                {', '.join(f'{_quote(col)} DOUBLE' for col in feature_cols)}
            )
            """
        )
        insert_cols = [
            "feature_set_id",
            "stock_code",
            "date",
            "forward_ret_20d",
            *feature_cols,
        ]
        rows = []
        for day_idx in range(10):
            day = f"2026-04-{day_idx + 1:02d}"
            for code_idx in range(2):
                rows.append((
                    "tdx_keep_challenger_v1",
                    f"00000{code_idx + 1}",
                    day,
                    float(day_idx + code_idx),
                    *[float(day_idx + code_idx) for _ in feature_cols],
                ))
        rows.append((
            "other_set",
            "000003",
            "2026-04-01",
            1.0,
            *[1.0 for _ in feature_cols],
        ))
        conn.executemany(
            f"""
            INSERT INTO fact_feature_panel_tdx_keep_challenger
            ({', '.join(_quote(col) for col in insert_cols)})
            VALUES ({', '.join('?' for _ in insert_cols)})
            """,
            rows,
        )

        panel = subject.load_panel_records(
            conn,
            "2026-04-01",
            "2026-04-10",
            feature_table="fact_feature_panel_tdx_keep_challenger",
            feature_set_id="tdx_keep_challenger_v1",
        )
        selected, schema = subject.resolve_tdx_keep_features(panel)
        train, valid, holdout = subject.split_time_series_records(panel)

        assert len(panel) == 20
        assert schema == "m8_tdx_keep_challenger_v1"
        assert all(col in selected for col in TDX_KEEP_FEATURE_COLS)
        assert len({row["date"] for row in train}) == 7
        assert len({row["date"] for row in valid}) == 1
        assert len({row["date"] for row in holdout}) == 2
    finally:
        conn.close()


def test_sample_and_prediction_rows_are_deterministic():
    rows = [
        {"stock_code": f"00000{idx}", "date": f"2026-04-{idx:02d}"}
        for idx in range(1, 8)
    ]

    sample = subject._sample(rows, 3)
    preds = subject._prediction_rows(
        "model_1",
        [
            {"stock_code": "000001", "date": "2026-04-01"},
            {"stock_code": "000002", "date": "2026-04-01"},
            {"stock_code": "000003", "date": "2026-04-01"},
        ],
        [0.2, 0.5, 0.5],
    )

    assert sample == [
        {"stock_code": "000001", "date": "2026-04-01"},
        {"stock_code": "000006", "date": "2026-04-06"},
        {"stock_code": "000007", "date": "2026-04-07"},
    ]
    assert preds == [
        ("model_1", "000001", "2026-04-01", 0.2, 3, pytest.approx(1 / 3)),
        ("model_1", "000002", "2026-04-01", 0.5, 1, pytest.approx(5 / 6)),
        ("model_1", "000003", "2026-04-01", 0.5, 1, pytest.approx(5 / 6)),
    ]
