import unittest
from urllib.parse import unquote

from honeypot import INJECTION_MARKERS


def flagged(request):
    return bool(INJECTION_MARKERS.search(request) or INJECTION_MARKERS.search(unquote(request)))


class HoneypotInputTests(unittest.TestCase):
    def test_detects_url_encoded_command_separator(self):
        self.assertTrue(flagged("GET /maintenance?cmd=ping%20127.0.0.1%3Bwhoami HTTP/1.1"))

    def test_detects_pipes_substitution_and_backticks(self):
        for request in (
            "GET /x?q=a|cat%20/etc/passwd HTTP/1.1",
            "GET /x?q=$(id) HTTP/1.1",
            "GET /x?q=`id` HTTP/1.1",
            "GET /x?a=1&&ls HTTP/1.1",
        ):
            self.assertTrue(flagged(request), request)

    def test_ordinary_requests_are_not_flagged(self):
        for request in (
            "GET /maintenance/status HTTP/1.1",
            "GET /maintenance?id=5 HTTP/1.1",
            "GET /cat/status HTTP/1.1",
            "GET /x?a=1&id=5 HTTP/1.1",
            "GET /status?a=1;b=2 HTTP/1.1",
        ):
            self.assertFalse(flagged(request), request)


if __name__ == "__main__":
    unittest.main()
