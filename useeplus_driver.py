#!/usr/bin/env python3
"""
UseePlus USB-C Endoscope Windows User-Mode Driver & Virtual Camera Bridge.

Protocol reverse-engineered from com.i4season.useeplus (1.0.106) and libOtgCameraLibUSB.so.
Requires Microsoft WinUSB (winusb.sys) bound to the endoscope's vendor-specific interface
(Class 0xFF, SubClass 0xF0, Protocol 0x01) via Zadig.
"""

import argparse
import os
import queue
import struct
import sys
import threading
import time
from datetime import datetime
from typing import Optional, Tuple

import cv2
import libusb_package
import numpy as np
import usb.core
import usb.util

VENDOR_CLASS = 0xFF
VENDOR_SUBCLASS = 0xF0
PROTOCOL_JPEG_AUX = 0x00
PROTOCOL_STREAM = 0x01
UVC_CLASS = 0x0E

# Known VIDs/PIDs from res/xml/device_filter.xml (used as hints in --list)
KNOWN_VID_PIDS = {
    (0x2565, 0x12E4),
    (0x046D, 0x08B5),
    (0x0581, 0x0280),
    (0x240C, 0x0102),
    (0x1B71, 0x3002),
    (0x05E1, 0x0408),
    (0xEB1A, 0x2861),
    (0x1C88, 0x0007),
    (0x1C88, 0x003C),
    (0x1C88, 0x003D),
    (0x1C88, 0x003E),
    (0x1C88, 0x003F),
    (0x1C88, 0x1001),
    (0x05E3, 0x0515),
    (0x1E4E, 0x0102),
}

COMMON_YUV_RESOLUTIONS = [
    (640, 480),
    (1280, 720),
    (1600, 1200),
    (1920, 1080),
    (480, 480),
    (800, 600),
    (1024, 768),
    (320, 240),
]


def get_usb_backend():
    """Return a pyusb libusb1 backend using libusb_package's bundled DLL."""
    backend = libusb_package.get_libusb1_backend()
    if backend is None:
        raise RuntimeError(
            "Could not load libusb-1.0 backend. Ensure 'libusb-package' is installed."
        )
    return backend


def classify_device(dev: usb.core.Device) -> Tuple[Optional[int], Optional[int]]:
    """
    Match OtgCameraApi.get_utype(UsbDevice):
      ctype = 0 (JPEG bulk): has (0xFF, 0xF0, 0x00) AND (0xFF, 0xF0, 0x01)
      ctype = 1 (YUV bulk):  has (0xFF, 0xF0, 0x01) without 0x00
      ctype = 2 (UVC):       has (0x0E, 0x01, 0x00)
    Returns (ctype, stream_interface_number) or (None, None).
    """
    has_proto_0 = False
    stream_intf = None
    has_uvc = False

    try:
        cfgs = list(dev.configurations())
        if not cfgs:
            return None, None
        cfg = cfgs[0]
    except (usb.core.USBError, NotImplementedError):
        return None, None

    for intf in cfg:
        cls = intf.bInterfaceClass
        sub = intf.bInterfaceSubClass
        proto = intf.bInterfaceProtocol
        if cls == VENDOR_CLASS and sub == VENDOR_SUBCLASS:
            if proto == PROTOCOL_JPEG_AUX:
                has_proto_0 = True
            elif proto == PROTOCOL_STREAM and stream_intf is None:
                stream_intf = intf.bInterfaceNumber
        elif cls == UVC_CLASS and sub == 0x01 and proto == 0x00:
            has_uvc = True

    if stream_intf is not None:
        ctype = 0 if has_proto_0 else 1
        return ctype, stream_intf
    if has_uvc:
        return 2, None
    return None, None


def list_devices():
    """Scan and print all USB devices, highlighting UseePlus endoscopes."""
    backend = get_usb_backend()
    devices = list(usb.core.find(find_all=True, backend=backend))
    if not devices:
        print("No USB devices found.")
        return

    print(f"Found {len(devices)} USB device(s):\n")
    for dev in devices:
        vid, pid = dev.idVendor, dev.idProduct
        ctype, intf_num = classify_device(dev)
        tag = ""
        if ctype == 0:
            tag = f" [USEEPLUS JPEG BULK, interface {intf_num}]"
        elif ctype == 1:
            tag = f" [USEEPLUS YUV BULK, interface {intf_num}]"
        elif ctype == 2:
            tag = " [STANDARD UVC WEBCAM - works natively in Windows]"
        elif (vid, pid) in KNOWN_VID_PIDS:
            tag = " [KNOWN ENDOSCOPE VID/PID]"

        try:
            mfg = usb.util.get_string(dev, dev.iManufacturer) or ""
            prod = usb.util.get_string(dev, dev.iProduct) or ""
            desc = f"{mfg} {prod}".strip()
        except Exception:
            desc = "(string descriptor unavailable - WinUSB may not be bound)"

        print(f"  VID=0x{vid:04X} PID=0x{pid:04X}  {desc}{tag}")


def find_endoscope(
    vid: Optional[int] = None, pid: Optional[int] = None
) -> Tuple[usb.core.Device, int, int]:
    """Find a UseePlus endoscope and return (device, ctype, interface_number)."""
    backend = get_usb_backend()
    kwargs = {"find_all": True, "backend": backend}
    if vid is not None:
        kwargs["idVendor"] = vid
    if pid is not None:
        kwargs["idProduct"] = pid

    for dev in usb.core.find(**kwargs):
        ctype, intf_num = classify_device(dev)
        if ctype in (0, 1) and intf_num is not None:
            return dev, ctype, intf_num
        if vid is not None and pid is not None:
            raise RuntimeError(
                f"Device 0x{vid:04X}:0x{pid:04X} found, but it does not expose "
                f"vendor interface (Class=0xFF, SubClass=0xF0, Protocol=0x01). "
                f"Detected ctype={ctype}."
            )

    raise RuntimeError(
        "No UseePlus endoscope found.\n"
        "1. Plug in the USB-C endoscope.\n"
        "2. Run `python useeplus_driver.py --list` to check if it is detected.\n"
        "3. Bind WinUSB (winusb.sys) to the endoscope's interface using Zadig "
        "(see driver/README_DRIVER.md)."
    )


class JpegStreamParser:
    """
    Reassembles MJPEG frames from UseePlus 0xBB 0xAA / 0xAA 0xBB bulk packets.
    Matches libOtgCameraLibUSB.so: mfi_handle_data2 + handle_pro.
    """

    def __init__(self):
        self._stream_buf = bytearray()
        self._frame_buf = bytearray()
        self._last_frame_id: Optional[int] = None
        self.last_button_flags: int = 0

    def feed(self, data: bytes):
        """Yield complete JPEG bytes objects extracted from incoming bulk data."""
        self._stream_buf.extend(data)
        frames = []

        while len(self._stream_buf) >= 5:
            b0, b1 = self._stream_buf[0], self._stream_buf[1]
            if not ((b0 == 0xBB and b1 == 0xAA) or (b0 == 0xAA and b1 == 0xBB)):
                # Scan forward for next sync header
                idx1 = self._stream_buf.find(b"\xbb\xaa", 1)
                idx2 = self._stream_buf.find(b"\xaa\xbb", 1)
                candidates = [i for i in (idx1, idx2) if i != -1]
                if candidates:
                    del self._stream_buf[: min(candidates)]
                else:
                    # Keep last byte in case it's 0xBB or 0xAA
                    del self._stream_buf[:-1]
                continue

            pkt_type = self._stream_buf[2]
            payload_len = struct.unpack_from("<H", self._stream_buf, 3)[0]
            if payload_len > 0xFFFA:
                del self._stream_buf[:2]
                continue

            total_len = payload_len + (5 if pkt_type in (7, 10, 14) else 6)
            if len(self._stream_buf) < total_len:
                break

            pkt = bytes(self._stream_buf[:total_len])
            del self._stream_buf[:total_len]

            if pkt_type in (7, 14) and payload_len >= 7:
                frame_id = pkt[5]
                self.last_button_flags = pkt[7]
                chunk = pkt[12 : 12 + payload_len - 7]

                if self._last_frame_id is None:
                    self._last_frame_id = frame_id
                elif frame_id != self._last_frame_id:
                    completed = self._finalize_frame()
                    if completed is not None:
                        frames.append(completed)
                    self._last_frame_id = frame_id

                if len(self._frame_buf) + len(chunk) <= 2 * 1024 * 1024:
                    self._frame_buf.extend(chunk)

        return frames

    def _finalize_frame(self) -> Optional[bytes]:
        buf = self._frame_buf
        self._frame_buf = bytearray()
        if len(buf) < 5 or buf[0] != 0xFF or buf[1] != 0xD8:
            return None
        # Strip trailing 0x00 padding and verify JPEG EOI (0xFF 0xD9)
        end = len(buf)
        while end > 4 and buf[end - 1] == 0:
            end -= 1
        if end >= 4 and buf[end - 2] == 0xFF and buf[end - 1] == 0xD9:
            return bytes(buf[:end])
        return None


class YuvStreamParser:
    """
    Reassembles YUY2 (YUYV 4:2:2) frames from UseePlus ctype=1 bulk transfers.
    Matches libOtgCameraLibUSB.so: mfi_handle_data_yuv.
    """

    def __init__(self, width: Optional[int] = None, height: Optional[int] = None):
        self._buf = bytearray()
        self.width = width
        self.height = height

    def feed(self, data: bytes):
        n = len(data)
        frames = []
        if (n % 512) == 511:
            if n > 511:
                self._buf.extend(data[: n - 511])
            frame_bytes = bytes(self._buf)
            self._buf.clear()
            if frame_bytes:
                img = self._decode_yuy2(frame_bytes)
                if img is not None:
                    frames.append(img)
        else:
            if len(self._buf) + n <= 8 * 1024 * 1024:
                self._buf.extend(data)
        return frames

    def _decode_yuy2(self, raw: bytes) -> Optional[np.ndarray]:
        w, h = self.width, self.height
        if w is None or h is None:
            for cw, ch in COMMON_YUV_RESOLUTIONS:
                if cw * ch * 2 == len(raw):
                    w, h = cw, ch
                    self.width, self.height = w, h
                    break
        if w is None or h is None or len(raw) != w * h * 2:
            return None
        arr = np.frombuffer(raw, dtype=np.uint8).reshape((h, w, 2))
        return cv2.cvtColor(arr, cv2.COLOR_YUV2BGR_YUY2)


class UseePlusCamera:
    """User-mode USB driver for the UseePlus USB-C endoscope."""

    def __init__(
        self,
        dev: usb.core.Device,
        ctype: int,
        intf_num: int,
        camera_id: int = 1,
    ):
        self.dev = dev
        self.ctype = ctype
        self.intf_num = intf_num
        self.camera_id = camera_id
        self.ep_in = None
        self.ep_out = None
        self._claimed = False

    def open(self):
        try:
            if self.dev.is_kernel_driver_active(self.intf_num):
                self.dev.detach_kernel_driver(self.intf_num)
        except (NotImplementedError, usb.core.USBError):
            pass

        usb.util.claim_interface(self.dev, self.intf_num)
        self._claimed = True

        cfg = self.dev.get_active_configuration()
        selected_intf = None
        for intf in cfg:
            if intf.bInterfaceNumber != self.intf_num:
                continue
            has_in = any(
                usb.util.endpoint_type(ep.bmAttributes) == usb.util.ENDPOINT_TYPE_BULK
                and usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_IN
                for ep in intf
            )
            if has_in:
                selected_intf = intf
                break

        if selected_intf is None:
            raise RuntimeError(
                f"No Bulk IN endpoint found on interface {self.intf_num}."
            )

        if selected_intf.bAlternateSetting != 0:
            self.dev.set_interface_altsetting(
                interface=self.intf_num,
                alternate_setting=selected_intf.bAlternateSetting,
            )

        for ep in selected_intf:
            if usb.util.endpoint_type(ep.bmAttributes) == usb.util.ENDPOINT_TYPE_BULK:
                if usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_IN:
                    self.ep_in = ep
                else:
                    self.ep_out = ep

        # Query device info (OTGDeviceHandle.getinfo)
        try:
            self.dev.ctrl_transfer(0xA0, 0x00, 0x0005, 0x0000, 512, timeout=1000)
        except usb.core.USBError:
            pass

    def start_stream(self):
        if self.ctype == 1:
            # OTGDeviceHandle.camera_up()
            self.dev.ctrl_transfer(
                0x20, 0x01, 0x0005, 0x0000, bytes(64), timeout=1000
            )
        else:
            # OtgCameraApi.send_connect(cameratype)
            if self.ep_out is None:
                raise RuntimeError("No Bulk OUT endpoint found for start command.")
            cmd = bytes([0xBB, 0xAA, 0x05 if self.camera_id == 1 else 0x06, 0x00, 0x00])
            self.ep_out.write(cmd, timeout=1000)

    def switch_camera(self, cam1: int, cam2: int = 8):
        """Switch lens on dual-lens endoscopes (OtgCameraApi.changeCamera)."""
        if self.ctype == 0 and self.ep_out is not None:
            cmd = bytes([0xBB, 0xAA, 0x0B, 0x00, 0x02, cam1 & 0xFF, cam2 & 0xFF, 0x00])
            self.ep_out.write(cmd, timeout=1000)

    def stop_stream(self):
        try:
            if self.ctype == 1:
                self.dev.ctrl_transfer(0x20, 0x02, 0x0005, 0x0000, 0, timeout=1000)
            elif self.ep_out is not None:
                cmd = bytes(
                    [0xBB, 0xAA, 0x06 if self.camera_id == 1 else 0x08, 0x00, 0x00]
                )
                self.ep_out.write(cmd, timeout=1000)
        except usb.core.USBError:
            pass

    def read_chunk(self, size: int = 65536, timeout: int = 1000) -> bytes:
        try:
            data = self.ep_in.read(size, timeout=timeout)
            return bytes(data)
        except usb.core.USBTimeoutError:
            return b""

    def close(self):
        try:
            self.stop_stream()
        finally:
            if self._claimed:
                try:
                    usb.util.release_interface(self.dev, self.intf_num)
                except usb.core.USBError:
                    pass
                self._claimed = False
            usb.util.dispose_resources(self.dev)


def main():
    parser = argparse.ArgumentParser(
        description="UseePlus USB-C Endoscope Windows Driver & Virtual Camera Bridge"
    )
    parser.add_argument(
        "--list", action="store_true", help="List connected USB devices and exit"
    )
    parser.add_argument(
        "--vid", type=lambda x: int(x, 0), default=None, help="USB Vendor ID (e.g. 0x2565)"
    )
    parser.add_argument(
        "--pid", type=lambda x: int(x, 0), default=None, help="USB Product ID (e.g. 0x12e4)"
    )
    parser.add_argument(
        "--mode",
        choices=["auto", "jpeg", "yuv"],
        default="auto",
        help="Force stream mode instead of auto-detecting from USB descriptors",
    )
    parser.add_argument(
        "--camera",
        type=int,
        default=1,
        choices=[1, 2],
        help="Initial camera lens (1 or 2 for dual-lens endoscopes)",
    )
    parser.add_argument("--width", type=int, default=None, help="YUV frame width override")
    parser.add_argument("--height", type=int, default=None, help="YUV frame height override")
    parser.add_argument(
        "--scale",
        type=float,
        default=1.0,
        help="Preview window scale factor (e.g. 2.0 for 1280x960)",
    )
    parser.add_argument(
        "--virtual-cam",
        action="store_true",
        help="Stream frames to a Windows Virtual Webcam (requires OBS Virtual Camera or UnityCapture)",
    )
    parser.add_argument(
        "--no-preview",
        action="store_true",
        help="Disable the OpenCV preview window (useful with --virtual-cam)",
    )
    args = parser.parse_args()

    if args.list:
        list_devices()
        return

    dev, detected_ctype, intf_num = find_endoscope(args.vid, args.pid)
    ctype = (
        detected_ctype
        if args.mode == "auto"
        else (0 if args.mode == "jpeg" else 1)
    )
    mode_name = "JPEG Bulk" if ctype == 0 else "YUV Bulk"
    print(
        f"Connected to UseePlus endoscope VID=0x{dev.idVendor:04X} PID=0x{dev.idProduct:04X} "
        f"(interface {intf_num}, mode={mode_name})"
    )

    cam = UseePlusCamera(dev, ctype, intf_num, camera_id=args.camera)
    cam.open()
    cam.start_stream()

    jpeg_parser = JpegStreamParser() if ctype == 0 else None
    yuv_parser = YuvStreamParser(args.width, args.height) if ctype == 1 else None

    vcam = None
    vcam_size = None
    win_name = None
    os.makedirs("captures", exist_ok=True)

    frame_queue: queue.Queue = queue.Queue(maxsize=2)
    stop_event = threading.Event()

    def reader_loop():
        while not stop_event.is_set():
            chunk = cam.read_chunk(16384, timeout=200)
            if not chunk:
                continue
            if ctype == 0:
                items = jpeg_parser.feed(chunk)
            else:
                items = yuv_parser.feed(chunk)
            for item in items:
                if frame_queue.full():
                    try:
                        frame_queue.get_nowait()
                    except queue.Empty:
                        pass
                frame_queue.put(item)

    reader_thread = threading.Thread(target=reader_loop, daemon=True)
    reader_thread.start()
    print("Streaming started. Press 'q' to quit, 's' to save snapshot, '1'/'2' to switch lens.")

    try:
        while True:
            try:
                item = frame_queue.get(timeout=0.5)
            except queue.Empty:
                if not args.no_preview and win_name is not None:
                    key = cv2.waitKey(1) & 0xFF
                    if key in (ord("q"), 27):
                        return
                continue

            if ctype == 0:
                arr = np.frombuffer(item, dtype=np.uint8)
                frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if frame is None:
                    continue
            else:
                frame = item

            h, w = frame.shape[:2]

            if args.virtual_cam:
                if vcam is None or vcam_size != (w, h):
                    if vcam is not None:
                        vcam.close()
                    try:
                        import pyvirtualcam

                        vcam = pyvirtualcam.Camera(width=w, height=h, fps=30)
                    except ImportError as exc:
                        raise RuntimeError(
                            "pyvirtualcam is not installed. Run setup.ps1 with Python 3.10-3.12."
                        ) from exc
                    except RuntimeError as exc:
                        raise RuntimeError(
                            "No Windows virtual camera backend found. "
                            "Install OBS Studio (includes OBS Virtual Camera) or UnityCapture."
                        ) from exc
                    vcam_size = (w, h)
                    print(f"Virtual camera active: {vcam.device} ({w}x{h})")
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                vcam.send(rgb)
                vcam.sleep_until_next_frame()

            if not args.no_preview:
                if win_name is None:
                    win_name = f"UseePlus Endoscope ({w}x{h})"
                    cv2.namedWindow(win_name, cv2.WINDOW_NORMAL)
                    if args.scale != 1.0:
                        cv2.resizeWindow(
                            win_name,
                            max(1, int(w * args.scale)),
                            max(1, int(h * args.scale)),
                        )
                disp = (
                    cv2.resize(
                        frame,
                        (max(1, int(w * args.scale)), max(1, int(h * args.scale))),
                        interpolation=cv2.INTER_LINEAR,
                    )
                    if args.scale != 1.0
                    else frame
                )
                cv2.imshow(win_name, disp)
                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), 27):
                    return
                elif key == ord("s"):
                    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                    out_path = os.path.join("captures", f"endoscope_{ts}.png")
                    cv2.imwrite(out_path, frame)
                    print(f"Saved {out_path}")
                elif key == ord("1"):
                    cam.switch_camera(1, 8)
                elif key == ord("2"):
                    cam.switch_camera(2, 8)
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        reader_thread.join(timeout=1.0)
        if vcam is not None:
            vcam.close()
        if not args.no_preview:
            cv2.destroyAllWindows()
        cam.close()
        print("Disconnected.")


if __name__ == "__main__":
    main()
