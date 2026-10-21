-- 001 — schema inicial do stock-ledger
--
-- Fonte: a especificação do projeto (projetos/estoque-reserva-spec-2026-10-21.md
-- na base privada). Convenções: identificadores em inglês, tabelas no plural,
-- toda constraint nomeada com pk_/fk_/uk_/ck_.
--
-- Este arquivo não abre transação: quem aplica envolve os arquivos em uma
-- transação por arquivo (ver scripts/apply_db.sh e o runner dos testes).
--
-- A ordem importa: reservations antes de stock_movements (FK), e product_stock
-- por último entre as tabelas de negócio.

CREATE TABLE products (
    id          bigint GENERATED ALWAYS AS IDENTITY,
    name        text        NOT NULL,
    unit        text        NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT pk_products PRIMARY KEY (id),
    CONSTRAINT uk_products_name UNIQUE (name),
    CONSTRAINT ck_products_name_not_blank CHECK (btrim(name) <> ''),
    CONSTRAINT ck_products_unit_allowed CHECK (unit IN ('un', 'kg', 'l'))
);

CREATE TABLE orders (
    id          bigint GENERATED ALWAYS AS IDENTITY,
    status      text        NOT NULL DEFAULT 'open',
    created_at  timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT pk_orders PRIMARY KEY (id),
    CONSTRAINT ck_orders_status CHECK (status IN ('open', 'confirmed', 'cancelled'))
);

CREATE TABLE reservations (
    id          bigint GENERATED ALWAYS AS IDENTITY,
    order_id    bigint      NOT NULL,
    product_id  bigint      NOT NULL,
    quantity    integer     NOT NULL,
    status      text        NOT NULL DEFAULT 'active',
    expires_at  timestamptz NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT pk_reservations PRIMARY KEY (id),
    CONSTRAINT fk_reservations_order FOREIGN KEY (order_id) REFERENCES orders (id),
    CONSTRAINT fk_reservations_product FOREIGN KEY (product_id) REFERENCES products (id),
    CONSTRAINT ck_reservations_quantity_positive CHECK (quantity > 0),
    CONSTRAINT ck_reservations_status CHECK (
        status IN ('active', 'confirmed', 'expired', 'cancelled')
    ),
    CONSTRAINT ck_reservations_expiry_after_creation CHECK (expires_at > created_at)
);

-- Invariante 5: uma requisição repetida (ou um duplo clique) não cria duas
-- reservas ativas para o mesmo par pedido+produto. Índice parcial porque
-- reserva cancelada ou expirada não deve bloquear uma nova tentativa.
CREATE UNIQUE INDEX uk_reservations_active_order_product
    ON reservations (order_id, product_id)
 WHERE status = 'active';

CREATE TABLE stock_movements (
    id             bigint GENERATED ALWAYS AS IDENTITY,
    product_id     bigint      NOT NULL,
    kind           text        NOT NULL,
    quantity       integer     NOT NULL,
    reason         text        NOT NULL,
    reservation_id bigint,
    created_at     timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT pk_stock_movements PRIMARY KEY (id),
    CONSTRAINT fk_stock_movements_product FOREIGN KEY (product_id) REFERENCES products (id),
    CONSTRAINT fk_stock_movements_reservation FOREIGN KEY (reservation_id)
        REFERENCES reservations (id),
    CONSTRAINT ck_stock_movements_kind CHECK (
        kind IN ('inbound', 'outbound', 'reserve', 'release')
    ),
    CONSTRAINT ck_stock_movements_quantity_positive CHECK (quantity > 0),
    -- Movimento de reserva exige reserva; movimento de entrada proíbe.
    CONSTRAINT ck_stock_movements_reservation_link CHECK (
        (kind IN ('reserve', 'release')) = (reservation_id IS NOT NULL)
    )
);

CREATE INDEX idx_stock_movements_product_created
    ON stock_movements (product_id, created_at DESC);

CREATE TABLE product_stock (
    product_id  bigint,
    on_hand     integer     NOT NULL DEFAULT 0,
    reserved    integer     NOT NULL DEFAULT 0,
    available   integer     GENERATED ALWAYS AS (on_hand - reserved) STORED,
    updated_at  timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT pk_product_stock PRIMARY KEY (product_id),
    CONSTRAINT fk_product_stock_product FOREIGN KEY (product_id) REFERENCES products (id),
    CONSTRAINT ck_product_stock_on_hand_not_negative CHECK (on_hand >= 0),
    CONSTRAINT ck_product_stock_reserved_not_negative CHECK (reserved >= 0),
    -- Invariante 1: disponível nunca negativo. Como `available` é coluna
    -- gerada, a invariante inteira cabe nesta expressão.
    CONSTRAINT ck_product_stock_reserved_within_on_hand CHECK (reserved <= on_hand)
);

CREATE TABLE idempotency_keys (
    key            text        NOT NULL,
    request_hash   text        NOT NULL,
    reservation_id bigint,
    created_at     timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT pk_idempotency_keys PRIMARY KEY (key),
    CONSTRAINT fk_idempotency_keys_reservation FOREIGN KEY (reservation_id)
        REFERENCES reservations (id),
    CONSTRAINT ck_idempotency_keys_key_not_blank CHECK (btrim(key) <> '')
);

-- Invariante 4: movimento é imutável. A regra fica no banco porque um
-- `UPDATE` esquecido em qualquer camada acima não pode reescrever história.
CREATE FUNCTION prevent_movement_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'stock_movements e append-only'
        USING ERRCODE = 'restrict_violation';
END;
$$;

CREATE TRIGGER tr_stock_movements_append_only
    BEFORE UPDATE OR DELETE ON stock_movements
    FOR EACH ROW EXECUTE FUNCTION prevent_movement_change();
