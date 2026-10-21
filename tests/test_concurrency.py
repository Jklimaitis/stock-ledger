"""O teste-assinatura: disputa pela última unidade com conexões reais.

Nada aqui usa mock. A prova só vale porque são duas conexões distintas no
servidor, com bloqueio de linha de verdade entre elas.
"""

from __future__ import annotations

import threading

import psycopg

GUARDA = (
    "UPDATE product_stock SET reserved = reserved + 1 "
    "WHERE product_id = %s AND on_hand - reserved >= 1 "
    "RETURNING reserved"
)


def _disputar(dsn: str, produto: int, quantos: int) -> list[bool]:
    """`quantos` threads tentam reservar 1 unidade ao mesmo tempo."""
    resultados: list[bool] = []
    barreira = threading.Barrier(quantos)

    def tentar() -> None:
        with psycopg.connect(dsn) as conexao:
            barreira.wait()
            linha = conexao.execute(GUARDA, (produto,)).fetchone()
            conexao.commit()
            resultados.append(linha is not None)

    threads = [threading.Thread(target=tentar) for _ in range(quantos)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    return resultados


def test_duas_conexoes_disputando_a_ultima_unidade(dsn, produto):
    """Uma sucede, a outra recebe zero linhas. Nunca as duas."""
    resultados = _disputar(dsn, produto, 2)

    assert len(resultados) == 2
    assert sum(resultados) == 1, f"esperava exatamente 1 vencedor, veio {resultados}"

    with psycopg.connect(dsn) as conexao:
        reservado = conexao.execute(
            "SELECT reserved, available FROM product_stock WHERE product_id = %s",
            (produto,),
        ).fetchone()
    assert reservado == (1, 0)


def test_cinco_disputam_tres_unidades(dsn, vazio):
    """Com concorrência maior, o número de vencedores tem de ser exato."""
    with psycopg.connect(dsn, autocommit=True) as conexao:
        pid = conexao.execute(
            "INSERT INTO products (name, unit) VALUES ('caderno', 'un') RETURNING id"
        ).fetchone()[0]
        conexao.execute(
            "INSERT INTO product_stock (product_id, on_hand, reserved) VALUES (%s, 3, 0)",
            (pid,),
        )

    resultados = _disputar(dsn, pid, 5)

    assert sum(resultados) == 3, f"esperava 3 vencedores, veio {sum(resultados)}"
    with psycopg.connect(dsn) as conexao:
        assert conexao.execute(
            "SELECT reserved FROM product_stock WHERE product_id = %s", (pid,)
        ).fetchone()[0] == 3


def test_sem_a_guarda_o_banco_estoura_em_vez_de_recusar_limpo(dsn, vazio):
    """Sem o `WHERE on_hand - reserved >= q`, o perdedor recebe erro, não negativa.

    Com a guarda, quem perde recebe zero linhas e a API responde 409 "sem
    estoque". Sem a guarda, o UPDATE passa e quem barra é a constraint: o
    cliente recebe erro de banco. Mesmo desfecho de negócio, resposta errada.
    """
    with psycopg.connect(dsn, autocommit=True) as conexao:
        pid = conexao.execute(
            "INSERT INTO products (name, unit) VALUES ('borracha', 'un') RETURNING id"
        ).fetchone()[0]
        conexao.execute(
            "INSERT INTO product_stock (product_id, on_hand, reserved) VALUES (%s, 1, 0)",
            (pid,),
        )

    resultados: list[str] = []
    barreira = threading.Barrier(2)

    def tentar_sem_guarda() -> None:
        with psycopg.connect(dsn) as conexao:
            barreira.wait()
            try:
                conexao.execute(
                    "UPDATE product_stock SET reserved = reserved + 1 WHERE product_id = %s",
                    (pid,),
                )
                conexao.commit()
                resultados.append("ok")
            except psycopg.errors.CheckViolation:
                conexao.rollback()
                resultados.append("erro-de-constraint")

    threads = [threading.Thread(target=tentar_sem_guarda) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(resultados) == ["erro-de-constraint", "ok"], resultados

