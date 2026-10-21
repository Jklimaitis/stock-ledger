# stock-ledger

Estoque com reserva de unidades que **não permite prometer mais mercadoria do que existe**, em nenhuma ordem de execução concorrente.

Este projeto existe para provar uma coisa só, e ela é a mais difícil do backend: que a regra de negócio está garantida pelo banco, não pela boa vontade do código acima. Tudo o mais é consequência.

## As seis invariantes

O sistema garante, sempre:

1. O estoque disponível nunca fica negativo.
2. Uma reserva nunca reserva mais do que está livre no instante em que é criada.
3. Uma reserva vencida deixa de segurar estoque sem intervenção manual.
4. Toda alteração de estoque é um movimento imutável com motivo. Não existe `UPDATE` de quantidade; correção se faz com movimento contrário.
5. A mesma requisição enviada duas vezes produz uma reserva, não duas.
6. A soma dos movimentos é sempre igual ao contador de saldo.

As invariantes 3 e 6 não podem ser constraint: uma depende de passagem de tempo, a outra é detectável e não prevenível. Elas entram por varredura e por comando de reconciliação — e a distinção entre *impossibilitar* e *detectar* é parte do que este projeto ensina.

## Arquitetura: o mesmo fato em dois registros

| Tabela | Papel |
|---|---|
| `stock_movements` | append-only, a verdade auditável: entrada, saída, reserva, liberação |
| `product_stock` | contador materializado, uma linha por produto — **mecanismo de guarda** |

A razão é técnica: constraint só existe sobre coluna, nunca sobre `SUM()`. Sem o contador, a invariante 1 não seria exprimível como `CHECK`. Com ele, a invariante inteira cabe em `reserved <= on_hand`, sobre a coluna gerada `available`.

O contador não é fonte de verdade. A divergência entre os dois é o que a reconciliação detecta — e é bug, não manutenção.

### A operação crítica

```sql
UPDATE product_stock
   SET reserved = reserved + 1
 WHERE product_id = $1
   AND on_hand - reserved >= 1
RETURNING reserved;
```

Zero linhas significa estoque insuficiente. Sem `SELECT` antes, sem `if`, sem janela entre ler e gravar: o `WHERE` e o `UPDATE` acontecem sob o mesmo bloqueio de linha, e o `CHECK` fica como última barreira.

## Rodando

Precisa de Docker e Python 3.12+.

```bash
docker compose up -d          # Postgres 16 em 127.0.0.1:55432
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
./scripts/apply_db.sh         # aplica db/*.sql no banco de dev
python -m pytest -q           # a suíte cria o próprio banco descartável
```

## A prova de concorrência

O teste que dá sentido ao repositório abre **duas conexões reais** e disputa a última unidade:

```bash
python -m pytest tests/test_concurrency.py -v
```

```
test_duas_conexoes_disputando_a_ultima_unidade PASSED
test_cinco_disputam_tres_unidades PASSED
test_sem_a_guarda_o_banco_estoura_em_vez_de_recusar_limpo PASSED
```

O terceiro é o mais instrutivo: mostra que, sem o `WHERE` de guarda, quem perde a corrida recebe **erro de constraint** em vez de uma resposta limpa de "sem estoque". Mesmo desfecho de negócio, resposta errada para o cliente.

Nenhum teste usa mock de banco. Mock não tem bloqueio de linha, e é justamente o bloqueio que está sendo provado.

## Estado

Primeiro degrau do projeto: **núcleo sem HTTP**. O schema, as invariantes e a concorrência estão de pé e testados. Ainda não existem API, varredura de vencimento nem serviço.

| Degrau | Estado |
|---|---|
| 1. Núcleo sem HTTP | em andamento — schema, constraints e concorrência prontos |
| 2. API em FastAPI | não iniciado |
| 3. Entrada em lote por CSV | não iniciado |
| 4. Expiração e assíncrono | não iniciado |
| 5. Operação | CI pronto; deploy e métricas pendentes |

## Licença

MIT — veja [LICENSE](LICENSE).
