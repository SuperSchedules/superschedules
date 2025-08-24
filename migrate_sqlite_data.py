import os
from pathlib import Path
from contextlib import contextmanager

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django
django.setup()

from django.conf import settings as dj_settings
from django.db import connections, transaction, DEFAULT_DB_ALIAS
from django.db.models import Model
from events.models import Source, Event


SQLITE_ALIAS = "sqlite_tmp"
SQLITE_PATH = Path(__file__).parent / "db.sqlite3"  # adjust if needed
PG_ALIAS = DEFAULT_DB_ALIAS  # "default"


def register_sqlite_alias():
    dj_settings.DATABASES[SQLITE_ALIAS] = {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": str(SQLITE_PATH),
        "USER": "",
        "PASSWORD": "",
        "HOST": "",
        "PORT": "",
        "OPTIONS": {},
        "TIME_ZONE": None,
        "CONN_MAX_AGE": 0,
        "CONN_HEALTH_CHECKS": False,
        "AUTOCOMMIT": True,
        "ATOMIC_REQUESTS": False,
        "TEST": {"NAME": None},
    }
    # Close any stale handle and register the alias
    if SQLITE_ALIAS in connections:
        connections[SQLITE_ALIAS].close()
    connections.databases[SQLITE_ALIAS] = dj_settings.DATABASES[SQLITE_ALIAS]
    # Force connect
    with connections[SQLITE_ALIAS].cursor():
        pass
    print("🔌 SQLite:", connections[SQLITE_ALIAS].settings_dict["NAME"])


@contextmanager
def pg_atomic():
    with transaction.atomic(using=PG_ALIAS):
        yield


def pg_reset_sequences(models):
    """
    Reset Postgres sequences for AutoField/BigAutoField to max(id).
    """
    from django.db import connection as default_conn
    conn = connections[PG_ALIAS]
    vendor = conn.vendor
    if vendor != "postgresql":
        print("⚠️ Skipping sequence reset (not PostgreSQL)")
        return

    with conn.cursor() as cur:
        for m in models:
            tbl = m._meta.db_table
            pk_col = m._meta.pk.column
            cur.execute(f"SELECT setval(pg_get_serial_sequence(%s,%s), COALESCE((SELECT MAX({pk_col}) FROM {tbl}), 1), TRUE);",
                        (tbl, pk_col))
    print("✅ Sequences reset.")


def copy_table_preserve_pk(ModelCls: type[Model], batch_size=1000):
    """
    Copies all rows from SQLite alias to Postgres, preserving PKs.
    Assumes PG table is empty or safe to accept these PKs.
    """
    src_qs = ModelCls.objects.using(SQLITE_ALIAS).all().order_by("pk")

    total = src_qs.count()
    if total == 0:
        print(f"— {ModelCls.__name__}: nothing to copy.")
        return 0

    # Prepare objects with same PK & field data
    fields = [f for f in ModelCls._meta.local_fields if not f.many_to_many]
    # Exclude AutoFields from kwargs, but set pk explicitly after
    non_pk_fields = [f for f in fields if not f.primary_key]

    to_create = []
    for obj in src_qs.iterator(chunk_size=batch_size):
        new_obj = ModelCls()
        # set non-pk columns
        for f in non_pk_fields:
            setattr(new_obj, f.attname, getattr(obj, f.attname))
        # preserve pk
        new_obj.pk = obj.pk
        to_create.append(new_obj)
        if len(to_create) >= batch_size:
            ModelCls.objects.using(PG_ALIAS).bulk_create(to_create, batch_size=batch_size)
            to_create.clear()

    if to_create:
        ModelCls.objects.using(PG_ALIAS).bulk_create(to_create, batch_size=batch_size)

    copied = total
    in_pg = ModelCls.objects.using(PG_ALIAS).count()
    print(f"✅ {ModelCls.__name__}: copied {copied}. Now in PG: {in_pg}")
    return copied


def migrate_events_and_sources():
    register_sqlite_alias()

    # Quick sanity: ensure tables exist in SQLite
    sqlite_tables = set(connections[SQLITE_ALIAS].introspection.table_names())
    for t in (Source._meta.db_table, Event._meta.db_table):
        assert t in sqlite_tables, f"Table {t} not found in {SQLITE_PATH}"

    with pg_atomic():
        # If PG already has data and you want to *merge*, stop here and decide a dedupe strategy.
        # Assuming empty PG tables (best case):
        n_sources = copy_table_preserve_pk(Source, batch_size=2000)
        n_events  = copy_table_preserve_pk(Event,  batch_size=2000)

    # Reset sequences so future inserts don’t collide
    pg_reset_sequences([Source, Event])

    return n_sources, n_events


if __name__ == "__main__":
    s, e = migrate_events_and_sources()
    print(f"🎉 Done. Sources: {s}, Events: {e}")

