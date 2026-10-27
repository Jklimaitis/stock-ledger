"""Regras de estoque e reserva.

Este módulo é a camada de aplicação: é aqui que a transação começa e termina.
Nada de FastAPI, nada de HTTP — só domínio e SQL explícito.

Contrato do módulo (decidido em 2026-10-22):

* a transação mora AQUI, não no repositório e não na rota;
* cada desfecho recusado tem a sua exceção, porque cada uma vira um status
  HTTP diferente na borda:

      EstoqueInsuficiente   -> 409
      ProdutoInexistente    -> 404
      ChaveIdempotenciaReusada -> 409 (corpo diferente na mesma chave)

* o retorno de `reserve` é `(reserva, criada_agora)`: o booleano é o que
  permite responder 201 na criação e 200 no replay, sem inventar;
* `stock_movements` nunca recebe quantidade negativa — o sinal vem do `kind`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import psycopg


# ---------------------------------------------------------------------------
# Desfechos recusados. Cada um existe porque vira um status diferente.
# ---------------------------------------------------------------------------
class EstoqueInsuficiente(Exception):
    """Não há disponível suficiente. Vira 409 na borda."""

    def __init__(self, disponivel: int, solicitado: int) -> None:
        super().__init__(f"disponivel={disponivel}, solicitado={solicitado}")
        self.disponivel = disponivel
        self.solicitado = solicitado


class ProdutoInexistente(Exception):
    """O produto não existe em product_stock. Vira 404 na borda."""

    def __init__(self, product_id: int) -> None:
        super().__init__(f"product_id={product_id}")
        self.product_id = product_id


class ChaveIdempotenciaReusada(Exception):
    """Mesma Idempotency-Key com corpo diferente. Vira 409 na borda."""

    def __init__(self, key: str) -> None:
        super().__init__(f"key={key}")
        self.key = key


# Motivos gravados no ledger. Texto único para não divergir entre as transições
MOTIVO_RESERVA = "reserva criada"


@dataclass(frozen=True)
class Reserva:
    """A reserva como o domínio a enxerga — não como a linha do banco."""

    id: int
    order_id: int
    product_id: int
    quantity: int
    status: str
    expires_at: datetime


def _linha_para_reserva(linha: tuple) -> Reserva:
    """(id, order_id, product_id, quantity, status, expires_at) -> Reserva."""
    return Reserva(*linha)


# ---------------------------------------------------------------------------
# Transições
# ---------------------------------------------------------------------------
def reserve(
    conn: psycopg.Connection,
    *,
    order_id: int,
    product_id: int,
    quantity: int,
    ttl: timedelta,
    idempotency_key: str,
    request_hash: str,
) -> tuple[Reserva, bool]:
    """Reserva `quantity` unidades, ou devolve a reserva já criada para a chave.

    Retorna (reserva, criada_agora). Criada_agora=False significa replay: a
    mesma chave, com o mesmo corpo, já tinha produzido esta reserva.
    """
    colunas = "id, order_id, product_id, quantity, status, expires_at"

    with conn.transaction():
        res = conn.execute(
            """
            INSERT INTO idempotency_keys (key, request_hash)
            VALUES (%s, %s)
            ON CONFLICT (key) DO NOTHING
            RETURNING key
            """,
            (idempotency_key, request_hash),
        ).fetchone()

        if res is None:
            existente = conn.execute(
                """
                SELECT request_hash, reservation_id
                FROM idempotency_keys
                WHERE key = %s
                """,
                (idempotency_key,),
            ).fetchone()

            if existente is None or existente[0] != request_hash:
                raise ChaveIdempotenciaReusada(idempotency_key)

            linha = conn.execute(
                f"SELECT {colunas} FROM reservations WHERE id = %s",
                (existente[1],),
            ).fetchone()
            assert linha is not None
            return _linha_para_reserva(linha), False

        linha_criada = conn.execute(
            """
            INSERT INTO reservations (order_id, product_id, quantity, expires_at)
            VALUES (%s, %s, %s, now() + %s)
            RETURNING id, order_id, product_id, quantity, status, expires_at
            """,
            (order_id, product_id, quantity, ttl),
        ).fetchone()

        assert linha_criada is not None
        reserva = _linha_para_reserva(linha_criada)

        novo_reserved = conn.execute(
            """
            UPDATE product_stock
            SET reserved = reserved + %s, updated_at = now()
            WHERE product_id = %s
            AND on_hand - reserved >= %s
            RETURNING reserved
            """,
            (quantity, product_id, quantity),
        ).fetchone()

        if novo_reserved is None:
            linha_estoque = conn.execute(
                "SELECT on_hand, reserved FROM product_stock WHERE product_id = %s",
                (product_id,),
            ).fetchone()

            if linha_estoque is None:
                raise ProdutoInexistente(product_id)

            on_hand, reserved = linha_estoque # unpacking para linha_estoque 
            raise EstoqueInsuficiente(disponivel=on_hand - reserved, solicitado=quantity)

        conn.execute(
            """
            INSERT INTO stock_movements
            (product_id, kind, quantity, reservation_id, reason)
            VALUES (%s, 'reserve', %s, %s, %s)
            """,
            (
                product_id,
                quantity,
                reserva.id,
                MOTIVO_RESERVA,
            ),  # reserva.id pega do objeto da dataclass do linha_criada, vulgo 'reserva = _linha_para_reserva(linha_criada)'
        )

        conn.execute(
            """
            UPDATE idempotency_keys SET reservation_id = %s WHERE key = %s
            """,
            (reserva.id, idempotency_key),
        )

        # Se o fluxo chegou aqui, os cinco passos rodaram. Devolva (reserva, True).
        return reserva, True


def confirm(conn: psycopg.Connection, *, reservation_id: int) -> Reserva:
    """Confirma a reserva: a mercadoria sai. `release` + `outbound`.

    Regra da casa (spec, seção de transições): a confirmação gera DOIS
    movimentos. Registrar só `outbound` faria o `reserved` derivado dos
    movimentos divergir do contador, e a reconciliação acusaria bug inexistente.
    """
    raise NotImplementedError("incremento 1: confirm")


def cancel(conn: psycopg.Connection, *, reservation_id: int) -> Reserva:
    """Cancela a reserva ativa e devolve a quantidade: um movimento `release`."""
    raise NotImplementedError("incremento 1: cancel")


def expire(conn: psycopg.Connection, *, reservation_id: int) -> Reserva:
    """Expira uma reserva vencida. Mesma mecânica do cancel, motivo diferente."""
    raise NotImplementedError("incremento 1: expire")
