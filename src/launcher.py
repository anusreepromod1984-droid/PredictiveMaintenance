"""
Production APMS Launcher Module
Handles Railway dynamic PORT binding, dual-port forwarding (8000/8080/PORT),
and clean startup for FastAPI + Socket.IO.
"""

import asyncio
import os
import sys
import threading
import uvicorn
from src.utils.logger import get_logger

logger = get_logger("APMS.Launcher")


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    try:
        while not reader.at_eof():
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def _forward(client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter, target_port: int):
    try:
        target_reader, target_writer = await asyncio.open_connection("127.0.0.1", target_port)
        await asyncio.gather(
            _pipe(client_reader, target_writer),
            _pipe(target_reader, client_writer),
            return_exceptions=True,
        )
    except Exception:
        try:
            client_writer.close()
            await client_writer.wait_closed()
        except Exception:
            pass


def _start_tcp_proxy(listen_port: int, target_port: int):
    """Binds listen_port and transparently forwards all TCP traffic to target_port."""
    async def _run():
        try:
            server = await asyncio.start_server(
                lambda r, w: _forward(r, w, target_port),
                "0.0.0.0",
                listen_port,
                reuse_port=True,
            )
            async with server:
                logger.info(f"[Launcher] Dual-bind proxy active: 0.0.0.0:{listen_port} -> 127.0.0.1:{target_port}")
                await server.serve_forever()
        except Exception as e:
            logger.warning(f"[Launcher] Could not bind auxiliary port {listen_port}: {e}")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(_run())


def main():
    raw_port = os.environ.get("PORT", "").strip()
    try:
        primary_port = int(raw_port) if raw_port else 8000
    except ValueError:
        primary_port = 8000

    host = "0.0.0.0"
    logger.info(f"=== Starting APMS Production Server on {host}:{primary_port} (raw PORT='{raw_port}') ===")

    # If Railway assigned an arbitrary PORT (e.g. 8080, 5000), bind a proxy on 8000 and 8080
    # to guarantee that any edge proxy, load balancer, or container healthcheck reaching 8000 or 8080 succeeds.
    auxiliary_ports = [8000, 8080]
    for aux_port in auxiliary_ports:
        if aux_port != primary_port:
            t = threading.Thread(target=_start_tcp_proxy, args=(aux_port, primary_port), daemon=True)
            t.start()

    from src.api.main import application

    # Run uvicorn on the primary port
    uvicorn.run(
        application,
        host=host,
        port=primary_port,
        log_level="info",
        access_log=False,
    )


if __name__ == "__main__":
    main()
