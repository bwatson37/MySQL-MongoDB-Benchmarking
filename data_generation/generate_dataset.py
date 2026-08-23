import time
import sys
import json
import random
import uuid
from datetime import datetime, timedelta
from faker import Faker
import os

# Protective gateway: Wait for cloud-init to finish installing pip packages
while True:
    try:
        import faker
        break
    except ModuleNotFoundError:
        print("Waiting for 'faker' library to finish installing on cloud instance...", flush=True)
        time.sleep(10)

fake = Faker('en_GB')

start_date = datetime(2023, 1, 1, 0, 0, 0)
end_date = datetime(2026, 6, 30, 23, 59, 59)

def generate_random_order_date():
    delta = end_date - start_date
    random_seconds = random.randint(0, int(delta.total_seconds()))
    return (start_date + timedelta(seconds=random_seconds)).strftime('%Y-%m-%d %H:%M:%S')

CITY_DATA = {
    "Colchester": {"region": "East", "outcode": "CO"},
    "Cambridge": {"region": "East", "outcode": "CB"},
    "Norwich": {"region": "East", "outcode": "NR"},
    "Peterborough": {"region": "East", "outcode": "PE"},
    
    "Birmingham": {"region": "West Midlands", "outcode": "B"},
    "Wolverhampton": {"region": "West Midlands", "outcode": "WV"},
    "Coventry": {"region": "West Midlands", "outcode": "CV"},
    "Worcester": {"region": "West Midlands", "outcode": "WR"},
    
    "Nottingham": {"region": "East Midlands", "outcode": "NG"},
    "Leicester": {"region": "East Midlands", "outcode": "LE"},
    "Derby": {"region": "East Midlands", "outcode": "DE"},
    "Lincoln": {"region": "East Midlands", "outcode": "LN"},
    
    "Manchester": {"region": "Northwest", "outcode": "M"},
    "Liverpool": {"region": "Northwest", "outcode": "L"},
    "Chester": {"region": "Northwest", "outcode": "CH"},
    "Lancaster": {"region": "Northwest", "outcode": "LA"},
    
    "Newcastle-upon-Tyne": {"region": "Northeast", "outcode": "NE"},
    "Sunderland": {"region": "Northeast", "outcode": "SR"},
    "Durham": {"region": "Northeast", "outcode": "DH"},
    "Middlesbrough": {"region": "Northeast", "outcode": "TS"},
    
    "Leeds": {"region": "Yorkshire", "outcode": "LS"},
    "Sheffield": {"region": "Yorkshire", "outcode": "S"},
    "Hull": {"region": "Yorkshire", "outcode": "HU"},
    "Bradford": {"region": "Yorkshire", "outcode": "BD"},
    
    "London": {"region": "Southeast", "outcode": "SW"},
    "Portsmouth": {"region": "Southeast", "outcode": "PO"},
    "Southampton": {"region": "Southeast", "outcode": "SO"},
    "Milton Keynes": {"region": "Southeast", "outcode": "MK"},
    
    "Exeter": {"region": "Southwest", "outcode": "EX"},
    "Bristol": {"region": "Southwest", "outcode": "BS"},
    "Plymouth": {"region": "Southwest", "outcode": "PL"},
    "Gloucester": {"region": "Southwest", "outcode": "GL"},
    
    "Cardiff": {"region": "Wales", "outcode": "CF"},
    "Wrexham": {"region": "Wales", "outcode": "LL"},
    "Swansea": {"region": "Wales", "outcode": "SA"},
    "Newport": {"region": "Wales", "outcode": "NP"},
    
    "Glasgow": {"region": "Scotland", "outcode": "G"},
    "Edinburgh": {"region": "Scotland", "outcode": "EH"},
    "Aberdeen": {"region": "Scotland", "outcode": "AB"},
    "Dundee": {"region": "Scotland", "outcode": "DD"},

    "Derry": {"region": "Northern Ireland", "outcode": "BT"},
    "Belfast": {"region": "Northern Ireland", "outcode": "BT"},
    "Lisburn": {"region": "Northern Ireland", "outcode": "BT"},
    "Bangor": {"region": "Northern Ireland", "outcode": "BT"},
}

def generate_ecommerce_data(customer_count=160000, product_count=500):
    # --- A. GENERATE CATEGORIES MAP (Recursive tree for Mongo Rule 2b) ---
    print("Generating product category hierarchies...", flush=True)
    
    # Define custom names for specific departments and subcategories
    CATEGORY_MAPPING = {
        "Electronics": {
            "subs": {
                "Computers": ["Laptops", "Desktops"],
                "Audio": ["Headphones", "Speakers"]
            }
        },
        "Clothing": {
            "subs": {
                "Men's": ["Shirts", "Trousers"],
                "Women's": ["Dresses", "Jackets"]
            }
        },
        "Home & Garden": {
            "subs": {
                "Kitchen": ["Cookware", "Utensils"],
                "Decor": ["Lighting", "Rugs"]
            }
        }
    }

    categories_flat = []
    categories_by_id = {}
    cat_id = 1

    for dept, dept_data in CATEGORY_MAPPING.items():
        dept_id = cat_id
        categories_by_id[dept_id] = {"category_name": dept, "parent_category": None}
        cat_id += 1
        
        for sub_name, leaves in dept_data["subs"].items():
            sub_id = cat_id
            categories_by_id[sub_id] = {"category_name": sub_name, "parent_category": dept_id}
            cat_id += 1
            
            for leaf_name in leaves:
                leaf_id = cat_id
                categories_by_id[leaf_id] = {"category_name": leaf_name, "parent_category": sub_id}
                cat_id += 1

    def build_mongo_category_tree(c_id):
        cat = categories_by_id[c_id]
        parent_id = cat["parent_category"]
        
        # Build the parent node recursively if a parent ID exists
        parent_tree = None
        if parent_id is not None and parent_id in categories_by_id:
            parent_cat = categories_by_id[parent_id]
            parent_tree = {
                "category_name": parent_cat["category_name"],
                "parent_category": categories_by_id[parent_cat["parent_category"]] if parent_cat["parent_category"] is not None else None
            }
            # If we want it fully recursive for deeper trees:
            parent_tree = build_mongo_category_tree(parent_id)

        return {
            "category_name": cat["category_name"], 
            "parent_category": parent_tree
        }

    # --- B. GENERATE PRODUCTS ---
    products_flat = []
    # Find all IDs that are used as a parent by someone else
    # remaining IDs are therefore leaf IDs
    parent_ids = {c["parent_category"] for c in categories_by_id.values() if c["parent_category"] is not None}
    leaf_ids = [c_id for c_id in categories_by_id.keys() if c_id not in parent_ids]
    
    for p_id in range(1, product_count + 1):
        cat_id = random.choice(leaf_ids)
        products_flat.append({
            "product_id": p_id,
            "product_name": f"Product Model {fake.word().upper()}-{p_id}",
            "price": round(random.uniform(2.99, 899.99), 2),
            "category_id": cat_id, # Retained for relational flat-map
            "category_tree": build_mongo_category_tree(cat_id) # Embeds parent tree (Rule 2a, 2b)
        })

    # --- C. STREAM UNIFIED DATASET TO MASTER PAYLOAD ---
    print(f"Streaming single dataset to customers_payload.jsonl...", flush=True)
    
    with open("customers_payload.jsonl", "w", encoding="utf-8") as f_out:
        # 1. Products
        for p in products_flat:
            f_out.write(json.dumps({"record_type": "product", **p}) + "\n")

        # 2. Customers and Orders
        for c_id in range(1, customer_count + 1):
            dob = (datetime.now() - timedelta(days=random.randint(19 * 365, 60 * 365))).date()
            username = f"{fake.user_name()}_{c_id}"
            
            # Helper function to generate an address object from CITY_DATA
            def create_address():
                c_name = random.choice(list(CITY_DATA.keys()))
                c_info = CITY_DATA[c_name]
                outcode = c_info["outcode"]
                postcode = f"{outcode}{random.randint(1, 99)} {random.randint(1, 9)}{fake.random_letter().upper()}{fake.random_letter().upper()}"
                return {
                    "street": fake.street_address(), 
                    "city": c_name, 
                    "region": c_info["region"], 
                    "postcode": postcode
                }

            # Generate primary address
            primary_addr = create_address()
            customer_addresses = [primary_addr]
            
            # 15% chance a customer has a secondary address (e.g., previous home or work)
            if random.random() < 0.15:
                customer_addresses.append(create_address())
            
            # Customer record
            cust = {
                "record_type": "customer",
                "customer_id": c_id,
                "username": username,
                "email": f"{username}@example.com",
                "dob": dob.isoformat(),
                "user_status": "Active",
                "addresses": customer_addresses # Bounded subdocument array (1 or 2 addresses)
            }
            f_out.write(json.dumps(cust) + "\n")

            # Orders (Rule 3a, 3b)
            num_orders = random.choices([0, 1, 2, 3, 4, 5, 9], weights=[26, 26, 18, 12, 9, 6, 3], k=1)[0]
            
            for order_idx in range(num_orders):
                p = random.choice(products_flat)
                
                # Determine shipping destination dynamically per order
                shipping_roll = random.random()
                if shipping_roll < 0.70 or len(customer_addresses) == 1:
                    shipping_addr = primary_addr
                elif shipping_roll < 0.90 and len(customer_addresses) > 1:
                    shipping_addr = customer_addresses[1]
                else:
                    shipping_addr = create_address() # Independent snapshot location (e.g., gift)

                num_items = random.choices([1, 2, 3, 4], weights=[60, 25, 10, 5], k=1)[0]
                chosen_products = random.sample(products_flat, num_items)
                
                order_items = []
                total_order_amount = 0.0

                for prod in chosen_products:
                    qty = random.randint(1, 5)
                    line_total = round(prod["price"] * qty, 2)
                    total_order_amount += line_total
                    
                    order_items.append({
                        "product_id": prod["product_id"],
                        "product_name": prod["product_name"],
                        "quantity": qty,
                        "unit_price": prod["price"]
                    })

                order = {
                    "record_type": "order",
                    "order_id": f"ORD_{c_id}_{order_idx + 1}_{random.randint(100,999)}",
                    "customer_id": c_id, 
                    "order_date": generate_random_order_date(),
                    "amount": round(total_order_amount, 2),
                    "shipping_snapshot": shipping_addr, 
                    "items": order_items
                }
                f_out.write(json.dumps(order) + "\n")
                
if __name__ == "__main__":
    target_count = int(os.environ.get("TARGET_CUSTOMER_COUNT", 160000))
    generate_ecommerce_data(customer_count=target_count)
    print("\n[SUCCESS] Data Compiling Completed Safely.")