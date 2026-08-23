import os
import json
import time
from pymongo import MongoClient

# --- STANDALONE CONFIGURATION ---
MONGO_HOST = "x.x.x.x"
MONGO_USER = "root"
MONGO_PASSWORD = "<ENTER DB PASSWORD>"
PAYLOAD_PATH = "/home/ubuntu/customers_payload.jsonl"
# --------------------------------

def stream_to_mongodb():
    # Establish connection using the standalone variables
    uri = f"mongodb://{MONGO_USER}:{MONGO_PASSWORD}@{MONGO_HOST}:27017/admin?authSource=admin"
    print(f"Connecting to MongoDB at {MONGO_HOST}:27017...")
    
    client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    db = client["ecommerce_benchmark"]
    
    # Clear testing collections just in case
    db["customers"].drop()
    db["products"].drop()
    db["orders"].drop()
    print("Database collections cleared. Beginning specification compliant routing...")
    
    # --- PRE-INGESTION INDEXING ---
    print("Building indexes on empty collections...")
    db["customers"].create_index("customer_id", unique=True)
    db["products"].create_index("product_id", unique=True)
    db["orders"].create_index("customer_id")
    print("Indexes built. Starting data ingestion...")

    if not os.path.exists(PAYLOAD_PATH):
        print(f"[CRITICAL] Target dataset source missing at {PAYLOAD_PATH}")
        return

    # Ingestion tracking buffers
    batches = {"customer": [], "product": [], "order": []}
    counters = {"customer": 0, "product": 0, "order": 0}
    start_time = time.time()

    with open(PAYLOAD_PATH, "r", encoding="utf-8") as f:
        for line in f:
            clean_line = line.strip()
            if not clean_line: continue
            
            doc = json.loads(clean_line)
            r_type = doc.get("record_type")
            
            # Remove the generator's metadata indicator before database commit
            del doc["record_type"]
            
            batches[r_type].append(doc)
            
            # Flush batch when limit is reached
            if len(batches[r_type]) >= 5000:
                collection_name = f"{r_type}s" if r_type != "product" else "products"
                db[collection_name].insert_many(batches[r_type])
                counters[r_type] += len(batches[r_type])
                print(f"[PROGRESS] Successfully ingested {counters[r_type]} {collection_name}...")
                batches[r_type] = []

    # Final trailing flushes
    for r_type, remaining_docs in batches.items():
        if remaining_docs:
            collection_name = f"{r_type}s" if r_type != "product" else "products"
            db[collection_name].insert_many(remaining_docs)
            counters[r_type] += len(remaining_docs)

    elapsed = time.time() - start_time
    print(f"\n=== MongoDB Specification Audit ===")
    print(f"  [USERS]    Stored: {counters['customer']}")
    print(f"  [PRODUCTS] Stored: {counters['product']} (Embedded Category Tree)")
    print(f"  [ORDERS]   Stored: {counters['order']} (Referenced Customer ID)")

    print(f"mongodb data streaming phase complete! Time: {elapsed:.2f} seconds.")

if __name__ == "__main__":
    stream_to_mongodb()
