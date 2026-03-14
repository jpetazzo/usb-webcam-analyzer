#!/usr/bin/env python3

"""
Windows equivalent of analyze-lsusb.py.

Reads USB descriptors directly from the Windows USB hub driver via IOCTLs
(the same approach used by Microsoft's USBView tool), then extracts UVC
Video Streaming interface data to produce reports identical in format
to analyze-lsusb.py.

Requires Administrator privileges (same as sudo lsusb -v on Linux).

Usage:
  python analyze-windows.py
  -> dumps all UVC device data as JSON to stdout

  python analyze-windows.py --json --yaml --txt
  -> writes per-device report files to devicereports/
"""

import ctypes
import ctypes.wintypes as wintypes
import json
import struct
import sys

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

VERBOSE = "--verbose" in sys.argv or "-v" in sys.argv

def verbose(*args):
    if VERBOSE:
        print(*args, file=sys.stderr)

# ---------------------------------------------------------------------------
# Windows constants
# ---------------------------------------------------------------------------

GENERIC_WRITE = 0x40000000
GENERIC_READ = 0x80000000
FILE_SHARE_READ = 1
FILE_SHARE_WRITE = 2
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
FILE_DEVICE_USB = 0x00000022
METHOD_BUFFERED = 0
FILE_ANY_ACCESS = 0

DIGCF_PRESENT = 0x02
DIGCF_DEVICEINTERFACE = 0x10

# GUIDs
GUID_DEVINTERFACE_USB_HOST_CONTROLLER = (
    0x3ABF6F2D, 0x71C4, 0x462A,
    (0x8A, 0x92, 0x1E, 0x68, 0x61, 0xE6, 0xAF, 0x27)
)
GUID_DEVINTERFACE_USB_HUB = (
    0xF18A0E88, 0xC30C, 0x11D0,
    (0x88, 0x15, 0x00, 0xA0, 0xC9, 0x06, 0xBE, 0xD8)
)


def CTL_CODE(device_type, function, method, access):
    return (device_type << 16) | (access << 14) | (function << 2) | method


IOCTL_USB_GET_ROOT_HUB_NAME = CTL_CODE(FILE_DEVICE_USB, 258, METHOD_BUFFERED, FILE_ANY_ACCESS)
IOCTL_USB_GET_NODE_INFORMATION = CTL_CODE(FILE_DEVICE_USB, 258, METHOD_BUFFERED, FILE_ANY_ACCESS)
# The above two happen to share the same code; they go to different device objects.
IOCTL_USB_GET_NODE_CONNECTION_INFORMATION_EX = CTL_CODE(FILE_DEVICE_USB, 274, METHOD_BUFFERED, FILE_ANY_ACCESS)
IOCTL_USB_GET_DESCRIPTOR_FROM_NODE_CONNECTION = CTL_CODE(FILE_DEVICE_USB, 260, METHOD_BUFFERED, FILE_ANY_ACCESS)
IOCTL_USB_GET_NODE_CONNECTION_NAME = CTL_CODE(FILE_DEVICE_USB, 261, METHOD_BUFFERED, FILE_ANY_ACCESS)

# USB descriptor types
USB_DEVICE_DESCRIPTOR_TYPE = 1
USB_CONFIGURATION_DESCRIPTOR_TYPE = 2
USB_STRING_DESCRIPTOR_TYPE = 3
USB_INTERFACE_DESCRIPTOR_TYPE = 4
USB_ENDPOINT_DESCRIPTOR_TYPE = 5
USB_SS_ENDPOINT_COMPANION_DESCRIPTOR_TYPE = 0x30

# UVC CS_INTERFACE descriptor subtypes (Video Streaming)
VS_FORMAT_UNCOMPRESSED = 4
VS_FRAME_UNCOMPRESSED = 5
VS_FORMAT_MJPEG = 6
VS_FRAME_MJPEG = 7
VS_FORMAT_FRAME_BASED = 16
VS_FRAME_FRAME_BASED = 17

# USB Video interface subclass
SC_VIDEOSTREAMING = 2
CC_VIDEO = 14
CS_INTERFACE = 0x24

# ---------------------------------------------------------------------------
# ctypes structures
# ---------------------------------------------------------------------------

kernel32 = ctypes.windll.kernel32
setupapi = ctypes.windll.setupapi

CreateFileW = kernel32.CreateFileW
CreateFileW.restype = wintypes.HANDLE
DeviceIoControl = ctypes.windll.kernel32.DeviceIoControl
DeviceIoControl.restype = wintypes.BOOL
DeviceIoControl.argtypes = [
    wintypes.HANDLE, wintypes.DWORD,
    ctypes.c_void_p, wintypes.DWORD,
    ctypes.c_void_p, wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
CloseHandle = kernel32.CloseHandle

class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("InterfaceClassGuid", GUID),
        ("Flags", wintypes.DWORD),
        ("Reserved", ctypes.POINTER(ctypes.c_ulong)),
    ]


class SP_DEVICE_INTERFACE_DETAIL_DATA_W(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("DevicePath", ctypes.c_wchar * 1024),
    ]


class USB_HUB_INFORMATION(ctypes.Structure):
    """Simplified — we only need bNumberOfPorts from the hub descriptor."""
    _fields_ = [
        ("bDescriptorLength", ctypes.c_ubyte),
        ("bDescriptorType", ctypes.c_ubyte),
        ("bNumberOfPorts", ctypes.c_ubyte),
        # ... more fields follow but we don't need them
        ("_pad", ctypes.c_ubyte * 253),
    ]


class USB_NODE_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("NodeType", ctypes.c_ulong),  # 0 = Hub
        ("HubInformation", USB_HUB_INFORMATION),
    ]


class USB_DEVICE_DESCRIPTOR(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("bLength", ctypes.c_ubyte),
        ("bDescriptorType", ctypes.c_ubyte),
        ("bcdUSB", ctypes.c_ushort),
        ("bDeviceClass", ctypes.c_ubyte),
        ("bDeviceSubClass", ctypes.c_ubyte),
        ("bDeviceProtocol", ctypes.c_ubyte),
        ("bMaxPacketSize0", ctypes.c_ubyte),
        ("idVendor", ctypes.c_ushort),
        ("idProduct", ctypes.c_ushort),
        ("bcdDevice", ctypes.c_ushort),
        ("iManufacturer", ctypes.c_ubyte),
        ("iProduct", ctypes.c_ubyte),
        ("iSerialNumber", ctypes.c_ubyte),
        ("bNumConfigurations", ctypes.c_ubyte),
    ]


class USB_NODE_CONNECTION_INFORMATION_EX(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("ConnectionIndex", ctypes.c_ulong),
        ("DeviceDescriptor", USB_DEVICE_DESCRIPTOR),
        ("CurrentConfigurationValue", ctypes.c_ubyte),
        ("Speed", ctypes.c_ubyte),
        ("DeviceIsHub", ctypes.c_ubyte),
        ("DeviceAddress", ctypes.c_ushort),
        ("NumberOfOpenPipes", ctypes.c_ulong),
        ("ConnectionStatus", ctypes.c_ulong),
        # Followed by pipe info array, but we don't need it
        ("_pad", ctypes.c_ubyte * 256),
    ]


class USB_DESCRIPTOR_REQUEST(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("ConnectionIndex", ctypes.c_ulong),
        ("bmRequest", ctypes.c_ubyte),
        ("bRequest", ctypes.c_ubyte),
        ("wValue", ctypes.c_ushort),
        ("wIndex", ctypes.c_ushort),
        ("wLength", ctypes.c_ushort),
        ("Data", ctypes.c_ubyte * 4096),
    ]


class USB_ROOT_HUB_NAME(ctypes.Structure):
    _fields_ = [
        ("ActualLength", ctypes.c_ulong),
        ("RootHubName", ctypes.c_wchar * 512),
    ]


class USB_NODE_CONNECTION_NAME(ctypes.Structure):
    _fields_ = [
        ("ConnectionIndex", ctypes.c_ulong),
        ("ActualLength", ctypes.c_ulong),
        ("NodeName", ctypes.c_wchar * 512),
    ]


# SetupAPI function signatures (must be after struct definitions)
setupapi.SetupDiGetClassDevsW.argtypes = [
    ctypes.POINTER(GUID), ctypes.c_wchar_p, wintypes.HWND, wintypes.DWORD]
setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p

setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
    ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(GUID),
    wintypes.DWORD, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA)]
setupapi.SetupDiEnumDeviceInterfaces.restype = wintypes.BOOL

setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
    ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
    ctypes.c_void_p]
setupapi.SetupDiGetDeviceInterfaceDetailW.restype = wintypes.BOOL

setupapi.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]
setupapi.SetupDiDestroyDeviceInfoList.restype = wintypes.BOOL


# ---------------------------------------------------------------------------
# Helpers for talking to Windows USB stack
# ---------------------------------------------------------------------------

def make_guid(data1, data2, data3, data4_tuple):
    g = GUID()
    g.Data1 = data1
    g.Data2 = data2
    g.Data3 = data3
    for i, b in enumerate(data4_tuple):
        g.Data4[i] = b
    return g


def open_device(path):
    h = CreateFileW(
        path,
        GENERIC_WRITE | GENERIC_READ,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        None,
        OPEN_EXISTING,
        0,
        None,
    )
    if h == INVALID_HANDLE_VALUE:
        return None
    return h


def ioctl(handle, code, in_buf, out_buf):
    bytes_returned = wintypes.DWORD(0)
    ok = DeviceIoControl(
        handle,
        code,
        ctypes.byref(in_buf) if in_buf is not None else None,
        ctypes.sizeof(in_buf) if in_buf is not None else 0,
        ctypes.byref(out_buf),
        ctypes.sizeof(out_buf),
        ctypes.byref(bytes_returned),
        None,
    )
    return ok, bytes_returned.value


def enumerate_device_interfaces(guid_tuple):
    """Return list of device paths for the given interface GUID."""
    guid = make_guid(*guid_tuple)
    h_info = setupapi.SetupDiGetClassDevsW(
        ctypes.byref(guid), None, None,
        DIGCF_PRESENT | DIGCF_DEVICEINTERFACE,
    )
    if h_info == INVALID_HANDLE_VALUE:
        return []

    paths = []
    index = 0
    while True:
        iface_data = SP_DEVICE_INTERFACE_DATA()
        iface_data.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DATA)
        ok = setupapi.SetupDiEnumDeviceInterfaces(
            h_info, None, ctypes.byref(guid), index, ctypes.byref(iface_data),
        )
        if not ok:
            break

        detail = SP_DEVICE_INTERFACE_DETAIL_DATA_W()
        detail.cbSize = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
        buf_size = wintypes.DWORD(ctypes.sizeof(detail))
        ok = setupapi.SetupDiGetDeviceInterfaceDetailW(
            h_info, ctypes.byref(iface_data),
            ctypes.byref(detail), buf_size, None, None,
        )
        if ok:
            paths.append(detail.DevicePath)
        index += 1

    setupapi.SetupDiDestroyDeviceInfoList(h_info)
    return paths


def get_root_hub_name(hc_handle):
    buf = USB_ROOT_HUB_NAME()
    ok, _ = ioctl(hc_handle, IOCTL_USB_GET_ROOT_HUB_NAME, None, buf)
    if ok:
        return buf.RootHubName
    return None


def get_hub_port_count(hub_handle):
    info = USB_NODE_INFORMATION()
    ok, _ = ioctl(hub_handle, IOCTL_USB_GET_NODE_INFORMATION, info, info)
    if ok:
        return info.HubInformation.bNumberOfPorts
    return 0


def get_connection_info(hub_handle, port):
    info = USB_NODE_CONNECTION_INFORMATION_EX()
    info.ConnectionIndex = port
    ok, nbytes = ioctl(hub_handle, IOCTL_USB_GET_NODE_CONNECTION_INFORMATION_EX, info, info)
    if not ok:
        err = ctypes.GetLastError()
        if err != 0:
            verbose("  Port {}: IOCTL failed, error={}".format(port, err))
        return None
    verbose("  Port {}: ok={} bytes={} status={} vid=0x{:04x} pid=0x{:04x} hub={}".format(
        port, ok, nbytes, info.ConnectionStatus,
        info.DeviceDescriptor.idVendor, info.DeviceDescriptor.idProduct,
        info.DeviceIsHub))
    if info.ConnectionStatus == 1:
        return info
    return None


def get_connection_name(hub_handle, port):
    """If the connected device is a hub, get its symbolic name."""
    buf = USB_NODE_CONNECTION_NAME()
    buf.ConnectionIndex = port
    ok, _ = ioctl(hub_handle, IOCTL_USB_GET_NODE_CONNECTION_NAME, buf, buf)
    if ok and buf.ActualLength > 0:
        return buf.NodeName
    return None


def get_descriptor(hub_handle, port, desc_type, desc_index, lang_id, length):
    req = USB_DESCRIPTOR_REQUEST()
    req.ConnectionIndex = port
    req.bmRequest = 0x80
    req.bRequest = 0x06
    req.wValue = (desc_type << 8) | desc_index
    req.wIndex = lang_id
    req.wLength = min(length, 4096)
    ok, bytes_ret = ioctl(hub_handle, IOCTL_USB_GET_DESCRIPTOR_FROM_NODE_CONNECTION, req, req)
    if ok and bytes_ret > ctypes.sizeof(USB_DESCRIPTOR_REQUEST) - 4096:
        data_len = bytes_ret - (ctypes.sizeof(USB_DESCRIPTOR_REQUEST) - 4096)
        return bytes(req.Data[:data_len])
    return None


def get_string_descriptor(hub_handle, port, index):
    if index == 0:
        return ""
    raw = get_descriptor(hub_handle, port, USB_STRING_DESCRIPTOR_TYPE, index, 0x0409, 256)
    if raw and len(raw) >= 2:
        str_len = raw[0]
        if str_len > 2:
            try:
                return raw[2:str_len].decode("utf-16-le")
            except Exception:
                return ""
    return ""


def get_config_descriptor(hub_handle, port):
    # First, get the 4-byte header to learn wTotalLength
    raw = get_descriptor(hub_handle, port, USB_CONFIGURATION_DESCRIPTOR_TYPE, 0, 0, 4)
    if not raw or len(raw) < 4:
        return None
    total_length = struct.unpack_from("<H", raw, 2)[0]
    # Now get the full descriptor
    raw = get_descriptor(hub_handle, port, USB_CONFIGURATION_DESCRIPTOR_TYPE, 0, 0, total_length)
    return raw


# ---------------------------------------------------------------------------
# USB vendor name lookup (best-effort via Windows registry)
# ---------------------------------------------------------------------------

_vendor_cache = {}

def lookup_vendor_name(vid):
    """Try to find a vendor name from the registry. Returns '' on failure."""
    if vid in _vendor_cache:
        return _vendor_cache[vid]
    name = ""
    try:
        import winreg
        key_path = r"SYSTEM\CurrentControlSet\Enum\USB"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as usb_key:
            i = 0
            while True:
                try:
                    subkey_name = winreg.EnumKey(usb_key, i)
                    if subkey_name.upper().startswith("VID_{:04X}".format(vid)):
                        # Found a device with this VID, try to read its mfg
                        with winreg.OpenKey(usb_key, subkey_name) as vid_key:
                            j = 0
                            while True:
                                try:
                                    inst_name = winreg.EnumKey(vid_key, j)
                                    with winreg.OpenKey(vid_key, inst_name) as inst_key:
                                        try:
                                            val, _ = winreg.QueryValueEx(inst_key, "Mfg")
                                            if val:
                                                # Strip section qualifiers like ";..."
                                                val = val.split(";")[-1].strip()
                                                if val:
                                                    name = val
                                                    break
                                        except OSError:
                                            pass
                                    j += 1
                                except OSError:
                                    break
                        if name:
                            break
                    i += 1
                except OSError:
                    break
    except Exception:
        pass
    _vendor_cache[vid] = name
    return name


# ---------------------------------------------------------------------------
# UVC descriptor parser
# ---------------------------------------------------------------------------

def format_bcd_usb(bcd):
    major = (bcd >> 8) & 0xFF
    minor = (bcd >> 4) & 0x0F
    sub = bcd & 0x0F
    if sub:
        return "{}.{:d}{:d}".format(major, minor, sub)
    return "{}.{:02d}".format(major, minor)


def parse_config_descriptor(raw):
    """
    Walk the raw configuration descriptor bytes and extract all
    interface descriptors, endpoint descriptors, and UVC CS_INTERFACE
    descriptors organized by interface number and alternate setting.
    """
    entries = []
    offset = 0
    while offset < len(raw):
        if offset + 1 >= len(raw):
            break
        b_length = raw[offset]
        if b_length == 0:
            break
        if offset + b_length > len(raw):
            break
        b_type = raw[offset + 1]
        desc_bytes = raw[offset:offset + b_length]
        entries.append((b_type, desc_bytes))
        offset += b_length
    return entries


def parse_format_descriptor(dbytes, name):
    """Parse a VS_FORMAT_UNCOMPRESSED or VS_FORMAT_FRAME_BASED descriptor."""
    fmt = {"name": name, "resolutions": []}
    guid_bytes = dbytes[5:21]
    fourcc = "".join(chr(b) for b in guid_bytes[:4] if 0x20 <= b < 0x7F)
    if fourcc:
        fmt["fourcc"] = fourcc
    fmt["bpp"] = dbytes[21]
    return fmt


def parse_frame_descriptor(dbytes):
    """Parse a VS_FRAME_UNCOMPRESSED, VS_FRAME_MJPEG, or VS_FRAME_FRAME_BASED descriptor."""
    w = struct.unpack_from("<H", dbytes, 5)[0]
    h = struct.unpack_from("<H", dbytes, 7)[0]
    min_bitrate = struct.unpack_from("<I", dbytes, 9)[0]
    max_bitrate = struct.unpack_from("<I", dbytes, 13)[0]
    frame_interval_type = dbytes[25]
    fps_list = []
    if frame_interval_type > 0:
        for fi in range(frame_interval_type):
            off = 26 + fi * 4
            if off + 4 <= len(dbytes):
                interval = struct.unpack_from("<I", dbytes, off)[0]
                if interval > 0:
                    fps_list.append(round(10000000 / interval))
    else:
        # Continuous: min, max, step
        if len(dbytes) >= 38:
            min_iv = struct.unpack_from("<I", dbytes, 26)[0]
            max_iv = struct.unpack_from("<I", dbytes, 30)[0]
            if min_iv > 0:
                fps_list.append(round(10000000 / min_iv))
            if max_iv > 0 and max_iv != min_iv:
                fps_list.append(round(10000000 / max_iv))

    resolution = {
        "w": w,
        "h": h,
        "resolution": "{}x{}".format(w, h),
        "minbps": min_bitrate,
        "maxbps": max_bitrate,
        "fps": fps_list,
    }
    if min_bitrate == max_bitrate:
        resolution["humanbitrate"] = humanize(min_bitrate)
    else:
        resolution["humanbitrate"] = "{}-{}".format(
            humanize(min_bitrate), humanize(max_bitrate)
        )
    return resolution


def extract_uvc_data(config_entries, hub_handle, port, dev_desc):
    """
    From the list of (descriptor_type, bytes) entries, extract UVC
    video streaming data and endpoint info, grouped by interface
    alternate setting.
    """
    is_video_streaming = False
    formats = []
    current_format = None
    endpoints = []
    has_vs_interface = False

    for i, (dtype, dbytes) in enumerate(config_entries):
        if dtype == USB_INTERFACE_DESCRIPTOR_TYPE and len(dbytes) >= 9:
            is_video_streaming = (
                dbytes[5] == CC_VIDEO and
                dbytes[6] == SC_VIDEOSTREAMING
            )
            if is_video_streaming:
                has_vs_interface = True

        elif dtype == CS_INTERFACE and is_video_streaming:
            # CS_INTERFACE — UVC class-specific
            if len(dbytes) < 3:
                continue
            subtype = dbytes[2]

            if subtype == VS_FORMAT_UNCOMPRESSED and len(dbytes) >= 27:
                current_format = parse_format_descriptor(dbytes, "FORMAT_UNCOMPRESSED")
                formats.append(current_format)

            elif subtype == VS_FORMAT_MJPEG and len(dbytes) >= 11:
                current_format = {"name": "FORMAT_MJPEG", "resolutions": []}
                formats.append(current_format)

            elif subtype == VS_FORMAT_FRAME_BASED and len(dbytes) >= 27:
                current_format = parse_format_descriptor(dbytes, "FORMAT_FRAME_BASED")
                formats.append(current_format)

            elif subtype in (VS_FRAME_UNCOMPRESSED, VS_FRAME_MJPEG, VS_FRAME_FRAME_BASED) and len(dbytes) >= 26:
                if current_format is not None:
                    current_format["resolutions"].append(parse_frame_descriptor(dbytes))

        elif dtype == USB_ENDPOINT_DESCRIPTOR_TYPE and is_video_streaming and len(dbytes) >= 7:
            wMaxPacketSize = struct.unpack_from("<H", dbytes, 4)[0]
            bInterval = dbytes[6]
            # Check for SuperSpeed Endpoint Companion immediately after
            bMaxBurst = None
            ss_mult = None
            if i + 1 < len(config_entries):
                next_type, next_bytes = config_entries[i + 1]
                if next_type == USB_SS_ENDPOINT_COMPANION_DESCRIPTOR_TYPE and len(next_bytes) >= 4:
                    bMaxBurst = next_bytes[2]
                    ss_mult = next_bytes[3] & 0x03

            ep_data = build_endpoint_data(wMaxPacketSize, bInterval, bMaxBurst, ss_mult)
            endpoints.append(ep_data)

    if not has_vs_interface:
        return None

    # Fetch string descriptors only for confirmed UVC devices
    vid = dev_desc.idVendor
    pid = dev_desc.idProduct
    product_str = get_string_descriptor(hub_handle, port, dev_desc.iProduct)
    manufacturer_str = get_string_descriptor(hub_handle, port, dev_desc.iManufacturer)
    vendor_name = manufacturer_str or lookup_vendor_name(vid)

    id_vendor = "0x{:04x}".format(vid)
    if vendor_name:
        id_vendor += " " + vendor_name
    id_product = "0x{:04x}".format(pid)
    if product_str:
        id_product += " " + product_str
    i_product = "{} {}".format(dev_desc.iProduct, product_str) if product_str else str(dev_desc.iProduct)

    return {
        "idVendor": id_vendor,
        "idProduct": id_product,
        "iProduct": i_product,
        "bcdUSB": format_bcd_usb(dev_desc.bcdUSB),
        "formats": formats,
        "endpoints": endpoints,
    }


# ---------------------------------------------------------------------------
# Bandwidth estimation (matches analyze-lsusb.py logic)
# ---------------------------------------------------------------------------

def humanize(bps):
    bps = int(bps)
    if bps > 10000000:
        return "{}Mb/s".format(bps // 1000000)
    if bps > 10000:
        return "{}Kb/s".format(bps // 1000)
    return "{}b/s".format(bps)


def estimate(wMaxPacketSize, bMaxBurst=None, ss_mult=None):
    size = wMaxPacketSize & 0x7FF
    multiplier = ((wMaxPacketSize >> 11) & 0x3) + 1
    bytes_per_packet = multiplier * size
    if bMaxBurst is not None:
        bytes_per_packet *= (1 + bMaxBurst)
    if ss_mult is not None:
        bytes_per_packet *= (1 + ss_mult)
    return bytes_per_packet * 8000 * 8


def format_packet_field(wMaxPacketSize):
    """Reconstruct the lsusb-style wMaxPacketSize string."""
    size = wMaxPacketSize & 0x7FF
    multiplier = ((wMaxPacketSize >> 11) & 0x3) + 1
    return "0x{:04x}  {}x {} bytes".format(wMaxPacketSize, multiplier, size)


def build_endpoint_data(wMaxPacketSize, bInterval, bMaxBurst, ss_mult):
    bitrate = estimate(wMaxPacketSize, bMaxBurst, ss_mult)
    return {
        "interval": str(bInterval),
        "maxburst": str(bMaxBurst) if bMaxBurst is not None else "?",
        "mult": str(ss_mult) if ss_mult is not None else "?",
        "packet": format_packet_field(wMaxPacketSize),
        "bitrate": bitrate,
        "humanbitrate": humanize(bitrate),
    }


# ---------------------------------------------------------------------------
# Hub tree walker — recursively enumerate all USB devices
# ---------------------------------------------------------------------------

def probe_hub_ports(hub_handle, results, recurse=True):
    """Probe each port on an open hub for UVC devices."""
    num_ports = get_hub_port_count(hub_handle)
    verbose("  Hub has {} ports".format(num_ports))
    for port in range(1, num_ports + 1):
        conn = get_connection_info(hub_handle, port)
        if conn is None:
            continue

        desc = conn.DeviceDescriptor
        verbose("  Port {}: VID=0x{:04x} PID=0x{:04x} class={} hub={}".format(
            port, desc.idVendor, desc.idProduct, desc.bDeviceClass, conn.DeviceIsHub))

        if conn.DeviceIsHub and recurse:
            child_name = get_connection_name(hub_handle, port)
            if child_name:
                verbose("  -> child hub: {}".format(child_name))
                walk_hub(child_name, results)
        elif not conn.DeviceIsHub:
            config_raw = get_config_descriptor(hub_handle, port)
            if config_raw is None:
                verbose("  -> could not read config descriptor")
                continue
            verbose("  -> config descriptor: {} bytes".format(len(config_raw)))
            entries = parse_config_descriptor(config_raw)
            report = extract_uvc_data(entries, hub_handle, port, conn.DeviceDescriptor)
            if report and (report["formats"] or report["endpoints"]):
                verbose("  -> UVC device found! {} formats, {} endpoints".format(
                    len(report["formats"]), len(report["endpoints"])))
                results.append(report)
            elif report:
                verbose("  -> UVC device but no formats/endpoints")
            else:
                verbose("  -> not a UVC device")


def walk_hub(hub_path, results):
    """Open a hub and probe each port for UVC devices."""
    h = open_device("\\\\.\\" + hub_path)
    if h is None:
        return
    try:
        probe_hub_ports(h, results)
    finally:
        CloseHandle(h)


def enumerate_all_uvc_devices():
    """Walk all USB host controllers → root hubs → hubs → devices."""
    results = []

    # Method 1: via host controllers
    hc_paths = enumerate_device_interfaces(GUID_DEVINTERFACE_USB_HOST_CONTROLLER)
    verbose("Found {} host controller(s)".format(len(hc_paths)))
    for hc_path in hc_paths:
        verbose("HC: {}".format(hc_path))
        h = open_device(hc_path)
        if h is None:
            verbose("  -> could not open")
            continue
        root_hub_name = get_root_hub_name(h)
        CloseHandle(h)
        if root_hub_name:
            verbose("  Root hub: {}".format(root_hub_name))
            walk_hub(root_hub_name, results)
        else:
            verbose("  -> no root hub name")

    # Method 2 fallback: enumerate hubs directly if method 1 found nothing
    if not results:
        hub_paths = enumerate_device_interfaces(GUID_DEVINTERFACE_USB_HUB)
        verbose("Fallback: found {} hub(s) directly".format(len(hub_paths)))
        for hub_path in hub_paths:
            verbose("Hub: {}".format(hub_path))
            h = open_device(hub_path)
            if h is None:
                verbose("  -> could not open")
                continue
            try:
                probe_hub_ports(h, results, recurse=False)
            finally:
                CloseHandle(h)

    # Deduplicate by (idVendor, idProduct, bcdUSB)
    seen = set()
    unique = []
    for r in results:
        key = (r["idVendor"], r["idProduct"], r["bcdUSB"])
        if key not in seen:
            seen.add(key)
            unique.append(r)

    return unique


# ---------------------------------------------------------------------------
# Report output (matches analyze-lsusb.py format exactly)
# ---------------------------------------------------------------------------

def write_reports(reports):
    write_yaml = "--yaml" in sys.argv
    if write_yaml and not HAS_YAML:
        print("Warning: PyYAML not installed, skipping .yaml output", file=sys.stderr)
        write_yaml = False
    for report in reports:
        basename = "devicereports/{}_{}_USB{}".format(
            report["idVendor"].split()[0],
            report["idProduct"].split()[0],
            report["bcdUSB"],
        )
        if "--json" in sys.argv:
            with open(basename + ".json", "w", newline='\n') as f:
                json.dump(report, f)
        if write_yaml:
            with open(basename + ".yaml", "w", newline='\n') as f:
                yaml.safe_dump(report, f, default_flow_style=None)
        if "--txt" in sys.argv:
            with open(basename + ".txt", "w", newline='\n') as f:
                # First line: product name (strip index prefix if present)
                product = report["iProduct"]
                parts = product.split(" ", 1)
                name = (parts[1] if len(parts) > 1 else parts[0]).strip()
                f.write("{}\n".format(name))
                f.write("Vendor ID: {idVendor}\n".format(**report))
                f.write("Product ID: {idProduct}\n".format(**report))
                f.write("USB version: {bcdUSB}\n".format(**report))
                f.write("Endpoints:")
                eps = report["endpoints"]
                eps = [(ep["bitrate"], ep["humanbitrate"]) for ep in eps]
                eps = sorted(set(eps))
                for ep in eps:
                    f.write(" {}".format(ep[1]))
                f.write("\n")
                f.write("Formats:\n")
                for fmt in report["formats"]:
                    f.write("- {name}".format(**fmt))
                    if "fourcc" in fmt:
                        f.write(", {fourcc}".format(**fmt))
                    if "bpp" in fmt:
                        f.write(", {bpp}bpp".format(**fmt))
                    f.write("\n")
                    for res in fmt["resolutions"]:
                        f.write("  {} @ {}fps ~ {}\n".format(
                            res["resolution"],
                            max(res["fps"]) if res["fps"] else "?",
                            res["humanbitrate"],
                        ))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if not ctypes.windll.shell32.IsUserAnAdmin():
        print("Warning: not running as Administrator. USB descriptor access may fail.",
              file=sys.stderr)
        print("Run from an elevated command prompt for full results.", file=sys.stderr)

    reports = enumerate_all_uvc_devices()

    if not reports:
        print("No UVC (webcam) devices found.", file=sys.stderr)
        sys.exit(0)

    if len(sys.argv) == 1:
        json.dump(reports, sys.stdout)
        sys.exit(0)

    write_reports(reports)
    print("Wrote {} device report(s) to devicereports/".format(len(reports)), file=sys.stderr)


if __name__ == "__main__":
    main()
