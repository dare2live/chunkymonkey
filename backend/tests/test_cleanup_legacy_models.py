import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import duck_mem
from scripts import cleanup_legacy_models as subject


def test_cleanup_candidates_never_include_champion_or_challenger():
    conn = duck_mem()
    try:
        conn.executescript(
            """
            CREATE TABLE mart_multidim_model (
                model_id TEXT,
                created_at TEXT,
                n_features INTEGER
            );
            CREATE TABLE mart_model_lifecycle (
                model_id TEXT,
                status TEXT
            );
            INSERT INTO mart_multidim_model VALUES
                ('champion_model', '2026-05-01', 54),
                ('challenger_model', '2026-05-02', 54),
                ('retired_model', '2026-04-01', 110),
                ('cleanup_recent_model', '2026-05-03', 54);
            INSERT INTO mart_model_lifecycle VALUES
                ('champion_model', 'champion'),
                ('challenger_model', 'challenger'),
                ('retired_model', 'retired');
            """
        )

        candidates = subject.candidate_models(conn)
        candidate_ids = {item["model_id"] for item in candidates}

        assert "champion_model" not in candidate_ids
        assert "challenger_model" not in candidate_ids
        assert "retired_model" in candidate_ids
        assert "cleanup_recent_model" in candidate_ids
    finally:
        conn.close()
