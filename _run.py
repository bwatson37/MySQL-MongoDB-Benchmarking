import os
import time
import paramiko
from logger import logger
from infra import run_terraform, get_outputs
from provision import setup_nodes
from load import stream_data, perform_audit

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    def _load_env_file_manually(path=".env"):
        if not os.path.exists(path):
            return
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip().strip('"').strip("'")
                os.environ.setdefault(key, value)
    _load_env_file_manually()

# variables to configure dataset size
# NOTE: 10m rows is roughly 6.5 GB, not counting order details, so should hit the targeted 10GB dataset
num_records_to_generate = int(os.getenv("PIPELINE_CUSTOMER_COUNT", 10000000))   

# configure how long to wait in seconds before each new try in loops
wait_timer_seconds = 10

# how many polling iterations to allow before giving up on a stalled remote step
max_poll_attempts = 180

# Fetch environment-specific configuration
DB_PASSWORD = os.getenv("DB_BENCHMARK_PASSWORD")
if not DB_PASSWORD:
    raise RuntimeError(
        "DB_BENCHMARK_PASSWORD environment variable must be set before running this pipeline "
        "(e.g. in a .env file next to _run.py, or exported in your shell)."
    )

def harvest_remote_logs(ips, local_private_key):
    """Collects workload and benchmark execution logs using a dedicated SSH session."""
    logger.log("\n=====================================================================")
    logger.log("REMOTE DIAGNOSTIC HARVEST: Collecting workload execution logs...")
    logger.log("=====================================================================")
    
    local_log_dir = "./remote_logs"
    os.makedirs(local_log_dir, exist_ok=True)
    
    harvest_ssh = paramiko.SSHClient()
    harvest_ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    try:
        # Re-connect using the loaded private key object with pkey=
        harvest_ssh.connect(ips['driver'], username="ubuntu", pkey=local_private_key, timeout=10)
        harvest_ssh.get_transport().set_keepalive(30)
        h_sftp = harvest_ssh.open_sftp()
        
        log_targets = [
            "/home/ubuntu/generator_output.log",
            "/home/ubuntu/mysql_ingest.log",
            "/home/ubuntu/mongodb_ingest.log",
            "/home/ubuntu/mysql_benchmark_results.json",
            "/home/ubuntu/mongo_benchmark_results.json"
        ]
        
        for remote_path in log_targets:
            filename = os.path.basename(remote_path)
            local_path = os.path.join(local_log_dir, filename)
            try:
                h_sftp.get(remote_path, local_path)
                logger.log(f"  [SFTP] Retrieved {filename}")
            except Exception as e:
                logger.log(f"  [WARN] Failed to fetch {filename}: {e}")
                
        h_sftp.close()
    except Exception as e:
        logger.log(f"  [ERROR] Harvest failed: {e}")
    finally:
        harvest_ssh.close()

def execute_remote_benchmarks(driver_gateway, ips, local_private_key, db_password):
    """Orchestrates pushing and executing benchmark scripts directly on the target DB nodes via the driver."""
    logger.log("\n=== Executing Isolated Target Node Benchmarks ===" )
    
    # Upload individual benchmark scripts to the driver node first
    sftp = driver_gateway.open_sftp()
    sftp.put("benchmark_mysql.py", "/home/ubuntu/benchmark_mysql.py")
    sftp.put("benchmark_mongo.py", "/home/ubuntu/benchmark_mongo.py")
    sftp.close()
    
    # NOTE: the keys here must match infra.get_outputs() exactly
    target_nodes = [
        {"name": "MySQL", "ip": ips['mysql_internal'], "script": "benchmark_mysql.py", "output": "mysql_benchmark_results.json"},
        {"name": "MongoDB", "ip": ips['mongodb_internal'], "script": "benchmark_mongo.py", "output": "mongo_benchmark_results.json"}
    ]
    
    # Enable TCP keepalive on the driver transport so a silently-dead connection gets detected via a failed probe
    driver_gateway.get_transport().set_keepalive(30)

    for node in target_nodes:
        logger.log(f"  [SSH] Connecting to {node['name']} node ({node['ip']}) to run benchmarks...")
        
        node_ssh = paramiko.SSHClient()
        node_ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        try:
            # Tunnel through the driver node's already-open SSH transport
            driver_transport = driver_gateway.get_transport()
            proxy_channel = driver_transport.open_channel(
                "direct-tcpip", (node['ip'], 22), ("127.0.0.1", 0), timeout=30
            )
            node_ssh.connect(node['ip'], username="ubuntu", pkey=local_private_key, sock=proxy_channel, timeout=15)
            node_ssh.get_transport().set_keepalive(30)
            
            # Copy the benchmark script from driver node onto the target
            driver_sftp = driver_gateway.open_sftp()
            with driver_sftp.open(f"/home/ubuntu/{node['script']}", "r") as f:
                script_contents = f.read()
            driver_sftp.close()
            
            node_sftp = node_ssh.open_sftp()
            with node_sftp.file(f"/home/ubuntu/{node['script']}", "w") as f:
                f.write(script_contents)
            
            # Run the benchmark script on the target node with environment variables
            logger.log(f"  [RUN] {node['name']} benchmark script started - this can legitimately "
                       f"take a while on an unindexed collection; logging progress every 60s so a "
                       f"stall is visible instead of the log going silent.")
            run_cmd = f"DB_PASSWORD='{db_password}' python3 -u /home/ubuntu/{node['script']}"
            stdin, stdout, stderr = node_ssh.exec_command(run_cmd)
            
            # Poll for completion so a stall is visible in the log
            wait_start = time.time()
            while not stdout.channel.exit_status_ready():
                time.sleep(5)
                elapsed = time.time() - wait_start
                if int(elapsed) % 60 < 5:
                    logger.log(f"  [RUN] {node['name']} benchmark still running... ({elapsed/60:.1f} min elapsed)")
            exit_status = stdout.channel.recv_exit_status()
            if exit_status == 0:
                logger.log(f"  [SUCCESS] {node['name']} benchmarks finished successfully.")
            else:
                err_msg = stderr.read().decode('utf-8')
                raise RuntimeError(f"{node['name']} benchmark script failed: {err_msg}")
                
            # Pull the result from the target node and store on the driver node
            with node_sftp.file(f"/home/ubuntu/{node['output']}", "r") as f:
                result_contents = f.read()
            node_sftp.close()
            
            driver_sftp = driver_gateway.open_sftp()
            with driver_sftp.file(f"/home/ubuntu/{node['output']}", "w") as f:
                f.write(result_contents)
            driver_sftp.close()
            
        except Exception as e:
            raise RuntimeError(f"Failed executing benchmark on {node['name']} node: {e}")
        finally:
            node_ssh.close()

def main():
    try:
        # Build Infrastructure
        run_terraform("apply -auto-approve")
        ips = get_outputs()
        
        # Establish Gateway
        ssh_key_path = os.path.expanduser("~/.ssh/id_rsa")
        local_private_key = paramiko.RSAKey.from_private_key_file(ssh_key_path)
        driver_gateway = paramiko.SSHClient()
        driver_gateway.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        logger.log(f"  [SSH] Waiting for driver node {ips['driver']} to initialize...")
        max_retries = 6
        for attempt in range(1, max_retries + 1):
            try:
                driver_gateway.connect(ips['driver'], username="ubuntu", pkey=local_private_key, timeout=10)
                driver_gateway.get_transport().set_keepalive(30)
                logger.log("  [SSH] Master driver gateway channel active!")
                break
            except (paramiko.SSHException, TimeoutError, OSError) as e:
                if attempt == max_retries:
                    raise RuntimeError(f"Could not reach driver gateway node: {e}")
                logger.log(f"  [SSH] Driver port 22 not open yet. Waiting 10 seconds...")
                time.sleep(wait_timer_seconds)
                
        # Provision Nodes
        setup_nodes(driver_gateway, ips, DB_PASSWORD)
        
        # Data Generation Step
        logger.log("\n=== Spawning Dataset Generation Natively inside GCP ===")
        
        logger.log("  [SFTP] Uploading generate_dataset.py...")
        sftp = driver_gateway.open_sftp()
        sftp.put("data_generation/generate_dataset.py", "/home/ubuntu/generate_dataset.py")
        sftp.close()
        
        logger.log("  [EXEC] Starting background data generator engine...")
        ssh_cmd = f"sudo TARGET_CUSTOMER_COUNT={num_records_to_generate} python3 -u /home/ubuntu/generate_dataset.py > /home/ubuntu/generator_output.log 2>&1 &"
        driver_gateway.exec_command(ssh_cmd)
        
        time.sleep(wait_timer_seconds)
        
        generation_complete = False
        start_time = time.time()
        poll_attempts = 0
        
        while not generation_complete:
            poll_attempts += 1
            if poll_attempts > max_poll_attempts:
                driver_gateway.close()
                raise RuntimeError(
                    f"Data generation did not report completion after {poll_attempts} polls "
                    f"({(poll_attempts * wait_timer_seconds) / 60:.0f} min) - aborting instead of hanging forever."
                )
            cmd = "wc -c /home/ubuntu/customers_payload.jsonl 2>/dev/null | awk '{print $1}'"
            stdin, stdout, stderr = driver_gateway.exec_command(cmd)
            current_size_str = stdout.read().decode().strip()
            current_size = int(current_size_str) if current_size_str.isdigit() else 0
            current_gb = current_size / (1024 ** 3)
            
            elapsed_minutes = (time.time() - start_time) / 60
            logger.log(f"  [Progress Report] Elapsed: {elapsed_minutes:.1f} min | File Footprint: {current_gb:.4f} GB")
            
            _, log_out, _ = driver_gateway.exec_command("cat /home/ubuntu/generator_output.log")
            log_status = log_out.read().decode('utf-8')
            
            if "Completed Safely" in log_status:
                logger.log("  [SUCCESS] Data engine finished formatting all records smoothly.")
                generation_complete = True
            elif "Error" in log_status or "Exception" in log_status:
                driver_gateway.close()
                raise RuntimeError(f"Cloud data generator crashed internally: {log_status}")
            else:
                time.sleep(wait_timer_seconds)
        
        # Stream Data to Target Nodes and Audit
        logger.log("  [SFTP] Uploading ingestion and benchmark scripts to Driver Node...")
        sftp = driver_gateway.open_sftp()
        sftp.put("./loaders/load_to_mysql.py", "/home/ubuntu/load_to_mysql.py")
        sftp.put("./loaders/load_to_mongodb.py", "/home/ubuntu/load_to_mongodb.py")
        sftp.close()
        
        ips = get_outputs()
        ingestion_success = stream_data(driver_gateway, ips, DB_PASSWORD)

        ips = get_outputs()
        if ingestion_success:
            perform_audit(driver_gateway, ips, DB_PASSWORD)
            
            # Execute Isolated Node Benchmarks via Driver Orchestration
            execute_remote_benchmarks(driver_gateway, ips, local_private_key, DB_PASSWORD)
            
            # Harvest all logs and performance result files back to local machine
            harvest_remote_logs(ips, local_private_key)
            
        else:
            logger.log("  [ABORT] Ingestion failed. Harvesting logs...")
            harvest_remote_logs(ips, local_private_key)
        
    except Exception as err:
        logger.log(f"\n[ERROR] Pipeline broken: {err}")
        
    finally:
        # Teardown
        logger.log("\n=== Finalizing: Initiating resource cleanup ===")
        try:
            # logger.log("Skipping tear down to enable closer inspection...")
            logger.log("Tear down instances to preserve credit balance...")
            run_terraform("destroy -auto-approve")
        except Exception as destroy_err:
            logger.log(f"[WARNING] Destruction phase reported errors: {destroy_err}")
        
        logger.close()

if __name__ == "__main__":
    main()