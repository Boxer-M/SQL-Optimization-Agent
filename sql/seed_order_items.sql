\set ON_ERROR_STOP on

BEGIN;

DO $$
BEGIN
    IF current_database() <> 'sql_optimization' OR to_regclass('public.orders') IS NULL THEN
        RAISE EXCEPTION 'Run against sql_optimization after loading public.orders';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sql_optimization_reader') THEN
        RAISE EXCEPTION 'Create the existing application reader before loading order_items';
    END IF;
END;
$$;

CREATE TABLE IF NOT EXISTS public.order_items (
    item_id bigint PRIMARY KEY,
    order_id bigint NOT NULL REFERENCES public.orders(order_id),
    line_number smallint NOT NULL CHECK (line_number BETWEEN 1 AND 3)
);

-- Preserve orders and any existing items; serialize loaders and freeze parent rows.
LOCK TABLE public.orders IN SHARE MODE;
LOCK TABLE public.order_items IN SHARE ROW EXCLUSIVE MODE;

CREATE TEMP TABLE expected_order_items ON COMMIT DROP AS
SELECT o.order_id * 10 + n AS item_id, o.order_id, n::smallint AS line_number
FROM public.orders AS o
CROSS JOIN LATERAL generate_series(1, CASE o.order_id % 3
    WHEN 0 THEN 0 WHEN 1 THEN 1 ELSE 3 END) AS series(n)
WHERE o.order_id BETWEEN 1 AND 300000 OR o.order_id BETWEEN 900001 AND 900005;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM public.order_items AS actual
        JOIN expected_order_items AS expected USING (item_id)
        WHERE ROW(actual.order_id, actual.line_number)
            IS DISTINCT FROM ROW(expected.order_id, expected.line_number)
    ) THEN
        RAISE EXCEPTION 'Existing order_items conflict with seed IDs; no rows overwritten';
    END IF;
END;
$$;

INSERT INTO public.order_items (item_id, order_id, line_number)
SELECT item_id, order_id, line_number FROM expected_order_items
ON CONFLICT (item_id) DO NOTHING;

CREATE INDEX IF NOT EXISTS order_items_order_id_idx ON public.order_items USING btree (order_id);
GRANT SELECT ON TABLE public.order_items TO sql_optimization_reader;
ANALYZE public.order_items;

COMMIT;
