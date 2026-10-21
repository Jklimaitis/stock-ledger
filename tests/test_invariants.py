"""As invariantes que o banco precisa garantir sozinho.

Se algum destes testes passar por causa de código Python, ele está no lugar
errado: aqui a asserção é sobre a constraint, o índice e o trigger.
"""

from __future__ import annotations

import psycopg
import pytest


def test_disponivel_nunca_fica_negativo(conn, produto):
    """Invariante 1: reservar acima do estoque físico é recusado pelo banco."""
    with pytest.raises(psycopg.errors.CheckViolation):
        with conn.transaction():
            conn.execute(
                "UPDATE product_stock SET reserved = on_hand + 1 WHERE product_id = %s",
                (produto,),
            )


def test_quantidade_de_movimento_e_sempre_positiva(conn, produto):
    """O sinal vem do `kind`; aceitar quantidade negativa criaria combinações inválidas."""
    with pytest.raises(psycopg.errors.CheckViolation):
        with conn.transaction():
            conn.execute(
                "INSERT INTO stock_movements (product_id, kind, quantity, reason) "
                "VALUES (%s, 'inbound', 0, 'teste')",
                (produto,),
            )


def test_movimento_de_reserva_exige_reserva(conn, produto):
    """`reserve` sem `reservation_id` é lixo: a constraint amarra tipo e vínculo."""
    with pytest.raises(psycopg.errors.CheckViolation):
        with conn.transaction():
            conn.execute(
                "INSERT INTO stock_movements (product_id, kind, quantity, reason) "
                "VALUES (%s, 'reserve', 1, 'teste')",
                (produto,),
            )


def test_movimento_de_entrada_proibe_reserva(conn, produto):
    """O outro lado da mesma constraint: `inbound` não pode apontar para reserva."""
    oid = conn.execute("INSERT INTO orders DEFAULT VALUES RETURNING id").fetchone()[0]
    rid = conn.execute(
        "INSERT INTO reservations (order_id, product_id, quantity, expires_at) "
        "VALUES (%s, %s, 1, now() + interval '10 minutes') RETURNING id",
        (oid, produto),
    ).fetchone()[0]
    with pytest.raises(psycopg.errors.CheckViolation):
        with conn.transaction():
            conn.execute(
                "INSERT INTO stock_movements (product_id, kind, quantity, reason, reservation_id) "
                "VALUES (%s, 'inbound', 1, 'teste', %s)",
                (produto, rid),
            )


def test_movimento_e_append_only(conn, produto):
    """Invariante 4: história não se reescreve. O trigger recusa UPDATE."""
    with conn.transaction():
        mid = conn.execute(
            "INSERT INTO stock_movements (product_id, kind, quantity, reason) "
            "VALUES (%s, 'inbound', 5, 'carga inicial') RETURNING id",
            (produto,),
        ).fetchone()[0]

    with pytest.raises(psycopg.errors.RestrictViolation):
        with conn.transaction():
            conn.execute("UPDATE stock_movements SET quantity = 99 WHERE id = %s", (mid,))


def test_uma_reserva_ativa_por_pedido_e_produto(conn, produto):
    """Invariante 5 no nível do banco: o índice único parcial, não um `if`."""
    oid = conn.execute("INSERT INTO orders DEFAULT VALUES RETURNING id").fetchone()[0]
    sql = (
        "INSERT INTO reservations (order_id, product_id, quantity, expires_at) "
        "VALUES (%s, %s, 1, now() + interval '10 minutes')"
    )
    conn.execute(sql, (oid, produto))

    with pytest.raises(psycopg.errors.UniqueViolation):
        with conn.transaction():
            conn.execute(sql, (oid, produto))


def test_reserva_cancelada_libera_nova_tentativa(conn, produto):
    """O índice é parcial de propósito: reserva cancelada não bloqueia o pedido."""
    oid = conn.execute("INSERT INTO orders DEFAULT VALUES RETURNING id").fetchone()[0]
    sql = (
        "INSERT INTO reservations (order_id, product_id, quantity, status, expires_at) "
        "VALUES (%s, %s, 1, %s, now() + interval '10 minutes') RETURNING id"
    )
    conn.execute(sql, (oid, produto, "cancelled"))
    conn.execute(sql, (oid, produto, "active"))

    total = conn.execute(
        "SELECT count(*) FROM reservations WHERE order_id = %s", (oid,)
    ).fetchone()[0]
    assert total == 2
