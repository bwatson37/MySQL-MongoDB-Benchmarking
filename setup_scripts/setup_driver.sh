#!/bin/bash
export DEBIAN_FRONTEND=noninteractive
sudo apt-get update -y
sudo apt-get install -y python3-pip

# Install explicitly using the python3 module flag to ensure correct path
sudo python3 -m pip install --upgrade pip
sudo python3 -m pip install faker mysql-connector-python pymongo

cd /home/ubuntu
if [ ! -d "ycsb" ]; then
    curl -O --location https://github.com/brianfrankcooper/YCSB/releases/download/0.17.0/ycsb-0.17.0.tar.gz
    tar xfvz ycsb-0.17.0.tar.gz
    mv ycsb-0.17.0 ycsb
fi
chown -R ubuntu:ubuntu /home/ubuntu
