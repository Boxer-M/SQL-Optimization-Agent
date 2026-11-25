-- Executed only after the shared isolated benchmark loader and marker guard.
INSERT INTO public.users VALUES (100003,'all-null'),(100004,'boundary');
INSERT INTO public.orders VALUES
 (900000010,100003,'2025-06-15 00:00:00','paid',NULL,1),
 (900000011,100003,'2025-06-15 23:59:59.999999','paid',NULL,1),
 (900000020,100004,'2024-12-31 23:59:59.999999','paid',100,1),
 (900000021,100004,'2025-01-01 00:00:00','paid',7,1),
 (900000022,100004,'2025-12-31 23:59:59.999999','paid',11,1),
 (900000023,100004,'2026-01-01 00:00:00','paid',100,1),
 (900000024,100004,NULL,'paid',100,1);
CREATE INDEX IF NOT EXISTS orders_customer_created_cover_idx
 ON public.orders(customer_id,created_at) INCLUDE(amount,status);
CREATE TABLE IF NOT EXISTS public.customer_metrics_seed (
 singleton boolean PRIMARY KEY CHECK(singleton), fixed_seed integer NOT NULL,
 total_orders bigint NOT NULL, seed_version text NOT NULL
);
TRUNCATE public.customer_metrics_seed;
INSERT INTO public.customer_metrics_seed VALUES (true,20261125,1000000,'customer-metrics-v1');
GRANT SELECT ON public.customer_metrics_seed TO sql_optimization_reader;
