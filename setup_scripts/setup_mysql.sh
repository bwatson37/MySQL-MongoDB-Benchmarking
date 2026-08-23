#!/bin/bash
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

# benchmark_mysql.py is executed directly on THIS node (see _run.py:
# execute_remote_benchmarks), so the connector library needs to live here too,
# not just on the driver node.
sudo python3 -m pip install --upgrade pip
sudo python3 -m pip install mysql-connector-python psutil

# Force bind-address to 0.0.0.0 globally in the primary config file
sudo sed -i 's/bind-address\s*=\s*127.0.0.1/bind-address = 0.0.0.0/g' /etc/mysql/mysql.conf.d/mysqld.cnf

# Extend network timeouts to survive slow disk I/O stalls
echo -e "
[mysqld]
net_read_timeout=1200
net_write_timeout=1200
max_allowed_packet=256M" | sudo tee -a /etc/mysql/mysql.conf.d/mysqld.cnf

sudo systemctl restart mysql

# Wait explicitly for port 3306 to open before running SQL commands
echo "Waiting for MySQL to accept connections..."
for i in {1..30}; do
    nc -z localhost 3306 && echo "MySQL is up!" && break
    sleep 2
done

sudo mysql -u root -e "
ALTER USER 'root'@'localhost' IDENTIFIED WITH mysql_native_password BY 'ultra-secure-multiword-string-password-1'; 
CREATE USER 'root'@'%' IDENTIFIED BY 'ultra-secure-multiword-string-password-1';
GRANT ALL PRIVILEGES ON *.* TO 'root'@'%' WITH GRANT OPTION; 
CREATE DATABASE IF NOT EXISTS benchmark_db;
FLUSH PRIVILEGES;
"
# NOTE: 'root'@'%' is only safe to expose because main.tf now restricts DB ports
# to the internal subnet rather than 0.0.0.0/0. Do not widen that firewall rule
# without also tightening this grant.

# Verify the connector actually imports before moving on, same as the driver check.
python3 -c "import mysql.connector; print('mysql-connector-python installed')"
