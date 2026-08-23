import os
from logger import logger

def run_proxy_setup(driver_gateway, target_ip, script_path, description):
    """Reusable helper to proxy commands with retry logic and detailed logging."""
    logger.log(f"  [PROXY] Routing {description} to {target_ip}...")
    
    proxy_cmd = (
        f"success=0; "
        f"for i in {{1..12}}; do "
        f"ssh -i /home/ubuntu/id_rsa_tmp -o StrictHostKeyChecking=no ubuntu@{target_ip} 'bash -s' < {script_path} "
        f"&& {{ success=1; break; }} || sleep 10; "
        f"done; "
        f"exit $((1 - success))"
    )
    
    # Execute and capture output
    _, out, _ = driver_gateway.exec_command(proxy_cmd, get_pty=True)
    
    for line in out:
        if line.strip():
            logger.log(f"    [{description} Build]: {line.strip()}")
            
    exit_status = out.channel.recv_exit_status()
    
    if exit_status == 0:
        logger.log(f"  [PASS] {description} setup complete.")
    else:
        logger.log(f"  [FAIL] {description} setup failed.")
        raise RuntimeError(f"{description} setup failed (exit status {exit_status}). See log above for details.")

def setup_nodes(driver_gateway, ips, password):
    # 1. SSH Key Staging
    d_sftp = driver_gateway.open_sftp()
    local_ssh_key = os.path.expanduser("~/.ssh/id_rsa")
    d_sftp.put(local_ssh_key, "/home/ubuntu/id_rsa_tmp")
    driver_gateway.exec_command("chmod 600 /home/ubuntu/id_rsa_tmp")

    # 2. Driver Node Setup
    driver_script = """#!/bin/bash
export DEBIAN_FRONTEND=noninteractive
sudo apt-get update -y
sudo apt-get install -y python3-pip

sudo python3 -m pip install --upgrade pip
sudo python3 -m pip install faker mysql-connector-python pymongo

cd /home/ubuntu
if [ ! -d "ycsb" ]; then
    curl -O --location https://github.com/brianfrankcooper/YCSB/releases/download/0.17.0/ycsb-0.17.0.tar.gz
    tar xfvz ycsb-0.17.0.tar.gz
    mv ycsb-0.17.0 ycsb
fi
chown -R ubuntu:ubuntu /home/ubuntu
"""
    with d_sftp.file("/home/ubuntu/setup_driver.sh", "w") as f: f.write(driver_script)
    stdin, stdout, stderr = driver_gateway.exec_command("bash /home/ubuntu/setup_driver.sh")
    stdout.channel.recv_exit_status() 
    
    # Force a verification check
    _, out, _ = driver_gateway.exec_command("python3 -c 'import faker; print(\"Faker installed\")'")
    if "Faker installed" not in out.read().decode():
        raise RuntimeError("Faker was not installed correctly on the driver node.")

    # 3. MySQL Setup
    mysql_script = f"""#!/bin/bash
export DEBIAN_FRONTEND=noninteractive

# --- SWAP CONFIGURATION ---
if [ ! -f /swapfile ]; then
    echo "Creating 4GB swap space to prevent OOM termination..."
    sudo fallocate -l 4G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
    sudo sysctl vm.swappiness=10
    echo 'vm.swappiness=10' | sudo tee -a /etc/sysctl.conf
fi
# --------------------------

sudo apt-get update -y && sudo apt-get install -y mysql-server netcat python3-pip

sudo python3 -m pip install --upgrade pip
sudo python3 -m pip install mysql-connector-python psutil

# Force bind-address to 0.0.0.0 globally in the primary config file
sudo sed -i 's/bind-address\\s*=\\s*127.0.0.1/bind-address = 0.0.0.0/g' /etc/mysql/mysql.conf.d/mysqld.cnf

# Extend network timeouts and increase packet size
echo -e "\n[mysqld]\nnet_read_timeout=1200\nnet_write_timeout=1200\nmax_allowed_packet=256M" | sudo tee -a /etc/mysql/mysql.conf.d/mysqld.cnf

sudo systemctl restart mysql

echo "Waiting for MySQL to accept connections..."
for i in {{1..30}}; do
    nc -z localhost 3306 && echo "MySQL is up!" && break
    sleep 2
done

sudo mysql -u root -e "
ALTER USER 'root'@'localhost' IDENTIFIED WITH mysql_native_password BY '{password}'; 
CREATE USER 'root'@'%' IDENTIFIED BY '{password}';
GRANT ALL PRIVILEGES ON *.* TO 'root'@'%' WITH GRANT OPTION; 
CREATE DATABASE IF NOT EXISTS benchmark_db;
FLUSH PRIVILEGES;
"
# NOTE: 'root'@'%' is only safe to expose because main.tf now restricts DB ports
# to the internal subnet rather than 0.0.0.0/0. Do not widen that firewall rule
# without also tightening this grant.

# Verify the connector actually imports before moving on, same as the driver check.
python3 -c "import mysql.connector; print('mysql-connector-python installed')"
"""
    with d_sftp.file("/home/ubuntu/setup_mysql.sh", "w") as f: f.write(mysql_script)
    run_proxy_setup(driver_gateway, ips['mysql_internal'], "/home/ubuntu/setup_mysql.sh", "MySQL")

    # 4. MongoDB Setup
    mongo_script = f"""#!/bin/bash
export DEBIAN_FRONTEND=noninteractive

# --- SWAP CONFIGURATION ---
if [ ! -f /swapfile ]; then
    echo "Creating 4GB swap space to prevent OOM termination..."
    sudo fallocate -l 4G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
    sudo sysctl vm.swappiness=10
    echo 'vm.swappiness=10' | sudo tee -a /etc/sysctl.conf
fi
# --------------------------

sudo apt-get update -y && sudo apt-get install -y gnupg curl netcat python3-pip

sudo python3 -m pip install --upgrade pip
sudo python3 -m pip install pymongo psutil

curl -fsSL https://www.mongodb.org/static/pgp/server-7.0.asc | sudo gpg --batch --yes --dearmor -o /usr/share/keyrings/mongodb-archive-keyring.gpg
echo "deb [ arch=amd64,arm64 signed-by=/usr/share/keyrings/mongodb-archive-keyring.gpg ] https://repo.mongodb.org/apt/ubuntu jammy/mongodb-org/7.0 multiverse" | sudo tee /etc/apt/sources.list.d/mongodb-org-7.0.list
sudo apt-get update -y
sudo apt-get install -y mongodb-org

# Ensure bindIp is set globally to 0.0.0.0
sudo sed -i 's/bindIp: 127.0.0.1/bindIp: 0.0.0.0/g' /etc/mongod.conf

sudo systemctl daemon-reload
sudo systemctl enable mongod
sudo systemctl restart mongod

# Wait explicitly for port 27017 to open before running shell commands
echo "Waiting for MongoDB service to open port 27017..."
for i in {{1..30}}; do
    nc -z localhost 27017 && echo "MongoDB is up!" && break
    sleep 2
done

# Retry loop for mongosh user creation to handle internal startup lag
echo "Configuring MongoDB root user..."
for i in {{1..10}}; do
    if sudo mongosh --quiet --eval "
    try {{
      db.getSiblingDB('admin').createUser({{
        user: 'root',
        pwd: '{password}',
        roles: [{{ role: 'root', db: 'admin' }}]
      }});
    }} catch (e) {{
      db.getSiblingDB('admin').updateUser('root', {{
        pwd: '{password}',
        roles: [{{ role: 'root', db: 'admin' }}]
      }});
    }}
    " 2>/dev/null; then
        echo "MongoDB root user configured successfully."
        break
    fi
    echo "MongoDB auth subsystem initializing, retrying in 3 seconds..."
    sleep 3
done

# Enforce authentication now that the root user exists. Without this, the root
# user above is decorative: any client that can reach port 27017 (including,
# currently, the whole internet - see main.tf) can connect with no credentials.
if ! grep -q "authorization: enabled" /etc/mongod.conf; then
    echo -e "\nsecurity:\n  authorization: enabled" | sudo tee -a /etc/mongod.conf > /dev/null
fi
sudo systemctl restart mongod
echo "Waiting for MongoDB to come back up with authorization enabled..."
for i in {{1..30}}; do
    nc -z localhost 27017 && echo "MongoDB is back up!" && break
    sleep 2
done
"""
    with d_sftp.file("/home/ubuntu/setup_mongodb.sh", "w") as f: f.write(mongo_script)
    run_proxy_setup(driver_gateway, ips['mongodb_internal'], "/home/ubuntu/setup_mongodb.sh", "MongoDB")
    
    d_sftp.close()