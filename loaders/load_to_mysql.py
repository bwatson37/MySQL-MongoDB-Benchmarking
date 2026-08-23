import json
import mysql.connector
import sys
import os

# Securely grab configuration from the environment variables
DB_HOST = os.getenv("MYSQL_HOST", "localhost")
DB_USER = os.getenv("MYSQL_USER", "root")
DB_PASSWORD = os.getenv("MYSQL_PASSWORD", "FallbackTestingPasswordOnly")
DB_NAME = os.getenv("MYSQL_DB", "benchmark_db")

JSONL_FILE_PATH = "/home/ubuntu/customers_payload.jsonl"
BATCH_SIZE = 5000

def get_table_schema(record_type, sample_record):
    """Defines schema dynamically based on record type (excluding embedded arrays like items/addresses)."""
    columns = list(sample_record.keys())
    defs = []
    for col in columns:
        if (record_type == "customer" and col == "customer_id") or \
           (record_type == "product" and col == "product_id") or \
           (record_type == "order" and col == "order_id"):
            defs.append(f"`{col}` VARCHAR(64) PRIMARY KEY")
        else:
            defs.append(f"`{col}` TEXT")
    return columns, defs

def load_jsonl_to_mysql():
    # Abort if the source file can't be found
    if not os.path.exists(JSONL_FILE_PATH):
        print(f"[ERROR] Source file not found at: {JSONL_FILE_PATH}")
        sys.exit(1)

    # Connect to MySQL instance
    print(f"Connecting to MySQL instance at {DB_HOST}...")
    try:
        conn = mysql.connector.connect(
            host=DB_HOST,
            user=DB_USER,
            password=DB_PASSWORD
        )
        cursor = conn.cursor()
    except Exception as e:
        print(f"[CRITICAL] MySQL connection failed: {e}")
        sys.exit(1)

    # Initialise database
    cursor.execute(f"CREATE DATABASE IF NOT EXISTS {DB_NAME}")
    cursor.execute(f"USE {DB_NAME}")

    # Drop existing tables during development to prevent duplicate key collisions
    cursor.execute("DROP TABLE IF EXISTS order_items")
    cursor.execute("DROP TABLE IF EXISTS orders")
    cursor.execute("DROP TABLE IF EXISTS addresses")
    cursor.execute("DROP TABLE IF EXISTS customers")
    cursor.execute("DROP TABLE IF EXISTS products")
    conn.commit()
    
    # Explicitly create core structural and relational tables
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS customers (
            customer_id VARCHAR(64) PRIMARY KEY,
            username TEXT,
            email TEXT,
            dob TEXT,
            user_status TEXT
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS addresses (
            address_id INT AUTO_INCREMENT PRIMARY KEY,
            customer_id VARCHAR(64),
            street TEXT,
            city VARCHAR(100),
            region VARCHAR(100),
            postcode VARCHAR(20),
            address_type VARCHAR(10),
            is_default TINYINT(1)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            order_id VARCHAR(64) PRIMARY KEY,
            customer_id VARCHAR(64),
            order_date DATETIME,
            amount DECIMAL(10, 2),
            shipping_snapshot TEXT
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS order_items (
            order_item_id INT AUTO_INCREMENT PRIMARY KEY,
            order_id VARCHAR(64),
            product_id VARCHAR(64),
            quantity INT,
            unit_price DECIMAL(10, 2)
        )
    """)
    conn.commit()

    created_tables = {}
    batches = {
        "customer": [], 
        "address": [],
        "order": [],
        "order_item": [],
        "product": []
    }

    seen_customer_ids = set()

    # Check for data in JSONL file
    with open(JSONL_FILE_PATH, 'r', encoding='utf-8') as f:
        line = f.readline()
        if not line:
            print("[ERROR] JSONL file is empty.")
            return

        f.seek(0)
        
        for line in f:
            if not line.strip():
                continue
            record = json.loads(line)
            r_type = record.pop("record_type")
            
            if r_type == "customer":
                customer_id = record.get("customer_id")
                
                # Prevent duplicate entries if payload contains repeats
                if customer_id in seen_customer_ids:
                    continue
                seen_customer_ids.add(customer_id)

                addresses = record.pop("addresses", [])
                
                cust_values = (
                    customer_id,
                    record.get("username"),
                    record.get("email"),
                    record.get("dob"),
                    record.get("user_status")
                )
                batches["customer"].append(cust_values)

                # The 'default' address in MongoDB's embedded customer.addresses array 
                # is always the first one, so addresses[0] must be treated as the default here
                default_index = 0

                for idx, addr in enumerate(addresses):
                    is_default = 1 if idx == default_index else 0
                    address_type = 'S'
                    
                    addr_values = (
                        customer_id,
                        addr.get("street"),
                        addr.get("city"),
                        addr.get("region"),
                        addr.get("postcode"),
                        address_type,
                        is_default
                    )
                    batches["address"].append(addr_values)
                
                # Flush batches before continuing 
                if len(batches["customer"]) >= BATCH_SIZE:
                    cursor.executemany(
                        "INSERT INTO customers (customer_id, username, email, dob, user_status) VALUES (%s, %s, %s, %s, %s)",
                        batches["customer"]
                    )
                    conn.commit()
                    batches["customer"] = []

                if len(batches["address"]) >= BATCH_SIZE:
                    _flush_addresses(cursor, conn, batches["address"])
                    batches["address"] = []
                
                continue

            if r_type == "order":
                # Extract embedded items array so it goes into order_items table
                order_id = record.get("order_id")
                items = record.pop("items", [])
                
                for item in items:
                    batches["order_item"].append((
                        order_id,
                        item.get("product_id"),
                        item.get("quantity"),
                        item.get("unit_price")
                    ))

            # Setup table dynamically if first time seeing this record type (products, orders)
            if r_type not in created_tables:
                cols, defs = get_table_schema(r_type, record)
                cursor.execute(f"CREATE TABLE IF NOT EXISTS {r_type}s ({', '.join(defs)})")
                created_tables[r_type] = cols
            
            # Prepare standard record data
            cols = created_tables[r_type]
            values = [json.dumps(record.get(c)) if isinstance(record.get(c), (dict, list)) else record.get(c) for c in cols]
            batches[r_type].append(values)

            # Flush standard batches if limit reached
            if len(batches[r_type]) >= BATCH_SIZE:
                _flush_batch(cursor, conn, r_type, batches[r_type], cols)
                batches[r_type] = []

            if len(batches["address"]) >= BATCH_SIZE:
                _flush_addresses(cursor, conn, batches["address"])
                batches["address"] = []

            if len(batches["order_item"]) >= BATCH_SIZE:
                _flush_order_items(cursor, conn, batches["order_item"])
                batches["order_item"] = []

    # Final flush for all remaining batches
    for r_type, batch in batches.items():
        if batch:
            if r_type == "address":
                _flush_addresses(cursor, conn, batch)
            elif r_type == "order_item":
                _flush_order_items(cursor, conn, batch)
            elif r_type == "customer":
                cursor.executemany(
                    "INSERT INTO customers (customer_id, username, email, dob, user_status) VALUES (%s, %s, %s, %s, %s)",
                    batch
                )
                conn.commit()
            else:
                cols = created_tables[r_type]
                _flush_batch(cursor, conn, r_type, batch, cols)

    # Performance indexes for the benchmark queries.
    print("Creating performance indexes...", flush=True)

    # orders: customer_id supports every join/group-by that touches orders;
    #         order_date and amount are included as a covering index for q3/q5/q6
    # NOTE: InnoDB secondary indexes always implicitly carry the primary key so q5 is also covered
    cursor.execute("CREATE INDEX idx_orders_customer_date_amount ON orders (customer_id, order_date, amount)")
    
    # dob is stored as TEXT, in the format 'YYYY-MM-DD' so a 10 chars covers it
    cursor.execute("CREATE INDEX idx_customers_dob ON customers (dob(10))")
    
    # addresses: customer_id + is_default covers both q1 and q2's join condition
    #            city is appended so q1's equality check can also be satisfied from the index.
    # NOTE: Composite index created on addresses before FK constraint so MySQL can reuse it 
    cursor.execute("CREATE INDEX idx_addresses_customer_default_city ON addresses (customer_id, is_default, city)")
    
    # unit_price included so the revenue calculation doesn't need a separate row lookup per item
    cursor.execute("CREATE INDEX idx_order_items_product_covering ON order_items (product_id, quantity, unit_price)")
    
    conn.commit()
    print("Performance indexes created.", flush=True)

    # Add foreign key constraints after all data has finished loading
    print("Applying foreign key constraints...", flush=True)
    cursor.execute("""
        ALTER TABLE addresses 
        ADD CONSTRAINT fk_customer_address 
        FOREIGN KEY (customer_id) REFERENCES customers(customer_id) 
        ON DELETE CASCADE
    """)
    cursor.execute("""
        ALTER TABLE order_items 
        ADD CONSTRAINT fk_order_item_order 
        FOREIGN KEY (order_id) REFERENCES orders(order_id) 
        ON DELETE CASCADE
    """)
    conn.commit()

    cursor.close()
    conn.close()
    
    # This exact phrase is polled for by stream_data() in load.py - keep them in sync.
    print("[SUCCESS] MySQL multi-table ingestion complete! mysql data streaming phase complete!")

def _flush_batch(cursor, conn, r_type, batch, cols):
    placeholders = ", ".join(["%s"] * len(cols))
    query = f"INSERT INTO {r_type}s ({', '.join([f'`{c}`' for c in cols])}) VALUES ({placeholders})"
    cursor.executemany(query, batch)
    conn.commit()

def _flush_addresses(cursor, conn, batch):
    query = """
        INSERT INTO addresses 
        (customer_id, street, city, region, postcode, address_type, is_default) 
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """
    cursor.executemany(query, batch)
    conn.commit()

def _flush_order_items(cursor, conn, batch):
    query = """
        INSERT INTO order_items 
        (order_id, product_id, quantity, unit_price) 
        VALUES (%s, %s, %s, %s)
    """
    cursor.executemany(query, batch)
    conn.commit()

if __name__ == "__main__":
    load_jsonl_to_mysql()