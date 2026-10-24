import unittest
from unittest import mock

from sleeperfpl import http
from sleeperfpl.http import FetchError, get_json


class BrokenResponse:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, *a):
        raise ConnectionResetError("peer reset")


class HttpTests(unittest.TestCase):
    def test_read_errors_become_fetch_error(self):
        with mock.patch.object(http.urllib.request, "urlopen", return_value=BrokenResponse()):
            with self.assertRaisesRegex(FetchError, "peer reset"):
                get_json("https://example.invalid/x", retries=0)


if __name__ == "__main__":
    unittest.main()
