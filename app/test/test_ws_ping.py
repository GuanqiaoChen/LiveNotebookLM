import asyncio
import websockets


async def main():
    async with websockets.connect("ws://127.0.0.1:8080/ws/ping", open_timeout=5) as ws:
        msg = await ws.recv()
        print(msg)


if __name__ == "__main__":
    asyncio.run(main())