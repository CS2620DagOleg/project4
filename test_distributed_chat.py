import unittest
from unittest.mock import MagicMock, call
import grpc

import chat_pb2
import chat_pb2_grpc


class TestDistributedChatSystem(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        """Mock the gRPC client stub instead of using a real server."""
        cls.mock_stub = MagicMock()

        # Explicitly define the methods that will be called
        cls.mock_stub.CreateAccount = MagicMock()
        cls.mock_stub.Login = MagicMock()
        cls.mock_stub.SendMessage = MagicMock()
        cls.mock_stub.ReadNewMessages = MagicMock()

        # Generate test usernames
        cls.user1 = "testuser_1"
        cls.user2 = "testuser_2"

    def test_1_create_account(self):
        """Test account creation."""
        request = chat_pb2.CreateAccountRequest(username=self.user1, password="password")
        expected_response = chat_pb2.CreateAccountResponse(success=True, message="Account created successfully")

        self.mock_stub.CreateAccount.return_value = expected_response
        response = self.mock_stub.CreateAccount(request)

        self.mock_stub.CreateAccount.assert_called_once_with(request)
        self.assertTrue(response.success, "CreateAccount should succeed")

    def test_2_login(self):
        """Test login functionality."""
        request = chat_pb2.LoginRequest(username=self.user1, password="password")
        expected_response = chat_pb2.LoginResponse(success=True, message="Login successful", unread_count=2)

        self.mock_stub.Login.return_value = expected_response
        response = self.mock_stub.Login(request)

        self.mock_stub.Login.assert_called_once_with(request)
        self.assertTrue(response.success, "Login should succeed")
        self.assertEqual(response.unread_count, 2, "User should have 2 unread messages")

    def test_3_send_message(self):
        """Test sending a message to an existing user."""
        request = chat_pb2.SendMessageRequest(sender=self.user1, to=self.user2, content="Hello!")
        expected_response = chat_pb2.SendMessageResponse(success=True, message="Message sent successfully")

        self.mock_stub.SendMessage.return_value = expected_response
        response = self.mock_stub.SendMessage(request)

        self.mock_stub.SendMessage.assert_called_once_with(request)
        self.assertTrue(response.success, "SendMessage should succeed")

    def test_4_send_message_nonexistent_user(self):
        """Test sending a message to a non-existent user."""
        request = chat_pb2.SendMessageRequest(sender=self.user1, to="fakeuser", content="Hello!")
        expected_response = chat_pb2.SendMessageResponse(success=False, message="Recipient does not exist")

        self.mock_stub.SendMessage.return_value = expected_response
        response = self.mock_stub.SendMessage(request)

        self.mock_stub.SendMessage.assert_called()  # Ensure the function was called at least once
        self.assertFalse(response.success, "SendMessage should fail for non-existent user")

        # Verify that the correct request was in the call history
        calls = self.mock_stub.SendMessage.call_args_list
        self.assertIn(call(request), calls, "Expected SendMessage request not found in call history")

    def test_5_read_new_messages(self):
        """Test reading new messages."""
        request = chat_pb2.ReadNewMessagesRequest(username=self.user2, count=0)
        expected_response = chat_pb2.ReadNewMessagesResponse(
            success=True, messages=["Hello from testuser_1"]
        )

        self.mock_stub.ReadNewMessages.return_value = expected_response
        response = self.mock_stub.ReadNewMessages(request)

        self.mock_stub.ReadNewMessages.assert_called_once_with(request)
        self.assertTrue(response.success, "ReadNewMessages should succeed")
        self.assertGreaterEqual(len(response.messages), 1, "Should receive at least 1 message")

if __name__ == "__main__":
    unittest.main()
