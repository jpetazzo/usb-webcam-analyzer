#!/bin/sh
cd /sys/class/video4linux
for DEV in *; do
  cat $DEV/name
  DEV=/dev/$DEV
  v4l2-ctl --list-formats-ext --device $DEV
  #ffplay -hide_banner -list_formats all -f v4l2 $DEV
done