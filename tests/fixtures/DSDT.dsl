// Synthetic firmware for exercising the adapter, never for this laptop's EFI.
DefinitionBlock ("", "DSDT", 2, "TEST", "FIXTURE", 1)
{
    Scope (\_PR)
    {
        Processor (CPU0, 0, 0x510, 6) {}
    }
    Scope (\_SB)
    {
        Device (PCI0)
        {
            Name (_HID, EisaId ("PNP0A08"))
            Name (_ADR, Zero)
            Device (IGPU)
            {
                Name (_ADR, 0x00020000)
            }
            Device (LPCB)
            {
                Name (_ADR, 0x001F0000)
                Device (EC0)
                {
                    Name (_HID, EisaId ("PNP0C09"))
                    Name (_GPE, 0x17)
                    Name (_CRS, ResourceTemplate ()
                    {
                        IO (Decode16, 0x62, 0x62, 1, 1)
                        IO (Decode16, 0x66, 0x66, 1, 1)
                    })
                }
            }
        }
    }
}
