#!/usr/bin/env python3
"""
Test script to simulate a call locally.

This sends test messages to the voice pipeline without
requiring an actual phone call.
"""

import asyncio
import json
import sys

import websockets


async def test_call(host: str = "localhost", port: int = 8765):
    """Simulate a test call to the voice agent."""
    uri = f"ws://{host}:{port}/twilio/media-stream"

    print(f"Connecting to {uri}...")

    try:
        async with websockets.connect(uri) as ws:
            print("Connected!")

            # Send connected event
            await ws.send(
                json.dumps(
                    {
                        "event": "connected",
                        "protocol": "Call",
                        "version": "1.0.0",
                    }
                )
            )

            # Send start event
            await ws.send(
                json.dumps(
                    {
                        "event": "start",
                        "sequenceNumber": "1",
                        "start": {
                            "streamSid": "test-stream-123",
                            "accountSid": "test-account",
                            "callSid": "test-call",
                            "tracks": ["inbound", "outbound"],
                            "customParameters": {},
                        },
                        "streamSid": "test-stream-123",
                    }
                )
            )

            print("Stream started. Listening for responses...")
            print("(Press Ctrl+C to stop)")

            # Listen for responses
            try:
                while True:
                    response = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    data = json.loads(response)

                    if data.get("event") == "media":
                        # Audio response received
                        payload_len = len(data.get("media", {}).get("payload", ""))
                        print(f"Audio received: {payload_len} bytes")
                    else:
                        print(f"Event: {data.get('event')}")

            except asyncio.TimeoutError:
                print("No response in 5s...")
            except KeyboardInterrupt:
                print("\nStopping...")

            # Send stop event
            await ws.send(json.dumps({"event": "stop", "streamSid": "test-stream-123"}))

    except ConnectionRefusedError:
        print(f"Could not connect to {uri}")
        print("Make sure the server is running: python -m src.main")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    host = sys.argv[1] if len(sys.argv) > 1 else "localhost"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8765

    asyncio.run(test_call(host, port))
