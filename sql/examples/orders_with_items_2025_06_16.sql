SELECT COUNT(DISTINCT o.order_id) AS order_count
FROM public.orders AS o JOIN public.order_items AS i ON i.order_id = o.order_id
WHERE o.created_at >= '2025-06-16 00:00:00'::timestamp AND o.created_at < '2025-06-17 00:00:00'::timestamp;
