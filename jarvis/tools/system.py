import shutil
import subprocess
import time

import psutil

from jarvis.tools import tool


def _gpu():
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=temperature.gpu,utilization.gpu,"
                              "memory.used,memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=5, check=True).stdout
        temp, util, used, total = (v.strip() for v in out.split(","))
        return f"{temp}°C, {util}% busy, {used} of {total} MB memory"
    except (OSError, subprocess.SubprocessError, ValueError):
        return "unavailable"


@tool("This computer's health: CPU temperature and load, GPU, memory, disk, uptime.")
def system_status():
    temps = psutil.sensors_temperatures()
    cpu = temps.get("k10temp") or temps.get("coretemp") or temps.get("acpitz") or []
    mem, disk = psutil.virtual_memory(), shutil.disk_usage("/")
    up = int(time.time() - psutil.boot_time())
    return {
        "cpu_temperature_c": round(max(t.current for t in cpu)) if cpu else None,
        "cpu_load_percent": psutil.cpu_percent(interval=0.5),
        "load_average_1m": round(psutil.getloadavg()[0], 2),
        "gpu": _gpu(),
        "memory": f"{mem.used / 2**30:.1f} of {mem.total / 2**30:.1f} GB used",
        "disk": f"{disk.free / 2**30:.0f} GB free of {disk.total / 2**30:.0f} GB",
        "uptime": f"{up // 86400} days {up % 86400 // 3600} hours {up % 3600 // 60} minutes",
    }
