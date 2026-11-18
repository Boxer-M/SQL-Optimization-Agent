\set ON_ERROR_STOP on

BEGIN;

CREATE TABLE IF NOT EXISTS public.orders (
    order_id bigint PRIMARY KEY,
    customer_id integer NOT NULL,
    created_at timestamp without time zone,
    status text NOT NULL,
    amount numeric(12,2) NOT NULL
);

-- Never truncate, drop, or overwrite existing rows. Serialize concurrent loaders.
LOCK TABLE public.orders IN SHARE ROW EXCLUSIVE MODE;

CREATE TEMP TABLE expected_orders ON COMMIT DROP AS
SELECT
    n::bigint AS order_id,
    (1 + (n * 37) % 10000)::integer AS customer_id,
    timestamp '2025-01-01 00:00:00'
        + (((n - 1) * 31535999::bigint) / 299999) * interval '1 second' AS created_at,
    (ARRAY['pending', 'paid', 'shipped', 'cancelled'])[1 + n % 4] AS status,
    ((100 + (n * 7919::bigint) % 9999900)::numeric / 100)::numeric(12,2) AS amount
FROM generate_series(1, 300000) AS series(n)
UNION ALL
SELECT * FROM (VALUES
    (900001::bigint, 10001, timestamp '2025-06-15 00:00:00', 'paid', 123.45::numeric(12,2)),
    (900002::bigint, 10002, timestamp '2025-06-15 23:59:59.999999', 'shipped', 234.56::numeric(12,2)),
    (900003::bigint, 10003, timestamp '2025-06-16 00:00:00', 'pending', 345.67::numeric(12,2)),
    (900004::bigint, 10004, NULL::timestamp, 'cancelled', 456.78::numeric(12,2)),
    (900005::bigint, 10001, timestamp '2025-06-15 00:00:00', 'paid', 123.45::numeric(12,2))
) AS boundaries(order_id, customer_id, created_at, status, amount);

-- Refuse conflicting IDs rather than silently replacing unrelated data.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM public.orders AS actual
        JOIN expected_orders AS expected USING (order_id)
        WHERE ROW(actual.customer_id, actual.created_at, actual.status, actual.amount)
            IS DISTINCT FROM
            ROW(expected.customer_id, expected.created_at, expected.status, expected.amount)
    ) THEN
        RAISE EXCEPTION 'Existing orders conflict with seed IDs; no data changed';
    END IF;
END;
$$;

INSERT INTO public.orders (order_id, customer_id, created_at, status, amount)
SELECT order_id, customer_id, created_at, status, amount FROM expected_orders
ON CONFLICT (order_id) DO NOTHING;

CREATE INDEX IF NOT EXISTS orders_created_at_idx ON public.orders USING btree (created_at);
ANALYZE public.orders;

COMMIT;
