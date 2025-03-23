# project4

install any dependendies as needed for gprc 


## proto initial setup:

python -m grpc_tools.protoc --proto_path=. --python_out=. --grpc_python_out=. chat.proto


## HOW TO RUN 


### servers: (for each terminal )

python replicated_server.py --server_id 1 --server_host localhost --server_port 50051 --initial_leader true

python replicated_server.py --server_id 2 --server_host localhost --server_port 50052 --initial_leader false

python replicated_server.py --server_id 3 --server_host localhost --server_port 50053 --initial_leader false





### new server:

python replicated_server.py --server_id 4 --server_host localhost --server_port 50054 --initial_leader false --join true




## Description 

Built on gPRC. 3 initial servers. Leader is elected with lowest id given priority. Heartbeat is sent from leader and replica list updated. Lease is used so that if replica does not receive signal from leader, election is triggered. Hearbeat is also sent by client to check connection. If client does not receive response, client will search for leader in replica list from config_client.json. 

If new server joins, it uses --join argument. It will find leader and get the chat state. Replica list will be updated so if the current leader crashes, the new server can become the leader and client will find it. 