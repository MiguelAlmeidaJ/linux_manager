from types import SimpleNamespace
from unittest.mock import patch
import unittest

import app


def request(peer, host="lm.example.com", origin="https://lm.example.com", proto="https"):
    return SimpleNamespace(
        client=SimpleNamespace(host=peer),
        url=SimpleNamespace(scheme="http"),
        headers={"host": host, "origin": origin,
                 "x-forwarded-proto": proto, "x-forwarded-host": host},
    )


class ProxySecurityTests(unittest.TestCase):
    def test_trusted_https_proxy_can_manage_database(self):
        with patch.dict("os.environ", {"TRUSTED_PROXY_IPS": "10.255.1.215/32"}):
            app.require_secure_database_admin(request("10.255.1.215"))

    def test_spoofed_https_header_from_untrusted_peer_is_blocked(self):
        with patch.dict("os.environ", {"TRUSTED_PROXY_IPS": "10.255.1.215/32"}):
            with self.assertRaises(Exception):
                app.require_secure_database_admin(request("192.168.199.70"))

    def test_trusted_proxy_with_http_origin_is_blocked(self):
        with patch.dict("os.environ", {"TRUSTED_PROXY_IPS": "10.255.1.215/32"}):
            with self.assertRaises(Exception):
                app.require_secure_database_admin(request("10.255.1.215", origin="http://lm.example.com"))

    def test_trust_all_network_is_rejected(self):
        with patch.dict("os.environ", {"TRUSTED_PROXY_IPS": "0.0.0.0/0"}):
            with self.assertRaises(Exception):
                app.require_secure_database_admin(request("192.168.199.70"))

    def test_unconfigured_proxy_is_rejected(self):
        with patch.dict("os.environ", {"TRUSTED_PROXY_IPS": ""}):
            with self.assertRaises(Exception):
                app.require_secure_database_admin(request("10.255.1.215"))


if __name__ == "__main__":
    unittest.main()
