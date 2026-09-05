"""Arranca Uvicorn en un único socket IPv4/IPv6 para Railway."""

from __future__ import annotations

import os
import socket

import uvicorn


def create_socket(port: int) -> socket.socket:
    """Crea un listener IPv6 que también acepte conexiones IPv4."""
    listener = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
    listener.bind(("::", port))
    listener.listen(2048)
    return listener


def main() -> None:
    port = int(os.environ.get("PORT", "8000"))
    listener = create_socket(port)
    config = uvicorn.Config("server.main:app")
    uvicorn.Server(config).run(sockets=[listener])


if __name__ == "__main__":
    main()
