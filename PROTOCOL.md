# UseePlus USB-C Endoscope Protocol Specification

Reverse-engineered from `Usee+Plus_1.0.106_APKPure.xapk` (`com.i4season.useeplus.apk` + `libOtgCameraLibUSB.so`).

## 1. Device Classification (`OtgCameraApi.get_utype`)

The UseePlus app classifies connected USB devices by scanning their USB interface descriptors:

| Mode (`ctype`) | Interface Match | Protocol | Notes |
|---|---|---|---|
| `0` (`OTG_CAMERA_CTYPE_JPEG`) | Has both `(0xFF, 0xF0, 0x00)` and `(0xFF, 0xF0, 0x01)` | Proprietary Bulk MJPEG | Most modern UseePlus USB-C endoscopes |
| `1` (`OTG_CAMERA_CTYPE_YUV`) | Has `(0xFF, 0xF0, 0x01)` without `0x00` | Proprietary Bulk YUY2 + Control | Older YUV-mode endoscopes |
| `2` (`OTG_CAMERA_CTYPE_OTG`) | Class `0x0E` (Video), SubClass `0x01`, Protocol `0x00` | Standard UVC | Works natively in Windows Camera |

> **Why Windows has no built-in driver:** Modes `0` and `1` use USB Vendor-Specific class `0xFF`, subclass `0xF0`, protocol `0x00/0x01` instead of standard UVC (`0x0E`). Binding Microsoft's in-box `WinUSB` (`winusb.sys`) driver allows user-space software to claim interface `(0xFF, 0xF0, 0x01)` and communicate with the Bulk IN/OUT endpoints.

### Known VID/PIDs (`res/xml/device_filter.xml`)

- `2565:12e4`, `046d:08b5`, `0581:0280`, `240c:0102`, `1b71:3002`, `05e1:0408`, `eb1a:2861`
- `1c88:0007`, `1c88:003c`, `1c88:003d`, `1c88:003e`, `1c88:003f`, `1c88:1001`
- `05e3:0515`, `1e4e:0102`
- Any device exposing interface `bInterfaceClass=0xFF, bInterfaceSubClass=0xF0, bInterfaceProtocol=0x01`

---

## 2. USB Endpoints (`OTGDeviceHandle.getPoint`)

On the claimed interface `(0xFF, 0xF0, 0x01)`:
- **Bulk IN** (`bmAttributes & 0x03 == 2`, `bEndpointAddress & 0x80 == 0x80`): Video stream & command responses
- **Bulk OUT** (`bmAttributes & 0x03 == 2`, `bEndpointAddress & 0x80 == 0x00`): Start/stop & camera-switch commands

---

## 3. Control & Bulk Commands

### Device Info (`OTGDeviceHandle.getinfo`)
- **Control Transfer IN**: `bmRequestType = 0xA0`, `bRequest = 0x00`, `wValue = 0x0005`, `wIndex = 0x0000`, `wLength = 512`

### Start Video Stream (`OtgCameraApi.send_connect_command`)
- **JPEG Mode (`ctype == 0`)** — Bulk OUT (5 bytes):
  - Primary camera (`cameratype == 1`): `BB AA 05 00 00`
  - Secondary camera (`cameratype != 1`): `BB AA 06 00 00`
- **YUV Mode (`ctype == 1`)** — Control Transfer OUT:
  - `bmRequestType = 0x20`, `bRequest = 0x01`, `wValue = 0x0005`, `wIndex = 0x0000`, `data = 64 zero bytes`

### Stop Video Stream (`OtgCameraApi.send_disconnect_command`)
- **JPEG Mode (`ctype == 0`)** — Bulk OUT (5 bytes):
  - Primary camera (`cameratype == 1`): `BB AA 06 00 00`
  - Secondary camera (`cameratype != 1`): `BB AA 08 00 00`
- **YUV Mode (`ctype == 1`)** — Control Transfer OUT:
  - `bmRequestType = 0x20`, `bRequest = 0x02`, `wValue = 0x0005`, `wIndex = 0x0000`, `wLength = 0`

### Switch Dual-Lens Camera (`OtgCameraApi.changeCamera(cam1, cam2)`)
- **Bulk OUT** (8 bytes): `BB AA 0B 00 02 <cam1> <cam2> 00`

---

## 4. Video Stream Framing (`libOtgCameraLibUSB.so`)

### JPEG Mode (`mfi_handle_data2` / `handle_pro`)
Bulk IN returns framed packets:
- `pkt[0..1]`: Sync magic `0xBB 0xAA` (or `0xAA 0xBB`)
- `pkt[2]`: Packet type (`0x07` = primary video, `0x0E` = secondary video, `0x0B` = command ack)
- `pkt[3..4]`: `uint16_t payload_len` (little-endian). Total packet length = `payload_len + 5` for types `0x07, 0x0A, 0x0E` (else `payload_len + 6`).
- `pkt[5]`: `frame_id` (changes on each new JPEG frame)
- `pkt[6]`: `pkt_seq >> 1`
- `pkt[7]`: Button / status flags
- `pkt[8..11]`: `uint32_t` frame metadata
- `pkt[12 .. 12 + payload_len - 7]`: Raw JPEG chunk bytes

When `frame_id` changes, strip any trailing `0x00` padding bytes from the accumulated frame buffer; a valid frame starts with `0xFF 0xD8` (JPEG SOI) and ends with `0xFF 0xD9` (JPEG EOI).

### YUV Mode (`mfi_handle_data_yuv`)
Bulk IN returns raw YUY2 (`YUYV` 4:2:2, 2 bytes/pixel) frame chunks. When a bulk read has `len % 512 == 511`, the trailing 511 bytes are a `yuvhead` trailer, and the preceding `len - 511` bytes complete the current YUV frame.
