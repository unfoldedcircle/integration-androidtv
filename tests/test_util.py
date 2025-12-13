import unittest
from copy import deepcopy

from ucapi.media_player import Attributes as MediaAttr
from ucapi.media_player import States as MediaState

from src.util import filter_data_img_properties, key_update_helper
from util import handle_entity_state_after_update


class TestFilterDataImgProperties(unittest.TestCase):
    def test_none_data(self):
        data = None
        result = filter_data_img_properties(data)
        self.assertEqual(result, {})

    def test_empty_data(self):
        data = {}
        result = filter_data_img_properties(data)
        self.assertEqual(result, {})

    def test_data_with_non_data_uri_icon(self):
        data = {"icon": "http://example.com/icon.png"}
        result = filter_data_img_properties(data)
        self.assertEqual(result, data)
        self.assertEqual(data, {"icon": "http://example.com/icon.png"})

    def test_data_with_data_uri_icon(self):
        data = {"icon": "data:image/png;base64,somedata"}
        result = filter_data_img_properties(data)
        self.assertEqual(result["icon"], "data:***")
        self.assertEqual(data["icon"], "data:image/png;base64,somedata")

    def test_data_with_media_image_url(self):
        data = {MediaAttr.MEDIA_IMAGE_URL: "data:image/png;base64,somedata"}
        result = filter_data_img_properties(data)
        self.assertEqual(result[MediaAttr.MEDIA_IMAGE_URL], "data:***")
        self.assertEqual(data[MediaAttr.MEDIA_IMAGE_URL], "data:image/png;base64,somedata")

    def test_msg_data_with_attributes(self):
        data = {"msg_data": {"attributes": {MediaAttr.MEDIA_IMAGE_URL: "data:image/png;base64,somedata"}}}
        result = filter_data_img_properties(deepcopy(data))
        self.assertEqual(result["msg_data"]["attributes"][MediaAttr.MEDIA_IMAGE_URL], "data:***")
        self.assertEqual(data["msg_data"]["attributes"][MediaAttr.MEDIA_IMAGE_URL], "data:image/png;base64,somedata")

    def test_msg_data_list_with_attributes_mixed(self):
        data = {
            "msg_data": [
                {"attributes": {MediaAttr.MEDIA_IMAGE_URL: "data:image/png;base64,somedata"}},
                {"attributes": {MediaAttr.MEDIA_IMAGE_URL: "http://example.com/icon.png"}},
            ]
        }
        result = filter_data_img_properties(deepcopy(data))
        self.assertEqual(result["msg_data"][0]["attributes"][MediaAttr.MEDIA_IMAGE_URL], "data:***")
        self.assertEqual(data["msg_data"][0]["attributes"][MediaAttr.MEDIA_IMAGE_URL], "data:image/png;base64,somedata")

        self.assertEqual(result["msg_data"][1]["attributes"][MediaAttr.MEDIA_IMAGE_URL], "http://example.com/icon.png")
        self.assertEqual(data["msg_data"][1]["attributes"][MediaAttr.MEDIA_IMAGE_URL], "http://example.com/icon.png")

    def test_msg_data_list_with_attributes(self):
        data = {
            "msg_data": [
                {"attributes": {MediaAttr.MEDIA_IMAGE_URL: "data:image/png;base64,somedata"}},
                {"attributes": {MediaAttr.MEDIA_IMAGE_URL: "data:image/png;base64,somedata"}},
            ]
        }
        result = filter_data_img_properties(deepcopy(data))
        self.assertEqual(result["msg_data"][0]["attributes"][MediaAttr.MEDIA_IMAGE_URL], "data:***")
        self.assertEqual(result["msg_data"][1]["attributes"][MediaAttr.MEDIA_IMAGE_URL], "data:***")

        self.assertEqual(data["msg_data"][0]["attributes"][MediaAttr.MEDIA_IMAGE_URL], "data:image/png;base64,somedata")
        self.assertEqual(data["msg_data"][1]["attributes"][MediaAttr.MEDIA_IMAGE_URL], "data:image/png;base64,somedata")

    def test_no_modifications_needed(self):
        data = {
            "icon": "http://example.com/icon.png",
            MediaAttr.MEDIA_IMAGE_URL: "http://example.com/image.png",
            "msg_data": [{"attributes": {MediaAttr.MEDIA_IMAGE_URL: "http://example.com/image.png"}}],
        }
        result = filter_data_img_properties(deepcopy(data))
        self.assertEqual(result, data)
        self.assertEqual(result[MediaAttr.MEDIA_IMAGE_URL], "http://example.com/image.png")
        self.assertEqual(result["msg_data"][0]["attributes"][MediaAttr.MEDIA_IMAGE_URL], "http://example.com/image.png")


class TestKeyUpdateHelper(unittest.TestCase):
    def test_key_update_with_new_key(self):
        attributes = {}
        original_attributes = {}
        result = key_update_helper("key1", "value1", attributes, original_attributes)
        self.assertEqual({"key1": "value1"}, result)

    def test_key_update_with_existing_key_and_same_value(self):
        attributes = {"key1": "value1"}
        original_attributes = {"key1": "value1"}
        result = key_update_helper("key1", "value1", attributes, original_attributes)
        self.assertEqual({"key1": "value1"}, result)

    def test_key_update_with_existing_key_and_different_value(self):
        attributes = {"key1": "old_value"}
        original_attributes = {"key1": "old_value"}
        result = key_update_helper("key1", "new_value", attributes, original_attributes)
        self.assertEqual({"key1": "new_value"}, result)

    def test_key_update_with_value_none(self):
        attributes = {"key1": "value1"}
        original_attributes = {"key1": "value1"}
        result = key_update_helper("key2", None, attributes, original_attributes)
        self.assertEqual({"key1": "value1"}, result)

    def test_key_update_with_missing_original_key(self):
        attributes = {"key1": "value1"}
        original_attributes = {}
        result = key_update_helper("key2", "value2", attributes, original_attributes)
        self.assertEqual({"key1": "value1", "key2": "value2"}, result)


class TestHandleEntityStateAfterUpdate(unittest.TestCase):
    def test_attributes_with_state_returns_same_object(self):
        for state in [
            MediaState.UNAVAILABLE,
            MediaState.UNKNOWN,
            MediaState.ON,
            MediaState.OFF,
            MediaState.PLAYING,
            MediaState.PAUSED,
            MediaState.STANDBY,
            MediaState.BUFFERING,
        ]:
            original = {MediaAttr.STATE: state}
            attributes = {MediaAttr.STATE: MediaState.STANDBY}
            result = handle_entity_state_after_update(attributes, original)
            self.assertEqual({MediaAttr.STATE: MediaState.STANDBY}, result)

    def test_empty_attributes_with_valid_original_state_returns_empty(self):
        for state in [
            MediaState.UNKNOWN,
            MediaState.ON,
            MediaState.OFF,
            MediaState.PLAYING,
            MediaState.PAUSED,
            MediaState.STANDBY,
            MediaState.BUFFERING,
        ]:
            original = {MediaAttr.STATE: state}
            attributes = {}
            result = handle_entity_state_after_update(attributes, original)
            self.assertEqual({}, result)

    def test_empty_attributes_with_original_state_unavailable_returns_unknown(self):
        original = {MediaAttr.STATE: MediaState.UNAVAILABLE}
        attributes = {}
        result = handle_entity_state_after_update(attributes, original)
        self.assertEqual({MediaAttr.STATE: MediaState.UNKNOWN}, result)


if __name__ == "__main__":
    unittest.main()
