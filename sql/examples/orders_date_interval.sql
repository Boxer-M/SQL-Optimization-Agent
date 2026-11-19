SELECT customer_id, created_at, status, amount
FROM public.orders
WHERE created_at::date BETWEEN DATE '2025-06-15' AND DATE '2025-06-16';
