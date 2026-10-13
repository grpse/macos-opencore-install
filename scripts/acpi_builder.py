#!/usr/bin/env python3
"""Adapter for the pinned SSDTTime revision; never writes firmware tables."""
import json
from pathlib import Path
import sys


def main():
    source, tables, output = [Path(x).resolve() for x in sys.argv[1:]]
    sys.path.insert(0, str(source))
    from Scripts import dsdt
    # The main script has already installed the compiler; do not fetch the
    # unused legacy compiler or make unbounded network calls here.
    check_iasl = dsdt.DSDT.check_iasl
    dsdt.DSDT.check_iasl = lambda self, legacy=False, try_downloading=True: check_iasl(
        self, legacy=legacy, try_downloading=False)
    from SSDTTime import SSDT
    tool = SSDT()
    tool.output = str(output)
    tool.resize_window = False

    def answer(prompt, *args, **kwargs):
        if "Press [enter] to return" in prompt:
            return ""
        if "default is B" in prompt:
            return "b"
        if "target _UID" in prompt:
            return "16"  # Skylake/Kaby Lake, from SSDTTime's own menu.
        raise RuntimeError(f"Unexpected SSDTTime question; refusing to guess: {prompt}")

    tool.u.grab = answer
    loaded = tool.load_dsdt(str(tables))
    if not loaded or not tool.d.acpi_tables:
        raise RuntimeError("SSDTTime could not disassemble the firmware tables")
    tool.dsdt = loaded
    tool.fake_ec(laptop=True)
    tool.plugin_type()
    tool.ssdt_usbx()
    tool.ssdt_pnlf()
    (output / "generation.json").write_text(json.dumps({"source": str(tables),
        "functions": ["fake_ec(laptop=True)", "plugin_type", "ssdt_usbx", "ssdt_pnlf"]}, indent=2))


if __name__ == "__main__":
    main()
