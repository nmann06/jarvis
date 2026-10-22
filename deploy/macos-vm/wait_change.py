#!/usr/bin/env python3
"""Exit when the VM's screen stops being the dark Apple-logo/progress screen: the OpenCore
picker, or a bright screen (Setup Assistant, a desktop). Saves a screenshot."""
import sys, time
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import vmctl
from PIL import Image, ImageStat

limit = time.time() + 60 * float(sys.argv[1] if len(sys.argv) > 1 else 120)
while time.time() < limit:
    try:
        q = vmctl.QMP()
        try:
            q.hmp("screendump /tmp/vmwatch.ppm")
        finally:
            q.close()
        im = Image.open("/tmp/vmwatch.ppm").convert("L")
        w, h = im.size
        mean = ImageStat.Stat(im).mean[0]
        icon = max(im.crop((int(w * .465), int(h * .925), int(w * .49), int(h * .96))).getdata())
        if mean > 15 or icon > 120:
            q = vmctl.QMP()
            path, _ = vmctl.shot(q, "changed")
            q.close()
            print("CHANGED", time.strftime("%H:%M:%S"), f"mean {mean:.0f} icon {icon}", path)
            sys.exit(0)
    except Exception as e:
        print("watch error:", e, flush=True)
    time.sleep(15)
print("TIMEOUT", time.strftime("%H:%M:%S"))
sys.exit(1)
