import unittest

from src.external_metadata import get_resized_image_url


class TestGetResizedImageUrl(unittest.TestCase):

    def test_valid_url_with_both_dimensions_exceeding_max_size(self):
        url = "https://192.168.1.10:32400/photo/:/transcode?height=1800&machineIdentifier=xxx&quality=90&url=http%3A%2F%2F127.0.0.1%3A32400%2Flibrary%2Fmetadata%2F20%2Fthumb%2F1764553003&width=1200&X-Plex-Token=abc"
        result = get_resized_image_url(url, max_size=480)
        expected_url = "https://192.168.1.10:32400/photo/:/transcode?height=480&machineIdentifier=xxx&quality=90&url=http%3A%2F%2F127.0.0.1%3A32400%2Flibrary%2Fmetadata%2F20%2Fthumb%2F1764553003&width=320&X-Plex-Token=abc"
        self.assertEqual(expected_url, result, "Expected resized dimensions within max_size")

    def test_valid_url_with_only_width_parameter(self):
        url = "https://192.168.1.10:32400/photo/:/transcode?machineIdentifier=xxx&quality=90&url=http%3A%2F%2F127.0.0.1%3A32400%2Flibrary%2Fmetadata%2F20%2Fthumb%2F1764553003&width=1200&X-Plex-Token=abc"
        result = get_resized_image_url(url, max_size=480)
        expected_url = "https://192.168.1.10:32400/photo/:/transcode?machineIdentifier=xxx&quality=90&url=http%3A%2F%2F127.0.0.1%3A32400%2Flibrary%2Fmetadata%2F20%2Fthumb%2F1764553003&width=480&X-Plex-Token=abc"
        self.assertEqual(expected_url, result, "Expected max_size applied only to width")

    def test_valid_url_with_only_height_parameter(self):
        url = "https://192.168.1.10:32400/photo/:/transcode?height=1800&machineIdentifier=xxx&quality=90&url=http%3A%2F%2F127.0.0.1%3A32400%2Flibrary%2Fmetadata%2F20%2Fthumb%2F1764553003&X-Plex-Token=abc"
        result = get_resized_image_url(url, max_size=480)
        expected_url = "https://192.168.1.10:32400/photo/:/transcode?height=480&machineIdentifier=xxx&quality=90&url=http%3A%2F%2F127.0.0.1%3A32400%2Flibrary%2Fmetadata%2F20%2Fthumb%2F1764553003&X-Plex-Token=abc"
        self.assertEqual(expected_url, result, "Expected max_size applied only to height")

    def test_valid_url_with_both_dimensions_within_max_size(self):
        url = "http://example.com/photo/:/transcode?width=50&height=40"
        result = get_resized_image_url(url, max_size=100)
        self.assertEqual(url, result, "Expected original URL as dimensions fit within max_size")

    def test_url_with_invalid_width_and_height(self):
        url = "http://example.com/photo/:/transcode?width=-50&height=abc"
        result = get_resized_image_url(url, max_size=100)
        self.assertEqual(url, result, "Expected original URL for invalid width and height")

    def test_invalid_url(self):
        url = "not_a_valid_url"
        result = get_resized_image_url(url, max_size=100)
        self.assertEqual(url, result, "Expected original URL for an invalid URL input")

    def test_empty_url(self):
        url = ""
        result = get_resized_image_url(url, max_size=100)
        self.assertEqual(url, result, "Expected original empty URL")

    def test_url_without_query_parameters(self):
        url = "http://example.com/photo/:/transcode"
        result = get_resized_image_url(url, max_size=100)
        self.assertEqual(url, result, "Expected original URL when no query parameters are present")

    def test_valid_non_plex_url_with_both_dimensions_exceeding_max_size(self):
        url = "https://192.168.1.10:32400/transcode?height=1800&machineIdentifier=xxx&quality=90&url=http%3A%2F%2F127.0.0.1%3A32400%2Flibrary%2Fmetadata%2F20%2Fthumb%2F1764553003&width=1200&X-Plex-Token=abc"
        result = get_resized_image_url(url, max_size=480)
        self.assertEqual(url, result, "Non-Plex URLs should not be modified")
