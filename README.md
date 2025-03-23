# project4

install any dependendies as needed for gprc 


## proto initial setup:

python -m grpc_tools.protoc --proto_path=. --python_out=. --grpc_python_out=. chat.proto


## HOW TO RUN 

### Configuration and Connectivity:

Default value is set in config.json

Multiple start config is set in config_master.json.
'instances' are NOT currently used.
launch_servers.py is NOT currently used in order to separate servers to respective shells. 

### servers: (for each terminal )

python replicated_server.py --server_id 1 --server_host localhost --server_port 50051 --initial_leader true

python replicated_server.py --server_id 2 --server_host localhost --server_port 50052 --initial_leader false

python replicated_server.py --server_id 3 --server_host localhost --server_port 50053 --initial_leader false


### clients: (for each terminal, two)

python client.py


### new server:

python replicated_server.py --server_id 4 --server_host localhost --server_port 50054 --initial_leader false --join true



### Timing Variables

Pertinent variables such as lease duration, time-out frequency is set in each config.

### Logging/troubleshooting

Each server/client has shell logging

to inspect database:

python list_db.py chosen.db





## Description 

Built on gPRC. 3 initial servers. Leader is elected with lowest id given priority. Heartbeat is sent from leader and replica list updated. Lease is used so that if replica does not receive signal from leader, election is triggered. Hearbeat is also sent by client to check connection. If client does not receive response, client will search for leader in replica list from config_client.json. 

If new server joins, it uses --join argument. It will find leader and get the chat state. Replica list will be updated so if the current leader crashes, the new server can become the leader and client will find it. 




========================================================



# Distributed Chat System Documentation

This document describes the overall design and flow of our distributed chat system. The system uses gRPC and SQLite to provide a persistent, fault-tolerant chat service with dynamic membership. Key features include:

- **Leader Election:**  
  Servers periodically monitor heartbeats and vote to elect a new leader if the current one fails.

- **Client Leader Discovery:**  
  Clients use concurrent RPC calls to discover the current leader and update their fallback replica list dynamically.

- **Runtime Replica List Distribution:**  
  The leader (and followers) maintain a runtime list of replica addresses. When a new server joins, its address is added to the list and distributed through `GetLeaderInfo` responses and the JoinCluster process.

- **Database Persistency and Replication:**  
  Each server maintains its own persistent SQLite database. The leader commits writes locally and then replicates operations (e.g., account creation, message sending) to followers. When a new server joins, it receives a full state transfer from the leader to synchronize its local database.

- **Dynamic Membership:**  
  New servers can join the cluster at runtime (even if they weren’t part of the original configuration) by calling the JoinCluster RPC. Once joined, the new server is added to the runtime replica list and participates in replication and leader election.

---

## 1. Leader Election

### Overview

- **Trigger:**  
  If a follower does not receive a heartbeat within a configured lease timeout, it starts an election.

- **Voting Rule:**  
  Each server votes "yes" if its `server_id` is greater than or equal to the candidate's `server_id`.

### Pertinent Code

```python
# Election RPC Handler:
def Election(self, request, context):
    return chat_pb2.ElectionResponse(vote_granted=(self.server_id >= request.candidate_id))

# Election process:
if not lower_id_found:
    self.is_leader = True  # Candidate wins the election
```

---

## 2. Client Leader Discovery

### Overview

- **Mechanism:**  
  The client periodically calls `GetLeaderInfo` on its fallback addresses concurrently. When a valid leader is found, the client updates its connection and merges the returned replica list with its local fallback list.

### Pertinent Code

```python
# In update_leader() on client:
resp = stub.GetLeaderInfo(GetLeaderInfoRequest(), timeout=FALLBACK_TIMEOUT)
if resp.success and resp.leader_address != "Unknown":
    self.connect_to_leader(resp.leader_address)
    client_config["replica_addresses"] |= set(resp.replica_addresses)
```

---

## 3. Runtime Replica List Distribution & Dynamic Membership

### Overview

- **New Server Join:**  
  When a server starts with `--join true`, it loads `config_master.json` to get all instance addresses, queries them via `GetLeaderInfo` to identify the current leader, and then calls `JoinCluster`.  
- **Replica List Update:**  
  The leader’s `JoinCluster` RPC adds the new server’s address to its runtime replica list and returns it as part of the state transfer. Both leader and followers return their current runtime replica list in `GetLeaderInfo`.

### Pertinent Code

**Leader’s JoinCluster RPC:**

```python
def JoinCluster(self, request, context):
    if request.new_server_address not in self.replica_addresses:
        self.replica_addresses.append(request.new_server_address)
    return chat_pb2.JoinClusterResponse(
        success=True,
        state=json.dumps(state),
        message="State transfer complete"
    )
```

**GetLeaderInfo (returns runtime replica list):**

```python
def GetLeaderInfo(self, request, context):
    return chat_pb2.GetLeaderInfoResponse(
        leader_address=(self.my_address if self.is_leader else self.current_leader_address),
        replica_addresses=self.replica_addresses
    )
```

**Client Merge of Replica List:**

```python
new_list = resp.replica_addresses if resp.replica_addresses else []
client_config["replica_addresses"] = list(set(client_config.get("replica_addresses", [])) | set(new_list))
```

---

## 4. Database Persistency and Replication

### Overview

- **Persistency:**  
  Each server maintains its own SQLite database for accounts and messages. The data is persisted locally, so a server can restart without losing data.
  
- **Replication:**  
  For write operations (like creating accounts or sending messages), the leader commits the change locally and then replicates the operation to all followers (excluding itself). The replication is done via a dedicated RPC (`ReplicateOperation`) where the operation type and its JSON-encoded data are sent.

- **New Server Synchronization:**  
  When a new server joins via JoinCluster, the leader sends a full state transfer (including all accounts and messages) so the new server can populate its own database.

### Pertinent Code

**Leader Write and Replication:**

```python
def CreateAccount(self, request, context):
    self.cursor.execute("INSERT INTO accounts (username, password) VALUES (?,?)", ...)
    self.conn.commit()
    self.replicate_to_followers("create_account", {...})
```

**Replication RPC:**

```python
def ReplicateOperation(self, request, context):
    # Decodes JSON data and applies the corresponding SQL commands.
```

**New Server Synchronization in join_cluster():**

```python
state = json.loads(resp.state)
self.cursor.execute("DELETE FROM accounts")
self.cursor.execute("DELETE FROM messages")
for account in state.get("accounts", []):
    self.cursor.execute("INSERT INTO accounts ...", (account["username"], account["password"]))
for message in state.get("messages", []):
    self.cursor.execute("INSERT INTO messages ...", (message["sender"], message["recipient"], ...))
self.conn.commit()
```

---

## 5. Handling Leader Failure

### Overview

- **Heartbeat Checks:**  
  Clients and followers send periodic heartbeats using `GetLeaderInfo`. If no valid heartbeat is received, they trigger a leader update process.
  
- **Fallback and Reconnection:**  
  The client queries all fallback addresses concurrently. When it finds a valid leader, it updates its connection and merges the runtime replica list from the response.

### Pertinent Code

```python
def client_heartbeat_check(self):
    resp = self.stub.GetLeaderInfo(GetLeaderInfoRequest(), timeout=RPC_TIMEOUT)
    if not (resp.success and resp.leader_address != "Unknown"):
        self.update_leader()
    else:
        client_config["replica_addresses"] |= set(resp.replica_addresses)
```
