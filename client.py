import os
import json
import tkinter as tk
from tkinter import messagebox, simpledialog
import grpc
import hashlib
import time

import chat_pb2
import chat_pb2_grpc

# ---------------------------
# Load client configuration from config_client.json
# ---------------------------
with open("config_client.json", "r") as config_file:
    client_config = json.load(config_file)

# Force IPv4: use 127.0.0.1 instead of "localhost"
host = client_config.get("client_connect_host", "127.0.0.1")
if host == "localhost":
    host = "127.0.0.1"
client_config["client_connect_host"] = host

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

class ChatClientApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Chat Client")
        self.geometry("400x350")
        self.current_user = None

        # Connect using IPv4
        self.leader_address = f"{client_config['client_connect_host']}:{client_config['client_connect_port']}"
        self.connect_to_leader(self.leader_address)

        container = tk.Frame(self)
        container.pack(fill="both", expand=True)
        self.frames = {}
        for FrameClass in (StartFrame, MainFrame):
            frame = FrameClass(parent=container, controller=self)
            self.frames[FrameClass] = frame
            frame.grid(row=0, column=0, sticky="nsew")
        self.show_frame(StartFrame)

    def connect_to_leader(self, address):
        """Establish a new channel with the provided leader address."""
        self.leader_address = address
        self.channel = grpc.insecure_channel(address)
        self.stub = chat_pb2_grpc.ChatServiceStub(self.channel)
        print(f"Connected to leader at {address}")

    def update_leader(self):
        """
        Call GetLeaderInfo RPC to update the leader address and reconnect.
        If the call fails, keep the current connection.
        """
        try:
            resp = self.stub.GetLeaderInfo(chat_pb2.GetLeaderInfoRequest(), timeout=2)
            if resp.success and resp.leader_address and resp.leader_address != "Unknown":
                if resp.leader_address != self.leader_address:
                    print(f"Leader changed to {resp.leader_address}")
                self.connect_to_leader(resp.leader_address)
                # Also update fallback replica addresses.
                client_config["replica_addresses"] = list(resp.replica_addresses)
                return
        except Exception as e:
            print("Current leader lookup failed. Trying fallback addresses...", e)
            time.sleep(1)

    def call_rpc_with_retry(self, func, request, retries=3):
        """
        Helper to call an RPC. On UNAVAILABLE error, update leader and retry.
        """
        for i in range(retries):
            try:
                return func(request, timeout=3)
            except grpc.RpcError as e:
                if e.code() == grpc.StatusCode.UNAVAILABLE:
                    print("RPC UNAVAILABLE. Updating leader and retrying...")
                    self.update_leader()
                    time.sleep(1)
                else:
                    raise
        raise Exception("RPC failed after retries.")

    def show_frame(self, frame_class):
        frame = self.frames[frame_class]
        frame.tkraise()

    def set_current_user(self, username):
        self.current_user = username

    def get_current_user(self):
        return self.current_user

    def cleanup(self):
        self.destroy()

class StartFrame(tk.Frame):
    def __init__(self, parent, controller: ChatClientApp):
        super().__init__(parent)
        self.controller = controller
        tk.Label(self, text="Welcome to the Chat Client", font=("Arial", 14, "bold")).pack(pady=10)
        tk.Button(self, text="Create Account", width=20, command=self.create_account).pack(pady=5)
        tk.Button(self, text="Login", width=20, command=self.login).pack(pady=5)
        tk.Button(self, text="Exit", width=20, command=self.exit_app).pack(pady=5)

    def create_account(self):
        username = simpledialog.askstring("Create Account", "Enter a new username:", parent=self)
        if not username:
            return
        password = simpledialog.askstring("Create Account", "Enter a new password:", parent=self, show="*")
        if not password:
            return
        hashed_pass = hash_password(password)
        request = chat_pb2.CreateAccountRequest(username=username, password=hashed_pass)
        try:
            response = self.controller.call_rpc_with_retry(self.controller.stub.CreateAccount, request)
        except Exception as e:
            messagebox.showerror("Error", str(e))
            return
        if response.success:
            messagebox.showinfo("Success", response.message)
        else:
            messagebox.showerror("Error", response.message)

    def login(self):
        username = simpledialog.askstring("Login", "Enter username:", parent=self)
        if not username:
            return
        password = simpledialog.askstring("Login", "Enter password:", parent=self, show="*")
        if not password:
            return
        hashed_pass = hash_password(password)
        request = chat_pb2.LoginRequest(username=username, password=hashed_pass)
        try:
            response = self.controller.call_rpc_with_retry(self.controller.stub.Login, request)
        except Exception as e:
            messagebox.showerror("Error", str(e))
            return
        if response.success:
            self.controller.set_current_user(username)
            messagebox.showinfo("Logged In", f"{response.message}\nUnread messages: {response.unread_count}")
            self.controller.show_frame(MainFrame)
        else:
            messagebox.showerror("Error", response.message)

    def exit_app(self):
        self.controller.cleanup()

class MainFrame(tk.Frame):
    def __init__(self, parent, controller: ChatClientApp):
        super().__init__(parent)
        self.controller = controller
        tk.Label(self, text="Main Menu", font=("Arial", 14, "bold")).pack(pady=10)
        self.logged_in_label = tk.Label(self, text="", font=("Arial", 10, "italic"))
        self.logged_in_label.pack(pady=(0, 10))
        tk.Button(self, text="List Accounts", width=20, command=self.list_accounts).pack(pady=5)
        tk.Button(self, text="Send Message", width=20, command=self.send_message).pack(pady=5)
        tk.Button(self, text="Read New Messages", width=20, command=self.read_new_messages).pack(pady=5)
        tk.Button(self, text="Show All Messages", width=20, command=self.show_all_messages).pack(pady=5)
        tk.Button(self, text="Delete My Account", width=20, command=self.delete_account).pack(pady=5)
        tk.Button(self, text="Logout", width=20, command=self.logout).pack(pady=5)

    def tkraise(self, aboveThis=None):
        user = self.controller.get_current_user()
        self.logged_in_label.config(text=f"Logged in as: {user}" if user else "Not logged in")
        super().tkraise(aboveThis)

    def list_accounts(self):
        pattern = simpledialog.askstring("List Accounts", "Enter wildcard pattern (or leave blank):", parent=self)
        if pattern is None:
            pattern = ""
        request = chat_pb2.ListAccountsRequest(username=self.controller.get_current_user(), pattern=pattern)
        try:
            response = self.controller.call_rpc_with_retry(self.controller.stub.ListAccounts, request)
        except Exception as e:
            messagebox.showerror("Error", str(e))
            return
        if response.success:
            msg = "\n".join(response.accounts) if response.accounts else "No matching accounts found."
            messagebox.showinfo("Accounts", msg)
        else:
            messagebox.showerror("Error", "Error listing accounts.")

    def send_message(self):
        recipient = simpledialog.askstring("Send Message", "Recipient username:", parent=self)
        if not recipient:
            return
        content = simpledialog.askstring("Send Message", "Message content:", parent=self)
        if content is None:
            return
        request = chat_pb2.SendMessageRequest(
            sender=self.controller.get_current_user(),
            to=recipient,
            content=content
        )
        try:
            response = self.controller.call_rpc_with_retry(self.controller.stub.SendMessage, request)
        except Exception as e:
            messagebox.showerror("Error", str(e))
            return
        if response.success:
            messagebox.showinfo("Success", response.message)
        else:
            messagebox.showerror("Error", response.message)

    def read_new_messages(self):
        count_str = simpledialog.askstring("Read New Messages", "How many new messages to read? (leave blank for all)", parent=self)
        if count_str is None or count_str.strip() == "":
            count = 0
        else:
            try:
                count = int(count_str)
            except ValueError:
                count = 0
        request = chat_pb2.ReadNewMessagesRequest(username=self.controller.get_current_user(), count=count)
        try:
            response = self.controller.call_rpc_with_retry(self.controller.stub.ReadNewMessages, request)
        except Exception as e:
            messagebox.showerror("Error", str(e))
            return
        if response.success:
            if response.messages:
                display_str = "\n".join(f"{idx+1}. {msg}" for idx, msg in enumerate(response.messages))
                messagebox.showinfo("New Messages", display_str)
            else:
                messagebox.showinfo("New Messages", "No new messages.")
        else:
            messagebox.showerror("Error", "Error reading messages.")

    def show_all_messages(self):
        request = chat_pb2.ListMessagesRequest(username=self.controller.get_current_user())
        try:
            response = self.controller.call_rpc_with_retry(self.controller.stub.ListMessages, request)
        except Exception as e:
            messagebox.showerror("Error", str(e))
            return
        if response.success:
            ShowMessagesWindow(self.controller, response.messages)
        else:
            messagebox.showerror("Error", "Error listing messages.")

    def delete_account(self):
        confirm = messagebox.askyesno("Delete Account", "Are you sure you want to delete this account?\nUnread messages will be lost.")
        if not confirm:
            return
        request = chat_pb2.DeleteAccountRequest(username=self.controller.get_current_user())
        try:
            response = self.controller.call_rpc_with_retry(self.controller.stub.DeleteAccount, request)
        except Exception as e:
            messagebox.showerror("Error", str(e))
            return
        if response.success:
            messagebox.showinfo("Account Deleted", response.message)
            self.controller.set_current_user("")
            self.controller.show_frame(StartFrame)
        else:
            messagebox.showerror("Error", response.message)

    def logout(self):
        self.controller.set_current_user("")
        self.controller.show_frame(StartFrame)

class ShowMessagesWindow(tk.Toplevel):
    def __init__(self, controller: ChatClientApp, messages):
        super().__init__()
        self.controller = controller
        self.title("All Messages")
        self.geometry("400x300")
        tk.Label(self, text="All Read Messages", font=("Arial", 12, "bold")).pack(pady=5)
        self.messages = messages
        self.check_vars = []
        frame = tk.Frame(self)
        frame.pack(fill="both", expand=True)
        for idx, msg in enumerate(self.messages, start=1):
            var = tk.BooleanVar()
            chk = tk.Checkbutton(frame, text=f"{idx}. {msg}", variable=var, anchor="w", justify="left", wraplength=350)
            chk.pack(fill="x", padx=5, pady=2)
            self.check_vars.append((var, idx))
        tk.Button(self, text="Delete Selected", command=self.delete_selected).pack(pady=5)
        tk.Button(self, text="Close", command=self.destroy).pack(pady=5)

    def delete_selected(self):
        selected = [idx for var, idx in self.check_vars if var.get()]
        if not selected:
            messagebox.showinfo("Info", "No messages selected.")
            return
        request = chat_pb2.DeleteMessagesRequest(username=self.controller.get_current_user(), message_ids=selected)
        try:
            response = self.controller.call_rpc_with_retry(self.controller.stub.DeleteMessages, request)
        except Exception as e:
            messagebox.showerror("Error", str(e))
            return
        if response.success:
            messagebox.showinfo("Success", response.message)
            self.destroy()
        else:
            messagebox.showerror("Error", response.message)

def main():
    app = ChatClientApp()
    app.protocol("WM_DELETE_WINDOW", app.cleanup)
    app.mainloop()

if __name__ == "__main__":
    main()
