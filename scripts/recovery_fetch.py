#!/usr/bin/env python3
"""Run upstream macrecovery with bounded IPv4 sockets on this Linux host."""
import runpy
import socket
import sys

socket.setdefaulttimeout(45)
original_getaddrinfo = socket.getaddrinfo


def ipv4_addresses(host, port, family=0, type=0, proto=0, flags=0):
    return original_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)


socket.getaddrinfo = ipv4_addresses
script = sys.argv[1]
sys.argv = sys.argv[1:]
runpy.run_path(script, run_name="__main__")
