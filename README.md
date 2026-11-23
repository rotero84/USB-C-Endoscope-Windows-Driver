# UseePlus USB-C Endoscope Windows Driver

A user-mode Windows USB driver, live viewer, and virtual webcam bridge for **UseePlus** USB-C endoscopes (`com.i4season.useeplus`).

> **AI Assistance Disclosure:** This project—including the reverse-engineering of the USB protocol from `Usee+Plus_1.0.106.xapk` (`com.i4season.useeplus.apk` and `libOtgCameraLibUSB.so`), the protocol documentation, and the Python driver/viewer implementation—was created with AI assistance.

## Why This Exists

Mostly just because I was curious.

UseePlus USB-C endoscopes (such as `VID=0x2CE3 PID=0x3828`, `supercamera`) expose a **Vendor-Specific USB interface (`bInterfaceClass = 0xFF`, `bInterfaceSubClass = 0xF0`, `bInterfaceProtocol = 0x01`)** with custom `0xBB 0xAA` bulk-transfer framing instead of standard USB Video Class (`0x0E`). Because of this, Windows does not recognize them as a standard webcam.

By binding Microsoft's signed in-box **WinUSB (`winusb.sys`)** driver to the endoscope interface, this user-mode driver communicates directly with the camera over USB bulk/control transfers—no unsigned kernel driver or Test Signing mode required.

## Features

- **Auto-discovery** of UseePlus endoscopes (`0xFF/0xF0/0x01`), handling multi-alternate-setting interfaces (e.g., `alt=1` on `0x2CE3:0x3828`)
- **MJPEG Bulk (`ctype=0`)** and **YUY2 Bulk (`ctype=1`)** stream support
- **Dedicated USB reader thread** for jitter-free capture
- **Live OpenCV preview** with `--scale` resizing, snapshot capture (`s`), and lens-switch commands (`1`/`2`, or use the inline hardware button)
- **Virtual Webcam output** (`--virtual-cam`) via `pyvirtualcam` (requires OBS Virtual Camera or UnityCapture)

## Setup

### 1. Bind WinUSB Driver (One-Time)
Follow [driver/README_DRIVER.md](driver/README_DRIVER.md) to bind **WinUSB** to your endoscope using **Zadig**.

### 2. Install Python Dependencies
Run:
```powershell
.\setup.ps1
```
*(Uses Python 3.12 if available via `py -3.12` so `pyvirtualcam` installs from a prebuilt wheel.)*

## Usage

```powershell
# List detected USB devices and verify endoscope detection
.\run_camera.bat --list

# Launch live preview
.\run_camera.bat

# Launch with 2x scaled preview window
.\run_camera.bat --scale 2.0

# Stream to a Windows virtual webcam (requires OBS Virtual Camera / UnityCapture)
.\run_camera.bat --virtual-cam
```

### Controls
- `q` or `Esc`: Quit
- `s`: Save a PNG snapshot to `captures/`
- `1` / `2`: Send software lens-switch command (note: some models only switch lenses via the inline hardware button)

## Protocol Documentation

See [PROTOCOL.md](PROTOCOL.md) for the reverse-engineered USB descriptors, control transfers, bulk commands, and packet framing.
