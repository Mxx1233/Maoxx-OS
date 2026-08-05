import os
import subprocess
import unittest
from uuid import uuid4

import psycopg
from psycopg import sql
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


@unittest.skipUnless(
    os.getenv("RUN_DATABASE_INTEGRATION_TESTS") == "1",
    "set RUN_DATABASE_INTEGRATION_TESTS=1 for isolated PostgreSQL tests",
)
class DatabaseIntegrationTests(unittest.TestCase):
    def test_clean_database_upgrades_to_head_without_drift(self) -> None:
        source_url = make_url(os.environ["DATABASE_URL"])
        database_name = f"maoxx_test_{uuid4().hex}"
        self.assertTrue(database_name.startswith("maoxx_test_"))

        admin_kwargs = {
            "host": source_url.host,
            "port": source_url.port,
            "user": source_url.username,
            "password": source_url.password,
            "dbname": "postgres",
            "autocommit": True,
        }
        test_url = source_url.set(database=database_name).render_as_string(
            hide_password=False
        )
        test_env = os.environ.copy()
        test_env["DATABASE_URL"] = test_url

        with psycopg.connect(**admin_kwargs) as connection:
            connection.execute(
                sql.SQL("CREATE DATABASE {}").format(
                    sql.Identifier(database_name)
                )
            )

        try:
            for command in (
                ["alembic", "upgrade", "head"],
                ["alembic", "current"],
                ["alembic", "heads"],
                ["alembic", "check"],
            ):
                subprocess.run(
                    command,
                    check=True,
                    env=test_env,
                    capture_output=True,
                    text=True,
                )

            engine = create_engine(test_url)
            try:
                with engine.connect() as connection:
                    revision = connection.execute(
                        text("SELECT version_num FROM alembic_version")
                    ).scalar_one()
                    indexes = set(
                        connection.execute(
                            text(
                                "SELECT indexname FROM pg_indexes "
                                "WHERE schemaname = 'core'"
                            )
                        ).scalars()
                    )
                    constraints = set(
                        connection.execute(
                            text(
                                "SELECT constraint_name "
                                "FROM information_schema.table_constraints "
                                "WHERE table_schema = 'core'"
                            )
                        ).scalars()
                    )
            finally:
                engine.dispose()

            self.assertEqual(revision, "0001_core_foundation")
            self.assertIn("ix_entities_user_type_status", indexes)
            self.assertIn("ix_raw_inputs_user_received", indexes)
            self.assertIn("uq_raw_inputs_channel_message", constraints)
        finally:
            with psycopg.connect(**admin_kwargs) as connection:
                connection.execute(
                    sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                        sql.Identifier(database_name)
                    )
                )


if __name__ == "__main__":
    unittest.main()
