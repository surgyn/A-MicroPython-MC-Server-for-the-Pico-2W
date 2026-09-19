# A Tiny MicroPython Minecraft Server (Pico 2 W)

A Minecraft 1.8.9 server implemented from scratch in MicroPython for the Raspberry Pi Pico 2 W. No external Minecraft libraries — the handshake, login, chunk generation, and gameplay packets are all implemented directly over raw sockets.

This started as a small experiment to see how far a $6 microcontroller could be pushed, and it turned into a working (if minimal) place to log in and walk around.

## What it does

- Speaks Minecraft protocol version 47 (1.8 - 1.8.9)
- Handles the full connection lifecycle: handshake, server list ping/status, offline-mode login, and the play state
- Generates a small fixed world: a single 16x16 grass platform surrounded by invisible barrier walls to prevent falling into the void
- Barrier blocks are enforced server-side, so they can't be broken even in Creative mode
- Spawns players in Creative mode, walking by default (they can fly if they choose to)
- Sends periodic keep-alives, a resync ping, and (if available) the Pico's own CPU temperature as a server chat message
- Runs on Wi-Fi, with reconnect handling if the connection drops
- Includes basic hardening: bounded packet/string lengths, handshake timeouts, a watchdog timer, and safe shutdown on Ctrl+C

## Hardware

- Raspberry Pi Pico 2 W (needs the "W" for Wi-Fi)
- MicroPython firmware flashed onto the board

## Setup

1. Flash the latest MicroPython firmware for the Pico 2 W.
2. Open `main.py` and set your Wi-Fi credentials:
   ```python
   WIFI_SSID = "YOUR_WIFI_SSID"
   WIFI_PASSWORD = "YOUR_WIFI_PASSWORD"
   ```
3. Copy `main.py` onto the Pico (e.g. with Thonny, `mpremote`, or `rshell`).
4. Reset the board. It will connect to Wi-Fi and start listening on port 25565.
5. In Minecraft 1.8.9, add a server using the Pico's IP address, which is printed over serial on startup.

## Limitations

This is a toy server, not a general-purpose one. Some notable limitations:

- Only a single fixed chunk exists — there's no real world generation
- No inventory, crafting, mobs, or persistence between restarts
- Movement packets are read but not validated server-side
- Handles one connection at a time rather than many concurrent players
- Offline-mode only (no Mojang authentication)

## Why MicroPython on a Pico?

Mostly because it seemed like it shouldn't be possible. The Pico 2 W has a fraction of the RAM and CPU of anything Minecraft servers normally run on, so a lot of the code is shaped around working within very tight memory constraints (manual VarInt encoding, cached chunk payloads, explicit garbage collection, and so on).

## Contributing

This is very much a work in progress, and contributions are welcome — whether that's fixing bugs, improving protocol accuracy, adding features, or just cleaning things up.

If you'd like to get involved or have questions about the project, feel free to reach out: **m830@proton.me** | Discord: **@sockraw** | Telegram: **@zmapper** | Signal: **@jackskid.67**

## License

See [LICENSE](https://github.com/surgyn/A-Tiny-MicroPython-Minecraft-Server-Pico-2-W-/main/LICENSE) for details.
