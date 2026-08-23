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

sudo apt-get update -y && sudo apt-get install -y gnupg curl netcat python3-pip

# benchmark_mongo.py is executed directly on THIS node (see _run.py:
# execute_remote_benchmarks), so pymongo needs to live here too, not just on
# the driver node.
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
for i in {1..30}; do
    nc -z localhost 27017 && echo "MongoDB is up!" && break
    sleep 2
done

# Retry loop for mongosh user creation to handle internal startup lag
echo "Configuring MongoDB root user..."
for i in {1..10}; do
    if sudo mongosh --quiet --eval "
    try {
      db.getSiblingDB('admin').createUser({
        user: 'root',
        pwd: 'ultra-secure-multiword-string-password-1',
        roles: [{ role: 'root', db: 'admin' }]
      });
    } catch (e) {
      db.getSiblingDB('admin').updateUser('root', {
        pwd: 'ultra-secure-multiword-string-password-1',
        roles: [{ role: 'root', db: 'admin' }]
      });
    }
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
    echo -e "
security:
  authorization: enabled" | sudo tee -a /etc/mongod.conf > /dev/null
fi
sudo systemctl restart mongod
echo "Waiting for MongoDB to come back up with authorization enabled..."
for i in {1..30}; do
    nc -z localhost 27017 && echo "MongoDB is back up!" && break
    sleep 2
done
