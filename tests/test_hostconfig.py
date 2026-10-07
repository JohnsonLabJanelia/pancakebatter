import os
import subprocess
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import hostconfig

REPO = Path(hostconfig.REPO_ROOT)


class ResolverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.explicit = Path(self.tmp.name) / "explicit.yml"
        self.explicit.write_text("schema: {name: system_config, version: 1}\n")
        self.installed = Path(self.tmp.name) / "host.yml"

    def tearDown(self):
        self.tmp.cleanup()

    def test_checkout_path_follows_host_and_explicit_override_wins(self):
        with unittest.mock.patch.dict(os.environ, {"PANCAKEBATTER_HOST": "dumpling"}, clear=False):
            os.environ.pop("PANCAKEBATTER_HOST_CONFIG", None)
            self.assertEqual(hostconfig.default_config_path(), REPO / "hosts" / "dumpling" / "config.yml")
        with unittest.mock.patch.dict(os.environ, {"PANCAKEBATTER_HOST_CONFIG": str(self.explicit)}):
            self.assertEqual(hostconfig.default_config_path(), self.explicit)

    def test_consumer_resolution_env_then_installed_then_error(self):
        with unittest.mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PANCAKEBATTER_HOST_CONFIG", None)
            with self.assertRaises(FileNotFoundError) as ctx:
                hostconfig.resolve_host_config(installed=self.installed)
            self.assertIn("install_host_config.sh", str(ctx.exception))
            self.installed.write_text("x: 1\n")
            self.assertEqual(hostconfig.resolve_host_config(installed=self.installed), self.installed)
        with unittest.mock.patch.dict(os.environ, {"PANCAKEBATTER_HOST_CONFIG": str(self.explicit)}):
            self.assertEqual(hostconfig.resolve_host_config(installed=self.installed), self.explicit)
        with unittest.mock.patch.dict(os.environ, {"PANCAKEBATTER_HOST_CONFIG": str(self.explicit) + ".missing"}):
            with self.assertRaises(FileNotFoundError):
                hostconfig.resolve_host_config(installed=self.installed)

    def test_cli_prints_paths(self):
        env = {k: v for k, v in os.environ.items() if k != "PANCAKEBATTER_HOST_CONFIG"}
        env["PANCAKEBATTER_HOST"] = "pancake0"
        out = subprocess.run(["python3", str(REPO / "hostconfig.py"), "--checkout"], capture_output=True, text=True, env=env)
        self.assertEqual(out.stdout.strip(), str(REPO / "hosts" / "pancake0" / "config.yml"))
        env["PANCAKEBATTER_HOST_CONFIG"] = str(self.explicit)
        out = subprocess.run(["python3", str(REPO / "hostconfig.py")], capture_output=True, text=True, env=env)
        self.assertEqual((out.returncode, out.stdout.strip()), (0, str(self.explicit)))

    def test_shell_resolver_matches_python(self):
        script = f'source "{REPO}/lib/host_config.sh"; resolve_host_config'
        env = {k: v for k, v in os.environ.items() if k != "PANCAKEBATTER_HOST_CONFIG"}
        env["PANCAKEBATTER_HOST_CONFIG"] = str(self.explicit)
        out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)
        self.assertEqual((out.returncode, out.stdout.strip()), (0, str(self.explicit)))
        env["PANCAKEBATTER_HOST_CONFIG"] = str(self.explicit) + ".missing"
        out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)
        self.assertEqual(out.returncode, 1)
        self.assertIn("does not exist", out.stderr)
        # HOST_CONFIG_FILE (the checkout path) honours the explicit override too
        env["PANCAKEBATTER_HOST_CONFIG"] = str(self.explicit)
        out = subprocess.run(["bash", "-c", f'source "{REPO}/lib/host_config.sh"; echo "$HOST_CONFIG_FILE"'], capture_output=True, text=True, env=env)
        self.assertEqual(out.stdout.strip(), str(self.explicit))


if __name__ == "__main__":
    unittest.main()
