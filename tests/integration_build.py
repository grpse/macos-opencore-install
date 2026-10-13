#!/usr/bin/env python3
"""Integration test using downloaded packages and synthetic ACPI, no disk writes."""
from contextlib import redirect_stdout
import importlib.util
import io
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("prep", ROOT / "prepare_opencore.py")
prep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prep)
cache = ROOT / "opencore-work"
oc, kexts, hfs, ssdt = prep.packages(cache)

with tempfile.TemporaryDirectory(prefix="opencore-fixture-") as directory:
    state = Path(directory)
    tables = state / "original"
    tables.mkdir()
    prep.run([ssdt / "Scripts/iasl", "-p", tables / "DSDT", ROOT / "tests/fixtures/DSDT.dsl"])
    generated = prep.generate_acpi(state, ssdt, tables)
    efi = prep.build(state, prep.hardware(), oc, kexts, hfs, generated)
    config = prep.check_efi(efi)
    assert not config["Kernel"]["Quirks"]["XhciPortLimit"]
    assert len(config["ACPI"]["Add"]) >= 3
    assert not (state / "ready.json").exists()
    print("PASS: synthetic ACPI generation, full EFI assembly, dependency checks and real ocvalidate.")
    print("The synthetic EFI was isolated in a temporary directory and removed; no USB was touched.")
