# Install macOS on this Lenovo IdeaPad using OpenCore

Prepared on 13 October 2026 from the English auto-generated transcript of [Spec Tech’s video](https://www.youtube.com/watch?v=QLxIWA0tOvE), with corrections from the linked project documentation. The hardware below was read directly from this computer. Installation has **not** been performed, and no bootable EFI has yet been built or tested.

An Ubuntu preparation/USB script is now available: [script usage](SCRIPT-USAGE.md). It automates the preparation below with an initial USB 2-only map; real laptop firmware boot remains untested.

## 1. Understand what carries over from the video

The video installs **Big Sur on a Dell OptiPlex 7020**. Follow its sequence, but build the EFI for this Lenovo:

| Video position | Task in this guide |
| --- | --- |
| [0:58](https://www.youtube.com/watch?v=QLxIWA0tOvE&t=58s) | Check hardware and choose macOS |
| [1:56](https://www.youtube.com/watch?v=QLxIWA0tOvE&t=116s) | Build a machine-specific EFI |
| [2:06](https://www.youtube.com/watch?v=QLxIWA0tOvE&t=126s) | Download recovery files |
| [3:02](https://www.youtube.com/watch?v=QLxIWA0tOvE&t=182s) | Prepare the USB |
| [3:20](https://www.youtube.com/watch?v=QLxIWA0tOvE&t=200s) | Set firmware options and boot |
| [3:58](https://www.youtube.com/watch?v=QLxIWA0tOvE&t=238s) | Prepare the destination disk and install |
| [4:56](https://www.youtube.com/watch?v=QLxIWA0tOvE&t=296s) | Put OpenCore on the destination disk |

Three corrections matter:

- The narration calls the PC EFI-generation utility “OpenCore Legacy Patcher.” OCLP targets older Macs; use PC OpenCore configuration here. The described automation resembles **OpCore Simplify**, but the transcript alone does not establish the on-screen utility’s identity. [OCLP supported models](https://dortania.github.io/OpenCore-Legacy-Patcher/MODELS.html), [OpCore Simplify](https://github.com/lzhoang2801/OpCore-Simplify).
- `com.apple.recovery.boot` contains a recovery environment; installation still downloads macOS over the network. [Installer explanation](https://dortania.github.io/OpenCore-Install-Guide/installer-guide/).
- The installed disk normally already has an EFI System Partition. Copying OpenCore into it makes the disk independently bootable. [Booting without USB](https://dortania.github.io/OpenCore-Post-Install/universal/oc2hdd.html).

## 2. Your actual hardware

| Component | Detected on this machine | Installation implication |
| --- | --- | --- |
| Laptop | Lenovo IdeaPad 320-14IKB, type 80YF | Use a laptop configuration |
| CPU | Intel Core i5-7200U, Kaby Lake, 2 cores / 4 threads | Intel Kaby Lake laptop branch |
| GPU | Intel HD Graphics 620, `8086:5916` | Needs Intel graphics configuration |
| RAM | Approximately 12 GB installed | Enough for this experiment |
| Internal SSD | Kingston SUV300S37A120G, 111.8 GiB | Currently contains Ubuntu |
| Ethernet | Realtek RTL8111/8168 family, `10ec:8168` | Use `RealtekRTL8111.kext` |
| Wi-Fi | Qualcomm Atheros QCA9377, `168c:0042` | Plan on Ethernet; this card is outside the standard supported Wi-Fi set |
| Audio | Linux reports ALC236, codec ID `10ec:0230` | Verify ALC230/ALC236 handling when choosing AppleALC layout |
| Keyboard | PS/2 | `VoodooPS2Controller.kext` |
| Touchpad | ELAN0608, I2C | Likely `VoodooI2C` + `VoodooI2CHID`, with ACPI work |
| Firmware | UEFI; Secure Boot already disabled | Keep UEFI boot mode |
| Preparation OS | Ubuntu 26.04.1 LTS | Use Linux commands instead of CMD/Rufus |
| Attached USB | SanDisk Cruzer Blade, 57.3 GiB | Currently holds an Ubuntu installer and an ext4 partition |

Driver choices are starting points, not proof that every laptop feature works. [Gathering drivers](https://dortania.github.io/OpenCore-Install-Guide/ktext.html), [Wi-Fi support](https://dortania.github.io/Wireless-Buyers-Guide/unsupported.html).

For matching the video, this guide uses **macOS Big Sur 11**. Your CPU is newer than the video's CPU, so its stated Monterey ceiling should not be applied to your laptop. Big Sur is an old release: check that the applications you need still support it before choosing it for regular use. This guide does not configure a newer macOS release.

## 3. Protect Ubuntu and choose where macOS will go

At inspection time:

- `/dev/sda` was the internal Kingston SSD.
- `/dev/sda1` was Ubuntu's EFI partition; `/dev/sda2` was the mounted Ubuntu root filesystem.
- `/dev/sdb` was the SanDisk USB.
- Ubuntu had only **about 6.4 GiB available**. That is below Dortania's 15 GB preparation-space guidance for Linux. [Prerequisites](https://dortania.github.io/OpenCore-Install-Guide/prerequisites.html).

**Device names can change after reconnecting drives or rebooting. Identify disks by model and size every time.**

Back up your personal files, projects, and existing EFI to another physical disk. Verify that you can read the backup. Keep a separate Ubuntu recovery USB if repurposing the attached SanDisk.

Choose one installation route:

- **Preserve Ubuntu:** use a separate SSD, including an external SSD for an initial trial. A 128 GB or larger destination is a practical starting point; allow more for applications and files. This is my suggested route for this almost-full SSD.
- **Replace Ubuntu:** the video’s whole-disk erase procedure removes Ubuntu and all files from the Kingston SSD. Proceed only after deciding to replace it and verifying your backup.
- **Dual boot on the same SSD:** requires freeing substantial space and resizing ext4 from a live Linux environment. Do not use the whole-disk erase instructions below. Consult [OpenCore multiboot](https://dortania.github.io/OpenCore-Multiboot/) for a separately planned partition layout.

## 4. Prepare the tools on Ubuntu

The commands in this guide are instructions for you; they have not been executed as installation steps.

After making adequate free space, install the preparation packages:

```bash
sudo apt update
sudo apt install git python3 python3-tk unzip acpica-tools gdisk dosfstools
mkdir -p ~/Projects/macos/tools
mkdir -p ~/Projects/macos/build
```

Download the **DEBUG ZIP** from [OpenCorePkg releases](https://github.com/acidanthera/OpenCorePkg/releases). At research time, GitHub listed 1.0.8 as latest. Extract it and rename the extracted folder to `OpenCore` inside `~/Projects/macos/tools/`.

Use the `Sample.plist`, `OpenCore.efi`, `OpenRuntime.efi`, documentation and `ocvalidate` from **the same release**. DEBUG makes first-boot problems easier to diagnose. [Base EFI files](https://dortania.github.io/OpenCore-Install-Guide/installer-guide/opencore-efi.html).

## 5. Build an EFI for this laptop

This expands the video's very short automatic-generation step.

### Optional automation, if you have a hardware report

[OpCore Simplify](https://github.com/lzhoang2801/OpCore-Simplify) runs on Linux with `python3 OpCore-Simplify.py`, but needs a hardware report and ACPI data. Its companion [Hardware Sniffer](https://github.com/lzhoang2801/Hardware-Sniffer) documents a Windows executable; Linux collection is still described as work in progress. Do not assume it can scan Ubuntu automatically or feed it a report from another PC.

If you collect the report on this same Lenovo using Windows, export both the report and ACPI tables, import them into OpCore Simplify, explicitly select Big Sur, and choose **Build OpenCore EFI** by its label. Menu numbers can change. Review the generated EFI using steps 6–8.

### Manual route from Ubuntu

Copy `tools/OpenCore/X64/EFI` into `build/EFI`. Keep `OpenRuntime.efi`; obtain `HfsPlus.efi` through the driver links in Dortania. Remove unrelated sample drivers and tools. Preserve `BOOT/BOOTx64.efi` and `OC/OpenCore.efi`.

Download release builds of the required kexts from the project links on [Gathering files](https://dortania.github.io/OpenCore-Install-Guide/ktext.html). Put the actual `.kext` bundles into `build/EFI/OC/Kexts/`:

| Kext | Purpose |
| --- | --- |
| Lilu | Dependency for graphics/audio patches |
| VirtualSMC | Apple SMC emulation |
| WhateverGreen | Intel graphics fixes |
| RealtekRTL8111 | Your Ethernet controller |
| AppleALC | Audio; layout needs testing |
| VoodooPS2Controller | Built-in keyboard |
| SMCBatteryManager | Battery reporting |

Use a USB mouse initially. Add VoodooI2C and its appropriate HID satellite when configuring the ELAN touchpad; the ACPI requirements need checking. Do not add every available kext.

Install the editing and ACPI tools:

```bash
cd ~/Projects/macos/tools
git clone https://github.com/corpnewt/SSDTTime.git
git clone https://github.com/corpnewt/ProperTree.git
git clone https://github.com/corpnewt/GenSMBIOS.git
python3 SSDTTime/SSDTTime.py
```

In SSDTTime, use **Dump DSDT** for this computer, then generate the applicable laptop EC/USB-power, PLUG and PNLF fixes. Follow its laptop prompts. Put the compiled `.aml` outputs into `build/EFI/OC/ACPI/`. Merge any associated OpenCore patches using SSDTTime's PatchMerge tool; copying AML files alone does not apply required renames. The dumped DSDT is input data, not a replacement table to inject. [SSDTTime](https://github.com/corpnewt/SSDTTime), [ACPI walkthrough](https://dortania.github.io/Getting-Started-With-ACPI/ssdt-methods/ssdt-easy.html).

Copy `tools/OpenCore/Docs/Sample.plist` to `build/EFI/OC/config.plist`, then open it:

```bash
python3 ~/Projects/macos/tools/ProperTree/ProperTree.py
```

In ProperTree, open the copied config and use **File → OC Clean Snapshot** against `build/EFI/OC`. After merging custom patches, preserve those changes; use ordinary OC Snapshot for later file additions. Confirm only one copy of shared plugins such as VoodooInput is enabled. [Config setup](https://dortania.github.io/OpenCore-Install-Guide/config.plist/), [ProperTree](https://github.com/corpnewt/ProperTree).

## 6. Configure Kaby Lake laptop settings

Work through the complete [Kaby Lake laptop configuration page](https://dortania.github.io/OpenCore-Install-Guide/config-laptop.plist/kaby-lake.html). These are the relevant starting values; this table is **not a complete config.plist**. Retain other required keys and review every section in that page against your release's `Docs/Configuration.pdf`.

| Config path | Starting value |
| --- | --- |
| `DeviceProperties → Add → PciRoot(0x0)/Pci(0x2,0x0) → AAPL,ig-platform-id` | Data `00001B59` |
| `Kernel → Quirks → AppleXcpmCfgLock` | True if CFG Lock remains enabled/unknown |
| `Kernel → Quirks → DisableIoMapper` | True if VT-d remains enabled |
| `Kernel → Quirks → DisableLinkeditJettison` | True |
| `Kernel → Quirks → PanicNoKextDump` | True |
| `Kernel → Quirks → PowerTimeoutKernelPanic` | True |
| `Kernel → Quirks → XhciPortLimit` | False for Big Sur 11.3+ |
| `Misc → Security → ScanPolicy` | Integer `0` for initial discovery |
| `Misc → Security → SecureBootModel` | String `Default` |
| `Misc → Security → Vault` | String `Optional` |
| `Misc → Security → DmgLoading` | String `Signed` |
| `PlatformInfo → Generic → SystemProductName` | String `MacBookPro14,1` |
| `NVRAM → Add → 7C436110-AB2A-4BBB-A880-FE41995C9F82 → boot-args` | String `-v keepsyms=1 debug=0x100` |
| Same GUID → `csr-active-config` | Data `00000000` |

If DVMT is fixed at 32 MB, that page documents conditional framebuffer memory patches. Do not add UHD 620 spoofing simply because the names look similar: yours is **HD 620**.

Generate unique SMBIOS identifiers using:

```bash
python3 ~/Projects/macos/tools/GenSMBIOS/GenSMBIOS.py
```

Install/update macserial, select your config, then generate for `MacBookPro14,1`. Populate SystemSerialNumber, MLB and SystemUUID with the generated values; supply a stable six-byte ROM value. Keep these identifiers private and do not copy someone else's serial numbers. [GenSMBIOS](https://github.com/corpnewt/GenSMBIOS).

## 7. Plan USB mapping and validate the EFI

For Big Sur 11.3+, do not rely on `XhciPortLimit=True`. Map the laptop's actual ports where possible before installation. USBToolBox documents Windows/macOS support, including Windows PE; it is not a supported Linux mapping tool. In Windows/PE, discover ports with both USB 2 and USB 3 devices, select the needed ports within macOS's 15-port-per-controller limit, then build the map. Use `USBToolBox.kext` plus your generated `UTBMap.kext` when choosing its USBToolBox-based method. [USBToolBox instructions](https://github.com/USBToolBox/tool).

Without a map, first-boot USB availability remains uncertain. If recovery loses the installer, mouse or keyboard, return to USB mapping instead of repeatedly reinstalling. Once macOS boots, [USBMap](https://github.com/corpnewt/USBMap) offers a macOS mapping route.

After the final snapshot and edits, use `Utilities/ocvalidate/ocvalidate` from the matching OpenCore ZIP to validate `build/EFI/OC/config.plist`. If that release does not provide a Linux executable, build the utility using its project instructions or run the matching binary on a supported OS. Fix all reported errors before proceeding. Validation checks configuration structure; it cannot guarantee hardware compatibility.

## 8. Download the same macOS release as the video

From Ubuntu:

```bash
cd ~/Projects/macos/tools/OpenCore/Utilities/macrecovery
python3 ./macrecovery.py -b Mac-2BD1B31983FE1663 -m 00000000000000000 download
```

This is the documented **Big Sur** recovery request. On Linux, keep `./macrecovery.py`; the video's instruction about removing `./` concerns its Windows command example. [Recovery commands](https://github.com/acidanthera/OpenCorePkg/blob/master/Utilities/macrecovery/recovery_urls.txt).

Find the downloaded `.dmg` and matching `.chunklist`, normally inside `com.apple.recovery.boot`. Keep both. You need working Ethernet in recovery to download the installation payload. [Recovery versus full installer](https://dortania.github.io/OpenCore-Install-Guide/installer-guide/).

## 9. Prepare the installer USB on Ubuntu

**This erases the selected USB.** The attached SanDisk currently contains an Ubuntu installer and an ext4 partition; back up anything needed from it first.

1. Run `lsblk -o NAME,SIZE,MODEL,TRAN,FSTYPE,MOUNTPOINTS` and identify the USB again.
2. Open **Disks** with `gnome-disks`.
3. Select the SanDisk by model and size. Verify you did not select the Kingston SSD.
4. Unmount its mounted partitions.
5. Use **Format Disk**, choosing GPT partitioning.
6. Create a FAT32 partition named `OPENCORE` with at least 4 GB. An 8 GB partition is sufficient for this recovery layout; the rest can remain unused.
7. Mount it and copy your completed `build/EFI` folder and recovery folder into its root.

This adapts Dortania's single-FAT32-partition Linux method. Some firmware may need its alternate partition layout if the first method does not boot. [Linux USB creation](https://dortania.github.io/OpenCore-Install-Guide/installer-guide/linux-install.html).

Expected layout:

```text
OPENCORE/
├── EFI/
│   ├── BOOT/BOOTx64.efi
│   └── OC/
│       ├── OpenCore.efi
│       ├── config.plist
│       ├── ACPI/          # compiled .aml tables
│       ├── Drivers/       # OpenRuntime.efi, HfsPlus.efi
│       └── Kexts/         # actual .kext bundles
└── com.apple.recovery.boot/
    ├── BaseSystem.dmg
    └── BaseSystem.chunklist
```

RecoveryImage-named files are also possible. Avoid an extra nesting level such as `EFI/EFI/OC`. [Folder structure](https://dortania.github.io/OpenCore-Install-Guide/installer-guide/opencore-efi.html).

## 10. Boot recovery

Connect the charger, Ethernet, installer USB and intended destination SSD. Enter the Lenovo firmware setup/boot menu.

Use UEFI mode, keep Secure Boot disabled, disable Fast Boot/CSM if present, and keep SATA AHCI. AHCI and disabled Secure Boot were already confirmed on this machine. Photograph current firmware settings before changing anything. Match other available settings to the linked Kaby Lake BIOS instructions; hidden options require the relevant OpenCore quirks.

Select the USB's UEFI boot entry. In OpenCore, press **Space** if recovery is hidden, then select the recovery entry. Scrolling text is expected. If the computer consistently stalls or panics, photograph the final lines before changing the EFI. [Installation checks](https://dortania.github.io/OpenCore-Install-Guide/installation/installation-process.html).

## 11. Install Big Sur on the chosen destination

**Whole-disk erase removes every existing partition on that disk.** Follow this only for your separate destination SSD or after deliberately choosing to replace Ubuntu.

1. In recovery, open Disk Utility and enable **View → Show All Devices**.
2. Identify the destination by its physical model and capacity.
3. Select that physical disk, choose Erase, name it `Macintosh HD`, and use **APFS** with **GUID Partition Map**.
4. Close Disk Utility and choose **Reinstall macOS Big Sur**.
5. Confirm Ethernet works and select `Macintosh HD`.
6. Leave the installer USB connected through the restarts. Boot through OpenCore each time; choose the destination's **macOS Installer** entry while installation continues, then the installed macOS volume when ready.
7. Complete region, keyboard and local-account setup. Leave Apple account sign-in until the system is stable.

If recovery offers a different macOS release, stop and verify the downloaded image before erasing anything. [Installation process](https://dortania.github.io/OpenCore-Install-Guide/installation/installation-process.html).

## 12. Make macOS boot without the installer USB

Keep a copy of the working USB EFI. In macOS, use [MountEFI](https://github.com/corpnewt/MountEFI) as in the video:

1. Mount the EFI partition on the **disk containing macOS**.
2. Copy the working `EFI/OC` and `EFI/BOOT` into that partition's `EFI` directory.
3. Preserve other bootloader directories if the partition is shared. Do not replace Ubuntu's entire EFI directory.
4. Shut down, remove the installer USB and choose the destination disk/OpenCore from the firmware boot menu.

For an external macOS SSD, copy OpenCore to that external SSD's EFI partition. If firmware cannot find it, boot again using the installer USB and check the destination EFI and boot entry. Keep the USB as a recovery option. [Move OpenCore to the macOS disk](https://dortania.github.io/OpenCore-Post-Install/universal/oc2hdd.html).

## 13. Check laptop functionality

- Verify graphics acceleration, built-in display and brightness. An image on screen alone is not proof of acceleration.
- Test Ethernet, each USB port, keyboard, touchpad, battery reporting, restart, shutdown and lid sleep/wake. Expect laptop-specific adjustments.
- Test speakers, headphones and microphone. Linux's codec name and numeric ID disagree between ALC236 and ALC230 naming; confirm the appropriate AppleALC resource before selecting a layout. The [supported codec table](https://github.com/acidanthera/AppleALC/wiki/Supported-codecs) lists layouts 13/20 for ALC230 and multiple layouts for ALC236. Test one appropriate `alcid=` value at a time; no layout is verified here.
- Keep using Ethernet. Do not expect Intel Wi-Fi kexts or legacy Atheros patches to enable the QCA9377.
- Finish USB mapping and laptop fixes before treating the installation as reliable. Use [OpenCore Post-Install](https://dortania.github.io/OpenCore-Post-Install/) for the remaining checks.

**Your immediate next step:** back up Ubuntu, free preparation space, and choose a destination disk. Then build and validate this laptop's EFI before formatting a USB or erasing a destination.
