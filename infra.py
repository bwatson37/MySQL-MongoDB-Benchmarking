import subprocess
import json
from logger import logger

def run_terraform(action):
    """
    Executes Terraform commands.
    'action' should be a string like 'apply -auto-approve' or 'destroy -auto-approve'.
    """
    cmd = f"terraform {action}"
    logger.log(f"--- Running: {cmd} ---")
    
    process = subprocess.Popen(
        cmd, shell=True, 
        stdout=subprocess.PIPE, 
        stderr=subprocess.STDOUT, 
        text=True
    )
    
    for line in process.stdout:
        clean_line = line.strip()
        if clean_line:
            logger.log(f"  [Terraform] {clean_line}")
            
    process.wait()
    if process.returncode != 0:
        raise RuntimeError(f"Terraform command failed: {cmd}")

def get_outputs():
    """
    Extracts dynamic IP addresses from Terraform outputs.
    """
    result = subprocess.run("terraform output -json", shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError("Failed to retrieve terraform outputs.")
        
    outputs = json.loads(result.stdout)
    
    def find_val(possible_keys, default=""):
        for k in possible_keys:
            if k in outputs and "value" in outputs[k]:
                return outputs[k]["value"]
        return default

    return {
        "driver": find_val(["driver_public_ip", "driver_ip", "driver"]),
        "mysql_internal": find_val(["mysql_internal_ip", "mysql_private_ip", "mysql_internal"]),
        "mongodb_internal": find_val(["mongodb_internal_ip", "mongodb_private_ip", "mongodb_internal"])
    }