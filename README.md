# USB webcam analyzer

It can be difficult to use multiple USB webcams (technically:
USB Video Class compliant devices, or UVC in short) simultaneously,
because they will share the USB bandwidth of the root port that
they're connected to.

Consider the following facts:

- USB 2 is limited to 480 Mb/s per root port
- USB 3.0 is limited to 5 Gb/s per root port
- USB 2 and USB 3 are two completely separated buses
  (a bit like having two separate train tracks that never cross)
- on many motherboards, all the USB ports are internally connected
  to the same root port (so they all share the same bandwidth)
- UVC devices use isochronous transfers (a mode that "reserves"
  bandwidth for the device, whether it will use it or not)
- only 80% of the USB bandwidth can be allocated to isochronous
  transfers
- isochronous bandwidth isn't allocated in "bits per second", but
  in "bytes per frame"; there is one frame every millisecond
  (technically it's "bytes per packet" + "packet per microframe"
  and one microframe is 125µs and it's even a bit more complicated
  than that for USB 3!)
- when using USB audio interfaces, they will also use isochronous
  bandwidth (typically on USB 2), and they are often
  "full speed" (12 Mb/s) instead of "high speed" (480 Mb/s),
  meaning that depending on the circumstances, they might use
  the bus "longer" for the same amount of data transferred
- a webcam or audio interface cannot reserve an arbitrary amount
  of bandwidth; instead, it has multiple "alternate settings",
  each corresponding to a fixed amount of bandwidth
- some webcams have many "alternate settings", ideally corresponding
  to the amount of bandwidth required by each combination of
  `(video format, resolution, framerate)`
- some webcams, however, have only a couple of "alternate settings"
  (e.g. the Razer Kiyo Pro only has 2 alternate settings: one that
  uses 196 Mb/s, another 2162 Mb/s)
- when using compressed video formats like MJPEG or H264, it is
  necessary to allocate an amount of bandwidth corresponding to the
  upper bound of what will actually be required, which is way more
  than will actually be necessary
- 1080p @ 30 fps in a non-compressed format like YUY2 (aka YUYV422)
  requires almost 1 Gb/s of bandwidth (995.3 Mb/s), *not including
  the overhead required by e.g. packet headers*
  ([at least 10 to 15 percent][overhead])

The end result is that trying to obtain two high-quality video
streams from the same USB root port can be very difficult.

It should always be possible to use one webcam on USB 3, and
another on USB 2. The USB 2 webcam won't be able to send
1080p @ 30 fps non-compressed, though (USB 2 doesn't have enough
bandwidth for that), so it'll have to use compression (typically
MJPEG or H264).

Some webcams like the Logitech BRIO have many alternate settings,
and will reserve "just the right amount" of bandwidth on the bus.

Other webcams like the Razer Kiyo Pro will reserve more than 2 Gb/s
of bandwidth on the bus, even at lower resolutions and framerates,
meaning that it will be very difficult to plug other webcams
on the same bus.

This repo aims at holding a bunch of scripts and tools to try
and figure out what modes are supported by various webcams.

[overhead]: https://web.archive.org/web/20101205151115/http://www.pcworld.com/article/82005/news_and_trends_usb_20s_real_deal.html