import time
from logger import logger

MAX_WAIT_ITERATIONS = 8640  # sets a 24-hour limit: (24 * 60 * 60) / 10 seconds per iteration

def stream_data(driver_gateway, ips, db_password):
    logger.log("\n=== Starting Robust Ingestion Monitoring ===")
    
    # 1. Start MySQL ingestion with explicit exports
    mysql_cmd = (
        f"MYSQL_HOST={ips['mysql_internal']} "
        f"MYSQL_USER=root "
        f"MYSQL_PASSWORD='{db_password}' "
        f"python3 -u /home/ubuntu/load_to_mysql.py > /home/ubuntu/mysql_ingest.log 2>&1 &"
    )
    driver_gateway.exec_command(mysql_cmd)
    
    # 2. Monitor MySQL
    # NOTE: this must match the exact phrase load_to_mysql.py prints on success
    ingestion_active = True
    iterations = 0
    elapsed_wait = 0
    while ingestion_active:
        iterations += 1
        if iterations > MAX_WAIT_ITERATIONS:
            logger.log(f"  [TIMEOUT] MySQL ingestion did not report completion after {iterations * 10}s.")
            return False
            
        _, log_out, _ = driver_gateway.exec_command("cat /home/ubuntu/mysql_ingest.log 2>/dev/null")
        data = log_out.read().decode('utf-8').lower()
        
        if "mysql data streaming phase complete!" in data:
            logger.log("  [SUCCESS] MySQL ingestion complete.")
            ingestion_active = False
        elif "[critical]" in data or "[error]" in data or "traceback" in data or "exception" in data:
            logger.log(f"  [CRITICAL] MySQL failed: {data}")
            return False
        else:
            elapsed_wait = iterations * 10
            
            # Check disk usage every 60 seconds (every 6th loop)
            if iterations % 6 == 0:
                # Ask the driver to SSH into the internal node and check the root partition
                ssh_cmd = f"ssh -i /home/ubuntu/id_rsa_tmp -o StrictHostKeyChecking=no ubuntu@{ips['mysql_internal']} 'df -h / | tail -n 1'"
                _, df_out, _ = driver_gateway.exec_command(ssh_cmd)
                
                # Parse the output to extract the percentage and available space
                disk_stats = df_out.read().decode('utf-8').split()
                if len(disk_stats) >= 5:
                    avail_space = disk_stats[3]
                    usage_percent = disk_stats[4]
                    logger.log(f"  [Ingesting] MySQL ingestion in progress... ({elapsed_wait}s elapsed) | Disk: {usage_percent} used, {avail_space} remaining")
                else:
                    logger.log(f"  [Ingesting] MySQL ingestion in progress... ({elapsed_wait}s elapsed)")
            
            time.sleep(10)
            
    # 3. Start MongoDB ingestion 
    mongo_cmd = (
    f"MONGO_HOST={ips['mongodb_internal']} "
    f"MONGO_USER=root "
    f"MONGO_PASSWORD='{db_password}' "
    f"python3 -u /home/ubuntu/load_to_mongodb.py > /home/ubuntu/mongodb_ingest.log 2>&1 &"
    )
    driver_gateway.exec_command(mongo_cmd)
    
    # 4. Monitor MongoDB
    ingestion_active = True
    iterations = 0
    elapsed_wait = 0
    while ingestion_active:
        iterations += 1
        if iterations > MAX_WAIT_ITERATIONS:
            logger.log(f"  [TIMEOUT] MongoDB ingestion did not report completion after {iterations * 10}s.")
            return False
        
        _, log_out, _ = driver_gateway.exec_command("cat /home/ubuntu/mongodb_ingest.log 2>/dev/null")
        data = log_out.read().decode('utf-8').lower()
        
        if "mongodb data streaming phase complete!" in data:
            logger.log("  [SUCCESS] MongoDB ingestion complete.")
            ingestion_active = False
        elif "[critical]" in data or "[error]" in data or "traceback" in data or "exception" in data:
            logger.log(f"  [CRITICAL] MongoDB failed with unhandled exception:\n{data}")
            return False 
        else:
            elapsed_wait = iterations * 10
            
            # Check disk usage every 60 seconds (every 6th loop)
            if iterations % 6 == 0:
                # Ask the driver to SSH into the internal node and check the root partition
                ssh_cmd = f"ssh -i /home/ubuntu/id_rsa_tmp -o StrictHostKeyChecking=no ubuntu@{ips['mongodb_internal']} 'df -h / | tail -n 1'"
                _, df_out, _ = driver_gateway.exec_command(ssh_cmd)
                
                # Parse the output to extract the percentage and available space
                disk_stats = df_out.read().decode('utf-8').split()
                if len(disk_stats) >= 5:
                    avail_space = disk_stats[3]
                    usage_percent = disk_stats[4]
                    logger.log(f"  [Ingesting] MongoDB ingestion in progress... ({elapsed_wait}s elapsed) | Disk: {usage_percent} used, {avail_space} remaining")
                else:
                    logger.log(f"  [Ingesting] MongoDB ingestion in progress... ({elapsed_wait}s elapsed)")
            
            time.sleep(10)
    
    # Blocking wait: monitor active ingestion processes
    iterations = 0
    while True:
        iterations += 1
        if iterations > MAX_WAIT_ITERATIONS:
            logger.log(f"  [TIMEOUT] Ingestion processes still running after {iterations * 10}s.")
            return False
        _, out, _ = driver_gateway.exec_command("pgrep -f load_to")
        if not out.read().strip():
            logger.log("  [SUCCESS] All ingestion processes finished.")
            break
        time.sleep(10)
       
    logger.log("  [CHECK] Verifying ingestion logs for errors...")
    _, out, _ = driver_gateway.exec_command("grep -i 'error' /home/ubuntu/mysql_ingest.log /home/ubuntu/mongodb_ingest.log")
    errors = out.read().decode().strip()
    
    if errors:
        logger.log(f"  [FAIL] Ingestion errors detected:\n{errors}")
        return False
    return True

def run_remote_audit(driver_gateway, ip, command, description):
    """Helper to jump to internal nodes and verify data integrity."""
    # Escape inner double quotes so they don't break the outer ssh string
    escaped_command = command.replace('"', '\\"')
    cmd = f'ssh -i /home/ubuntu/id_rsa_tmp -o StrictHostKeyChecking=no ubuntu@{ip} "{escaped_command}"'
    stdin, stdout, stderr = driver_gateway.exec_command(cmd)
    if stdout.channel.recv_exit_status() == 0:
        logger.log(f"  [PASS] {description}")
        raw_out = stdout.read().decode().strip()
        out = " ".join(raw_out.split())
        if out: logger.log(f"         └─ Raw Output: {out}")
    else:
        logger.log(f"  [FAIL] {description} (Error: {stderr.read().decode().strip()})")

def perform_audit(driver_gateway, ips, password):
    logger.log("\n=== Initiating Data Integrity Audit ===")
    
    tables = ["customers", "orders", "products"]
    
    # MySQL Audit
    for table in tables:
        mysql_cmd = f"mysql -u root -p'{password}' -D benchmark_db -e 'SELECT count(*) FROM {table};'"
        run_remote_audit(driver_gateway, ips['mysql_internal'], mysql_cmd, f"MySQL {table} Audit")
        
    # MongoDB Audit
    for collection in tables:
        py_code = f'from pymongo import MongoClient; client = MongoClient("mongodb://root:{password}@{ips["mongodb_internal"]}:27017/admin?authSource=admin"); print(client["ecommerce_benchmark"]["{collection}"].count_documents({{}}))'
        mongo_py_cmd = f"python3 -c '{py_code}'"
        description = f"MongoDB {collection} Audit"
        stdin, stdout, stderr = driver_gateway.exec_command(mongo_py_cmd)
        if stdout.channel.recv_exit_status() == 0:
            logger.log(f"  [PASS] {description}")
            out = stdout.read().decode().strip()
            if out: logger.log(f"         └─ Raw Output: {out}")
        else:
            logger.log(f"  [FAIL] {description} (Error: {stderr.read().decode().strip()})")