# Distributed Chat System

This repository contains a distributed chat system built on gRPC and SQLite. The system is designed for persistency, fault tolerance, dynamic membership, and leader election. Each server maintains its own persistent database and replicates data to its followers. Clients dynamically discover the current leader and maintain a runtime fallback list of replicas.

---

## Table of Contents

1. [Requirements](#requirements)
2. [Setup](#setup)
   - [Installing Dependencies](#installing-dependencies)
   - [Compiling Proto Files](#compiling-proto-files)
3. [Configuration](#configuration)
   - [config.json and config_master.json](#configjson-and-config_masterjson)
   - [config_client.json](#config_clientjson)
4. [How to Run](#how-to-run)
   - [Starting Servers](#starting-servers)
   - [Starting Clients](#starting-clients)
   - [Adding a New Server](#adding-a-new-server)
   - [Inspecting the Database](#inspecting-the-database)
5. [System Flow Overview](#system-flow-overview)
   - [Leader Election](#leader-election)
   - [Client Leader Discovery](#client-leader-discovery)
   - [Runtime Replica List Distribution & Dynamic Membership](#runtime-replica-list-distribution--dynamic-membership)
   - [Database Persistency and Replication](#database-persistency-and-replication)
   - [Handling Leader Failure](#handling-leader-failure)
6. [Unit Testing](#unit-testing)
7. [Logging and Troubleshooting](#logging-and-troubleshooting)

---

## 1. Requirements

- Python 3.7+
- gRPC (`grpcio`, `grpcio-tools`)
- SQLite (built-in in Python)
- Tkinter (for client GUI)
- Additional packages: `coverage`, `unittest` for testing

---

## 2. Setup

### Installing Dependencies

Install required Python packages using pip:

```bash
pip install grpcio grpcio-tools coverage
```

### Compiling Proto Files

To generate the Python gRPC code from your `chat.proto` file, run:

```bash
python -m grpc_tools.protoc --proto_path=. --python_out=. --grpc_python_out=. chat.proto
```

---

## 3. Configuration

### config.json and config_master.json

- **config.json** contains default settings for a server (e.g., server_id, host, port, replica addresses, database file, heartbeat interval, lease timeout).
- **config_master.json** lists multiple server instances (even if not all are used at startup) and is used by new servers to discover candidates during JoinCluster.

### config_client.json

- **config_client.json** provides the client with the primary connection details, timeout values, and a fallback list of replica addresses.  
- **Note:** Use explicit IPv4 addresses (e.g., "127.0.0.1:50051") to avoid unintended IPv6 resolution.

---

## 4. How to Run

### Starting Servers

Open separate terminals and run the following commands:
(Replace localhost with an actual IPv4 address) 

```bash
python replicated_server.py --server_id 1 --server_host localhost --server_port 50051 --initial_leader true
python replicated_server.py --server_id 2 --server_host localhost --server_port 50052 --initial_leader false
python replicated_server.py --server_id 3 --server_host localhost --server_port 50053 --initial_leader false
```

### Starting Clients

Open separate terminals and run:

```bash
python client.py
```

### Adding a New Server

To add a new server at runtime (dynamic membership):
(Replace localhost with an actual IPv4 address) 

```bash
python replicated_server.py --server_id 4 --server_host localhost --server_port 50054 --initial_leader false --join true
```

This new server will join the cluster by finding the current leader (using `config_master.json` and runtime queries) and synchronizing its database.

### Inspecting the Database

To inspect a server’s SQLite database:

```bash
python list_db.py chosen.db
```

---

## 5. System Flow Overview

### Leader Election

- **Trigger:**  
  If a follower does not receive a heartbeat within the lease timeout, it starts an election.

- **Voting:**  
  Servers vote "yes" if their `server_id` is greater than or equal to the candidate's.
  
- **Pertinent Code:**

```python
def Election(self, request, context):
    return chat_pb2.ElectionResponse(vote_granted=(self.server_id >= request.candidate_id))
```

And in the election process:

```python
if not lower_id_found:
    self.is_leader = True  # Candidate wins the election
```

---

### Client Leader Discovery

- **Mechanism:**  
  Clients concurrently call `GetLeaderInfo` on fallback addresses. Upon receiving a valid leader response, the client updates its connection and merges the replica list.

- **Pertinent Code:**

```python
resp = stub.GetLeaderInfo(GetLeaderInfoRequest(), timeout=FALLBACK_TIMEOUT)
if resp.success and resp.leader_address != "Unknown":
    self.connect_to_leader(resp.leader_address)
    client_config["replica_addresses"] |= set(resp.replica_addresses)
```

---

### Runtime Replica List Distribution & Dynamic Membership

- **New Server Join:**  
  A new server uses the `--join` flag to call `JoinCluster`, during which the leader adds the new server’s address to its runtime list and returns state and updated replica list.

- **Pertinent Code (Leader’s JoinCluster):**

```python
def JoinCluster(self, request, context):
    if request.new_server_address not in self.replica_addresses:
        self.replica_addresses.append(request.new_server_address)
    return chat_pb2.JoinClusterResponse(state=json.dumps(state), message="State transfer complete")
```

- **Client Merging Replica List:**

```python
new_list = resp.replica_addresses if resp.replica_addresses else []
client_config["replica_addresses"] = list(set(client_config.get("replica_addresses", [])) | set(new_list))
```

---

### Database Persistency and Replication

- **Persistency:**  
  Each server uses SQLite to persist accounts and messages. The data remains available even if a server restarts.

- **Replication:**  
  The leader commits writes locally then sends a ReplicateOperation RPC to all followers (skipping itself) so that they update their local databases.

- **New Server Synchronization:**  
  When a new server joins, the leader sends a full state (all accounts and messages) to populate the new server’s database.

- **Pertinent Code (Replication and State Sync):**

```python
def CreateAccount(self, request, context):
    self.cursor.execute("INSERT INTO accounts (username, password) VALUES (?,?)", ...)
    self.conn.commit()
    self.replicate_to_followers("create_account", {...})
```

```python
state = json.loads(resp.state)
self.cursor.execute("DELETE FROM accounts")
for account in state.get("accounts", []):
    self.cursor.execute("INSERT INTO accounts ...")
self.conn.commit()
```

---

### Handling Leader Failure

- **Heartbeat Checks:**  
  Clients and followers send periodic `GetLeaderInfo` calls. If these calls fail or return an invalid leader, they trigger an update.

- **Fallback Reconnection:**  
  The client queries fallback addresses concurrently to find a valid leader, then updates its connection and merges the replica list.

- **Pertinent Code:**

```python
def client_heartbeat_check(self):
    resp = self.stub.GetLeaderInfo(GetLeaderInfoRequest(), timeout=RPC_TIMEOUT)
    if not (resp.success and resp.leader_address != "Unknown"):
        self.update_leader()
```

---

## 6. Unit Testing

### Dependencies
Install dependencies:
```bash
pip install grpcio grpcio-tools coverage
```

### Running Tests
Run your unit tests (for example, using unittest):
```bash
python -m unittest test_distributed_chat.py
```

---

## 7. Logging and Troubleshooting

- **Server Logging:**  
  Servers log current runtime replica lists in their heartbeat loops and in `GetLeaderInfo` responses.
  
- **Client Logging:**  
  Clients log updated fallback lists after heartbeat checks and leader lookups.

- **Example Logs:**

```plaintext
[Server Heartbeat] Current replica list: ['127.0.0.1:50051', '127.0.0.1:50054']
[Client Heartbeat] Updated replica list: ['127.0.0.1:50051', '127.0.0.1:50052', '127.0.0.1:50054']
```

These logs allow you to verify in real time that new servers are being added to the runtime replica lists, and that clients update their fallback lists accordingly.

---

## 8. Gen. AI Statement

  - We have used copilot, powered by GPT 4, in this assignment for research, debugging and implementation design. 
