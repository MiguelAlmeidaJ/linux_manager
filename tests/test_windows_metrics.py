import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / "agents" / "windows_metrics.py"
spec = importlib.util.spec_from_file_location("windows_metrics_under_test", MODULE)
metrics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(metrics)


class MetricsTests(unittest.TestCase):
    def test_cpu_ram_and_process_ranking(self):
        sample = {"cpu_percent": 35.5, "memory_used": 8_000_000_000,
                  "memory_total": 16_000_000_000, "cores": 4,
                  "processes": [
                      {"Name": "web", "IDProcess": 120, "PercentProcessorTime": 40,
                       "WorkingSetPrivate": 120_000_000},
                      {"Name": "sql", "IDProcess": 321, "PercentProcessorTime": 80,
                       "WorkingSetPrivate": 800_000_000},
                  ],
                  "disks": [{"DeviceID": "C:", "Size": 100_000_000_000, "FreeSpace": 40_000_000_000}]}
        with patch.object(metrics.subprocess, "run",
                          return_value=SimpleNamespace(returncode=0, stdout=json.dumps(sample))) as run:
            result = metrics.collect()
        self.assertEqual(result["cpu_percent"], 35.5)
        self.assertEqual(result["memory_percent"], 50.0)
        self.assertEqual(result["top_processes"][0]["name"], "sql")
        self.assertEqual(result["top_processes"][0]["cpu_percent"], 20.0)
        self.assertEqual(result["disks"][0]["name"], "C:")
        self.assertIn("powershell", run.call_args.args[0][0].lower())

    def test_no_metrics_reported_if_powershell_fails(self):
        with patch.object(metrics.subprocess, "run",
                          return_value=SimpleNamespace(returncode=1, stdout="")):
            self.assertEqual(metrics.collect(), {})


if __name__ == "__main__":
    unittest.main()
