#!/bin/bash
# NOTE: this script is NOT invoked by _run.py - the real pipeline embeds an
# equivalent (parameterized) script inline in provision.py. This file is a
# leftover/manual-testing copy and can drift out of sync (e.g. this one still
# has a hardcoded password below) - prefer editing provision.py instead.
export DEBIAN_FRONTEND=noninteractive
echo "=== Enable Ubuntu Universe Repository ==="
sudo apt-get install -y software-properties-common
sudo apt-add-repository universe -y
sudo apt-get update -y

echo "=== Installing Core System Architectures ==="
sudo apt-get install -y openjdk-11-jdk maven python3-pip git curl python-is-python3

echo "=== Installing Core Python Modules ==="
sudo pip3 install faker mysql-connector-python pymongo

cd /home/ubuntu
if [ ! -d "ycsb" ]; then
    curl -O --location https://github.com/brianfrankcooper/YCSB/releases/download/0.17.0/ycsb-0.17.0.tar.gz
    tar xfvz ycsb-0.17.0.tar.gz
    mv ycsb-0.17.0 ycsb
fi
chown -R ubuntu:ubuntu /home/ubuntu
