import os
import json
import grpc
import threading
import time
import random
import sqlite3
import datetime
import logging
import argparse
from concurrent import futures
from concurrent.futures import ThreadPoolExecutor, as_completed

import chat_pb2
import chat_pb2_grpc

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--server_id", type=int, default=None)
    parser.add_argument("--server_host", type=str, default=None)
    parser.add_argument("--server_port", type=int, default=None)
    parser.add_argument("--initial_leader", type=lambda x: x.lower() in ('true','1','yes'), default=None)
    parser.add_argument("--join", type=lambda x: x.lower() in ('true','1','yes'), default=False,
                        help="Set to true if this server is joining an existing cluster")
    args = parser.parse_args()
    return args

args = parse_args()

with open("config.json", "r") as config_file:
    config = json.load(config_file)

if args.server_id is not None:
    config["server_id"] = args.server_id
if args.server_host is not None:
    config["server_host"] = args.server_host
if args.server_port is not None:
    config["server_port"] = args.server_port
if args.initial_leader is not None:
    config["initial_leader"] = args.initial_leader

if "REPLICA_ADDRESSES" in os.environ:
    config["replica_addresses"] = json.loads(os.environ["REPLICA_ADDRESSES"])
if "DB_FILE" in os.environ:
    config["db_file"] = os.environ["DB_FILE"]
else:
    config["db_file"] = f"chat_{config['server_id']}.db"
if "HEARTBEAT_INTERVAL" in os.environ:
    config["heartbeat_interval"] = int(os.environ["HEARTBEAT_INTERVAL"])
if "LEASE_TIMEOUT" in os.environ:
    config["lease_timeout"] = int(os.environ["LEASE_TIMEOUT"])

class ReplicatedChatService(chat_pb2_grpc.ChatServiceServicer):
    def __init__(self, config):
        self.config = config
        self.server_id = config.get("server_id", 1)
        self.is_leader = config.get("initial_leader", False)
        self.replica_addresses = config.get("replica_addresses", [])
        self.heartbeat_interval = config.get("heartbeat_interval", 3)
        self.lease_timeout = config.get("lease_timeout", 10)
        self.last_heartbeat = time.time()
        self.current_leader_address = None

        self.my_address = f"{config.get('server_host', 'localhost')}:{config.get('server_port', 50051)}"

        self.db_file = config.get("db_file", f"chat_{self.server_id}.db")
        self.conn = sqlite3.connect(self.db_file, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self.initialize_db()

        if self.is_leader:
            threading.Thread(target=self.send_heartbeat_loop, daemon=True).start()
        else:
            threading.Thread(target=self.election_monitor_loop, daemon=True).start()
            if args.join:
                self.join_cluster()

    def initialize_db(self):
        """Initialize SQLite tables if they do not exist."""
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS accounts (
                username TEXT PRIMARY KEY,
                password TEXT NOT NULL
            )
        ''')
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender TEXT,
                recipient TEXT,
                content TEXT,
                read INTEGER DEFAULT 0,
                timestamp TEXT
            )
        ''')
        self.conn.commit()

    def send_heartbeat_loop(self):
        """Leader sends heartbeat (with its address) to all replicas."""
        while True:
            for addr in self.replica_addresses:
                try:
                    channel = grpc.insecure_channel(addr)
                    stub = chat_pb2_grpc.ChatServiceStub(channel)
                    req = chat_pb2.HeartbeatRequest(
                        leader_id=self.server_id,
                        timestamp=int(time.time()),
                        leader_address=self.my_address
                    )
                    stub.Heartbeat(req, timeout=2)
                except Exception as e:
                    logging.error(f"Heartbeat to {addr} failed: {e}")
            time.sleep(self.heartbeat_interval)

    def election_monitor_loop(self):
        """Follower monitors heartbeat; trigger election if lease expires."""
        while True:
            if time.time() - self.last_heartbeat > self.lease_timeout:
                logging.info("Lease expired; starting election process.")
                self.start_election()
            time.sleep(1)

    def start_election(self):
        """Initiate an election after a random backoff to avoid contention."""
        backoff = random.uniform(0, 2)
        time.sleep(backoff)
        candidate_id = self.server_id
        lower_id_found = False
        for addr in self.replica_addresses:
            try:
                channel = grpc.insecure_channel(addr)
                stub = chat_pb2_grpc.ChatServiceStub(channel)
                req = chat_pb2.ElectionRequest(candidate_id=candidate_id)
                resp = stub.Election(req, timeout=2)
                if not resp.vote_granted:
                    lower_id_found = True
                    break
            except Exception as e:
                logging.error(f"Election RPC to {addr} failed: {e}")
        if not lower_id_found:
            self.is_leader = True
            logging.info("Elected as new leader.")
            threading.Thread(target=self.send_heartbeat_loop, daemon=True).start()
        else:
            logging.info("Election lost; remaining as follower.")

    def Heartbeat(self, request, context):
        """Update last heartbeat and current leader address on receiving heartbeat."""
        self.last_heartbeat = time.time()
        self.current_leader_address = request.leader_address
        return chat_pb2.HeartbeatResponse(success=True)

    def Election(self, request, context):
        candidate_id = request.candidate_id
        vote = True if self.server_id >= candidate_id else False
        return chat_pb2.ElectionResponse(vote_granted=vote)

    def ReplicateOperation(self, request, context):
        op_type = request.operation_type
        data = json.loads(request.data)
        try:
            if op_type == "create_account":
                self.cursor.execute("INSERT INTO accounts (username, password) VALUES (?,?)",
                                    (data["username"], data["password"]))
            elif op_type == "send_message":
                self.cursor.execute("INSERT INTO messages (sender, recipient, content, read, timestamp) VALUES (?,?,?,?,?)",
                                    (data["sender"], data["recipient"], data["content"], 0, data["timestamp"]))
            elif op_type == "delete_messages":
                for msg_id in data["message_ids"]:
                    self.cursor.execute("DELETE FROM messages WHERE id=?", (msg_id,))
            elif op_type == "delete_account":
                self.cursor.execute("DELETE FROM accounts WHERE username=?", (data["username"],))
                self.cursor.execute("DELETE FROM messages WHERE recipient=?", (data["username"],))
            self.conn.commit()
            return chat_pb2.ReplicationResponse(success=True)
        except Exception as e:
            logging.error(f"Replication operation failed: {e}")
            return chat_pb2.ReplicationResponse(success=False, message=str(e))

    def join_cluster(self):
        """
        Called when a new server (with --join true) starts.
        It loads config_master.json to obtain all instance addresses, then concurrently
        queries each (via GetLeaderInfo) to determine the current leader.
        Once a valid leader is found, the new server sends a JoinCluster request,
        and then synchronizes its local database with the state returned.
        """
        try:
            with open("config_master.json", "r") as f:
                master_config = json.load(f)
            instances = master_config.get("instances", [])
            candidate_addresses = []
            for instance in instances:
                addr = f"{instance['server_host']}:{instance['server_port']}"
                candidate_addresses.append(addr)
            def query_addr(addr):
                try:
                    channel = grpc.insecure_channel(addr)
                    stub = chat_pb2_grpc.ChatServiceStub(channel)
                    resp = stub.GetLeaderInfo(chat_pb2.GetLeaderInfoRequest(), timeout=2)
                    return addr, resp
                except Exception as ex:
                    return addr, None
            with ThreadPoolExecutor(max_workers=len(candidate_addresses)) as executor:
                futures = {executor.submit(query_addr, addr): addr for addr in candidate_addresses}
                for future in as_completed(futures, timeout=5):
                    addr, resp = future.result()
                    if resp and resp.success and resp.leader_address and resp.leader_address != "Unknown":
                        logging.info(f"Found leader at {resp.leader_address} via candidate {addr}")
                        self.current_leader_address = resp.leader_address
                        break
            if not self.current_leader_address:
                logging.error("No leader found among candidate addresses.")
                return
            channel = grpc.insecure_channel(self.current_leader_address)
            stub = chat_pb2_grpc.ChatServiceStub(channel)
            req = chat_pb2.JoinClusterRequest(new_server_address=self.my_address)
            resp = stub.JoinCluster(req, timeout=3)
            if resp.success:
                logging.info("Successfully joined cluster. State transferred.")
                self.last_heartbeat = time.time()
                # Synchronize local database with state from the leader.
                state = json.loads(resp.state)
                accounts = state.get("accounts", [])
                messages = state.get("messages", [])
                # Clear local tables.
                self.cursor.execute("DELETE FROM accounts")
                self.cursor.execute("DELETE FROM messages")
                for account in accounts:
                    self.cursor.execute("INSERT INTO accounts (username, password) VALUES (?, ?)",
                                        (account["username"], account["password"]))
                for message in messages:
                    self.cursor.execute("INSERT INTO messages (sender, recipient, content, read, timestamp) VALUES (?, ?, ?, ?, ?)",
                                        (message["sender"], message["recipient"], message["content"], message["read"], message["timestamp"]))
                self.conn.commit()
            else:
                logging.error("Failed to join cluster: " + resp.message)
        except Exception as e:
            logging.error("JoinCluster RPC failed: " + str(e))

    def JoinCluster(self, request, context):
        new_server_address = request.new_server_address
        if new_server_address and new_server_address not in self.replica_addresses:
            self.replica_addresses.append(new_server_address)
            logging.info(f"New server {new_server_address} registered.")
        self.cursor.execute("SELECT username, password FROM accounts")
        accounts = [{"username": row[0], "password": row[1]} for row in self.cursor.fetchall()]
        self.cursor.execute("SELECT id, sender, recipient, content, read, timestamp FROM messages")
        messages = [{"id": row[0], "sender": row[1], "recipient": row[2], "content": row[3],
                     "read": row[4], "timestamp": row[5]} for row in self.cursor.fetchall()]
        state = {"accounts": accounts, "messages": messages}
        return chat_pb2.JoinClusterResponse(success=True, state=json.dumps(state), message="State transfer complete")

    def GetLeaderInfo(self, request, context):
        if self.is_leader:
            return chat_pb2.GetLeaderInfoResponse(
                success=True,
                leader_address=self.my_address,
                message="I am leader",
                replica_addresses=self.replica_addresses
            )
        else:
            addr = self.current_leader_address if self.current_leader_address else "Unknown"
            # For followers, return the local replica_addresses list (even if it might be stale)
            return chat_pb2.GetLeaderInfoResponse(
                success=True,
                leader_address=addr,
                message="Follower reporting leader info",
                replica_addresses=self.replica_addresses
            )

    def replicate_to_followers(self, op_type, data):
        req = chat_pb2.ReplicationRequest(operation_type=op_type, data=json.dumps(data))
        for addr in self.replica_addresses:
            # Skip self to avoid duplicate insertion.
            if addr == self.my_address:
                continue
            try:
                channel = grpc.insecure_channel(addr)
                stub = chat_pb2_grpc.ChatServiceStub(channel)
                stub.ReplicateOperation(req, timeout=2)
            except Exception as e:
                logging.error(f"Replication to {addr} failed: {e}")

    # Client-facing RPCs (only leader processes writes)
    def CreateAccount(self, request, context):
        if not self.is_leader:
            return chat_pb2.CreateAccountResponse(success=False, message="Not leader. Please contact the leader.")
        username = request.username
        password = request.password
        if not username or not password:
            return chat_pb2.CreateAccountResponse(success=False, message="Username or password missing")
        try:
            self.cursor.execute("INSERT INTO accounts (username, password) VALUES (?,?)", (username, password))
            self.conn.commit()
        except sqlite3.IntegrityError:
            return chat_pb2.CreateAccountResponse(success=False, message="Username already taken")
        self.replicate_to_followers("create_account", {"username": username, "password": password})
        logging.info(f"Account created: {username}")
        return chat_pb2.CreateAccountResponse(success=True, message=f"Account '{username}' created successfully")

    def Login(self, request, context):
        username = request.username
        password = request.password
        if not username or not password:
            return chat_pb2.LoginResponse(success=False, message="Username or password missing", unread_count=0)
        self.cursor.execute("SELECT password FROM accounts WHERE username=?", (username,))
        row = self.cursor.fetchone()
        if row is None:
            return chat_pb2.LoginResponse(success=False, message="No such user", unread_count=0)
        if row[0] != password:
            return chat_pb2.LoginResponse(success=False, message="Incorrect password", unread_count=0)
        self.cursor.execute("SELECT COUNT(*) FROM messages WHERE recipient=? AND read=0", (username,))
        unread_count = self.cursor.fetchone()[0]
        logging.info(f"User logged in: {username}")
        return chat_pb2.LoginResponse(success=True, message=f"User '{username}' logged in successfully", unread_count=unread_count)

    def ListAccounts(self, request, context):
        pattern = request.pattern
        if pattern:
            self.cursor.execute("SELECT username FROM accounts WHERE username LIKE ?", ('%'+pattern+'%',))
        else:
            self.cursor.execute("SELECT username FROM accounts")
        accounts = [row[0] for row in self.cursor.fetchall()]
        logging.info(f"Listing accounts with pattern: '{pattern}'")
        return chat_pb2.ListAccountsResponse(success=True, accounts=accounts)

    def SendMessage(self, request, context):
        if not self.is_leader:
            return chat_pb2.SendMessageResponse(success=False, message="Not leader. Please contact the leader.")
        sender = request.sender
        recipient = request.to
        content = request.content
        if not sender or not recipient or content is None:
            return chat_pb2.SendMessageResponse(success=False, message="Missing fields")
        timestamp_str = datetime.datetime.now().strftime('%m/%d %H:%M')
        try:
            self.cursor.execute("INSERT INTO messages (sender, recipient, content, read, timestamp) VALUES (?,?,?,?,?)",
                                (sender, recipient, content, 0, timestamp_str))
            self.conn.commit()
        except Exception as e:
            return chat_pb2.SendMessageResponse(success=False, message=str(e))
        self.replicate_to_followers("send_message", {
            "sender": sender,
            "recipient": recipient,
            "content": content,
            "timestamp": timestamp_str
        })
        logging.info(f"Message from '{sender}' to '{recipient}' sent")
        return chat_pb2.SendMessageResponse(success=True, message="Message sent successfully")

    def ReadNewMessages(self, request, context):
        username = request.username
        count = request.count
        if not username:
            return chat_pb2.ReadNewMessagesResponse(success=False, messages=[])
        self.cursor.execute("SELECT id, sender, content, timestamp FROM messages WHERE recipient=? AND read=0", (username,))
        rows = self.cursor.fetchall()
        unread = rows if count <= 0 or count > len(rows) else rows[:count]
        for row in unread:
            self.cursor.execute("UPDATE messages SET read=1 WHERE id=?", (row[0],))
        self.conn.commit()
        messages = [f"{r[3]} - From: {r[1]} - {r[2]}" for r in unread]
        logging.info(f"Read {len(messages)} new messages for user '{username}'")
        return chat_pb2.ReadNewMessagesResponse(success=True, messages=messages)

    def DeleteMessages(self, request, context):
        if not self.is_leader:
            return chat_pb2.DeleteMessagesResponse(success=False, message="Not leader. Please contact the leader.")
        username = request.username
        msg_ids = request.message_ids
        if not username or not msg_ids:
            return chat_pb2.DeleteMessagesResponse(success=False, message="Missing fields")
        try:
            if len(msg_ids) == 1 and msg_ids[0] == -1:
                self.cursor.execute("DELETE FROM messages WHERE recipient=?", (username,))
            else:
                for i in msg_ids:
                    self.cursor.execute("DELETE FROM messages WHERE id=? AND recipient=?", (i, username))
            self.conn.commit()
        except Exception as e:
            return chat_pb2.DeleteMessagesResponse(success=False, message=str(e))
        self.replicate_to_followers("delete_messages", {"username": username, "message_ids": list(msg_ids)})
        logging.info(f"Deleted messages for user '{username}'")
        return chat_pb2.DeleteMessagesResponse(success=True, message="Messages deleted successfully")

    def DeleteAccount(self, request, context):
        if not self.is_leader:
            return chat_pb2.DeleteAccountResponse(success=False, message="Not leader. Please contact the leader.")
        username = request.username
        if not username:
            return chat_pb2.DeleteAccountResponse(success=False, message="Username missing")
        try:
            self.cursor.execute("DELETE FROM accounts WHERE username=?", (username,))
            self.cursor.execute("DELETE FROM messages WHERE recipient=?", (username,))
            self.conn.commit()
        except Exception as e:
            return chat_pb2.DeleteAccountResponse(success=False, message=str(e))
        self.replicate_to_followers("delete_account", {"username": username})
        logging.info(f"Account deleted: {username}")
        return chat_pb2.DeleteAccountResponse(success=True, message=f"Account '{username}' deleted successfully")

    def ListMessages(self, request, context):
        username = request.username
        if not username:
            return chat_pb2.ListMessagesResponse(success=False, messages=[])
        self.cursor.execute("SELECT sender, content, timestamp FROM messages WHERE recipient=? AND read=1", (username,))
        rows = self.cursor.fetchall()
        messages = [f"{r[2]} - From: {r[0]} - {r[1]}" for r in rows]
        logging.info(f"Listing all read messages for user '{username}'")
        return chat_pb2.ListMessagesResponse(success=True, messages=messages)

def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    chat_service = ReplicatedChatService(config)
    chat_pb2_grpc.add_ChatServiceServicer_to_server(chat_service, server)
    bind_address = f"{config.get('server_host', 'localhost')}:{config.get('server_port', 50051)}"
    server.add_insecure_port(bind_address)
    server.start()
    print(f"Server started on {bind_address} | server_id: {chat_service.server_id} | Leader: {chat_service.is_leader}")
    try:
        while True:
            time.sleep(86400)
    except KeyboardInterrupt:
        print("Shutting down server")
        server.stop(0)

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    serve()
