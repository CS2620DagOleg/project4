import argparse
import sqlite3

def list_db(db_file):
    # Connect to the specified SQLite database.
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()

    # Fetch and display all users.
    print("Users:")
    try:
        cursor.execute("SELECT * FROM accounts")
        users = cursor.fetchall()
        if users:
            for row in users:
                print(row)
        else:
            print("No users found.")
    except sqlite3.Error as e:
        print(f"Error retrieving users: {e}")

    # Fetch and display all messages.
    print("\nMessages:")
    try:
        cursor.execute("SELECT * FROM messages")
        messages = cursor.fetchall()
        if messages:
            for row in messages:
                print(row)
        else:
            print("No messages found.")
    except sqlite3.Error as e:
        print(f"Error retrieving messages: {e}")

    # Close the connection.
    conn.close()

def main():
    parser = argparse.ArgumentParser(description="List all users and messages from a SQLite database.")
    parser.add_argument("db_name", help="Name (and path) of the SQLite database file")
    args = parser.parse_args()

    list_db(args.db_name)

if __name__ == "__main__":
    main()
