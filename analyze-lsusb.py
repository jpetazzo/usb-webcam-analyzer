#!/usr/bin/env python

"""
Parse the output of lsusb to extract detailed information about
the alternate settings supported by USB Video Class compliant
devices.

Usage:
sudo lsusb -v | ./analyze-lsusb.py
-> converts the entire lsusb output to JSON

sudo lsusb -v | ./analyze-lsusb.py --json
sudo lsusb -v | ./analyze-lsusb.py --yaml
sudo lsusb -v | ./analyze-lsusb.py --txt
-> converts individual webcam entires into JSON/YAML/TXT,
   generating a file for each webcam found
"""

import json
import sys
import yaml

ONE = 1
MANY = 2

arity = {
  "Device Descriptor": ONE,
  "Configuration Descriptor": MANY,
  "Interface Descriptor": MANY,
  "Endpoint Descriptor": MANY,
  "Hub Descriptor": ONE,
  " Hub Port Status": ONE,
  "Binary Object Store Descriptor": ONE,
  "USB 2.0 Extension Device Capability": ONE,
  "SuperSpeed USB Device Capability": ONE,
  "SuperSpeedPlus USB Device Capability": ONE,
  "Container ID Device Capability": ONE,
  "Interface Association": MANY,
  "VideoControl Interface Descriptor": MANY,
  "VideoStreaming Interface Descriptor": MANY,
  "AudioControl Interface Descriptor": MANY,
  "AudioStreaming Interface Descriptor": MANY,
  "AudioStreaming Endpoint Descriptor": MANY,
  "Device Qualifier (for other device speed)": ONE,
  "CDC Header": ONE,
  "CDC Union": ONE,
  "CDC Ethernet": ONE,
}


def split_nodes(lines):
  assert not lines[0].startswith("  ")
  nodes = []
  current_node = lines.pop(0)
  current_subnodes = []
  # The "END" is just a sentinel so that we don't have to make
  # a special case for the last entry in the list.
  for line in lines + ["END"]:
    if line.startswith("  "):
      current_subnodes.append(line[2:])
    else:
      # Hack to work around weird stuff with "HID Device Descriptor:"
      if current_node.startswith("iInterface"):
        current_subnodes = []
      if current_subnodes:
        current_node = [ current_node, split_nodes(current_subnodes) ]     
      nodes.append(current_node)
      current_node = line
      current_subnodes = []
  return nodes


def make_tree(nodes):
  tree = {}
  for node in nodes:
    if type(node) == str:
      key, *maybe_value = node.split("  ", 1)
      tree[key] = maybe_value[0].strip() if maybe_value else True
    elif type(node) == list:
      assert len(node) == 2
      key = node[0]
      if (key.startswith("bm")
          or key.startswith("wHubCharacteristic")
          or key.startswith("wSpeedsSupported")
          or key.startswith("bFunctionalitySupport")
          or key.startswith("Device Status:")
          or key.startswith("bFlags")
          or key.startswith("wChannelConfig")
          ):
        # bitmasks
        node_arity = ONE
        key, raw_value = key.split(" ", 1)
        node[1].insert(0, "raw_value  "+raw_value.strip())
      elif key.endswith(":"):
        key = key[:-1]
        node_arity = arity[key]
      else:
        raise ValueError("Don't know how to handle group key: {}".format(key))      
      if node_arity == ONE:
        assert key not in tree
        tree[key] = make_tree(node[1])
      else: # arity[key] == MANY:
        if key not in tree:
          tree[key] = []
        tree[key].append(make_tree(node[1]))
    else:
      raise ValueError("Wrong node type: {}".format(type(node)))
  return tree


def parse(device):
  lines = device.split("\n")
  lines = [ line for line in lines if "Warning: Descriptor too short" not in line ]
  assert lines[0].startswith("Bus")
  nodes = split_nodes(lines[1:])
  tree = make_tree(nodes)
  return tree


devices = []
input_file = sys.stdin
for device in input_file.read().split("\n\n"):
  device = device.strip()
  tree = parse(device)
  devices.append(tree)


if len(sys.argv) == 1:
  json.dump(devices, sys.stdout)
  exit(0)


def humanize(bps):
  bps = int(bps)
  if bps > 10000000:
    return "{}Mb/s".format(bps//1000000)
  if bps > 10000:
    return "{}Kb/s".format(bps//1000)
  return "{}b/s".format(bps)


def framerates(vsid):
  rates = []
  for key in vsid:
    if key.startswith("dwFrameInterval"):
      interval = int(vsid[key])
      fps = round(10000000 / interval)
      rates.append(fps)
  return rates


def estimate(endpoint):
  packet = endpoint["wMaxPacketSize"]
  raw, multiplier, size, unit = packet.split()
  assert multiplier.endswith("x")
  assert unit == "bytes"
  multiplier = int(multiplier[:-1])
  size = int(size)
  bytes_per_packet = multiplier*size
  if "bMaxBurst" in endpoint:
    bytes_per_packet *= 1 + int(endpoint["bMaxBurst"])
  if "Mult" in endpoint:
    bytes_per_packet *= 1 + int(endpoint["Mult"])
  return bytes_per_packet * 8000 * 8

reports = []
for device in devices:
  descriptor = device["Device Descriptor"]
  interfaces = descriptor["Configuration Descriptor"][0]["Interface Descriptor"]
  interfaces = [ i for i in interfaces if i["bInterfaceSubClass"] == "2 Video Streaming" ]
  if interfaces:
    report = {}
    reports.append(report)
    report["idVendor"] = descriptor["idVendor"]
    report["idProduct"] = descriptor["idProduct"]
    report["iProduct"] = descriptor["iProduct"]
    report["bcdUSB"] = descriptor["bcdUSB"]
    report["formats"] = []
    report["endpoints"] = []
    #print("#{idVendor} - {idProduct}".format(**descriptor))
    for interface in interfaces:
      #print("Alternate setting: {bAlternateSetting}".format(**interface))
      for vsid in interface.get("VideoStreaming Interface Descriptor", []):
        if "bFormatIndex" in vsid:
          videoFormat = {}
          videoFormat["resolutions"] = []
          report["formats"].append(videoFormat)
          videoFormat["name"] = vsid.get("bDescriptorSubtype").split()[1][1:-1]
          if "guidFormat" in vsid:
            videoFormat["fourcc"] = "".join([
              chr(int(vsid["guidFormat"][7:9], 16)),
              chr(int(vsid["guidFormat"][5:7], 16)),
              chr(int(vsid["guidFormat"][3:5], 16)),
              chr(int(vsid["guidFormat"][1:3], 16)),
            ])
          if "bBitsPerPixel" in vsid:
            videoFormat["bpp"] = int(vsid["bBitsPerPixel"])
        if "bFrameIndex" in vsid:
          resolution = {}
          videoFormat["resolutions"].append(resolution)
          resolution["w"] = int(vsid["wWidth"])
          resolution["h"] = int(vsid["wHeight"])
          resolution["resolution"] = "{}x{}".format(vsid["wWidth"], vsid["wHeight"])
          if vsid["dwMinBitRate"] == vsid["dwMaxBitRate"]:
            resolution["humanbitrate"] = humanize(vsid["dwMinBitRate"])
          else:
            resolution["humanbitrate"] = "{}-{}".format(
              humanize(vsid["dwMinBitRate"]),
              humanize(vsid["dwMaxBitRate"]),
            )
          resolution["minbps"] = int(vsid["dwMinBitRate"])
          resolution["maxbps"] = int(vsid["dwMaxBitRate"])
          resolution["fps"] = framerates(vsid)
      for endpoint in interface.get("Endpoint Descriptor", []):
        data = {}
        report["endpoints"].append(data)
        data["interval"] = endpoint.get("bInterval", "?")
        data["maxburst"] = endpoint.get("bMaxBurst", "?")
        data["mult"] = endpoint.get("Mult", "?")
        data["packet"] = endpoint.get("wMaxPacketSize", "?")
        data["bitrate"] = estimate(endpoint)
        data["humanbitrate"] = humanize(estimate(endpoint))

for report in reports:
  basename = "devicereports/{}_{}_USB{}".format(
    report["idVendor"].split()[0],
    report["idProduct"].split()[0],
    report["bcdUSB"],
  )
  if "--json" in sys.argv:
    with open(basename + ".json", "w") as f:
      json.dump(report, f)
  if "--yaml" in sys.argv:
    with open(basename + ".yaml", "w") as f:
      yaml.safe_dump(report, f, default_flow_style=None)
  if "--txt" in sys.argv:
    with open(basename + ".txt", "w") as f:
      f.write("{}\n".format(report["iProduct"].split(" ", 1)[1]))
      f.write("Vendor ID: {idVendor}\n".format(**report))
      f.write("Product ID: {idProduct}\n".format(**report))
      f.write("USB version: {bcdUSB}\n".format(**report))
      f.write("Endpoints:")
      eps = report["endpoints"]
      eps = [ (ep["bitrate"], ep["humanbitrate"]) for ep in eps ]
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
            max(res["fps"]),
            res["humanbitrate"],
          ))
