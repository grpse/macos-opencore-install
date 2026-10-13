#!/usr/bin/env python3
"""Build a Big Sur recovery USB for the Lenovo 320-14IKB, from Ubuntu.

No disk writes occur during prepare. USB erasure requires an explicit device,
--erase, and an interactive identity confirmation. Uses Python's standard library.
"""
import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile

BASE = Path(__file__).resolve().parent
OC_VERSION = "1.0.8"
SSDT_COMMIT = "f002d597fdc575fe3c91015ff59ff0b436a29286"
APPLE_GUID = "7C436110-AB2A-4BBB-A880-FE41995C9F82"
GIB = 1024 ** 3


class Failure(Exception):
    pass


def run(args, *, capture=False, cwd=None, timeout=300):
    result = subprocess.run([str(a) for a in args], cwd=cwd, check=False,
                            env=os.environ | {"LC_ALL": "C"},
                            stdout=subprocess.PIPE if capture else None,
                            stderr=subprocess.PIPE if capture else None,
                            timeout=timeout)
    if result.returncode:
        error = result.stderr.decode(errors="replace") if capture else ""
        raise Failure(f"Command failed ({result.returncode}): {' '.join(map(str, args))}\n{error}")
    return result.stdout if capture else b""


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2) + "\n")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def fetch_json(url):
    return json.loads(run(["curl", "--fail", "--silent", "--show-error", "--location",
        "--connect-timeout", "10", "--max-time", "60", "--retry", "2",
        "--header", "Accept: application/vnd.github+json", url], capture=True, timeout=200))


def download(url, target, expected=None):
    if target.exists() and (expected is None or sha(target) == expected):
        return target
    print(f"Downloading {target.name} ...", flush=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    for attempt in range(3):
        try:
            run(["curl", "--fail", "--silent", "--show-error", "--location",
                 "--connect-timeout", "10", "--max-time", "180", "--output", partial, url], timeout=190)
            if expected and sha(partial) != expected:
                raise Failure(f"SHA-256 mismatch: {target.name}")
            partial.replace(target)
            return target
        except (OSError, Failure):
            partial.unlink(missing_ok=True)
            if attempt == 2:
                raise
            time.sleep(2)


def unzip(archive, dest):
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        for info in z.infolist():
            path = (dest / info.filename).resolve()
            if not path.is_relative_to(dest.resolve()):
                raise Failure("Unsafe archive path")
            if stat.S_ISLNK(info.external_attr >> 16):
                raise Failure("Archive symlinks are not accepted")
        z.extractall(dest)


def release(state, repo, pattern, tag=None):
    lockpath = state / "downloads.json"
    lock = json.loads(lockpath.read_text()) if lockpath.exists() else {}
    key = repo + ("@" + tag if tag else "@locked-latest")
    if key not in lock:
        suffix = "tags/" + tag if tag else "latest"
        metadata = fetch_json(f"https://api.github.com/repos/{repo}/releases/{suffix}")
        assets = [a for a in metadata["assets"] if re.fullmatch(pattern, a["name"], re.I)]
        if len(assets) != 1:
            raise Failure(f"Expected one release archive in {repo}; found {[a['name'] for a in assets]}")
        asset = assets[0]
        lock[key] = {"tag": metadata["tag_name"], "name": asset["name"],
                     "url": asset["browser_download_url"], "digest": asset.get("digest")}
        save_json(lockpath, lock)
    item = lock[key]
    digest = item.get("digest") or ""
    expected = digest.removeprefix("sha256:") if digest.startswith("sha256:") else item.get("sha256")
    archive = download(item["url"], state / "cache" / (repo.replace("/", "-") + "-" + item["name"]), expected)
    item["sha256"] = sha(archive)
    save_json(lockpath, lock)
    dest = state / "packages" / (repo.replace("/", "-") + "-" + item["tag"])
    if not (dest / ".complete").exists():
        if dest.exists():
            shutil.rmtree(dest)
        unzip(archive, dest)
        (dest / ".complete").touch()
    return dest


def hardware():
    def read(path):
        try:
            return Path(path).read_text().strip()
        except OSError:
            return "unknown"
    pci = run(["lspci", "-nn"], capture=True).decode()
    cpu_output = run(["lscpu"], capture=True).decode()
    # Scaling MHz changes continuously and must not invalidate a completed build.
    cpu = next(line.split(":", 1)[1].strip() for line in cpu_output.splitlines()
               if line.startswith("Model name:"))
    report = {"model": read("/sys/class/dmi/id/product_version"),
              "board": read("/sys/class/dmi/id/board_name"),
              "bios": read("/sys/class/dmi/id/bios_version"), "pci": pci, "cpu": cpu,
              "usb2_ports": read("/sys/bus/usb/devices/usb1/maxchild"),
              "xhci_path": read("/sys/bus/pci/devices/0000:00:14.0/firmware_node/path")}
    if not ("320-14IKB" in report["model"] and "i5-7200U" in cpu
            and "8086:5916" in pci and "10ec:8168" in pci):
        raise Failure("This script is restricted to this IdeaPad 320-14IKB / i5-7200U / HD620 / RTL8111 hardware.")
    if report["usb2_ports"] != "12" or report["xhci_path"] != "\\_SB_.PCI0.XHC_":
        raise Failure("Unexpected USB controller topology; refusing to guess a port map.")
    return report


def need(commands):
    missing = [c for c in commands if not shutil.which(c)]
    if missing:
        raise Failure("Missing commands: " + ", ".join(missing)
                      + ". On Ubuntu: sudo apt install pciutils util-linux gdisk dosfstools udev")


def packages(state):
    oc = release(state, "acidanthera/OpenCorePkg", r"OpenCore-1\.0\.8-DEBUG\.zip", OC_VERSION)
    kexts = {}
    specs = [("acidanthera/Lilu", r"Lilu-.*-RELEASE\.zip", ["Lilu.kext"]),
             ("acidanthera/VirtualSMC", r"VirtualSMC-.*-RELEASE\.zip", ["VirtualSMC.kext", "SMCBatteryManager.kext"]),
             ("acidanthera/WhateverGreen", r"WhateverGreen-.*-RELEASE\.zip", ["WhateverGreen.kext"]),
             ("acidanthera/AppleALC", r"AppleALC-.*-RELEASE\.zip", ["AppleALC.kext"]),
             ("acidanthera/VoodooPS2", r"VoodooPS2Controller-.*-RELEASE\.zip", ["VoodooPS2Controller.kext"]),
             ("Mieze/RTL8111_driver_for_OS_X", r"RealtekRTL8111.*\.zip", ["RealtekRTL8111.kext"])]
    for repo, pattern, names in specs:
        folder = release(state, repo, pattern)
        for name in names:
            found = list(folder.rglob(name))
            # Some archives include both Debug and Release bundles.
            preferred = [p for p in found if not any(x.lower() in ("debug", "__macosx") for x in p.parts)]
            if len(preferred) != 1:
                raise Failure(f"Cannot uniquely locate {name} in {folder}")
            kexts[name] = preferred[0]
    # Pin the binary-data repository commit in the download lock.
    lockpath = state / "downloads.json"
    lock = json.loads(lockpath.read_text())
    if "HfsPlus" not in lock:
        commit = fetch_json("https://api.github.com/repos/acidanthera/OcBinaryData/commits/master")["sha"]
        lock["HfsPlus"] = {"commit": commit}
        save_json(lockpath, lock)
    item = lock["HfsPlus"]
    hfs = download(f"https://raw.githubusercontent.com/acidanthera/OcBinaryData/{item['commit']}/Drivers/HfsPlus.efi",
                   state / "cache/HfsPlus.efi", item.get("sha256"))
    item["sha256"] = sha(hfs)
    save_json(lockpath, lock)
    archive = download(f"https://codeload.github.com/corpnewt/SSDTTime/zip/{SSDT_COMMIT}",
                       state / "cache" / f"SSDTTime-{SSDT_COMMIT}.zip")
    folder = state / "packages/SSDTTime"
    if not (folder / f"SSDTTime-{SSDT_COMMIT}/SSDTTime.py").exists():
        unzip(archive, folder)
    ssdt = folder / f"SSDTTime-{SSDT_COMMIT}"
    # Supply the compiler before SSDTTime initializes, avoiding its unbounded downloader.
    lock = json.loads(lockpath.read_text())
    if "iasl" not in lock:
        commit = fetch_json("https://api.github.com/repos/corpnewt/linux_iasl/commits/main")["sha"]
        lock["iasl"] = {"commit": commit}
        save_json(lockpath, lock)
    item = lock["iasl"]
    compiler_zip = download(f"https://raw.githubusercontent.com/corpnewt/linux_iasl/{item['commit']}/iasl.zip",
        state / "cache/iasl.zip", item.get("sha256"))
    item["sha256"] = sha(compiler_zip)
    save_json(lockpath, lock)
    compiler_dir = state / "packages/iasl"
    unzip(compiler_zip, compiler_dir)
    compiler = next(p for p in compiler_dir.rglob("iasl") if p.is_file())
    shutil.copy2(compiler, ssdt / "Scripts/iasl")
    (ssdt / "Scripts/iasl").chmod(0o700)
    return oc, kexts, hfs, ssdt


def recovery(state, oc):
    folder = state / "recovery/com.apple.recovery.boot"
    script = oc / "Utilities/macrecovery/macrecovery.py"
    spec = importlib.util.spec_from_file_location("apple_recovery", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if folder.exists():
        try:
            mod.verify_image(str(folder / "BaseSystem.dmg"), str(folder / "BaseSystem.chunklist"))
            return folder
        except (OSError, RuntimeError, AssertionError, ValueError, struct.error):
            folder.rename(folder.with_name("incomplete-" + str(time.time_ns())))
            print("Incomplete recovery retained separately; downloading a fresh verified copy.")
    folder.mkdir(parents=True, exist_ok=True)
    print("Downloading Big Sur recovery from Apple; upstream also verifies its chunklist.", flush=True)
    run([sys.executable, BASE / "scripts/recovery_fetch.py", script, "-b", "Mac-2BD1B31983FE1663", "-m", "00000000000000000",
         "-o", folder, "-n", "BaseSystem", "download"], timeout=3600)
    mod.verify_image(str(folder / "BaseSystem.dmg"), str(folder / "BaseSystem.chunklist"))
    return folder


def export_acpi(state, supplied=None):
    if supplied:
        folder = Path(supplied).resolve()
        if not (folder / "DSDT.aml").is_file():
            raise Failure("--acpi-dir must contain this laptop's DSDT.aml and preferably its SSDTs.")
        return folder
    folder = state / "acpi/collecting"
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True, exist_ok=True)
    # Refresh every prepare: do not silently reuse tables after a firmware update.
    source = Path("/sys/firmware/acpi/tables")
    files = [p for p in sorted(source.iterdir()) if p.name == "DSDT" or p.name.startswith("SSDT")]
    if not files:
        raise Failure("No ACPI firmware tables found.")
    for p in files:
        try:
            data = p.read_bytes()
        except PermissionError:
            if not sys.stdin.isatty():
                raise Failure("Firmware tables require sudo. Run prepare in your terminal, or provide --acpi-dir.")
            data = run(["sudo", "cat", str(p)], capture=True)
        if len(data) < 36 or data[:4] not in (b"DSDT", b"SSDT") or sum(data) % 256:
            raise Failure(f"Invalid firmware ACPI table: {p.name}")
        (folder / (p.name + ".aml")).write_bytes(data)
    original = state / "acpi/original"
    if original.exists():
        original.rename(original.with_name("original-backup-" + str(time.time_ns())))
    folder.rename(original)
    return original


def generate_acpi(state, ssdt, tables):
    output = state / "acpi/generated"
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    run([sys.executable, BASE / "scripts/acpi_builder.py", ssdt, tables, output], timeout=600)
    for name in ("SSDT-PLUG.aml", "SSDT-USBX.aml", "SSDT-PNLF.aml"):
        if not (output / name).is_file():
            raise Failure(f"ACPI generation did not produce {name}; see its output before proceeding.")
    return output


def usb2_map(report):
    # All twelve HS ports: no guessed SuperSpeed companion assignments.
    ports = {}
    for n in range(1, 13):
        removable = Path(f"/sys/bus/usb/devices/1-{n}/removable")
        internal = removable.exists() and removable.read_text().strip() == "fixed"
        ports[f"HS{n:02}"] = {"port": n.to_bytes(4, "little"), "UsbConnector": 255 if internal else 0}
    return {"CFBundleDevelopmentRegion": "English", "CFBundleIdentifier": "local.ideapad320.USB2Bootstrap",
            "CFBundleInfoDictionaryVersion": "6.0", "CFBundleName": "USB2Bootstrap",
            "CFBundlePackageType": "KEXT", "CFBundleShortVersionString": "1.0", "CFBundleVersion": "1.0",
            "OSBundleRequired": "Root", "IOKitPersonalities": {"MacBookPro14,1-XHC": {
                "CFBundleIdentifier": "com.apple.driver.AppleUSBHostMergeProperties",
                "IOClass": "AppleUSBHostMergeProperties", "IOProviderClass": "AppleUSBXHCIPCI",
                "IONameMatch": "XHC", "model": "MacBookPro14,1",
                "IOProviderMergeProperties": {"port-count": (12).to_bytes(4, "little"), "ports": ports}}}}


def snapshot_kexts(efi, template):
    entries = []
    # Order explicit dependency families; shared PS2 plugins load after their parent.
    order = ["Lilu.kext", "VirtualSMC.kext", "SMCBatteryManager.kext", "WhateverGreen.kext",
             "AppleALC.kext", "RealtekRTL8111.kext", "VoodooPS2Controller.kext", "USB2Bootstrap.kext"]
    for name in order:
        bundle = efi / "OC/Kexts" / name
        children = sorted(bundle.glob("Contents/PlugIns/*.kext"))
        children.sort(key=lambda p: (p.name != "VoodooInput.kext", p.name))
        for item in [bundle] + children:
            info = plistlib.loads((item / "Contents/Info.plist").read_bytes())
            ent = copy.deepcopy(template)
            ent.update(Arch="x86_64", BundlePath=str(item.relative_to(efi / "OC/Kexts")),
                       Comment=item.name, Enabled=True, MaxKernel="", MinKernel="",
                       PlistPath="Contents/Info.plist",
                       ExecutablePath=("Contents/MacOS/" + info["CFBundleExecutable"])
                       if info.get("CFBundleExecutable") else "")
            # The actual touchpad is I2C, not PS/2. Keep keyboard and USB mouse input.
            if item.name == "VoodooPS2Trackpad.kext":
                ent["Enabled"] = False
            entries.append(ent)
    return entries


def identities(state, oc):
    path = state / "private-identities.json"
    if path.exists():
        return json.loads(path.read_text())
    binary = oc / "Utilities/macserial/macserial.linux"
    binary.chmod(0o700)
    output = run([binary, "-m", "MacBookPro14,1", "-n", "1"], capture=True).decode()
    pair = next((line.split("|") for line in output.splitlines() if "|" in line), None)
    if not pair or len(pair) != 2:
        raise Failure("macserial did not return a serial/MLB pair")
    data = {"SystemSerialNumber": pair[0].strip(), "MLB": pair[1].strip(),
            "SystemUUID": str(uuid.uuid4()).upper(), "ROM": os.urandom(6).hex()}
    save_json(path, data)
    return data


def build(state, report, oc, kexts, hfs, acpi):
    staging = state / "staging"
    if staging.exists():
        shutil.rmtree(staging)
    efi = staging / "EFI"
    for name in ("ACPI", "Drivers", "Kexts", "Tools"):
        (efi / "OC" / name).mkdir(parents=True, exist_ok=True)
    (efi / "BOOT").mkdir()
    shutil.copy2(oc / "X64/EFI/BOOT/BOOTx64.efi", efi / "BOOT/BOOTx64.efi")
    shutil.copy2(oc / "X64/EFI/OC/OpenCore.efi", efi / "OC/OpenCore.efi")
    for name in ("OpenRuntime.efi", "ResetNvramEntry.efi"):
        shutil.copy2(oc / "X64/EFI/OC/Drivers" / name, efi / "OC/Drivers" / name)
    shutil.copy2(hfs, efi / "OC/Drivers/HfsPlus.efi")
    for name, source in kexts.items():
        shutil.copytree(source, efi / "OC/Kexts" / name)
    mapdir = efi / "OC/Kexts/USB2Bootstrap.kext/Contents"
    mapdir.mkdir(parents=True)
    (mapdir / "Info.plist").write_bytes(plistlib.dumps(usb2_map(report)))
    cfg = plistlib.loads((oc / "Docs/Sample.plist").read_bytes())
    patches = plistlib.loads((acpi / "patches_OC.plist").read_bytes())
    for key in ("Add", "Delete", "Patch"):
        cfg["ACPI"][key] = patches["ACPI"].get(key, [])
    for entry in cfg["ACPI"]["Add"]:
        if entry["Enabled"]:
            shutil.copy2(acpi / entry["Path"], efi / "OC/ACPI" / entry["Path"])
    cfg["Booter"]["MmioWhitelist"] = []
    cfg["Booter"]["Quirks"].update(AvoidRuntimeDefrag=True, EnableSafeModeSlide=True,
        EnableWriteUnprotector=True, ProvideCustomSlide=True, RebuildAppleMemoryMap=False,
        SetupVirtualMap=True, SyncRuntimePermissions=False)
    cfg["DeviceProperties"]["Add"] = {"PciRoot(0x0)/Pci(0x2,0x0)": {
        "AAPL,ig-platform-id": bytes.fromhex("00001B59"),
        # Conservative 32 MB DVMT allocation; firmware does not expose this setting.
        "framebuffer-patch-enable": bytes.fromhex("01000000"),
        "framebuffer-stolenmem": bytes.fromhex("00003001"),
        "framebuffer-fbmem": bytes.fromhex("00009000")}}
    cfg["DeviceProperties"]["Delete"] = {}
    cfg["Kernel"]["Add"] = snapshot_kexts(efi, cfg["Kernel"]["Add"][0])
    for key in ("Block", "Force", "Patch"):
        cfg["Kernel"][key] = []
    cfg["Kernel"]["Emulate"]["Cpuid1Data"] = b""
    cfg["Kernel"]["Emulate"]["Cpuid1Mask"] = b""
    cfg["Kernel"]["Quirks"].update(AppleXcpmCfgLock=True, DisableIoMapper=True,
        DisableLinkeditJettison=True, PanicNoKextDump=True, PowerTimeoutKernelPanic=True,
        XhciPortLimit=False)
    cfg["Misc"]["Boot"].update(HideAuxiliary=False, PickerMode="Builtin", ShowPicker=True, Timeout=10)
    cfg["Misc"]["Debug"].update(AppleDebug=True, ApplePanic=True, DisableWatchDog=True,
        Target=67, DisplayLevel=2147483650)
    cfg["Misc"]["Entries"] = []
    cfg["Misc"]["Tools"] = []
    cfg["Misc"]["Security"].update(AllowSetDefault=True, BlacklistAppleUpdate=True,
        ScanPolicy=0, SecureBootModel="Default", Vault="Optional", DmgLoading="Signed")
    cfg["NVRAM"]["Add"] = {APPLE_GUID: {"boot-args": "-v keepsyms=1 debug=0x100 alcid=13",
        "csr-active-config": bytes(4), "prev-lang:kbd": b"", "run-efi-updater": "No"}}
    cfg["NVRAM"]["Delete"] = {APPLE_GUID: ["boot-args", "csr-active-config", "prev-lang:kbd"]}
    identity = identities(state, oc)
    cfg["PlatformInfo"].update(Automatic=True, UpdateDataHub=True, UpdateNVRAM=True,
        UpdateSMBIOS=True, UpdateSMBIOSMode="Create")
    cfg["PlatformInfo"]["Generic"].update({**identity, "ROM": bytes.fromhex(identity["ROM"]),
        "SystemProductName": "MacBookPro14,1", "ProcessorType": 0, "SpoofVendor": True})
    cfg["UEFI"]["ConnectDrivers"] = True
    driver_template = cfg["UEFI"]["Drivers"][0]
    cfg["UEFI"]["Drivers"] = []
    for name in ("HfsPlus.efi", "OpenRuntime.efi", "ResetNvramEntry.efi"):
        ent = copy.deepcopy(driver_template)
        ent.update(Arguments="", Comment=name, Enabled=True, LoadEarly=False, Path=name)
        cfg["UEFI"]["Drivers"].append(ent)
    cfg["UEFI"]["Quirks"].update(ReleaseUsbOwnership=True, RequestBootVarRouting=True)
    cfg["UEFI"]["ReservedMemory"] = []
    (efi / "OC/config.plist").write_bytes(plistlib.dumps(cfg, sort_keys=False))
    validator = oc / "Utilities/ocvalidate/ocvalidate.linux"
    validator.chmod(0o700)
    run([validator, efi / "OC/config.plist"])
    check_efi(efi)
    final = state / "build"
    if final.exists():
        final.rename(state / ("build-backup-" + str(time.time_ns())))
    staging.rename(final)
    return final / "EFI"


def check_efi(efi):
    cfg = plistlib.loads((efi / "OC/config.plist").read_bytes())
    required = [efi / "BOOT/BOOTx64.efi", efi / "OC/OpenCore.efi"]
    for section, folder, key in ((cfg["ACPI"]["Add"], "ACPI", "Path"),
                                 (cfg["UEFI"]["Drivers"], "Drivers", "Path")):
        for ent in section:
            if ent["Enabled"]:
                required.append(efi / "OC" / folder / ent[key])
    ids = set()
    for ent in cfg["Kernel"]["Add"]:
        if not ent["Enabled"]:
            continue
        bundle = efi / "OC/Kexts" / ent["BundlePath"]
        plist = bundle / ent["PlistPath"]
        required.append(plist)
        if ent["ExecutablePath"]:
            required.append(bundle / ent["ExecutablePath"])
        info = plistlib.loads(plist.read_bytes())
        ident = info["CFBundleIdentifier"]
        if ident in ids:
            raise Failure(f"Duplicate enabled kext: {ident}")
        # Non-Apple plugin dependencies must precede the plugin.
        for dependency in info.get("OSBundleLibraries", {}):
            if not dependency.startswith("com.apple.") and dependency not in ids:
                raise Failure(f"{ident} loads before dependency {dependency}")
        ids.add(ident)
    for p in required:
        if not p.is_file() or p.stat().st_size == 0:
            raise Failure(f"Missing or empty EFI file: {p}")
    if cfg["Kernel"]["Quirks"]["XhciPortLimit"]:
        raise Failure("XhciPortLimit must remain disabled")
    return cfg


def tree_hashes(folder):
    return {str(p.relative_to(folder)): sha(p) for p in sorted(folder.rglob("*")) if p.is_file()}


def prepare(args):
    if os.geteuid() == 0:
        raise Failure("Run prepare as your normal user; it invokes sudo only to read firmware tables.")
    need(["lspci", "lscpu", "curl"])
    report = hardware()
    state = args.workdir
    state.mkdir(parents=True, exist_ok=True)
    state.chmod(0o700)
    free = shutil.disk_usage(state).free
    if free < 2 * GIB:
        raise Failure("Need at least 2 GiB free for this recovery-only build (15 GB is the guide's general recommendation).")
    print(f"Preparing for {report['model']}; free workspace: {free / GIB:.1f} GiB", flush=True)
    save_json(state / "hardware.json", report)
    oc, kexts, hfs, ssdt = packages(state)
    folder = recovery(state, oc)
    if args.downloads_only:
        print(f"Downloads verified. Continue with: {Path(__file__).name} --workdir {state} prepare")
        return
    tables = export_acpi(state, args.acpi_dir)
    acpi = generate_acpi(state, ssdt, tables)
    efi = build(state, report, oc, kexts, hfs, acpi)
    save_json(state / "ready.json", {"hardware": report, "opencore": OC_VERSION,
        "efi": tree_hashes(efi), "recovery": tree_hashes(folder),
        "limitations": ["USB 2 only; USB 3 needs a measured map", "Use Ethernet",
                        "I2C touchpad needs post-install configuration", "Audio layout 13 is untested",
                        "Firmware boot and graphics remain untested"]})
    print(f"\nValidated EFI: {efi}\nRecovery verified: {folder}\nUSB 2 only; use Ethernet and a USB mouse.")
    print("Next: ./prepare_opencore.py devices, then usb --device /dev/disk/by-id/... --dry-run")


def block_tree():
    return json.loads(run(["lsblk", "-J", "-b", "-o",
        "NAME,PATH,SIZE,MODEL,SERIAL,TRAN,TYPE,RM,RO,MAJ:MIN,FSTYPE,MOUNTPOINTS"], capture=True))["blockdevices"]


def descendants(node):
    yield node
    for child in node.get("children", []):
        yield from descendants(child)


def inspect_device(device, state):
    path = Path(device).resolve(strict=True)
    if not stat.S_ISBLK(path.stat().st_mode):
        raise Failure("USB target must be a real whole block device, not a file.")
    nodes = block_tree()
    disk = next((n for n in nodes if n["path"] == str(path)), None)
    if not disk or disk["type"] != "disk" or disk.get("tran") != "usb":
        raise Failure("Only whole USB disks are accepted. Internal disks and partitions are refused.")
    validate_disk_policy(disk, protected_numbers(state))
    for n in descendants(disk):
        holders = Path("/sys/class/block") / n["name"] / "holders"
        if holders.exists() and any(holders.iterdir()):
            raise Failure(f"{n['path']} has active RAID/LVM/encryption holders")
    return disk


def protected_numbers(state):
    numbers = set()
    for path in ("/", "/boot", "/boot/efi", str(BASE), str(state), os.getcwd()):
        if Path(path).exists():
            numbers.add(run(["findmnt", "-n", "-o", "MAJ:MIN", "-T", path], capture=True).decode().strip())
    for line in Path("/proc/swaps").read_text().splitlines()[1:]:
        path = line.split()[0]
        if Path(path).exists():
            if stat.S_ISBLK(Path(path).stat().st_mode):
                dev = Path(path).stat().st_rdev
                numbers.add(f"{os.major(dev)}:{os.minor(dev)}")
            else:
                numbers.add(run(["findmnt", "-n", "-o", "MAJ:MIN", "-T", path], capture=True).decode().strip())
    return numbers


def validate_disk_policy(disk, protected):
    if disk.get("type") != "disk" or disk.get("tran") != "usb" or disk.get("ro"):
        raise Failure("Target must be a writable whole USB disk")
    if not disk.get("serial"):
        raise Failure("Target must expose a serial number for identity confirmation")
    if not 4 * GIB <= int(disk["size"]) <= 512 * GIB:
        raise Failure("Accepted installer USB capacity is 4–512 GiB")
    for node in descendants(disk):
        if node.get("maj:min") in protected:
            raise Failure("Target contains the OS, active swap, script, or preparation workspace")
        if node["type"] not in ("disk", "part"):
            raise Failure("Target contains an active mapped device")
        for mount in node.get("mountpoints", []) or []:
            if mount and not mount.startswith(("/media/", "/run/media/", "/mnt/")):
                raise Failure(f"Refusing protected mount: {mount}")


def identity(disk):
    return tuple(disk.get(k) for k in ("path", "serial", "model", "size", "maj:min"))


def devices(args):
    need(["lsblk"])
    for disk in block_tree():
        if disk["type"] == "disk":
            print(f"{disk['path']}  {disk.get('model')}  {int(disk['size']) / GIB:.1f} GiB  transport={disk.get('tran')}")
            for child in descendants(disk):
                for mount in child.get("mountpoints", []) or []:
                    if mount:
                        print(f"  mounted: {child['path']} → {mount}")
            if disk.get("tran") == "usb":
                links = [str(p) for p in Path('/dev/disk/by-id').glob('usb-*') if p.resolve() == Path(disk['path'])]
                for link in links:
                    print(f"  stable path: {link}")


def ready(state):
    marker = state / "ready.json"
    if not marker.exists():
        raise Failure("No completed preparation. Run prepare first; USB will not be erased.")
    report = json.loads(marker.read_text())
    if hardware() != report["hardware"]:
        raise Failure("Hardware/BIOS/topology changed since prepare. Prepare again.")
    if tree_hashes(state / "build/EFI") != report["efi"]:
        raise Failure("EFI changed since validation. Prepare again before writing USB.")
    if tree_hashes(state / "recovery/com.apple.recovery.boot") != report["recovery"]:
        raise Failure("Recovery changed since verification. Prepare again.")
    check_efi(state / "build/EFI")
    return report


def usb(args):
    if os.geteuid() == 0:
        raise Failure("Run as your normal user; the script invokes sudo for disk operations.")
    need(["lsblk", "findmnt"])
    disk = inspect_device(args.device, args.workdir)
    print(f"Selected: {disk['path']} | {disk['model']} | {int(disk['size']) / GIB:.1f} GiB")
    print("All existing partitions and files on this USB will be erased.")
    print("Plan: unmount → GPT → FAT32 EFI partition → copy EFI/recovery → verify → unmount")
    if args.dry_run:
        print("DRY RUN: no USB files, partitions, mounts or settings were changed.")
        if not (args.workdir / "ready.json").exists():
            print("Preparation is not complete yet. An actual write will be refused until prepare succeeds.")
        else:
            ready(args.workdir)
            print("Prepared files verified.")
        return
    need(["sudo", "sgdisk", "wipefs", "mkfs.vfat", "udevadm", "mount", "umount", "blockdev"])
    ready(args.workdir)  # Before any unmount/erase or sudo request.
    if not args.erase or not sys.stdin.isatty():
        raise Failure("Writing requires --erase and an interactive terminal. Use --dry-run to inspect.")
    expected = "ERASE " + disk["serial"]
    print("Back up this USB's contents before continuing.")
    if input(f"Type exactly '{expected}' to erase the selected USB: ").strip() != expected:
        raise Failure("Confirmation did not match. Nothing erased.")
    run(["sudo", "-v"])
    original = identity(disk)

    def recheck(require_unmounted=False):
        current = inspect_device(args.device, args.workdir)
        if identity(current) != original:
            raise Failure("USB identity changed. Aborting.")
        if require_unmounted and any(m for n in descendants(current) for m in n.get("mountpoints", []) or []):
            raise Failure("USB was mounted again. Refusing a destructive operation; close file managers and retry.")
        return current

    recheck()
    for node in reversed(list(descendants(disk))):
        if any(node.get("mountpoints") or []):
            run(["sudo", "umount", node["path"]])
    current = recheck(require_unmounted=True)
    for node in reversed(list(descendants(current))):
        recheck(require_unmounted=True)
        run(["sudo", "wipefs", "--all", node["path"]])
    recheck(require_unmounted=True)
    run(["sudo", "sgdisk", "--zap-all", disk["path"]])
    end = "+8G" if int(disk["size"]) > 9 * GIB else "0"
    recheck(require_unmounted=True)
    run(["sudo", "sgdisk", "--clear", f"--new=1:2048:{end}", "--typecode=1:ef00",
         "--change-name=1:OPENCORE", disk["path"]])
    run(["sudo", "blockdev", "--rereadpt", disk["path"]])
    run(["sudo", "udevadm", "settle"])
    current = recheck(require_unmounted=True)
    parts = current.get("children", [])
    if len(parts) != 1 or parts[0]["type"] != "part":
        raise Failure("Expected exactly one new partition. USB has been repartitioned; no copying performed.")
    partition = parts[0]["path"]
    recheck(require_unmounted=True)
    run(["sudo", "mkfs.vfat", "-F", "32", "-n", "OPENCORE", partition])
    mountpoint = Path(tempfile.mkdtemp(prefix="opencore-usb-"))
    mounted = False
    try:
        recheck(require_unmounted=True)
        run(["sudo", "mount", "-t", "vfat", "-o", f"uid={os.getuid()},gid={os.getgid()},umask=0077",
             partition, mountpoint])
        mounted = True
        for source, name in ((args.workdir / "build/EFI", "EFI"),
                             (args.workdir / "recovery/com.apple.recovery.boot", "com.apple.recovery.boot")):
            shutil.copytree(source, mountpoint / name)
        os.sync()
        # Unmount/remount read-only to verify the persisted files, not only the write cache.
        run(["sudo", "umount", mountpoint])
        mounted = False
        run(["sudo", "blockdev", "--flushbufs", partition])
        recheck(require_unmounted=True)
        run(["sudo", "mount", "-t", "vfat", "-o", "ro", partition, mountpoint])
        mounted = True
        for source, name in ((args.workdir / "build/EFI", "EFI"),
                             (args.workdir / "recovery/com.apple.recovery.boot", "com.apple.recovery.boot")):
            if tree_hashes(source) != tree_hashes(mountpoint / name):
                raise Failure("USB read-back mismatch. Do not use this installer.")
    finally:
        if mounted:
            run(["sudo", "umount", mountpoint])
        mountpoint.rmdir()
    print("\nUSB written, read-back verified and unmounted. Boot its UEFI entry with Ethernet connected.")
    print("USB 2 only. Firmware boot remains untested; keep Ubuntu and the internal SSD intact.")


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--workdir", type=lambda s: Path(s).expanduser().resolve(), default=BASE / "opencore-work",
                   help="cache/build folder; must not be on the USB being erased")
    sub = p.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare", help="download, generate and validate without touching USB")
    prep.add_argument("--downloads-only", action="store_true", help="fetch/verify packages and Apple recovery without sudo")
    prep.add_argument("--acpi-dir", help="existing original firmware table directory containing DSDT.aml")
    sub.add_parser("devices", help="list disks and stable USB paths")
    sub.add_parser("validate", help="check prepared file hashes and hardware")
    write = sub.add_parser("usb", help="inspect or explicitly erase/write a USB")
    everything = sub.add_parser("all", help="prepare and then interactively create the USB")
    everything.add_argument("--acpi-dir", help="existing original firmware tables")
    everything.set_defaults(downloads_only=False)
    for command in (write, everything):
        command.add_argument("--device", required=True, help="whole USB disk, preferably /dev/disk/by-id/usb-...")
        command.add_argument("--dry-run", action="store_true")
        command.add_argument("--erase", action="store_true")
    for command in sub.choices.values():
        command.add_argument("--workdir", type=lambda s: Path(s).expanduser().resolve(), default=argparse.SUPPRESS)
    return p


def main():
    if not __debug__:
        raise Failure("Run Python without -O/PYTHONOPTIMIZE; Apple signature verification needs assertions enabled.")
    args = parser().parse_args()
    args.workdir = args.workdir.resolve()
    # Serialize all operations sharing a build/cache; Linux flock is released on exit.
    import fcntl
    lockname = hashlib.sha256(str(args.workdir).encode()).hexdigest()[:20]
    fd = os.open(f"/tmp/opencore-{os.getuid()}-{lockname}.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Failure("Another operation is using this workdir. Wait for it to finish.")
        if args.command == "prepare":
            prepare(args)
        elif args.command == "devices":
            devices(args)
        elif args.command == "validate":
            ready(args.workdir)
            print("Prepared files and hardware verified; firmware boot is still untested.")
        elif args.command == "all":
            if not args.dry_run:
                if not args.erase:
                    raise Failure("all requires --erase (with later interactive confirmation), or --dry-run.")
                need(["lsblk", "findmnt"])
                inspect_device(args.device, args.workdir)
                prepare(args)
            usb(args)
        else:
            usb(args)
    finally:
        os.close(fd)


if __name__ == "__main__":
    try:
        main()
    except (Failure, OSError, subprocess.TimeoutExpired, ValueError, KeyError) as exc:
        print(f"\nSTOP: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nInterrupted. Re-run prepare to resume downloads, or inspect the USB before retrying a write.", file=sys.stderr)
        sys.exit(130)
