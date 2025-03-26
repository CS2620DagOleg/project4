**Implementation Notebook**  
1\. Implementation Process  
The chat system is built on gRPC and SQLite. The fundamental components of our system are replicated servers, clients, and configuration files for defining connectivity. 

## **Replicated Server**

A requirement in the assignment instructions is to build a fault-tolerant system, where our chat application is robust against failures. In our previous implementation, if the server crashed, users would lose access to the chat and messages are also lost, to mitigate this we decided to use multiple servers that work together. These servers will have to synchronize and the configuration we agreed to for the synchronization is the following:

1. One server acts as the leader  
2. Other servers act as Followers  
3. Data is replicated across all servers  
4. Leader election happens if the leader fails

### The Leader

The leader server is the one that is in charge of all the primary operations in the chatbot, these primary operations include creating accounts, sending messages, and deleting accounts. The servers have configurations and when they start the first thing they do should be to check if they are chosen to be leaders initially, if initial\_leader=true, then the server declares itself as the leader. The reason we need a leader is because without a leader we have conflicts, because we might run into a situation where two users try to register with the same username and we don’t know which server should win, so we have one authoritative server that processes all the fundamental stuff while the others just make sure they sync up with the leader.

**But how does a new server know if there already is a leader?**

We make sure that a message is sent from the leader to the followers in regular intervals, and we call these **heartbeats,** so if a new server receives heartbeats it means a leader exists already so it just becomes a follower. If it doesn’t have heartbeats within the interval, then we know there is no leader and the servers should undergo elections and the server with the lowest server\_id wins the election and assumes the position of a leader. 

### Server Communication

gRPC is the protocol for remote communication, the config file has the list of all the server replicas:  
"replica\_addresses": \["192.168.137.75:50051", "192.168.137.75:50052", "192.168.137.75:50053"\]

And using this list, heartbeats are sent and leaders are found, the heartbeats are sent using the following code. 

def send\_heartbeat\_loop(self):  
         
        while True:  
            for addr in self.replica\_addresses:  
                if addr \== self.my\_address:  
                    continue  
                try:  
                    channel \= grpc.insecure\_channel(addr)  
                    stub \= chat\_pb2\_grpc.ChatServiceStub(channel)  
                    req \= chat\_pb2.HeartbeatRequest(  
                        leader\_id\=self.server\_id,  
                        timestamp\=int(time.time()),  
                        leader\_address\=self.my\_address  
                    )  
                    stub.Heartbeat(req, timeout\=2)  
                except Exception as e:  
                    logging.error(f"Heartbeat to {addr} failed: {e}")  
            logging.info(f"\[Server Heartbeat\] Current replica list: {self.replica\_addresses}")  
            time.sleep(self.heartbeat\_interval)

Each server would be like a computer, with its own SQLite database, the following is the process that happens. 

1. The leader executes all the fundamental operations  
2. The leader replicates all changes to followers  
3. Followers apply the changes to their SQLite database.   
     
   

Here is how the replication to followers is done:  
def replicate\_to\_followers(self, op\_type, data):  
        req \= chat\_pb2.ReplicationRequest(operation\_type\=op\_type, data\=json.dumps(data))  
        for addr in self.replica\_addresses:  
            if addr \== self.my\_address:  
                continue  
            try:  
                channel \= grpc.insecure\_channel(addr)  
                stub \= chat\_pb2\_grpc.ChatServiceStub(channel)  
                stub.ReplicateOperation(req, timeout\=2)  
            except Exception as e:  
                logging.error(f"Replication to {addr} failed: {e}")

## **New Servers**

When new servers join, they find the leader by querying known replicas and sending a request to join the cluster and receive the users and messages that would then allow them to sync.

**Finding the Leader**

**Requesting to join the cluster**

**def JoinCluster(self, request, context):**  
        **new\_server\_address \= request.new\_server\_address**  
        **if new\_server\_address and new\_server\_address not in self.replica\_addresses:**  
            **self.replica\_addresses.append(new\_server\_address)**  
            **logging.info(f"New server {new\_server\_address} registered.")**  
        **self.cursor.execute("SELECT username, password FROM accounts")**  
        **accounts \= \[{"username": row\[0\], "password": row\[1\]} for row in self.cursor.fetchall()\]**  
        **self.cursor.execute("SELECT id, sender, recipient, content, read, timestamp FROM messages")**  
        **messages \= \[{"id": row\[0\], "sender": row\[1\], "recipient": row\[2\], "content": row\[3\],**  
                     **"read": row\[4\], "timestamp": row\[5\]} for row in self.cursor.fetchall()\]**  
        **state \= {"accounts": accounts, "messages": messages}**  
        **logging.info(f"\[JoinCluster RPC\] Returning runtime replica list: {self.replica\_addresses}")**  
        **return chat\_pb2.JoinClusterResponse(success\=True, state\=json.dumps(state), message\="State transfer complete")**

## **The Client**

Because the client is in need of the fundamental operations, it connects to the leader that allows users to login and authenticate, send messages, read messages. If the client cannot discover the leader, it finds the leader by doing a query of all known replicas and updating the connection based on that. The query to find the leader should also be part of the implementation:  
 def update\_leader(self):  
         
        fallback \= client\_config.get("replica\_addresses", \[\])  
        def query\_addr(addr):  
            try:  
                channel \= grpc.insecure\_channel(addr)  
                stub \= chat\_pb2\_grpc.ChatServiceStub(channel)  
                resp \= stub.GetLeaderInfo(chat\_pb2.GetLeaderInfoRequest(), timeout\=FALLBACK\_TIMEOUT)  
                return addr, resp  
            except Exception as ex:  
                return addr, None

We also have an implementation for client service communication, which will require its own method. And configuration files, for a single server we have config.json, and config\_client.json. 

Everything comes together in the following way:

1.  **Client connects to the leader.**  
2. **Leader handles writes and replicates data to followers.**  
3. **If the leader fails, an election is held.**  
4. **Clients detect failure and find the new leader.**  
   1. **It does so in parallel thread process to save time and improve user experience through efficient and seamless operation.**  
5. **New servers can join dynamically.**

# 2\. Design Process

1. Why a Leader-Follower Model?  
   Having only one leader means no conflicts and helps in consistency, which means if we have the leader writing once and replicating it across all the followers would mean we prevent inconsistency. We also have a mechanism where if a leader fails, followers detect the failure and elect a new leader automatically.   
     
   If we instead went with multi-leader or leaderless configuration, then we would have the eventual consistency problem. Changes eventually propagate to all nodes over time but there is no guarantee that at any moment all the replicas have the same data.   
    Propagation delays due to connectivity or network delays could ultimately result in weird stuff happening such as messages appearing out of order.   
     
   The trade-off  for using a leader-follower model is that if the leader crashes no one can write until a new leader is elected. Overloading in high-traffic scenarios is one example of this happening.   
     
2. Why use Heartbeats to detect leaders?  
   

This way, each follower is detected independently and heartbeats are also very cheap to send and can be sent within seconds which helps in fast failure detection. The alternative implementation is for clients to instead ping the leader and periodically check if the leader responds which means incase of failures if we have no clients then leaders go undetected.   
Heartbeats in and of themselves are sources of network traffic, but overall given the robust system they provide we can take that. 

3. Why electing based on server\_id?  
   This makes the election process somewhat deterministic, and gets rid of randomness. A trade off here is that  If a low server\_id server is slow, it might win elections but perform poorly as a leader.  
     
4. Why SQLite for storage?  
   The primary reason we chose SQLite is because each server has its own SQLite database which can be easily replicated from the leader. No separate database needed which makes deployments easier. The alternative would be to use some kind of database that is shared among the servers which means the database crashing would be the same as the entire system going down. A trade off is that SQLite only allows one write operation at a time per database file. If multiple processes try to write at the same time, the database **serializes** those writes, causing a bottleneck. This is due to database-level locking, where an entire database file is locked during a write.   
     
5. **Why gRPC and not REST?**  
   The client-server communication is using gRPC, each function like CreateAccount and SendMessage is a **gRPC.** The protocol for gRPC is faster and smaller than JSON and it is also language agnostic. Real-time message updates are handled more efficiently than REST which uses JSON over HTTP, and REST is not particularly strong for persistent streaming. 

   

6. **Why dynamic storage of replicas?**  
   Everything is dynamic, or automatic, which means servers can join or leave without modifying configuration files. There will be no need to join or leave without modifying configuration files. The replica list still needs to be kept updated though. 

   3\. **Special Cases, Bonus**

A new server, which was not registered in the initial state and its IPv4 is unknown, starts and looks for a leader, because it is not initially a leader, it does not start an election, it just finds the leader by looking up existing servers from a list in config\_master.json. The new server then requests to join the cluster, once it finds the leader through receiving RPC message containing leader info which each replica or leader has, there is a JoinCluster request and the leader adds the new server to its runtime replica list, and full database transfer is sent to sync the new server. The new server receives the database state and applies it to its local SQLite database.

Client also updates its own server list through heartbeat response from the leader so if client’s heartbeat fails after a timeout, client will look in parallel threads for a functional server and once it finds an active leader, it will establish a connection with the leader server. 

Any number of new servers can be added. We Delete any old data in the new server’s database (to avoid conflicts), and are restoring the full state received from the leader. Then all the servers will know it automatically. 
7. **Gen AI Statement**
We have used copilot, powered by GPT 4, in this assignment for research, debugging and implementation design.


