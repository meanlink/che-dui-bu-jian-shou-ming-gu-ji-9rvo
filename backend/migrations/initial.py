from sqlalchemy import inspect, text
from src.config.database import Base, engine
from src import models
from src.models.report import Report


def _add_column(table, column, ddl):
    inspector = inspect(engine)
    if table not in inspector.get_table_names():
        return
    if column in {item['name'] for item in inspector.get_columns(table)}:
        return
    with engine.begin() as connection:
        connection.execute(text(f'ALTER TABLE {table} ADD COLUMN {column} {ddl}'))


def migrate():
    Base.metadata.create_all(engine)
    # Distributions may now originate from a passed acceptance case instead of an
    # analysis job, so the job reference has to accept nulls on older databases.
    _add_column('reports', 'acceptance_case_id', 'INTEGER REFERENCES acceptance_cases(id)')
    if engine.dialect.name == 'postgresql':
        with engine.begin() as connection:
            connection.execute(text('ALTER TABLE reports ALTER COLUMN job_id DROP NOT NULL'))
    elif engine.dialect.name == 'sqlite':
        _sqlite_relax_job_id()


def _sqlite_relax_job_id():
    """SQLite cannot drop NOT NULL; rebuild the table while keeping child FKs."""
    inspector = inspect(engine)
    if 'reports' not in inspector.get_table_names():
        return
    columns = {column['name']: column for column in inspector.get_columns('reports')}
    if 'job_id' not in columns or columns['job_id']['nullable']:
        return
    with engine.begin() as connection:
        connection.execute(text('PRAGMA legacy_alter_table=ON'))
        connection.execute(text('ALTER TABLE reports RENAME TO reports_legacy'))
        Report.__table__.create(connection)
        connection.execute(text(
            'INSERT INTO reports (id, name, job_id, acceptance_case_id, destination, '
            'interval_seconds, next_run, enabled) SELECT id, name, job_id, acceptance_case_id, '
            'destination, interval_seconds, next_run, enabled FROM reports_legacy'))
        connection.execute(text('DROP TABLE reports_legacy'))
        connection.execute(text('PRAGMA legacy_alter_table=OFF'))


if __name__ == '__main__':
    migrate()
