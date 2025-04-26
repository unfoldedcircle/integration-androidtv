import unittest
from copy import deepcopy

from ucapi.media_player import Attributes as MediaAttr

from src.util import filter_data_img_properties


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

    def test_data_with_data_uri_icon(self):
        data = {"icon": "data:image/png;base64,somedata"}
        result = filter_data_img_properties(data)
        self.assertEqual(result["icon"], "data:***")

    def test_data_with_media_image_url(self):
        data = {MediaAttr.MEDIA_IMAGE_URL: "data:image/png;base64,somedata"}
        result = filter_data_img_properties(data)
        self.assertEqual(result[MediaAttr.MEDIA_IMAGE_URL], "data:***")

    def test_msg_data_with_attributes(self):
        data = {"msg_data": {"attributes": {MediaAttr.MEDIA_IMAGE_URL: "data:image/png;base64,somedata"}}}
        result = filter_data_img_properties(deepcopy(data))
        self.assertEqual(result["msg_data"]["attributes"][MediaAttr.MEDIA_IMAGE_URL], "data:***")

    def test_msg_data_list_with_attributes_mixed(self):
        data = {
            "msg_data": [
                {"attributes": {MediaAttr.MEDIA_IMAGE_URL: "data:image/png;base64,somedata"}},
                {"attributes": {MediaAttr.MEDIA_IMAGE_URL: "http://example.com/icon.png"}},
            ]
        }
        result = filter_data_img_properties(deepcopy(data))
        self.assertEqual(result["msg_data"][0]["attributes"][MediaAttr.MEDIA_IMAGE_URL], "data:***")
        self.assertEqual(result["msg_data"][1]["attributes"][MediaAttr.MEDIA_IMAGE_URL], "http://example.com/icon.png")

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

    def test_no_modifications_needed(self):
        data = {
            "icon": "http://example.com/icon.png",
            MediaAttr.MEDIA_IMAGE_URL: "http://example.com/image.png",
            "msg_data": [{"attributes": {MediaAttr.MEDIA_IMAGE_URL: "http://example.com/image.png"}}],
        }
        result = filter_data_img_properties(deepcopy(data))
        self.assertEqual(result, data)


if __name__ == "__main__":
    unittest.main()
