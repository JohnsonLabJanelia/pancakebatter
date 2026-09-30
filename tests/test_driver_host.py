import subprocess
import unittest

from driver_upgrade.host import audit_host


EXPECTED_UUIDS = [f"GPU-{index:02d}" for index in range(9)]
PACKAGE_FORMAT = "-f=${binary:Package}\t${Version}\t${db:Status-Abbrev}\\n"
REQUIRED_PACKAGE_QUERY = (
    "dpkg-query",
    "-W",
    PACKAGE_FORMAT,
    "dkms",
    "linux-headers-6.5.0-44-generic",
    "mlnx-ofed-kernel-dkms",
)


def manifest():
    return {
        "expected": {
            "os_id": "ubuntu",
            "os_version_id": "22.04",
            "architecture": "amd64",
            "kernel": "6.5.0-44-generic",
            "driver_version": "535.183.06",
            "driver_owner": "runfile",
            "driver_flavor": "proprietary",
            "dkms_version": "2.8.7-2ubuntu2",
            "ofed_package_version": "24.01.OFED.24.01.0.3.3.1-1",
            "ofed_dkms_version": "24.01.OFED.24.01.0.3.3.1",
            "cuda_symlink_target": "/usr/local/cuda-12.2",
            "tensorrt_root": "/usr/local/TensorRT-10.0.1.6",
            "gpu_uuids": EXPECTED_UUIDS,
        },
        "target": {"driver_version": "610.57.04", "driver_owner": "apt"},
    }


class FakeRunner:
    def __init__(self, overrides=None):
        self.overrides = overrides or {}
        self.calls = []

    def __call__(self, argv, timeout=None):
        key = tuple(argv)
        self.calls.append((key, timeout))
        if key in self.overrides:
            value = self.overrides[key]
            if callable(value):
                value = value(argv)
            return subprocess.CompletedProcess(argv, *value)
        return self.default(argv)

    @staticmethod
    def default(argv):
        key = tuple(argv)
        if key == ("cat", "/etc/os-release"):
            return subprocess.CompletedProcess(argv, 0, 'ID=ubuntu\nVERSION_ID="22.04"\n', "")
        if key == ("dpkg", "--print-architecture"):
            return subprocess.CompletedProcess(argv, 0, "amd64\n", "")
        if key == ("uname", "-r"):
            return subprocess.CompletedProcess(argv, 0, "6.5.0-44-generic\n", "")
        if key[:2] == ("test", "-d"):
            return subprocess.CompletedProcess(argv, 0, "", "")
        if key == ("cat", "/proc/driver/nvidia/version"):
            return subprocess.CompletedProcess(argv, 0, "NVRM version: NVIDIA UNIX x86_64 Kernel Module  535.183.06\n", "")
        if key[:2] == ("nvidia-smi", "--query-gpu=uuid,index,name,driver_version,pci.bus_id"):
            rows = "\n".join(f"{uuid}, {i}, NVIDIA A16, 535.183.06, 00000000:{i:02x}:00.0" for i, uuid in enumerate(EXPECTED_UUIDS))
            return subprocess.CompletedProcess(argv, 0, rows + "\n", "")
        if key[:2] == ("nvidia-smi", "--query-compute-apps=gpu_uuid,pid,process_name"):
            return subprocess.CompletedProcess(argv, 0, "", "")
        if key == ("readlink", "-f", "/usr/local/cuda"):
            return subprocess.CompletedProcess(argv, 0, "/usr/local/cuda-12.2\n", "")
        if key[:2] == ("dpkg-query", "-W"):
            if "dkms" in key:
                out = (
                    "dkms\t2.8.7-2ubuntu2\tii \n"
                    "linux-headers-6.5.0-44-generic\t6.5.0-44.44~22.04.1\tii \n"
                    "mlnx-ofed-kernel-dkms\t24.01.OFED.24.01.0.3.3.1-1\tii \n"
                )
                return subprocess.CompletedProcess(argv, 0, out, "")
            return subprocess.CompletedProcess(argv, 1, "", "requested NVIDIA packages are absent\n")
        if key[:2] == ("dpkg-query", "-S"):
            return subprocess.CompletedProcess(argv, 1, "", "no path found matching pattern\n")
        if key[:3] == ("head", "-n", "80"):
            return subprocess.CompletedProcess(argv, 0, "register kernel module sources with DKMS? Yes\n", "")
        if key == ("cat", "/proc/modules"):
            out = "nvidia 1 1 nvidia_peermem, Live 0x0\nnvidia_peermem 1 0 - Live 0x0\nib_core 1 1 nvidia_peermem, Live 0x0\n"
            return subprocess.CompletedProcess(argv, 0, out, "")
        if key[:3] == ("modinfo", "-F", "filename"):
            return subprocess.CompletedProcess(argv, 0, f"/lib/modules/6.5.0-44-generic/updates/dkms/{argv[-1]}.ko\n", "")
        if key[:3] == ("modinfo", "-F", "version"):
            return subprocess.CompletedProcess(argv, 0, "535.183.06\n", "")
        if key[:3] == ("modinfo", "-F", "vermagic"):
            return subprocess.CompletedProcess(argv, 0, "6.5.0-44-generic SMP preempt mod_unload modversions\n", "")
        if key[:3] == ("modinfo", "-F", "depends"):
            out = "nvidia,ib_core\n" if argv[-1] == "nvidia_peermem" else "\n"
            return subprocess.CompletedProcess(argv, 0, out, "")
        if key[:3] == ("modinfo", "-F", "license"):
            return subprocess.CompletedProcess(argv, 0, "NVIDIA\n", "")
        if key == ("dkms", "status"):
            out = "mlnx-ofed-kernel/24.01.OFED.24.01.0.3.3.1, 6.5.0-44-generic, x86_64: installed\nnvidia/535.183.06, 6.5.0-44-generic, x86_64: installed\n"
            return subprocess.CompletedProcess(argv, 0, out, "")
        if key[:4] == ("dkms", "status", "-m", "nvidia"):
            return subprocess.CompletedProcess(argv, 0, "nvidia/535.183.06, 6.5.0-44-generic, x86_64: installed\n", "")
        if key[:4] == ("dkms", "status", "-m", "mlnx-ofed-kernel"):
            out = "mlnx-ofed-kernel/24.01.OFED.24.01.0.3.3.1, 6.5.0-44-generic, x86_64: installed\n"
            return subprocess.CompletedProcess(argv, 0, out, "")
        if key == ("dkms", "status", "-m", "nvidia-fs"):
            return subprocess.CompletedProcess(argv, 0, "", "")
        if key == ("ls", "-1", "/var/lib/dkms/nvidia"):
            return subprocess.CompletedProcess(argv, 0, "535.183.06\nkernel-6.5.0-44-generic-x86_64\n", "")
        raise AssertionError(f"unexpected command: {argv}")


class HostAuditTests(unittest.TestCase):
    def test_unrelated_dkms_error_and_missing_module_metadata_block(self):
        runner = FakeRunner({
            ("dkms", "status"): (0, "", "Error! knem source is missing\n"),
            ("modinfo", "-F", "vermagic", "nvidia_peermem"): (1, "", "missing"),
            ("modinfo", "-F", "vermagic", "nvidia"): (0, "5.19.0 SMP", ""),
            ("modinfo", "-F", "filename", "nvidia"): (0, "/tmp/nvidia.ko", ""),
        })
        codes = {x["code"] for x in audit_host(manifest(), runner=runner)["blockers"]}
        self.assertTrue({"dkms_diagnostic_failed", "nvidia_peermem_vermagic_mismatch",
                         "nvidia_vermagic_mismatch", "nvidia_path_mismatch"}.issubset(codes))

    def test_healthy_host_has_only_expected_transition_warning(self):
        result = audit_host(manifest(), runner=FakeRunner())
        self.assertEqual([], result["blockers"])
        self.assertFalse(result["observations"]["driver"]["dpkg_owned"])
        self.assertEqual(EXPECTED_UUIDS, result["observations"]["gpu_inventory"]["observed_uuids"])
        self.assertEqual(
            "24.01.OFED.24.01.0.3.3.1-1",
            result["observations"]["packages"]["selected"]["mlnx-ofed-kernel-dkms"]["version"],
        )
        self.assertEqual(
            "24.01.OFED.24.01.0.3.3.1",
            result["observations"]["dkms"]["ofed_expected_version"]["rows"][0]["version"],
        )
        self.assertTrue(result["observations"]["readiness"]["ready"])
        self.assertIn("runfile_uninstall_required", {item["code"] for item in result["warnings"]})

    def test_stale_nvidia_fs_blocks_even_when_dkms_exits_zero(self):
        stale = "nvidia-fs/2.17.5: Error! Could not locate dkms.conf file.\n"
        runner = FakeRunner(
            {
                ("dkms", "status"): (0, "mlnx-ofed-kernel/24.01.OFED.24.01.0.3.3.1, 6.5.0-44-generic, x86_64: installed\n" + stale, ""),
                ("dkms", "status", "-m", "nvidia-fs"): (0, "", stale),
            }
        )
        result = audit_host(manifest(), runner=runner)
        self.assertIn("stale_nvidia_fs_dkms", {item["code"] for item in result["blockers"]})
        self.assertTrue(result["observations"]["dkms"]["stale_nvidia_fs"])
        self.assertFalse(result["observations"]["readiness"]["ready"])

    def test_missing_gpu_visibility_blocks_without_claiming_hardware_failure(self):
        runner = FakeRunner(
            {
                (
                    "nvidia-smi",
                    "--query-gpu=uuid,index,name,driver_version,pci.bus_id",
                    "--format=csv,noheader,nounits",
                ): (255, "", "Failed to initialize NVML: Insufficient Permissions\n"),
            }
        )
        result = audit_host(manifest(), runner=runner)
        self.assertIn("gpu_visibility_unavailable", {item["code"] for item in result["blockers"]})
        warning = next(item for item in result["warnings"] if item["code"] == "gpu_visibility_environment")
        self.assertIn("not evidence that physical GPUs failed", warning["message"])
        self.assertEqual("Failed to initialize NVML: Insufficient Permissions", result["observations"]["gpu_inventory"]["diagnostic"])

    def test_multiple_registered_nvidia_versions_block(self):
        runner = FakeRunner(
            {
                ("ls", "-1", "/var/lib/dkms/nvidia"): (
                    0,
                    "535.183.06\n610.57.04\nkernel-6.5.0-44-generic-x86_64\n",
                    "",
                )
            }
        )
        result = audit_host(manifest(), runner=runner)
        self.assertIn("multiple_nvidia_dkms_versions", {item["code"] for item in result["blockers"]})

    def test_peermem_and_targeted_dkms_must_match_running_kernel(self):
        runner = FakeRunner(
            {
                ("modinfo", "-F", "vermagic", "nvidia_peermem"): (
                    0,
                    "5.19.0-32-generic SMP mod_unload modversions\n",
                    "",
                ),
                ("dkms", "status", "-m", "nvidia", "-v", "535.183.06"): (
                    0,
                    "nvidia/535.183.06, 5.19.0-32-generic, x86_64: installed\n",
                    "",
                ),
                (
                    "dkms",
                    "status",
                    "-m",
                    "mlnx-ofed-kernel",
                    "-v",
                    "24.01.OFED.24.01.0.3.3.1",
                ): (
                    0,
                    "mlnx-ofed-kernel/24.01.OFED.24.01.0.3.3.1, 5.19.0-32-generic, x86_64: installed\n",
                    "",
                ),
            }
        )
        result = audit_host(manifest(), runner=runner)
        codes = {item["code"] for item in result["blockers"]}
        self.assertIn("nvidia_peermem_vermagic_mismatch", codes)
        self.assertIn("nvidia_dkms_registration_missing", codes)
        self.assertIn("ofed_dkms_registration_missing", codes)

    def test_peermem_presence_driver_version_and_ib_core_dependency_are_required(self):
        runner = FakeRunner(
            {
                ("cat", "/proc/modules"): (0, "nvidia 1 0 - Live 0x0\nib_core 1 0 - Live 0x0\n", ""),
                ("modinfo", "-F", "version", "nvidia_peermem"): (0, "530.30.02\n", ""),
                ("modinfo", "-F", "depends", "nvidia_peermem"): (0, "nvidia\n", ""),
            }
        )
        result = audit_host(manifest(), runner=runner)
        codes = {item["code"] for item in result["blockers"]}
        self.assertIn("nvidia_peermem_not_loaded", codes)
        self.assertIn("nvidia_peermem_version_mismatch", codes)
        self.assertIn("nvidia_peermem_ib_core_missing", codes)

    def test_required_package_query_failure_is_distinct_from_absent_optional_packages(self):
        runner = FakeRunner({REQUIRED_PACKAGE_QUERY: (1, "", "dpkg database unavailable\n")})
        result = audit_host(manifest(), runner=runner)
        codes = {item["code"] for item in result["blockers"]}
        self.assertIn("baseline_package_query_failed", codes)
        self.assertNotIn("driver_owner_mismatch", codes)


if __name__ == "__main__":
    unittest.main()
