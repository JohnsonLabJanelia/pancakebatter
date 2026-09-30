import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parents[1]))
from driver_upgrade.apt_plan import (  # noqa: E402
    R610_FRAMEWORK_VERSIONS,
    _parse_simulation,
    _validate_required_roots,
    _validate_transaction,
)


def target(**updates):
    value = {
        "driver_version": "610.57.04",
        "package_version": "610.57.04-1ubuntu1",
        "driver_metapackage": "cuda-drivers",
        "pin_package": "nvidia-driver-pinning-610.57.04",
        "dkms_package_version": "1:3.2.1-1ubuntu1",
        "module_flavor": "proprietary",
        "repository_url": "https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/x86_64/",
        "pin_package_version": "610.57.04-1ubuntu1",
        "framework_package_versions": R610_FRAMEWORK_VERSIONS,
        "dependency_package_versions": {},
        "allowed_dependency_packages": [
            "nvidia-driver", "nvidia-dkms", "nvidia-driver-open",
            "linux-headers-generic", "mlnx-ofed-kernel-dkms", "cuda-toolkit-13-1",
        ],
    }
    value.update(updates)
    return value


class SimulationParserTests(unittest.TestCase):
    def test_installed_and_upgraded_transitions(self):
        output = """NOTE: This is only a simulation!
Inst nvidia-driver (610.57.04-1ubuntu1 NVIDIA:stable [amd64])
Inst dkms [2.8.7-2ubuntu2.2] (1:3.2.1-1ubuntu1 NVIDIA:stable [all])
Conf nvidia-driver (610.57.04-1ubuntu1 NVIDIA:stable [amd64])
Conf dkms (1:3.2.1-1ubuntu1 NVIDIA:stable [all])
"""
        changes, blockers = _parse_simulation(output)
        self.assertEqual([], blockers)
        self.assertEqual("installed", changes[0]["action"])
        self.assertEqual("upgraded", changes[1]["action"])
        self.assertEqual("2.8.7-2ubuntu2.2", changes[1]["from_version"])
        self.assertEqual("1:3.2.1-1ubuntu1", changes[1]["to_version"])

    def test_remove_transition(self):
        output = """NOTE: This is only a simulation!
Remv nvidia-driver-535 [535.183.06-0ubuntu1]
"""
        changes, blockers = _parse_simulation(output)
        self.assertEqual([], blockers)
        self.assertEqual("removed", changes[0]["action"])

    def test_truncated_simulation_is_rejected(self):
        output = """NOTE: This is only a simulation!
Inst dkms [2.8.7-2ubuntu2.2] (1:3.2.1-1ubuntu1 NVIDIA:stable [all])
"""
        _, blockers = _parse_simulation(output)
        self.assertIn("simulation_truncated", {item["code"] for item in blockers})

    def test_malformed_transition_is_rejected(self):
        output = "NOTE: This is only a simulation!\nInst dkms [truncated\n"
        _, blockers = _parse_simulation(output)
        self.assertIn("simulation_malformed", {item["code"] for item in blockers})


class PolicyTests(unittest.TestCase):
    def codes(self, *changes):
        return {item["code"] for item in _validate_transaction(list(changes), target())}

    @staticmethod
    def change(package, version="610.57.04-1ubuntu1", action="installed"):
        return {
            "action": action, "package": package,
            "from_version": None, "to_version": None if action == "removed" else version,
        }

    def test_target_branch_and_dkms_are_accepted(self):
        blockers = _validate_transaction([
            self.change("nvidia-driver"),
            self.change("nvidia-dkms"),
            self.change("dkms", "1:3.2.1-1ubuntu1"),
        ], target())
        self.assertEqual([], blockers)

    def test_wrong_nvidia_branch_is_rejected(self):
        self.assertIn("nvidia_version", self.codes(self.change("nvidia-driver", "615.12.01-1")))

    def test_wrong_dkms_is_rejected(self):
        self.assertIn("dkms_version", self.codes(self.change("dkms", "1:3.2.1-1ubuntu2")))

    def test_open_flavor_is_rejected(self):
        self.assertIn("open_module", self.codes(self.change("nvidia-driver-open")))

    def test_kernel_change_is_rejected_even_when_allowlisted(self):
        self.assertIn("kernel_change", self.codes(self.change("linux-headers-generic", "6.8.0")))

    def test_ofed_change_is_rejected_even_when_allowlisted(self):
        self.assertIn("ofed_change", self.codes(self.change("mlnx-ofed-kernel-dkms", "24.10")))

    def test_cuda_toolkit_change_is_rejected_even_when_allowlisted(self):
        self.assertIn("cuda_toolkit_change", self.codes(self.change("cuda-toolkit-13-1", "13.1")))

    def test_removal_is_rejected(self):
        self.assertIn("package_removal", self.codes(self.change("nvidia-driver", action="removed")))

    def test_unknown_dependency_is_rejected(self):
        self.assertIn("unallowlisted_change", self.codes(self.change("surprise-helper", "1.0")))

    def test_exact_dependency_version_is_enforced(self):
        configured = target(
            allowed_dependency_packages=["screen-resolution-extra"],
            dependency_package_versions={"screen-resolution-extra": "0.18.2"},
        )
        blockers = _validate_transaction(
            [self.change("screen-resolution-extra", "0.18.1")], configured
        )
        self.assertIn("dependency_version", {item["code"] for item in blockers})


class RequiredRootTests(unittest.TestCase):
    def test_empty_plan_with_missing_roots_is_rejected(self):
        blockers = _validate_required_roots([], target(), {})
        self.assertEqual(3, sum(item["code"] == "required_root" for item in blockers))

    def test_exact_installed_roots_satisfy_empty_plan(self):
        installed = {
            "cuda-drivers": "610.57.04-1ubuntu1",
            "nvidia-driver-pinning-610.57.04": "610.57.04-1ubuntu1",
            "dkms": "1:3.2.1-1ubuntu1",
        }
        self.assertEqual([], _validate_required_roots([], target(), installed))

    def test_missing_single_root_is_rejected(self):
        installed = {
            "cuda-drivers": "610.57.04-1ubuntu1",
            "dkms": "1:3.2.1-1ubuntu1",
        }
        blockers = _validate_required_roots([], target(), installed)
        self.assertEqual(1, len(blockers))
        self.assertIn("nvidia-driver-pinning", blockers[0]["message"])


if __name__ == "__main__":
    unittest.main()
