import time
import json
import os
import subprocess
import mysql.connector
import psutil

# manually set password here to avoid getenv issues
DB_PASSWORD = "<ENTER DB PASSWORD>"

# set variables; check target IP against terraform logs
TARGET_IP = "x.x.x.x"
TARGET_USER = "ubuntu"
SSH_KEY_PATH = "/home/ubuntu/id_rsa_tmp"
ITERATIONS = 3

QUERIES = {
    "q1_top_cities": """
        SELECT 
            o.shipping_snapshot->>'$.city' AS shipping_city, 
            COUNT(*) AS order_count
        FROM customers c
        INNER JOIN addresses a ON a.customer_id = c.customer_id AND a.is_default = 1 
        INNER JOIN orders o ON o.customer_id = c.customer_id 
        WHERE c.dob < '2005-01-01' 
          AND o.shipping_snapshot->>'$.city' = a.city
        GROUP BY shipping_city
        ORDER BY order_count DESC 
        LIMIT 10;
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

def start_ssh_tunnel():
    """Starts the SSH keepalive tunnel attached to the Python process."""
    print("Establishing SSH Keepalive Tunnel to bypass network timeouts...")
    cmd = [
        "ssh", "-i", SSH_KEY_PATH, "-N", 
        "-L", f"33060:127.0.0.1:3306", 
        f"{TARGET_USER}@{TARGET_IP}", 
        "-o", "ServerAliveInterval=30",
        "-o", "StrictHostKeyChecking=no"
    ]
    tunnel_proc = subprocess.Popen(cmd)
    
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
    
    scp_cmd = f"scp -i {SSH_KEY_PATH} -o StrictHostKeyChecking=no {TARGET_USER}@{TARGET_IP}:/home/ubuntu/hardware_metrics.log ./mysql_hardware_metrics.log"
    subprocess.run(scp_cmd, shell=True, check=True)
    print("Hardware metrics saved locally!")

def main():
    tunnel_proc = None
    conn = None
    cursor = None
    
    try:
        tunnel_proc = start_ssh_tunnel()
        
        conn = mysql.connector.connect(
            host="127.0.0.1", 
            port=33060,
            user="root", 
            password=DB_PASSWORD, 
            database="benchmark_db"
        )
        cursor = conn.cursor()
        results = {}

        cursor.execute("SET SESSION MAX_EXECUTION_TIME = 3600000;")
        cursor.execute("SET SESSION net_read_timeout = 3600;")
        cursor.execute("SET SESSION net_write_timeout = 3600;")
        cursor.execute("SET SESSION wait_timeout = 3600;")
        
        start_remote_telemetry()
        
        for q_name, query in QUERIES.items():
            print(f"  -> [BENCHMARK] Starting {q_name}...", flush=True)
            results[q_name] = []
            
            for i in range(ITERATIONS):
                print(f"     - Running iteration {i+1}/{ITERATIONS}...", flush=True)
                
                io_start = psutil.disk_io_counters()
                start_time = time.perf_counter()
                start_timestamp = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())
                
                try:
                    # 1. Capture temp tables before the query
                    cursor.execute("SHOW SESSION STATUS LIKE 'Created_tmp_disk_tables';")
                    tmp_tables_start = int(cursor.fetchone()[1])

                    # 2. Execute the benchmark query
                    cursor.execute(query)
                    for _row in cursor:
                        pass
                    
                    end_time = time.perf_counter()
                    end_timestamp = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())
                    io_end = psutil.disk_io_counters()
                    ram_percent = psutil.virtual_memory().percent
                    
                    # 3. Capture temp tables immediately after
                    cursor.execute("SHOW SESSION STATUS LIKE 'Created_tmp_disk_tables';")
                    tmp_tables_end = int(cursor.fetchone()[1])
                    
                    # 4. Calculate the exact difference
                    tmp_disk_tables = tmp_tables_end - tmp_tables_start
                    
                    duration = end_time - start_time
                    disk_read_mb = (io_end.read_bytes - io_start.read_bytes) / (1024 * 1024) if io_end else 0
                    disk_write_mb = (io_end.write_bytes - io_start.write_bytes) / (1024 * 1024) if io_end else 0
                    
                    print(f"     - Iteration {i+1} finished in {duration:.2f} seconds. Temp disk tables: {tmp_disk_tables}")
                    
                    results[q_name].append({
                        "iteration": i + 1,
                        "status": "success",
                        "start_timestamp_utc": start_timestamp,
                        "end_timestamp_utc": end_timestamp,
                        "latency_seconds": round(duration, 4),
                        "tmp_disk_tables_created": tmp_disk_tables,
                        "sys_ram_usage_percent": round(ram_percent, 2),
                        "disk_read_mb": round(disk_read_mb, 2),
                        "disk_write_mb": round(disk_write_mb, 2)
                    })
                    
                    with open("mysql_benchmark_results.json", "w") as f:
                        json.dump(results, f, indent=4)
                        
                except mysql.connector.Error as e:
                    if e.errno == 3024:
                        print(f"     - [TIMEOUT] Iteration {i+1} exceeded 60 minutes. Skipping remaining iterations.")
                        results[q_name].append({"iteration": i + 1, "status": "timeout", "start_timestamp_utc": start_timestamp, "latency_seconds": 3600})
                    elif e.errno == 2013:
                        print(f"     - [CONNECTION LOST] Target node dropped connection. Skipping remaining iterations.")
                        results[q_name].append({"iteration": i + 1, "status": "connection_lost", "start_timestamp_utc": start_timestamp, "error_msg": str(e)})
                    else:
                        print(f"     - [ERROR] Failed with error: {e}. Skipping remaining iterations.")
                        results[q_name].append({"iteration": i + 1, "status": "error", "start_timestamp_utc": start_timestamp, "error_msg": str(e)})
                    
                    with open("mysql_benchmark_results.json", "w") as f:
                        json.dump(results, f, indent=4)
                    break
                    
            print(f"  -> [BENCHMARK] Finished executing block for {q_name}\n", flush=True)

    finally:
        stop_and_fetch_telemetry()
        
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()
            
        if tunnel_proc:
            print("Tearing down SSH Keepalive Tunnel...")
            tunnel_proc.terminate()
            tunnel_proc.wait() 

if __name__ == "__main__":
    main()