#!/bin/bash
# Give the macOS VM its own Mac identity, which iMessage needs: the OpenCore image from OSX-KVM
# ships one serial (iMacPro1,1, C02FPBZPHX87) that every OSX-KVM user shares, and Apple refuses
# or flags iMessage sign-ins from it.
#
#   set-smbios.sh SERIAL MLB [--no-amfi-off]
#
# Make SERIAL and MLB with macserial or GenSMBIOS for model iMacPro1,1 (e.g. `macserial
# --model iMacPro1,1 --num 1` prints "SERIAL | MLB"), and check the serial at
# https://checkcoverage.apple.com: it must come back invalid, not belong to someone's Mac.
# The UUID is new and random; ROM is the VM's network card address (from run.sh), as
# iMessage expects. --no-amfi-off also drops amfi_get_out_of_my_way=1 from the boot
# arguments (it turns off a macOS security check the install doesn't need).
#
# Run it with the VM shut down. The old image is kept as OpenCore.qcow2.bak.
set -euo pipefail
SERIAL=${1:?usage: set-smbios.sh SERIAL MLB [--no-amfi-off]}
MLB=${2:?usage: set-smbios.sh SERIAL MLB [--no-amfi-off]}
AMFI=${3:-}
VM_DIR=${VM_DIR:-$HOME/vm/macos-monterey}
HERE=$(dirname "$(readlink -f "$0")")
cd "$VM_DIR"
if [ -e macos-monterey.pid ] && kill -0 "$(cat macos-monterey.pid)" 2>/dev/null; then
    echo "The VM is running: shut macOS down first (Apple menu > Shut Down)." >&2
    exit 1
fi
MAC=$(grep -o 'mac=[0-9a-f:]*' "$HERE/run.sh" | cut -d= -f2)
ROM=${MAC//:/}
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
qemu-img convert -O raw OpenCore.qcow2 "$WORK/oc.raw"
# The first partition (label EFI) holds EFI/OC/config.plist.
START=$(sfdisk -J "$WORK/oc.raw" | python3 -c "import json,sys; print(json.load(sys.stdin)['partitiontable']['partitions'][0]['start'])")
IMG="$WORK/oc.raw@@$((START * 512))"
mcopy -i "$IMG" ::/EFI/OC/config.plist "$WORK/config.plist"
python3 - "$WORK/config.plist" "$SERIAL" "$MLB" "$ROM" "$AMFI" <<'EOF'
import plistlib, sys, uuid
path, serial, mlb, rom, amfi = sys.argv[1:]
with open(path, "rb") as f:
    plist = plistlib.load(f)
generic = plist["PlatformInfo"]["Generic"]
generic.update(SystemSerialNumber=serial, MLB=mlb, SystemUUID=str(uuid.uuid4()).upper(),
               ROM=bytes.fromhex(rom))
if amfi == "--no-amfi-off":
    nvram = plist["NVRAM"]["Add"]["7C436110-AB2A-4BBB-A880-FE41995C9F82"]
    nvram["boot-args"] = " ".join(a for a in nvram["boot-args"].split()
                                  if a != "amfi_get_out_of_my_way=1")
with open(path, "wb") as f:
    plistlib.dump(plist, f)
print(f"{generic['SystemProductName']}: serial {serial}, MLB {mlb}, UUID {generic['SystemUUID']}, ROM {rom}")
EOF
mcopy -o -i "$IMG" "$WORK/config.plist" ::/EFI/OC/config.plist
qemu-img convert -O qcow2 "$WORK/oc.raw" "$WORK/OpenCore.qcow2"
[ -e OpenCore.qcow2.bak ] || cp OpenCore.qcow2 OpenCore.qcow2.bak
mv "$WORK/OpenCore.qcow2" OpenCore.qcow2
# A changed identity needs fresh firmware variables too (they remember the old one).
rm -f OVMF_VARS.fd
echo "Done. Start the VM again: $HERE/run.sh"
