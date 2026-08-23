import math
import sqlparse
from sqlparse.sql import Token

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
        GROUP BY a.city, a.region 
        ORDER BY AVG(amount) DESC;
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
            WHERE o.customer_id = c.customer_id
            AND o.order_date >= '2026-01-01'
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

def calculate_metrics(operators, operands, decision_points):
    """Calculates Halstead Volume and Cyclomatic Complexity."""
    n1 = len(set(operators))
    n2 = len(set(operands))
    N1 = len(operators)
    N2 = len(operands)
    
    n = n1 + n2
    N = N1 + N2
    
    volume = N * math.log2(n) if n > 0 else 0
    cc = 1 + decision_points
    
    return {"Cyclomatic_Complexity": cc, "Halstead_Volume": round(volume, 2)}

def analyse_sql(query_string):
    """Parses a MySQL query to extract operators and operands."""
    operators = []
    operands = []
    decision_points = 0
    
    cc_keywords = [
        'JOIN', 'INNER JOIN', 'LEFT JOIN', 'RIGHT JOIN', 'OUTER JOIN', 'CROSS JOIN',
        'WHERE', 'HAVING', 'AND', 'OR', 'XOR',
        'CASE', 'WHEN', 'IF', 'IFNULL', 'COALESCE', 'NULLIF',
        'UNION', 'INTERSECT', 'EXCEPT',
        'IN', 'EXISTS', 'ANY', 'ALL'
    ]
    
    parsed = sqlparse.parse(query_string)[0]
    
    for token in parsed.flatten():
        if token.is_whitespace or token.value in ['(', ')', ',', ';']:
            continue
            
        token_upper = token.value.upper()
        
        if token.ttype in sqlparse.tokens.Keyword or token.ttype in sqlparse.tokens.Operator:
            operators.append(token_upper)
            if token_upper in cc_keywords:
                decision_points += 1
        elif token.ttype in sqlparse.tokens.Name or token.ttype in sqlparse.tokens.Literal:
            operands.append(token.value)
        elif token.ttype in sqlparse.tokens.Wildcard:
            operands.append(token.value)
            
    return calculate_metrics(operators, operands, decision_points)

def analyse_mongo(pipeline):
    """Recursively parses a MongoDB pipeline dictionary."""
    operators = []
    operands = []
    decision_points = 0
    
    cc_keywords = [
        '$lookup', '$unwind', '$graphLookup',
        '$match', '$and', '$or', '$not', '$nor',
        '$cond', '$ifNull', '$switch',
        '$in', '$nin', '$setEquals', '$setIntersection', '$setDifference', '$setUnion',
        '$eq', '$lt', '$gt', '$gte', '$lte', '$expr'
    ]
    
    def traverse(node):
        nonlocal decision_points
        if isinstance(node, dict):
            for key, value in node.items():
                if str(key).startswith('$'):
                    operators.append(key)
                    if key in cc_keywords:
                        decision_points += 1
                else:
                    operands.append(key)
                traverse(value)
        elif isinstance(node, list):
            for item in node:
                traverse(item)
        else:
            operands.append(str(node))

    traverse(pipeline)
    return calculate_metrics(operators, operands, decision_points)

if __name__ == "__main__":
    print("-" * 50)
    print(f"{'Query':<25} | {'Engine':<10} | {'CC':<5} | {'Volume'}")
    print("-" * 50)

    # Process MySQL Queries
    for q_name, query_string in QUERIES.items():
        metrics = analyse_sql(query_string)
        print(f"{q_name:<25} | {'MySQL':<10} | {metrics['Cyclomatic_Complexity']:<5} | {metrics['Halstead_Volume']}")

    # Process MongoDB Pipelines
    for q_name, pipeline in PIPELINES.items():
        metrics = analyse_mongo(pipeline)
        print(f"{q_name:<25} | {'MongoDB':<10} | {metrics['Cyclomatic_Complexity']:<5} | {metrics['Halstead_Volume']}")
        
    print("-" * 50)