import unittest

from src.config import device_from_entity_id


class TestConfig(unittest.TestCase):
    def test_device_from_entity_id_with_valid_entity(self):
        entity_id = "media_player.atv_1"
        result = device_from_entity_id(entity_id)
        self.assertEqual("atv_1", result, "Expected device suffix from a valid entity ID")

    def test_device_from_entity_id_with_entity_missing_dot(self):
        entity_id = "media_player_atv_1"
        result = device_from_entity_id(entity_id)
        self.assertEqual(entity_id, result, "Expected same name for an entity ID with no dot")

    def test_device_from_entity_id_with_empty_entity(self):
        entity_id = ""
        result = device_from_entity_id(entity_id)
        self.assertIsNone(result, "Expected None for an empty entity ID")

    def test_device_from_entity_id_with_dot_at_start(self):
        entity_id = ".atv_1"
        result = device_from_entity_id(entity_id)
        self.assertEqual("atv_1", result, "Expected device suffix for entity ID starting with a dot")

    def test_device_from_entity_id_with_dot_at_end(self):
        entity_id = "media_player."
        result = device_from_entity_id(entity_id)
        self.assertIsNone(result, "Expected None for an entity ID ending with a dot")
