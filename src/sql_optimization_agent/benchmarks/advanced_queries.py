"""Controlled PostgreSQL templates; all date values are bound, never model SQL."""
from datetime import datetime

NARROW=(datetime(2025,6,15),datetime(2025,6,16))
WIDE=(datetime(2025,1,1),datetime(2026,1,1))
A_ORIGINAL="""SELECT u.region, p.category, SUM(i.quantity*p.price) AS amount,
 COUNT(*)::bigint AS line_count, COUNT(DISTINCT o.order_id)::bigint AS order_count
FROM public.orders o
JOIN public.order_items i ON i.order_id=o.order_id
JOIN public.products p ON p.product_id=i.product_id
JOIN public.users u ON u.customer_id=o.customer_id
WHERE o.created_at >= %s AND o.created_at < %s
GROUP BY u.region,p.category"""
A_CANDIDATE="""WITH filtered_orders AS MATERIALIZED (
 SELECT order_id,customer_id FROM public.orders WHERE created_at >= %s AND created_at < %s
), per_order_category AS (
 SELECT i.order_id,p.category,SUM(i.quantity*p.price) AS amount,COUNT(*) AS line_count
 FROM public.order_items i JOIN public.products p ON p.product_id=i.product_id
 JOIN filtered_orders f ON f.order_id=i.order_id
 GROUP BY i.order_id,p.category
)
SELECT u.region,c.category,SUM(c.amount) AS amount,
 SUM(c.line_count)::bigint AS line_count,COUNT(*)::bigint AS order_count
FROM filtered_orders f JOIN per_order_category c ON c.order_id=f.order_id
JOIN public.users u ON u.customer_id=f.customer_id
GROUP BY u.region,c.category"""
B_ORIGINAL="""SELECT u.customer_id,u.region,
 COALESCE(s.sales,0)::numeric AS sales,COALESCE(r.refund,0)::numeric AS refund
FROM public.users u
LEFT JOIN (
 SELECT o.customer_id,SUM(i.quantity*p.price) AS sales
 FROM public.orders o JOIN public.order_items i ON i.order_id=o.order_id
 JOIN public.products p ON p.product_id=i.product_id
 WHERE o.created_at >= %s AND o.created_at < %s AND o.status='paid' GROUP BY o.customer_id
) s ON s.customer_id=u.customer_id
LEFT JOIN (
 SELECT o.customer_id,SUM(r.amount) AS refund
 FROM public.orders o JOIN public.refunds r ON r.order_id=o.order_id
 WHERE o.created_at >= %s AND o.created_at < %s AND o.status='paid' GROUP BY o.customer_id
) r ON r.customer_id=u.customer_id"""
B_CANDIDATE="""WITH filtered_orders AS MATERIALIZED (
 SELECT order_id,customer_id FROM public.orders WHERE created_at >= %s AND created_at < %s AND status='paid'
), line_totals AS (
 SELECT i.order_id,SUM(i.quantity*p.price) AS sales
 FROM public.order_items i JOIN public.products p ON p.product_id=i.product_id
 JOIN filtered_orders f ON f.order_id=i.order_id GROUP BY i.order_id
), refund_totals AS (
 SELECT r.order_id,SUM(r.amount) AS refund FROM public.refunds r
 JOIN filtered_orders f ON f.order_id=r.order_id GROUP BY r.order_id
), totals AS (
 SELECT f.customer_id,SUM(COALESCE(l.sales,0)) AS sales,SUM(COALESCE(r.refund,0)) AS refund
 FROM filtered_orders f LEFT JOIN line_totals l ON l.order_id=f.order_id
 LEFT JOIN refund_totals r ON r.order_id=f.order_id GROUP BY f.customer_id
)
SELECT u.customer_id,u.region,COALESCE(t.sales,0)::numeric AS sales,
 COALESCE(t.refund,0)::numeric AS refund
FROM public.users u LEFT JOIN totals t ON t.customer_id=u.customer_id"""
B_FANOUT="""SELECT u.customer_id,u.region,
 COALESCE(SUM(i.quantity*p.price),0)::numeric AS sales,COALESCE(SUM(r.amount),0)::numeric AS refund
FROM public.users u LEFT JOIN public.orders o ON o.customer_id=u.customer_id
 AND o.created_at >= %s AND o.created_at < %s AND o.status='paid'
LEFT JOIN public.order_items i ON i.order_id=o.order_id
LEFT JOIN public.products p ON p.product_id=i.product_id
LEFT JOIN public.refunds r ON r.order_id=o.order_id
GROUP BY u.customer_id,u.region"""
B_DROPS_USERS=B_CANDIDATE+'\nWHERE t.customer_id IS NOT NULL'


def c_queries(key):
    if key not in ('merchant_id','customer_id'):
        raise ValueError('Unsupported grouping key')
    other='customer_id' if key=='merchant_id' else 'merchant_id'
    original=f'''SELECT {key},SUM(subtotal) AS amount,SUM(n)::bigint AS order_count
FROM (SELECT {key},{other},SUM(amount) AS subtotal,COUNT(*) AS n
 FROM public.orders WHERE order_id < 900000000 GROUP BY {key},{other}) t GROUP BY {key}'''
    candidate=f'''SELECT {key},SUM(amount) AS amount,COUNT(*)::bigint AS order_count
FROM public.orders WHERE order_id < 900000000 GROUP BY {key}'''
    return original,candidate

# Explicit semantic fixture: repeated category in one order, and an all-NULL SUM group.
# Read-only VALUES CTEs exercise the exact same A templates without mutating seed data.
A_FIXTURE_PREFIX="""WITH fixture_orders(order_id,customer_id,created_at) AS (
 VALUES (1::bigint,1::integer,TIMESTAMP '2025-06-15 00:00:00'),
        (2::bigint,1::integer,TIMESTAMP '2025-06-15 23:59:59.999999')
), fixture_items(order_id,product_id,quantity) AS (
 VALUES (1::bigint,1,1),(1::bigint,2,1),(1::bigint,1,1),(2::bigint,3,1)
), fixture_products(product_id,category,price) AS (
 VALUES (1,NULL::text,10::numeric),(2,NULL::text,20::numeric),(3,'all_null',NULL::numeric)
), fixture_users(customer_id,region) AS (VALUES (1,NULL::text))
"""


def a_fixture_queries():
    def fixture(query):
        for source,target in [('orders','fixture_orders'),('order_items','fixture_items'),('products','fixture_products'),('users','fixture_users')]:
            query=query.replace('public.'+source,target)
        return A_FIXTURE_PREFIX+(', '+query[5:] if query.startswith('WITH ') else query)
    return fixture(A_ORIGINAL),fixture(A_CANDIDATE)
