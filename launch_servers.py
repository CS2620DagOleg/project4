import json
import subprocess
import os

def main():
    # Load the master config file.
    with open("config_master.json", "r") as f:
        master_config = json.load(f)
    
    instances = master_config["instances"]
    replica_addresses = master_config["replica_addresses"]
    db_file = master_config.get("db_file", "chat.db")
    heartbeat_interval = master_config.get("heartbeat_interval", 3)
    lease_timeout = master_config.get("lease_timeout", 10)
    
    processes = []
    
    # Launch each server instance.
    for instance in instances:
        args = [
            "python", "replicated_server.py",
            "--server_id", str(instance["server_id"]),
            "--server_host", instance["server_host"],
            "--server_port", str(instance["server_port"]),
            "--initial_leader", str(instance.get("initial_leader", False))
        ]
        
        env = os.environ.copy()
        env["REPLICA_ADDRESSES"] = json.dumps(replica_addresses)
        env["DB_FILE"] = db_file
        env["HEARTBEAT_INTERVAL"] = str(heartbeat_interval)
        env["LEASE_TIMEOUT"] = str(lease_timeout)
        
        print(f"Launching server instance with args: {args}")
        proc = subprocess.Popen(args, env=env)
        processes.append(proc)
    
    # Wait for all server processes (or press Ctrl+C to terminate).
    for proc in processes:
        proc.wait()

if __name__ == '__main__':
    main()
