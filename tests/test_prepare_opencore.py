import copy
import importlib.util
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("prep", ROOT / "prepare_opencore.py")
prep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prep)


def disk():
    return {"path": "/dev/sdz", "name": "sdz", "type": "disk", "tran": "usb",
            "ro": False, "serial": "TEST-USB", "model": "Test USB", "size": 16 * prep.GIB,
            "maj:min": "8:240", "mountpoints": [], "children": [
                {"path": "/dev/sdz1", "name": "sdz1", "type": "part", "maj:min": "8:241",
                 "mountpoints": ["/media/test/USB"]}]}


class DiskSafety(unittest.TestCase):
    def test_accept_usb(self):
        prep.validate_disk_policy(disk(), {"8:2"})

    def test_refuse_internal_disk(self):
        target = disk()
        target["tran"] = "sata"
        with self.assertRaises(prep.Failure):
            prep.validate_disk_policy(target, set())

    def test_refuse_partition(self):
        target = disk()
        target["type"] = "part"
        with self.assertRaises(prep.Failure):
            prep.validate_disk_policy(target, set())

    def test_refuse_os_workspace_or_swap(self):
        with self.assertRaises(prep.Failure):
            prep.validate_disk_policy(disk(), {"8:241"})

    def test_refuse_system_mount(self):
        target = disk()
        target["children"][0]["mountpoints"] = ["/boot/efi"]
        with self.assertRaises(prep.Failure):
            prep.validate_disk_policy(target, set())

    def test_refuse_lvm_child(self):
        target = disk()
        target["children"][0]["type"] = "crypt"
        with self.assertRaises(prep.Failure):
            prep.validate_disk_policy(target, set())

    def test_refuse_missing_identity_readonly_or_wrong_size(self):
        for key, value in (("serial", ""), ("ro", True), ("size", 2 * prep.GIB), ("size", 1000 * prep.GIB)):
            with self.subTest(key=key, value=value):
                target = disk()
                target[key] = value
                with self.assertRaises(prep.Failure):
                    prep.validate_disk_policy(target, set())

    def test_dry_run_never_invokes_privileged_command(self):
        args = prep.parser().parse_args(["--workdir", "/nonexistent-opencore-test", "usb", "--device", "/dev/sdz", "--dry-run"])
        with patch.object(prep, "inspect_device", return_value=disk()), patch.object(prep, "need"), \
             patch.object(prep, "run", side_effect=AssertionError("command executed")), \
             patch.object(prep.os, "geteuid", return_value=1000):
            prep.usb(args)

    def test_missing_ready_marker_blocks_before_sudo(self):
        with tempfile.TemporaryDirectory() as folder:
            args = prep.parser().parse_args(["--workdir", folder, "usb", "--device", "/dev/sdz", "--erase"])
            with patch.object(prep, "inspect_device", return_value=disk()), patch.object(prep, "need"), \
                 patch.object(prep, "run", side_effect=AssertionError("command executed")), \
                 patch.object(prep.os, "geteuid", return_value=1000):
                with self.assertRaisesRegex(prep.Failure, "No completed preparation"):
                    prep.usb(args)

    def test_wrong_confirmation_never_invokes_sudo(self):
        args = prep.parser().parse_args(["usb", "--device", "/dev/sdz", "--erase"])
        with patch.object(prep, "inspect_device", return_value=disk()), patch.object(prep, "need"), \
             patch.object(prep, "ready"), patch.object(prep.sys.stdin, "isatty", return_value=True), \
             patch("builtins.input", return_value="yes"), patch.object(prep.os, "geteuid", return_value=1000), \
             patch.object(prep, "run", side_effect=AssertionError("sudo executed")):
            with self.assertRaisesRegex(prep.Failure, "Confirmation did not match"):
                prep.usb(args)

    def test_device_swap_aborts_before_unmount_or_erase(self):
        args = prep.parser().parse_args(["usb", "--device", "/dev/sdz", "--erase"])
        other = copy.deepcopy(disk())
        other["serial"] = "DIFFERENT-USB"
        with patch.object(prep, "inspect_device", side_effect=[disk(), other]), patch.object(prep, "need"), \
             patch.object(prep, "ready"), patch.object(prep.sys.stdin, "isatty", return_value=True), \
             patch("builtins.input", return_value="ERASE TEST-USB"), patch.object(prep.os, "geteuid", return_value=1000), \
             patch.object(prep, "run", return_value=b"") as commands:
            with self.assertRaisesRegex(prep.Failure, "identity changed"):
                prep.usb(args)
            self.assertEqual(commands.call_args_list, [unittest.mock.call(["sudo", "-v"])])

    def test_automount_blocks_before_any_wipe(self):
        args = prep.parser().parse_args(["usb", "--device", "/dev/sdz", "--erase"])
        with patch.object(prep, "inspect_device", return_value=disk()), patch.object(prep, "need"), \
             patch.object(prep, "ready"), patch.object(prep.sys.stdin, "isatty", return_value=True), \
             patch("builtins.input", return_value="ERASE TEST-USB"), patch.object(prep.os, "geteuid", return_value=1000), \
             patch.object(prep, "run", return_value=b"") as commands:
            with self.assertRaisesRegex(prep.Failure, "mounted again"):
                prep.usb(args)
            self.assertFalse(any("wipefs" in call.args[0] or "sgdisk" in call.args[0] for call in commands.call_args_list))

    def test_write_pipeline_copies_verifies_and_unmounts(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "build/EFI/BOOT").mkdir(parents=True)
            (root / "build/EFI/BOOT/BOOTx64.efi").write_bytes(b"test-efi")
            (root / "recovery/com.apple.recovery.boot").mkdir(parents=True)
            (root / "recovery/com.apple.recovery.boot/BaseSystem.dmg").write_bytes(b"test-dmg")
            args = prep.parser().parse_args(["--workdir", folder, "usb", "--device", "/dev/sdz", "--erase"])
            target = disk()
            target["children"][0]["mountpoints"] = []
            mounts = []

            def fake_run(command, **kwargs):
                if "mount" in command:
                    mounts.append(Path(command[-1]))
                if "umount" in command and len(mounts) == 2:
                    # Emulate the mount disappearing; the data lives on the fake USB.
                    for child in Path(command[-1]).iterdir():
                        if child.is_dir():
                            prep.shutil.rmtree(child)
                return b""

            with patch.object(prep, "inspect_device", return_value=target), patch.object(prep, "need"), \
                 patch.object(prep, "ready"), patch.object(prep.sys.stdin, "isatty", return_value=True), \
                 patch("builtins.input", return_value="ERASE TEST-USB"), patch.object(prep.os, "geteuid", return_value=1000), \
                 patch.object(prep.os, "sync"), patch.object(prep, "run", side_effect=fake_run) as commands:
                prep.usb(args)
                self.assertEqual(len(mounts), 2)
                issued = [c.args[0] for c in commands.call_args_list]
                self.assertTrue(any("mkfs.vfat" in c for c in issued))
                self.assertEqual(sum("umount" in c for c in issued), 2)
                self.assertTrue(any("ro" in c for c in issued if "mount" in c))


class Integrity(unittest.TestCase):
    def test_archive_traversal_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive = root / "bad.zip"
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr("../outside", b"bad")
            with self.assertRaises(prep.Failure):
                prep.unzip(archive, root / "extract")
            self.assertFalse((root / "outside").exists())

    def test_map_keeps_all_hs_ports_within_limit(self):
        info = prep.usb2_map({})
        personality = next(iter(info["IOKitPersonalities"].values()))
        ports = personality["IOProviderMergeProperties"]["ports"]
        self.assertEqual(len(ports), 12)
        self.assertTrue(all(n.startswith("HS") for n in ports))
        self.assertEqual({int.from_bytes(p["port"], "little") for p in ports.values()}, set(range(1, 13)))

    def test_ready_refuses_efi_tampering(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "build/EFI").mkdir(parents=True)
            prep.save_json(root / "ready.json", {"hardware": {"test": 1}, "efi": {"test": "hash"}})
            with patch.object(prep, "hardware", return_value={"test": 1}):
                with self.assertRaisesRegex(prep.Failure, "EFI changed"):
                    prep.ready(root)


if __name__ == "__main__":
    unittest.main()
