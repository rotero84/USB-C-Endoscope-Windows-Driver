# Windows Driver Setup (WinUSB)

## Why Windows Doesn't Recognize the Endoscope Automatically

Most USB webcams advertise **USB Video Class (`bInterfaceClass = 0x0E`)**, which Windows supports natively.

The UseePlus USB-C endoscope instead uses a **Vendor-Specific USB interface (`bInterfaceClass = 0xFF, bInterfaceSubClass = 0xF0, bInterfaceProtocol = 0x01`)** with custom `0xBB 0xAA` bulk-transfer framing. Because of this, Windows leaves it without a driver ("Unknown Device" or no camera).

## Installing Microsoft's Signed WinUSB Driver (One-Time Setup)

You do **not** need to disable Driver Signature Enforcement or install any unsigned kernel `.sys` driver. Windows includes Microsoft's signed `winusb.sys` driver out of the box, and **Zadig** binds it cleanly to your endoscope:

1. Plug your USB-C endoscope into your laptop.
2. Download and run **Zadig** (`https://zadig.akeo.ie/`).
3. In Zadig's menu, click **Options -> List All Devices**.
4. Select your endoscope from the dropdown (look for a device with `Interface 0` or `Interface 1` showing **USB ID** such as `2565:12E4`, `1B71:3002`, `1C88:xxxx`, `05E1:0408`, or `UseePlus` / `USB2.0 PC CAMERA` with driver `(NONE)`).
   - *Tip:* If your device has multiple interfaces, bind **WinUSB** to the interface with `bInterfaceClass = 0xFF, bInterfaceSubClass = 0xF0, bInterfaceProtocol = 0x01` (or the main composite interface if it only shows one).
5. Ensure the target driver box on the right says **WinUSB** (v6.1.x).
6. Click **Install Driver** (or **Replace Driver**).

## Verifying & Running

1. Run `.\setup.ps1` once to create the Python virtual environment and install dependencies.
2. Run `.\run_camera.bat --list` to verify your endoscope is detected as `[USEEPLUS JPEG BULK]` or `[USEEPLUS YUV BULK]`.
3. Run `.\run_camera.bat` to open the live camera viewer, or `.\run_camera.bat --virtual-cam` (with OBS Studio / OBS Virtual Camera installed) to expose it as a standard Windows webcam in Zoom, Teams, Chrome, or the Windows Camera app.
