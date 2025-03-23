# project4


proto:

python replicated_server.py --server_id 4 --server_host localhost --server_port 50054 --initial_leader false --join true


servers:

project4 % python replicated_server.py --server_id 1 --server_host localhost --server_port 50051 --initial_leader true

python replicated_server.py --server_id 2 --server_host localhost --server_port 50052 --initial_leader false

python replicated_server.py --server_id 3 --server_host localhost --server_port 50053 --initial_leader false








new server:

python replicated_server.py --server_id 4 --server_host localhost --server_port 50054 --initial_leader false --join true
