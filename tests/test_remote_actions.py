"""Remote agent actions: no shell text, no unmanaged processes, no replay."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
import server_registry as nodes


class RemoteActionQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old = nodes.NODES_DB
        nodes.NODES_DB = Path(self.temp.name) / "auth" / "nodes.sqlite3"
        created = nodes.create(nodes.NodeCreate(name="windows-tests"))
        self.node_id, self.token = created["id"], created["token"]
        body = nodes.Heartbeat(hostname="win", pm2=[
            {"name":"web-api","status":"online","pid":"123"}],
            services=[{"name":"Apache2.4","status":"Running","type":"Windows service"},
                      {"name":"Apache / XAMPP","status":"Running","type":"XAMPP process"}])
        nodes.heartbeat(self.node_id, "Bearer " + self.token, body)

    def tearDown(self):
        nodes.NODES_DB = self.old
        self.temp.cleanup()

    def test_queue_single_use_and_report_result(self):
        job = nodes.queue_action(self.node_id,"restart","web-api","admin")
        self.assertEqual(job["status"],"queued")
        command = nodes.heartbeat(self.node_id,"Bearer "+self.token,
                                  nodes.Heartbeat(hostname="win"))["action"]
        self.assertEqual(command["id"],job["id"])
        self.assertEqual(command["kind"],"restart")
        self.assertIsNone(nodes.heartbeat(self.node_id,"Bearer "+self.token,
                                          nodes.Heartbeat(hostname="win"))["action"])
        nodes.heartbeat(self.node_id,"Bearer "+self.token,
                        nodes.Heartbeat(hostname="win",action_result={
                            "id":job["id"],"ok":True,"output":"done"}))
        self.assertEqual(nodes.list_actions(self.node_id)[0]["state"],"done")

    def test_reject_unmanaged_target_and_shell_metacharacters(self):
        for target in ["not-reported","web-api & calc","x;dir"]:
            with self.assertRaises(HTTPException):
                nodes.queue_action(self.node_id,"stop",target,"admin")

    def test_restart_only_registered_services(self):
        with self.assertRaises(HTTPException):
            nodes.queue_action(self.node_id,"service_restart","Apache / XAMPP","admin")
        with self.assertRaises(HTTPException):
            nodes.queue_action(self.node_id,"service_restart","ServerManagerAgent","admin")
        result = nodes.queue_action(self.node_id,"service_restart","Apache2.4","admin")
        self.assertEqual(result["kind"],"service_restart")

    def test_logs_from_allowlisted_xampp_component(self):
        with self.assertRaises(HTTPException):
            nodes.queue_action(self.node_id,"service_logs","secrets.txt","admin")
        self.assertEqual(nodes.queue_action(self.node_id,"service_logs","apache","admin")["kind"],"service_logs")

    def test_duplicate_pending_blocked(self):
        nodes.queue_action(self.node_id,"logs","web-api","admin")
        with self.assertRaises(HTTPException) as error:
            nodes.queue_action(self.node_id,"start","web-api","admin")
        self.assertEqual(error.exception.status_code,409)


if __name__ == "__main__":
    unittest.main()
