-- Invoked only by the marked, isolated-database loader. Integer tokens are validated.
CREATE TABLE IF NOT EXISTS public.users (
  customer_id integer PRIMARY KEY, region text
);
CREATE TABLE IF NOT EXISTS public.products (
  product_id integer PRIMARY KEY, category text, price numeric(12,2)
);
-- Retain the original orders and order_items column types; extensions are test-only.
CREATE TABLE IF NOT EXISTS public.orders (
  order_id bigint PRIMARY KEY, customer_id integer NOT NULL REFERENCES public.users,
  created_at timestamp without time zone, status text NOT NULL, amount numeric(12,2),
  merchant_id integer NOT NULL
);
CREATE TABLE IF NOT EXISTS public.order_items (
  item_id bigint PRIMARY KEY, order_id bigint NOT NULL REFERENCES public.orders,
  line_number smallint NOT NULL CHECK (line_number BETWEEN 1 AND 3),
  product_id integer NOT NULL REFERENCES public.products, quantity smallint NOT NULL
);
CREATE TABLE IF NOT EXISTS public.refunds (
  refund_id bigint PRIMARY KEY, order_id bigint NOT NULL REFERENCES public.orders,
  amount numeric(12,2)
);
TRUNCATE public.refunds, public.order_items, public.orders, public.products, public.users;
DROP INDEX IF EXISTS public.orders_merchant_experiment_idx;
INSERT INTO public.users
SELECT g, CASE WHEN g % 17 = 0 THEN NULL ELSE 'region-' || (g % 5) END
FROM generate_series(1,100000) g;
INSERT INTO public.users VALUES (100001,'manual'),(100002,NULL);
INSERT INTO public.products
SELECT g, CASE WHEN g % 31 = 0 THEN NULL ELSE 'category-' || (g % 40) END,
       CASE WHEN g=1 THEN 10 WHEN g=2 THEN 20 WHEN g % 97=0 THEN NULL
            ELSE ((g*37) % 10000 + 1)::numeric/100 END
FROM generate_series(1,1000) g;
INSERT INTO public.orders
SELECT g, ((g*48271::bigint + __SEED__) % 100000 + 1)::integer,
       CASE WHEN g % 113=0 THEN NULL ELSE timestamp '2025-01-01'
         + ((g*7919::bigint + __SEED__) % 31536000) * interval '1 second' END,
       CASE WHEN g%7=0 THEN 'cancelled' ELSE 'paid' END,
       CASE WHEN g%127=0 THEN NULL ELSE ((g*17) % 100000)::numeric/100 END,
       CASE WHEN __HOT__=1 AND (g*37::bigint + __SEED__) % 100 < 80 THEN 1
            WHEN __HOT__=0 THEN ((g*13::bigint + (g/100000)*17 + __SEED__) % 100 + 1)::integer
            ELSE ((g*13::bigint + (g/100000)*17 + __SEED__) % 99 + 2)::integer END
FROM generate_series(1,__ORDER_COUNT__) g;
INSERT INTO public.order_items
SELECT o.order_id*10+n,o.order_id,n,
       ((o.order_id*53+n*19+__SEED__) % 1000 + 1)::integer,(n%2+1)::smallint
FROM public.orders o
CROSS JOIN LATERAL generate_series(1,CASE o.order_id%3 WHEN 0 THEN 0 WHEN 1 THEN 1 ELSE 3 END) n;
INSERT INTO public.refunds
SELECT order_id,order_id,CASE WHEN order_id%103=0 THEN NULL ELSE (order_id%1000)::numeric/100 END
FROM public.orders WHERE order_id%5=0;
-- Hand fixture: two lines worth 10+20, three refunds worth 2+3+5.
-- Second user has no orders; NULL region, NULL prices and dates exist in the wider dataset.
INSERT INTO public.orders VALUES (900000001,100001,'2025-06-15 00:00:00','paid',30,1);
INSERT INTO public.order_items VALUES
 (9000000011,900000001,1,1,1),(9000000012,900000001,2,2,1);
INSERT INTO public.refunds VALUES
 (9000000011,900000001,2),(9000000012,900000001,3),(9000000013,900000001,5);
CREATE INDEX IF NOT EXISTS orders_created_at_idx ON public.orders(created_at);
CREATE INDEX IF NOT EXISTS orders_customer_idx ON public.orders(customer_id);
CREATE INDEX IF NOT EXISTS order_items_order_id_idx ON public.order_items(order_id);
CREATE INDEX IF NOT EXISTS refunds_order_id_idx ON public.refunds(order_id);
GRANT CONNECT ON DATABASE sql_optimization TO sql_optimization_reader;
GRANT USAGE ON SCHEMA public TO sql_optimization_reader;
GRANT SELECT ON public.orders,public.order_items,public.users,public.products,public.refunds TO sql_optimization_reader;
ANALYZE public.orders; ANALYZE public.order_items; ANALYZE public.users;
ANALYZE public.products; ANALYZE public.refunds;
