# cardex

A Raspberry Pi camera pointed at a street, and the software around it: a Windows PC turns the Pi's stream into
10-minute and daily timelapse videos (RIFE frame interpolation on the GPU), logs every passing vehicle with a
make/model guess, and serves it all to a desktop app and an iPhone app.

| Folder | What | License |
|---|---|---|
| [`service/`](service/) | The PC side: capture, renders, vehicle sightings, the HTTP API and the desktop UI (Python, runs as a Windows scheduled task). Also the Pi's stream server in `service/pi/`. | [AGPL-3.0](service/LICENSE) |
| [`ios/`](ios/) | Campi for iPhone (SwiftUI): sightings, timelapse player, live view, push notifications, widget. | [MIT](ios/LICENSE) |
| [`api/`](api/) | The HTTP API contract both sides are built to: [`api-contract.md`](api/api-contract.md), a JSON schema and example fixtures. The mock server and compliance checker live in `ios/tools/`. | [MIT](api/LICENSE) |

The service is AGPL-3.0 because its sightings worker uses [Ultralytics YOLO](https://github.com/ultralytics/ultralytics),
which is AGPL-3.0. The app and the contract don't depend on it and are MIT.

## Not supported
This is built for my setup: a Windows 11 PC with an NVIDIA GPU (RIFE, NVENC) and an Intel iGPU (OpenVINO
sightings), a Raspberry Pi with a camera module, and Tailscale for reaching the PC from the phone. Paths, hardware
choices and defaults assume that setup. It's published as-is for reference; issues and pull requests may not get a
response.

Start with [`service/README.md`](service/README.md) (copy `service/config.example.toml` to `service/config.toml`) and
[`ios/README.md`](ios/README.md).
