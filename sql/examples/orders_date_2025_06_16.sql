SELECT customer_id, created_at, status, amount
FROM public.orders
WHERE created_at::date = DATE '2025-06-16';
