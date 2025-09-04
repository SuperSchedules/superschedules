from django.test.runner import DiscoverRunner
from django.db import connections

class PgOnlyRunner(DiscoverRunner):
    def setup_databases(self, **kwargs):
        conn = connections["default"]
        conn.ensure_connection()

        if conn.vendor != "postgresql":
            raise RuntimeError("Tests must run on PostgreSQL (not SQLite).")

        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_available_extensions WHERE name='vector'")
            if cur.fetchone() is None:
                raise RuntimeError("pgvector extension is not installed on the server.")

        return super().setup_databases(**kwargs)

    def teardown_databases(self, old_config, **kwargs):
        # ensure the drop works even if something holds a session
        from django.db import connections as conns
        for alias in conns:
            c = conns[alias]
            if c.vendor != "postgresql":
                continue
            try:
                c.ensure_connection()
                dbname = c.settings_dict["NAME"]
                with c.cursor() as cur:
                    cur.execute("""
                        SELECT pid FROM pg_stat_activity
                        WHERE datname = %s AND pid <> pg_backend_pid()
                    """, [dbname])
                    pids = [r[0] for r in cur.fetchall()]
                    for pid in pids:
                        cur.execute("SELECT pg_terminate_backend(%s)", [pid])
            except Exception:
                pass

        return super().teardown_databases(old_config, **kwargs)
 
