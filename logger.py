import os
from datetime import datetime


class ClusterLogger:
    """Simultaneously writes all pipeline outputs to the screen and a text file."""

    def __init__(self, log_dir="local_logs", filename_prefix="cluster_run"):
        # 1. Create the directory if it doesn't exist
        os.makedirs(log_dir, exist_ok=True)

        # 2. Create a timestamp for the filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{filename_prefix}_{timestamp}.txt"
        filepath = os.path.join(log_dir, filename)

        # 3. Open the file in the new path
        self.logfile = open(filepath, "w", encoding="utf-8")
        self.log(f"=== CLUSTER EXECUTION LOG: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")
        self.log(f"Log file created at: {filepath}")

    def log(self, message):
        print(message)
        self.logfile.write(message + "\n")
        self.logfile.flush()

    def close(self):
        self.logfile.close()

# Create a single global instance
logger = ClusterLogger(log_dir="local_logs")