import time
import json
import os
import subprocess
from pymongo import MongoClient
import pymongo.errors
import psutil

# manually set password here to avoid getenv issues
DB_PASSWORD = "<ENTER DB PASSWORD>"

# set variables; check target IP against terraform logs
TARGET_IP = "x.x.x.x"
TARGET_USER = "ubuntu"
SSH_KEY_PATH = "/home/ubuntu/id_rsa_tmp"
ITERATIONS = 3

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

def start_ssh_tunnel():
    """Starts the SSH keepalive tunnel attached to the Python process."""
    print("Establishing SSH Keepalive Tunnel to bypass network timeouts...")
    cmd = [
        "ssh", "-i", SSH_KEY_PATH, "-N", 
        "-L", f"27018:127.0.0.1:27017", 
        f"{TARGET_USER}@{TARGET_IP}", 
        "-o", "ServerAliveInterval=30",
        "-o", "StrictHostKeyChecking=no"
    ]
    tunnel_proc = subprocess.Popen(cmd)
    
    # Wait 2 seconds to ensure the tunnel is fully bound before PyMongo connects
    time.sleep(2)
    return tunnel_proc

def start_remote_telemetry():
    """Starts vmstat in the background on the target database node."""
    print("Starting remote hardware telemetry...")
    start_cmd = f"ssh -i {SSH_KEY_PATH} -o StrictHostKeyChecking=no {TARGET_USER}@{TARGET_IP} 'nohup vmstat -t 10 > /home/ubuntu/hardware_metrics.log 2>&1 &'"
    subprocess.run(start_cmd, shell=True, check=True)

def stop_and_fetch_telemetry():
    """Kills vmstat on the target node and copies the log back to the driver."""
    print("Stopping telemetry and fetching logs...")
    
    kill_cmd = f"ssh -i {SSH_KEY_PATH} -o StrictHostKeyChecking=no {TARGET_USER}@{TARGET_IP} 'pkill -f vmstat'"
    subprocess.run(kill_cmd, shell=True, check=False) 
    
    scp_cmd = f"scp -i {SSH_KEY_PATH} -o StrictHostKeyChecking=no {TARGET_USER}@{TARGET_IP}:/home/ubuntu/hardware_metrics.log ./mongo_hardware_metrics.log"
    subprocess.run(scp_cmd, shell=True, check=True)
    print("Hardware metrics saved locally!")

def main():
    tunnel_proc = None
    client = None
    
    try:
        # 1. Establish the SSH tunnel first
        tunnel_proc = start_ssh_tunnel()
        
        # 2. Connect to MongoDB though local end of the tunnel
        client = MongoClient(
            f"mongodb://root:{DB_PASSWORD}@127.0.0.1:27018/admin?authSource=admin", 
            connectTimeoutMS=3600000, 
            socketTimeoutMS=3600000,
            retryReads=False
        )
        db = client["ecommerce_benchmark"]
        results = {}

        # 3. Start Hardware Logging
        start_remote_telemetry()

        # 4. Run the Benchmark Loop
        for q_name, pipeline in PIPELINES.items():
            print(f"  -> [BENCHMARK] Starting {q_name}...", flush=True)
            results[q_name] = []
            
            if q_name in ["q1_top_cities", "q6_inactive_customers"]:
                target_coll = db.customers
            elif q_name == "q4_category_revenue":
                target_coll = db.products
            else:
                target_coll = db.orders
            
            for i in range(ITERATIONS):
                print(f"     - Running iteration {i+1}/{ITERATIONS}...", flush=True)
                
                io_start = psutil.disk_io_counters()
                start_time = time.perf_counter()
                start_timestamp = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())
                
                try:
                    # Turn on the MongoDB Database Profiler (Level 2 logs everything)
                    db.command({"profile": 2})
                    
                    # Execute the benchmark query
                    cursor = target_coll.aggregate(pipeline, allowDiskUse=True, maxTimeMS=3600000)
                    for _doc in cursor:
                        pass
                    
                    end_time = time.perf_counter()
                    end_timestamp = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())
                    io_end = psutil.disk_io_counters()
                    ram_percent = psutil.virtual_memory().percent
                    
                    # Turn off the Profiler immediately to prevent background noise
                    db.command({"profile": 0})
                    
                    # Fetch the execution profile for the exact query we just ran
                    profile_doc = db.system.profile.find_one(
                        {"op": "command", "command.aggregate": target_coll.name},
                        sort=[("ts", -1)]
                    )
                    
                    # Extract the boolean flag indicating if the 100MB RAM limit was breached
                    spilled_to_disk = profile_doc.get("usedDisk", False) if profile_doc else False
                    
                    duration = end_time - start_time
                    disk_read_mb = (io_end.read_bytes - io_start.read_bytes) / (1024 * 1024) if io_end else 0
                    disk_write_mb = (io_end.write_bytes - io_start.write_bytes) / (1024 * 1024) if io_end else 0
                    
                    print(f"     - Iteration {i+1} finished in {duration:.2f} seconds. Spilled to disk: {spilled_to_disk}")
                    
                    results[q_name].append({
                        "iteration": i + 1,
                        "status": "success",
                        "start_timestamp_utc": start_timestamp,
                        "end_timestamp_utc": end_timestamp,
                        "latency_seconds": round(duration, 4),
                        "spilled_to_disk": spilled_to_disk,
                        "sys_ram_usage_percent": round(ram_percent, 2),
                        "disk_read_mb": round(disk_read_mb, 2),
                        "disk_write_mb": round(disk_write_mb, 2)
                    })
                    
                    with open("mongo_benchmark_results.json", "w") as f:
                        json.dump(results, f, indent=4)
                        
                except pymongo.errors.ExecutionTimeout:
                    print(f"     - [TIMEOUT] Iteration {i+1} exceeded 60 minutes. Skipping remaining iterations.")
                    results[q_name].append({
                        "iteration": i + 1, 
                        "status": "timeout", 
                        "start_timestamp_utc": start_timestamp, 
                        "latency_seconds": 3600
                    })
                    
                    with open("mongo_benchmark_results.json", "w") as f:
                        json.dump(results, f, indent=4)
                    break
                
                except (pymongo.errors.AutoReconnect, pymongo.errors.ConnectionFailure) as e:
                    print(f"     - [CONNECTION LOST] Target node dropped connection (likely OOM or network timeout). Skipping remaining iterations.")
                    results[q_name].append({
                        "iteration": i + 1, 
                        "status": "connection_lost", 
                        "start_timestamp_utc": start_timestamp, 
                        "error_msg": str(e)
                    })
                    
                    with open("mongo_benchmark_results.json", "w") as f:
                        json.dump(results, f, indent=4)
                    break
                    
                except Exception as e:
                    print(f"     - [ERROR] Failed with error: {e}. Skipping remaining iterations.")
                    results[q_name].append({
                        "iteration": i + 1, 
                        "status": "error", 
                        "start_timestamp_utc": start_timestamp, 
                        "error_msg": str(e)
                    })
                    
                    with open("mongo_benchmark_results.json", "w") as f:
                        json.dump(results, f, indent=4)
                    break

            print(f"  -> [BENCHMARK] Finished executing block for {q_name}\n", flush=True)

    finally:
        # 5. Teardown Phase
        stop_and_fetch_telemetry()
        
        if client:
            client.close()
            
        if tunnel_proc:
            print("Tearing down SSH Keepalive Tunnel...")
            tunnel_proc.terminate()
            tunnel_proc.wait() 

if __name__ == "__main__":
    main()
