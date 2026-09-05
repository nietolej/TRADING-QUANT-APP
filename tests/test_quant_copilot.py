import unittest
from unittest.mock import patch, MagicMock

from web_gui.components.quant_copilot import QuantCopilotChat


class TestQuantCopilot(unittest.TestCase):

    def setUp(self):
        self.copilot = QuantCopilotChat()

    def test_initial_greeting(self):
        """Verifica que el copiloto inicie con un mensaje de bienvenida y chips sugeridos."""
        self.assertTrue(len(self.copilot.messages) >= 1)
        first_msg = self.copilot.messages[0]
        self.assertEqual(first_msg["sender"], "bot")
        self.assertIn("Copiloto Cuantitativo", first_msg["text"])
        self.assertTrue(len(first_msg["chips"]) >= 3)

    def test_toggle_chat(self):
        """Verifica la alternancia de visibilidad del chat."""
        mock_card = MagicMock()
        self.copilot.chat_card = mock_card
        self.assertFalse(self.copilot.is_open)

        self.copilot.toggle()
        self.assertTrue(self.copilot.is_open)
        mock_card.set_visibility.assert_called_with(True)

        self.copilot.toggle()
        self.assertFalse(self.copilot.is_open)
        mock_card.set_visibility.assert_called_with(False)

    def test_clear_history(self):
        """Verifica la limpieza de mensajes conservando el estado base."""
        self.copilot.messages.append({"sender": "user", "text": "Test message"})
        self.copilot.messages_container = MagicMock()
        self.copilot.messages_scroll = MagicMock()
        self.copilot._clear_history()

        self.assertEqual(len(self.copilot.messages), 1)
        self.assertEqual(self.copilot.messages[0]["sender"], "bot")


if __name__ == "__main__":
    unittest.main()
