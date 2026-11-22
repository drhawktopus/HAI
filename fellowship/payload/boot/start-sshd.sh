#!/data/data/com.termux/files/usr/bin/sh
# Runs at boot via Termux:Boot. Without this a rebooted tablet goes completely dark -- Termux does
# not start on its own, so sshd never comes back and the node needs physical hands.
#
# termux-wake-lock first: it keeps the CPU from sleeping under us mid-job. It does NOT prevent
# Android's app-standby freezer -- that is the doze whitelist, set separately via adb -- so both
# are needed and neither substitutes for the other.
termux-wake-lock
sshd
