import copy
import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

import yaml

import gui_config_editor as gui

BASE = {
    "schema": {"name": "system_config", "version": 1},
    "system_info": {"hostname": "t", "network_renderer": "NetworkManager"},
    "nics": [{"enp1": {"altname": None, "role": "camera", "managed": True, "expected_link": True,
                       "mac_address": "aa:bb:cc:dd:ee:ff", "mtu": 9000, "ip_address": "192.168.110.1/24",
                       "link_settings": {"speed": 25000, "autoneg": True}}}],
    "cameras": {},
}


class SaveTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.path = self.dir / "config.yml"
        self.path.write_text("# keep this header\n" + yaml.safe_dump(BASE, sort_keys=False))

    def test_valid_save_backs_up_and_keeps_header(self):
        cfg = copy.deepcopy(BASE)
        cfg["nics"][0]["enp1"]["transceiver"] = {"brand": "InnoLight"}
        self.assertEqual(gui.save(self.path, cfg), [])
        self.assertTrue(self.path.read_text().startswith("# keep this header\n"))
        self.assertEqual(gui.load(self.path)["nics"][0]["enp1"]["transceiver"]["brand"], "InnoLight")
        self.assertEqual(len(list(self.dir.glob("config.yml.bak.*"))), 1)

    def test_invalid_save_leaves_file_untouched(self):
        before = self.path.read_text()
        cfg = copy.deepcopy(BASE)
        cfg["nics"][0]["enp1"]["role"] = "bogus"
        self.assertTrue(gui.save(self.path, cfg))
        self.assertEqual(self.path.read_text(), before)
        self.assertEqual(list(self.dir.glob("*.bak.*")), [])

    def test_camera_outside_subnet_rejected(self):
        cfg = copy.deepcopy(BASE)
        cfg["cameras"] = {"E0-55-97-1E-AB-ED": {"serial_number": 1, "ip_address": "10.0.0.2", "nic_port": "enp1"}}
        self.assertTrue(gui.save(self.path, cfg))


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.path = self.dir / "config.yml"
        self.path.write_text(yaml.safe_dump(BASE, sort_keys=False))
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), gui.make_handler(self.path, "tok", 0))
        self.port = self.server.server_address[1]
        # allowed_hosts was built with port 0; rebuild with the real one
        self.server.RequestHandlerClass = gui.make_handler(self.path, "tok", self.port)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)

    def req(self, method, path, body=None, headers=None, host=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        h = {"Host": host or f"127.0.0.1:{self.port}", **(headers or {})}
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        return r.status, r.read()

    def test_requires_token(self):
        self.assertEqual(self.req("GET", "/api/config")[0], 403)
        self.assertEqual(self.req("GET", "/api/config", headers={"X-Token": "wrong"})[0], 403)
        self.assertEqual(self.req("GET", "/api/config", headers={"X-Token": "tok"})[0], 200)

    def test_rejects_foreign_host_header(self):
        self.assertEqual(self.req("GET", "/?token=tok", host="evil.example")[0], 403)

    def test_page_and_roundtrip(self):
        status, body = self.req("GET", "/?token=tok")
        self.assertEqual(status, 200)
        self.assertIn(b"Config Editor", body)
        cfg = copy.deepcopy(BASE)
        cfg["nics"][0]["enp1"]["role"] = "spare"
        status, body = self.req("POST", "/api/config", json.dumps(cfg), {"X-Token": "tok"})
        self.assertEqual((status, json.loads(body)["saved"]), (200, True))
        cfg["nics"][0]["enp1"]["role"] = "bogus"
        status, body = self.req("POST", "/api/config", json.dumps(cfg), {"X-Token": "tok"})
        self.assertEqual(status, 422)
        self.assertTrue(json.loads(body)["errors"])

    def test_bad_json(self):
        self.assertEqual(self.req("POST", "/api/config", "not json", {"X-Token": "tok"})[0], 400)


if __name__ == "__main__":
    unittest.main()
