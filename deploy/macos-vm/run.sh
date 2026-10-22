#!/bin/bash
# macOS Monterey VM for Jarvis's iMessage / SMS bridge. Headless; QEMU from Ubuntu's packages.
# Settings follow quickemu 4.9.9's choices for macOS 12 on an AMD host (Haswell CPU model
# presented as Intel, Apple SMC, OpenCore bootloader disk, virtio disk and network).
#   control:  socat - unix-connect:monitor.sock   (QEMU monitor; QMP on qmp.sock)
#   ssh:      ssh jarvis-mac (127.0.0.1:22220)   (once Remote Login is on in macOS)
#   screen:   VNC on 127.0.0.1:5901 only; from another computer: ssh -L 5901:localhost:5901 hyde
# Monterey rather than newer: this laptop's TSC is flagged unstable, which freezes Ventura+
# guests on Ryzen mobile CPUs unless the host boots with tsc=reliable. For the same reason, one
# virtual CPU: with two, the macOS kernel spins forever early in boot.
set -e
cd "${VM_DIR:-$HOME/vm/macos-monterey}"  # the disk, firmware and OpenCore images
[ -e OVMF_VARS.fd ] || cp OVMF_VARS-1920x1080.fd OVMF_VARS.fd
[ -e disk.qcow2 ] || qemu-img create -q -f qcow2 disk.qcow2 128G

CPU="Haswell-v2,vendor=GenuineIntel,-pdpe1gb,+avx,+sse,+sse2,+ssse3,vmware-cpuid-freq=on,+sse4.2"
CPU+=",+abm,+adx,+aes,+apic,+arat,+bmi1,+bmi2,+clflush,+cmov,+cx8,+cx16,+de,+erms,+f16c,+fma"
CPU+=",+fsgsbase,+fxsr,+invpcid,+invtsc,+lahf-lm,+lm,+mca,+mce,+mmx,+movbe,+msr,+mtrr,+nx,+pae"
CPU+=",+pat,+pge,+pse,+popcnt,+pse36,+rdrand,+rdtscp,+sep,+smep,+syscall,+tsc,+vaes,+vpclmulqdq"
CPU+=",+xgetbv1,+xsave,+xsaveopt,-pcid,+tsc-deadline"

exec qemu-system-x86_64 \
  -name macos-monterey,process=macos-monterey \
  -machine q35,hpet=off,smm=off,vmport=off -accel kvm \
  -cpu "$CPU" -smp ${SMP:-1} -m 4G -device virtio-balloon \
  -device isa-applesmc,osk="ourhardworkbythesewordsguardedpleasedontsteal(c)AppleComputerInc" \
  -global ICH9-LPC.disable_s3=1 -global ICH9-LPC.acpi-pci-hotplug-with-bridge-support=off \
  -smbios type=2 \
  -drive if=pflash,format=raw,unit=0,file=OVMF_CODE.fd,readonly=on \
  -drive if=pflash,format=raw,unit=1,file=OVMF_VARS.fd \
  -vga none -device vmware-svga -display none -vnc 127.0.0.1:1 \
  -device qemu-xhci,id=input -device usb-kbd,bus=input.0 -device usb-tablet,bus=input.0 \
  -netdev user,id=nic,hostname=macos-monterey,hostfwd=tcp:127.0.0.1:22220-:22 \
  -device virtio-net-pci,netdev=nic,mac=52:54:00:4d:d2:07 \
  -device ahci,id=ahci \
  -device ide-hd,bus=ahci.0,drive=BootLoader,bootindex=0 \
  -drive id=BootLoader,if=none,format=qcow2,file=OpenCore.qcow2 \
  -device ide-hd,bus=ahci.1,drive=RecoveryImage \
  -drive id=RecoveryImage,if=none,format=raw,file=RecoveryImage.img \
  -device virtio-blk-pci,drive=SystemDisk \
  -drive id=SystemDisk,if=none,format=qcow2,file=disk.qcow2,discard=unmap,detect-zeroes=unmap \
  -monitor unix:monitor.sock,server,nowait -qmp unix:qmp.sock,server,nowait \
  -pidfile macos-monterey.pid
