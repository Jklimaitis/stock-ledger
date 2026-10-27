"""Transições da reserva: o teste que falha primeiro.

Ordem de trabalho deste arquivo (TDD):
  1. `test_reserva_feliz...`  <- escrito, deve falhar agora (NotImplementedError)
  2. `test_replay...`         <- voce escreve depois de fazer o passo 1 e 5
  3. `test_sem_estoque...`    <- voce escreve depois do passo 3
  4. `test_chave_reusada...`  <- voce escreve depois do passo 1

Rode um teste por vez enquanto implementa:
    .venv/bin/python -m pytest tests/test_transicoes.py::test_reserva_feliz_debita_e_registra_movimento -x -q
"""

from __future__ import annotations

import hashlib
from datetime import timedelta

import pytest

from services.stock import ChaveIdempotenciaReusada, EstoqueInsuficiente, reserve


def hash_do_corpo(corpo: str = "{}") -> str:
    """Simula o hash canonico do corpo que a borda HTTP vai calcular."""
    return hashlib.sha256(corpo.encode()).hexdigest()


def test_reserva_feliz_debita_e_registra_movimento(conn, produto):
    """O caminho completo: reserva criada, contador debitado, fato registrado.

    Este teste e o alvo do passo 1 ao 5. Enquanto ele estiver vermelho por
    NotImplementedError, o resto nao importa.
    """
    pedido = conn.execute("INSERT INTO orders DEFAULT VALUES RETURNING id").fetchone()[0]

    reserva, criada = reserve(
        conn,
        order_id=pedido,
        product_id=produto,
        quantity=1,
        ttl=timedelta(minutes=15),
        idempotency_key="chave-1",
        request_hash=hash_do_corpo(),
    )

    # o retorno
    assert criada is True, "primeira vez com esta chave tem que criar"
    assert reserva.status == "active"
    assert reserva.order_id == pedido
    assert reserva.product_id == produto
    assert reserva.quantity == 1

    # o contador debitou (invariante 2 vista pelo contador)
    reservado = conn.execute(
        "SELECT reserved FROM product_stock WHERE product_id = %s", (produto,)
    ).fetchone()[0]
    assert reservado == 1

    # o fato imutavel existe, com o kind certo e quantidade positiva
    movimentos = conn.execute(
        "SELECT kind, quantity FROM stock_movements WHERE reservation_id = %s",
        (reserva.id,),
    ).fetchall()
    assert movimentos == [("reserve", 1)]

    # a chave aponta para a reserva (passo 5)
    aponta = conn.execute(
        "SELECT reservation_id FROM idempotency_keys WHERE key = %s", ("chave-1",)
    ).fetchone()[0]
    assert aponta == reserva.id


# ---------------------------------------------------------------------------
# Escreva estes tres. Cada um exercita um passo especifico.
# ---------------------------------------------------------------------------

def test_replay_devolve_a_mesma_reserva_sem_criar_outra(conn, produto):
    """Chave igual + corpo igual -> (mesma reserva, False) e o banco tem 1 so."""
    
    pedido = conn.execute("INSERT INTO orders DEFAULT VALUES RETURNING id").fetchone()[0]

    primeira, criada_na_primeira = reserve(
        conn,
        order_id=pedido,
        product_id=produto,
        quantity=1,
        ttl=timedelta(minutes=15),
        idempotency_key="chave-1",
        request_hash=hash_do_corpo(),
    )

    segunda, criada_na_segunda = reserve(
        conn,
        order_id=pedido,             # o MESMO pedido
        product_id=produto,
        quantity=1,
        ttl=timedelta(minutes=15),
        idempotency_key="chave-1",   # a MESMA chave
        request_hash=hash_do_corpo(),  # o MESMO hash
    )

    assert criada_na_primeira is True
    assert criada_na_segunda is False, "a segunda entrega tem que ser replay"
    assert segunda.id == primeira.id, "tem que ser a mesma reserva"
    assert segunda.order_id == pedido

    total = conn.execute("SELECT count(*) FROM reservations").fetchone()[0]
    assert total == 1, "o replay nao pode criar uma segunda reserva"


def test_sem_estoque_nao_deixa_rastro(conn, produto):
    """Pedir 2 com 1 disponivel -> EstoqueInsuficiente, e NADA gravado."""
    # estado inicial: um pedido novo, nada reservado
    pedido = conn.execute("INSERT INTO orders DEFAULT VALUES RETURNING id").fetchone()[0]

    # a chamada que DEVE falhar, dentro do context manager
    with pytest.raises(EstoqueInsuficiente) as excecao:
        reserve(
            conn,
            order_id=pedido,
            product_id=produto,
            quantity=2,
            ttl=timedelta(minutes=15),
            idempotency_key="chave-sem-estoque",
            request_hash=hash_do_corpo(),
        )

    # o motivo da recusa, com os dois numeros
    assert excecao.value.disponivel == 1
    assert excecao.value.solicitado == 2

    # o rastro: tres contagens, todas zero
    assert conn.execute(
        '''SELECT count(*) FROM reservations'''
    ).fetchone()[0] == 0   # reservations
    assert conn.execute(
        '''SELECT count(*) FROM stock_movements'''
    ).fetchone()[0] == 0   # stock_movements
    assert conn.execute(
        '''SELECT count(*) FROM idempotency_keys'''
    ).fetchone()[0] == 0   # idempotency_keys



def test_chave_reusada_com_corpo_diferente_e_recusada(conn, produto):
    """Mesma chave, hash diferente -> ChaveIdempotenciaReusada."""

    pedido = conn.execute("INSERT INTO orders DEFAULT VALUES RETURNING id").fetchone()[0]

    primeira, criada_na_chave1 = reserve(
        conn,
        order_id=pedido,
        product_id=produto,
        quantity=1,
        ttl=timedelta(minutes=15),
        idempotency_key="chave-1",
        request_hash=hash_do_corpo(),
    )   

        # o retorno
    assert criada_na_chave1 is True, "primeira vez com esta chave tem que criar"
    assert primeira.status == "active"
    assert primeira.order_id == pedido
    assert primeira.product_id == produto
    assert primeira.quantity == 1

    with pytest.raises(ChaveIdempotenciaReusada):
        reserve(
            conn,
            order_id=pedido,
            product_id=produto,
            quantity=2,
            ttl=timedelta(minutes=15),
            idempotency_key="chave-1",
            request_hash=hash_do_corpo('{"quantity": 2}'),
        )

    total = conn.execute("SELECT count(*) FROM reservations").fetchone()[0]
    assert total == 1, "a chave recusada nao pode ter criado uma segunda reserva"
