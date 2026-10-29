from app.db import _sqlalchemy_database_url


def test_postgres_urls_use_psycopg_driver():
    assert _sqlalchemy_database_url("postgres://user:pass@host/db?sslmode=require") == (
        "postgresql+psycopg://user:pass@host/db?sslmode=require"
    )
    assert _sqlalchemy_database_url("postgresql://user:pass@host/db") == (
        "postgresql+psycopg://user:pass@host/db"
    )


def test_non_postgres_database_url_is_unchanged():
    assert _sqlalchemy_database_url("sqlite:///./payday.db") == "sqlite:///./payday.db"
