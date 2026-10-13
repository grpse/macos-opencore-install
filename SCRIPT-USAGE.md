# Automated preparation on this Lenovo

Run these commands in a terminal from `/home/gilberto/Projects/macos`.

To do preparation and USB creation in one command, after backing up the selected SanDisk:

```bash
./prepare_opencore.py all --device /dev/disk/by-id/usb-SanDisk_Cruzer_Blade_04019702061122111801-0:0 --erase
```

It requests sudo when needed and asks for the exact USB serial before erasing. The separate commands below let you inspect each stage first.

## Prepare the EFI and recovery files

```bash
./prepare_opencore.py prepare
```

The script downloads OpenCore 1.0.8 DEBUG and the required release kexts, collects this laptop's original ACPI tables, generates EC/USBX/CPU/backlight fixes with pinned SSDTTime, builds a Kaby Lake config and runs the matching Linux `ocvalidate`. Apple recovery is downloaded and verified with OpenCore's `macrecovery.py`. Downloads are cached; rerun after interruptions. Run as your normal user; `sudo` is requested only when reading root-owned firmware tables. No USB or internal-disk formatting happens during preparation.

It uses `opencore-work/`, normally under 2 GiB for this recovery-only build. The broader Dortania recommendation is 15 GB of preparation space. Set another location with `./prepare_opencore.py --workdir /path/on/another/disk prepare`; use the same `--workdir` on later commands. That location cannot be on the USB you intend to erase.

To download and verify everything possible without sudo:

```bash
./prepare_opencore.py prepare --downloads-only
```

If disk utilities are missing, install them:

```bash
sudo apt install curl pciutils util-linux gdisk dosfstools udev
```

SSDTTime obtains its Linux ASL compiler from its upstream project. All downloaded packages are kept locally; GitHub-provided SHA-256 digests are checked where supplied, and selected release versions/hashes are recorded in `opencore-work/downloads.json`. Unique serial/MLB/UUID/ROM values are generated with OpenCore's Linux macserial and retained in a private file across rebuilds.

## Inspect the USB before erasing it

```bash
./prepare_opencore.py devices
./prepare_opencore.py usb --device /dev/disk/by-id/usb-SanDisk_Cruzer_Blade_04019702061122111801-0:0 --dry-run
```

That stable USB path was detected on this computer. Check the current `devices` output before using it. A dry run changes no USB contents. The script rejects internal disks, partitions, read-only disks, active swap, mapped devices, protected mounts, and any disk holding the running OS or preparation workspace.

## Create the bootable USB

**This erases all partitions and files on the selected SanDisk**, including its Ubuntu installer and ext4 partition. Back up anything needed and keep another Ubuntu recovery option first.

After preparation succeeds:

```bash
./prepare_opencore.py usb --device /dev/disk/by-id/usb-SanDisk_Cruzer_Blade_04019702061122111801-0:0 --erase
```

You must type the exact device-serial confirmation shown, then authenticate with sudo. There is no automatic yes option. The script checks the device identity again, makes GPT/FAT32 media, copies EFI and recovery files, unmounts/remounts read-only to verify every file hash, and unmounts the finished installer. An interrupted write can leave the USB unusable until you run the USB creation command again.

## What to expect at first boot

- Boot the USB in UEFI mode with **Ethernet and a USB mouse** connected. Secure Boot was already disabled on this laptop.
- The generated injector enables all twelve USB 2 HS ports. **USB 3 SuperSpeed is deliberately disabled** until you measure a complete port map. It should fall back to USB 2 for ordinary compatible devices; real boot behavior still needs testing.
- The I2C touchpad is deferred; the PS/2 keyboard driver and USB mouse provide initial input.
- Audio starts with `alcid=13`, which remains untested. Wireless, sleep and battery behavior need post-install checks.
- The script does not change BIOS settings, install macOS, resize Ubuntu, erase the internal SSD, or copy OpenCore into Ubuntu's EFI.

Schema validation and file integrity checks cannot prove firmware compatibility. Follow [the installation guide](OPENCORE-INSTALL-GUIDE.md) after USB creation, and photograph the final verbose lines if boot stalls.

## Verification and rebuilding

```bash
./prepare_opencore.py validate
python3 -m unittest discover -s tests -v
```

Preparation creates `opencore-work/ready.json` only after successful validation. USB writing refuses missing, changed or stale prepared files. Re-running preparation regenerates the EFI while preserving the previous build in a timestamped backup; serial identifiers and downloaded release versions remain stable. Changes made manually to a built EFI require a fresh reviewed/validated build; they are not silently approved by this script.

Technical sources: [OpenCore releases](https://github.com/acidanthera/OpenCorePkg/releases/tag/1.0.8), [Kaby Lake laptop configuration](https://dortania.github.io/OpenCore-Install-Guide/config-laptop.plist/kaby-lake.html), [SSDTTime](https://github.com/corpnewt/SSDTTime), [Linux recovery media](https://dortania.github.io/OpenCore-Install-Guide/installer-guide/linux-install.html), [USBMap](https://github.com/corpnewt/USBMap).
