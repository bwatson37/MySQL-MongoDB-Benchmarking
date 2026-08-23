import json
import sqlglot
from sqlglot import exp

def calculate_sql_cognitive_complexity(sql_query):
    """
    Calculates cognitive complexity for SQL by traversing its AST.
    Increments for: Joins, Subqueries (penalized by depth), CASE statements, and logical operators.
    """
    try:
        # Parse the SQL into an AST using the MySQL dialect to support ->> JSON operators
        parsed = sqlglot.parse_one(sql_query, read="mysql")
    except Exception as e:
        return f"Parse Error: {e}"

    score = 0
    
    # 1. Base increments for breaks in linear flow
    joins = list(parsed.find_all(exp.Join))
    score += len(joins)
    
    cases = list(parsed.find_all(exp.Case))
    score += len(cases)

    # Count Logical Operators (AND/OR sequences)
    ands_ors = list(parsed.find_all(exp.Connector))
    score += len(ands_ors)

    # 2. Nesting Penalties (Selects within Selects)
    for node in parsed.find_all(exp.Select):
        depth = 0
        current = node.parent
        while current:
            if isinstance(current, exp.Select):
                depth += 1
            current = current.parent
        
        score += depth

    return score

def calculate_mongo_cognitive_complexity(node, current_depth=0):
    """
    Calculates cognitive complexity for MongoDB aggregation pipelines.
    Increments for: Stages, Conditionals, and heavily penalizes nested pipelines.
    """
    score = 0
    
    # Base case: strings, ints, booleans have no cognitive complexity
    if not isinstance(node, (dict, list)):
        return 0

    if isinstance(node, list):
        # A pipeline is a sequence of stages. Each stage breaks linear flow.
        if current_depth == 0:
            score += len(node) 
            
        for item in node:
            score += calculate_mongo_cognitive_complexity(item, current_depth)
            
    elif isinstance(node, dict):
        for key, value in node.items():
            # Branching Conditionals
            if key in ['$cond', '$switch']:
                score += 1
                
            # Logical Operators
            if key in ['$and', '$or']:
                score += len(value) if isinstance(value, list) else 1
                
            # Nesting Penalty (e.g., a pipeline inside a $lookup)
            if key in ['pipeline', '$facet']:
                nested_depth = current_depth + 1
                score += nested_depth
                # Add the number of stages in the nested pipeline
                if isinstance(value, list):
                    score += len(value)
                score += calculate_mongo_cognitive_complexity(value, nested_depth)
            
            # Recurse for nested objects, ignoring primitives
            elif isinstance(value, (dict, list)):
                score += calculate_mongo_cognitive_complexity(value, current_depth)
                
    return score

# ==========================================
# The Benchmark Data
# ==========================================

QUERIES = {
    "q1_top_cities": """
        SELECT o.shipping_snapshot->>'$.city' AS shipping_city, COUNT(*) AS order_count
        FROM customers c
        INNER JOIN addresses a ON a.customer_id = c.customer_id AND a.is_default = 1 
        INNER JOIN orders o ON o.customer_id = c.customer_id 
        WHERE c.dob < '2005-01-01' AND o.shipping_snapshot->>'$.city' = a.city
        GROUP BY shipping_city ORDER BY order_count DESC LIMIT 10;
    """,
    "q2_avg_order_city": """
        SELECT a.city, a.region, ROUND(AVG(amount),2) AS average_order_amount
        FROM orders o
        INNER JOIN customers c ON c.customer_id = o.customer_id 
        INNER JOIN addresses a ON a.customer_id = c.customer_id AND a.is_default = 1
        GROUP BY a.city, a.region ORDER BY AVG(amount) DESC;
    """,
    "q3_customer_spend_stats": """
        SELECT customer_id, MIN(amount) AS min_order_amount, MAX(amount) AS max_order_amount,
               COUNT(amount) AS num_orders, SUM(amount) AS total_spend
        FROM orders GROUP BY customer_id ORDER BY COUNT(amount) DESC;
    """,
    "q4_category_revenue": """
        SELECT oi.product_id, p.product_name, p.product_category, p.department,
               SUM(oi.quantity * oi.unit_price) AS total_revenue
        FROM order_items oi
        INNER JOIN (
            SELECT product_id, product_name, 
                   category_tree->>'$.category_name' AS product_category,
                   category_tree->>'$.parent_category.parent_category.category_name' AS department
            FROM products
            WHERE category_tree->>'$.parent_category.parent_category.category_name' = 'Electronics'
        ) p ON p.product_id = oi.product_id
        GROUP BY oi.product_id, p.product_name, p.product_category, p.department
        HAVING SUM(oi.quantity * oi.unit_price) >= 2500000
        ORDER BY SUM(oi.quantity * oi.unit_price) DESC LIMIT 10;
    """,
    "q5_top_customers": """
        SELECT o.customer_id, c.username, COUNT(o.order_id) AS num_orders, SUM(o.amount) AS total_spend
        FROM orders o INNER JOIN customers c ON c.customer_id = o.customer_id
        GROUP BY o.customer_id, c.username ORDER BY SUM(o.amount) DESC LIMIT 10;
    """,
    "q6_inactive_customers": """
        SELECT username FROM customers c
        WHERE NOT EXISTS (
            SELECT 1 FROM orders o
            WHERE o.customer_id = c.customer_id AND o.order_date >= '2026-01-01'
        )
        ORDER BY username ASC;
    """
}

PIPELINES = {
    "q1_top_cities": [
        {"$match": {"dob": {"$lt": "2005-01-01"}}},
        {"$lookup": {"from": "orders", "localField": "customer_id", "foreignField": "customer_id", "as": "order"}},
        {"$unwind": "$order"},
        {"$match": {"$expr": {"$eq": ["$order.shipping_snapshot.city", {"$arrayElemAt": ["$addresses.city", 0]}]}}},
        {"$group": {"_id": "$order.shipping_snapshot.city", "order_count": {"$sum": 1}}},
        {"$sort": {"order_count": -1}}, 
        {"$limit": 10}
    ],
    "q2_avg_order_city": [
        {"$lookup": {"from": "customers", "localField": "customer_id", "foreignField": "customer_id", "as": "customer"}},
        {"$unwind": "$customer"},
        {"$addFields": {"default_address": {"$arrayElemAt": ["$customer.addresses", 0]}}},
        {"$group": {"_id": {"city": "$default_address.city", "region": "$default_address.region"}, "average_order_amount": {"$avg": "$amount"}}},
        {"$sort": {"average_order_amount": -1}}
    ],
    "q3_customer_spend_stats": [
        {"$group": {"_id": "$customer_id", "min_order_amount": {"$min": "$amount"}, "max_order_amount": {"$max": "$amount"}, "num_orders": {"$sum": 1}, "total_spend": {"$sum": "$amount"}}},
        {"$sort": {"num_orders": -1}}
    ],
    "q4_category_revenue": [
        {"$match": {"category_tree.parent_category.parent_category.category_name": "Electronics"}},
        {"$lookup": {"from": "orders", "localField": "product_id", "foreignField": "items.product_id", "as": "matched_orders"}},
        {"$unwind": "$matched_orders"},
        {"$unwind": "$matched_orders.items"},
        {"$match": {"$expr": {"$eq": ["$matched_orders.items.product_id", "$product_id"]}}},
        {"$group": {
            "_id": {
                "product_id": "$product_id",
                "product_name": "$product_name",
                "department": "$category_tree.parent_category.parent_category.category_name"
            },
            "total_revenue": {"$sum": {"$multiply": ["$matched_orders.items.quantity", "$matched_orders.items.unit_price"]}}
        }},
        {"$match": {"total_revenue": {"$gte": 2500000}}},
        {"$sort": {"total_revenue": -1}}, 
        {"$limit": 10}
    ],
    "q5_top_customers": [
        {"$group": {"_id": "$customer_id", "num_orders": {"$sum": 1}, "total_spend": {"$sum": "$amount"}}},
        {"$sort": {"total_spend": -1}}, {"$limit": 10},
        {"$lookup": {"from": "customers", "localField": "_id", "foreignField": "customer_id", "as": "customer"}},
        {"$unwind": "$customer"}
    ],
    "q6_inactive_customers": [
        {"$lookup": {
            "from": "orders", 
            "localField": "customer_id", 
            "foreignField": "customer_id",
            "pipeline": [
                {"$match": {"order_date": {"$gte": "2026-01-01"}}},
                {"$limit": 1}
            ],
            "as": "recent_orders"
        }},
        {"$match": {"recent_orders": {"$size": 0}}},
        {"$sort": {"username": 1}}
    ]
}

print("=== COGNITIVE COMPLEXITY SCORES ===")
for query_name in QUERIES.keys():
    sql_score = calculate_sql_cognitive_complexity(QUERIES[query_name])
    mongo_score = calculate_mongo_cognitive_complexity(PIPELINES[query_name])
    print(f"{query_name}:")
    print(f"  SQL   : {sql_score}")
    print(f"  Mongo : {mongo_score}\n")