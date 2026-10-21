"""Fixtures dos testes: um banco descartável por sessão, criado a partir de db/*.sql.

Nenhum teste usa mock de banco. O ponto do projeto é comportamento sob
concorrência e constraint, e mock não tem bloqueio de linha.
"""

from __future__ import annotations

import os
import pathlib

import psycopg
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
DB_FILES = sorted((ROOT / "db").glob("*.sql"))

ADMIN_DSN = os.environ.get(
    "STOCK_ADMIN_DSN", "postgresql://stock:stock@127.0.0.1:55432/postgres"
)
TEST_DB = "stock_ledger_test"


def _dsn_para(banco: str) -> str:
    return ADMIN_DSN.rsplit("/", 1)[0] + "/" + banco


@pytest.fixture(scope="session")
def dsn() -> str:
    """Cria o banco de teste do zero e aplica todas as migrações."""
    with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB}")
        admin.execute(f"CREATE DATABASE {TEST_DB}")

    alvo = _dsn_para(TEST_DB)
    with psycopg.connect(alvo, autocommit=True) as conn:
        for arquivo in DB_FILES:
            with conn.transaction():
                conn.execute(arquivo.read_text(encoding="utf-8"))
    return alvo


@pytest.fixture()
def conn(dsn: str):
    """Conexão limpa por teste; o rollback garante isolamento."""
    with psycopg.connect(dsn) as conexao:
        yield conexao
        conexao.rollback()


@pytest.fixture()
def vazio(dsn: str):
    """Zera as tabelas de negócio entre testes que precisam de estado conhecido."""
    with psycopg.connect(dsn, autocommit=True) as conexao:
        conexao.execute(
            "TRUNCATE stock_movements, reservations, orders, product_stock, "
            "products, idempotency_keys RESTART IDENTITY CASCADE"
        )


@pytest.fixture()
def produto(dsn: str, vazio) -> int:
    """Um produto com 1 unidade em estoque e nada reservado."""
    with psycopg.connect(dsn, autocommit=True) as conexao:
        pid = conexao.execute(
            "INSERT INTO products (name, unit) VALUES ('caneta', 'un') RETURNING id"
        ).fetchone()[0]
        conexao.execute(
            "INSERT INTO product_stock (product_id, on_hand, reserved) VALUES (%s, 1, 0)",
            (pid,),
        )
    return pid
