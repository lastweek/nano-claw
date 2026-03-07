"""Migration tests for HTTP persistence schema upgrades."""

from src.database.connection import initialize_database, managed_database_connection
from src.database.session_database import SessionDatabase


def test_migrate_runs_schema_to_turns(temp_dir):
    """Old runs/run_id schema should migrate to turns/turn_id."""
    db_path = temp_dir / "state.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    with managed_database_connection(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                summary_text TEXT,
                summary_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE runs (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                status TEXT NOT NULL,
                input_text TEXT NOT NULL,
                final_output TEXT,
                error_text TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                ended_at TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE messages (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO sessions(id, title, summary_text, summary_json, created_at, updated_at)
            VALUES ('sess_old', 'Old', NULL, NULL, '2026-03-05T00:00:00', '2026-03-05T00:00:00')
            """
        )
        connection.execute(
            """
            INSERT INTO runs(id, session_id, status, input_text, final_output, error_text, created_at, started_at, ended_at, updated_at)
            VALUES ('run_old', 'sess_old', 'completed', 'hello', 'world', NULL, '2026-03-05T00:00:00', '2026-03-05T00:00:01', '2026-03-05T00:00:02', '2026-03-05T00:00:02')
            """
        )
        connection.execute(
            """
            INSERT INTO messages(id, session_id, run_id, seq, role, content, created_at)
            VALUES ('msg_old', 'sess_old', 'run_old', 1, 'user', 'hello', '2026-03-05T00:00:00')
            """
        )
        connection.execute("PRAGMA user_version = 1")
        connection.commit()

    initialize_database(db_path)
    database = SessionDatabase(db_path)
    turn = database.get_turn("run_old")
    assert turn is not None
    assert turn.status == "completed"

    with managed_database_connection(db_path) as connection:
        tables = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert "turns" in tables
        assert "runs" not in tables

        session_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(sessions)").fetchall()
        }
        assert "state" in session_columns
        assert "closed_at" in session_columns

        message_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(messages)").fetchall()
        }
        assert "turn_id" in message_columns
        assert "run_id" not in message_columns
