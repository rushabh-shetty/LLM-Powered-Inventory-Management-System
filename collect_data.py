#!/usr/bin/env python3

import os
import sys
import json
import subprocess
import shutil
import time
import argparse
import re
from collections import OrderedDict
from tqdm import tqdm

# -------------------- Helpers --------------------

def locate_file(filename, default_paths=None, prompt_message=None):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if default_paths is None:
        default_paths = [
            os.path.join(script_dir, filename),
            os.path.expanduser(os.path.join("~", filename)),
        ]
    for path in default_paths:
        if os.path.exists(path) or os.access(os.path.dirname(path), os.W_OK):
            return path
    if prompt_message:
        print(prompt_message)
    while True:
        user_path = input(f"Enter path to '{filename}' (or press Enter to skip): ").strip()
        if not user_path:
            return None
        if os.path.exists(user_path) or os.access(os.path.dirname(user_path), os.W_OK):
            return user_path
        print(f"Error: File '{user_path}' not found or directory not writable. Try again or skip.")

def check_required_tools(tools):
    missing = [t for t in tools if shutil.which(t) is None]
    for tool in missing:
        install_cmd = {
            "sensors": "sudo apt install lm-sensors",
            "dmidecode": "sudo apt install dmidecode",
            "ipmitool": "sudo apt install ipmitool",
            "ethtool": "sudo apt install ethtool",
            "numactl": "sudo apt install numactl",
            "lscpu": "sudo apt install util-linux",
            "lspci": "sudo apt install pciutils",
            "lsusb": "sudo apt install usbutils",
            "perf": "sudo apt install linux-tools-common",
            "iostat": "sudo apt install sysstat",
            "nvidia-smi": "sudo apt install nvidia-driver",
            "rocm-smi": "sudo apt install rocm-smi-lib",
            "ping": "sudo apt install iputils-ping",
            "top": "sudo apt install procps",
            "free": "sudo apt install procps",
            "ip": "sudo apt install iproute2",
            "dpkg-query": "sudo apt install dpkg",
            "rpm": "sudo apt install rpm",
            "ps": "sudo apt install procps",
            "systemctl": "sudo apt install systemd",
            "service": "sudo apt install sysvinit-utils",
            "lsblk": "sudo apt install util-linux",
            "jq": "sudo apt install jq",
            "dpdk-devbind": "sudo apt install dpdk",
            "ibv_devinfo": "sudo apt install libibverbs-dev"
        }.get(tool, f"sudo apt install {tool}")
        print(f"Missing '{tool}'. Install with: {install_cmd}")
    return missing

def run_command(cmd, error_msg=None):
    try:
        return subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.STDOUT).strip()
    except subprocess.CalledProcessError:
        if error_msg:
            print(f"Warning: {error_msg}")
        return None

def is_valid_metric(metric):
    cmd = metric["command"]
    metric_type = metric["type"]
    output = run_command(cmd, error_msg=None)
    if output is None:  # Command failed (e.g., tool not found)
        return False
    if metric_type == "dynamic_single":  # Numeric metrics
        try:
            float(output.strip())
            return True
        except ValueError:
            return False
    else:  # static or dynamic_multi (e.g., rpm -qa)
        return bool(output.strip())  # True only if output is non-empty

def detect_nics():
    cmd = "ls /sys/class/net | grep -v lo"
    output = run_command(cmd, "No network interfaces detected")
    if output is None:
        return []
    interfaces = output.splitlines()
    valid_nics = []
    for iface in interfaces:
        if run_command(f"ethtool -i {iface}", error_msg=None) is not None:
            valid_nics.append(iface)
    return valid_nics

def detect_sensors():
    cmd = "sensors -j"
    output = run_command(cmd, error_msg=None)
    if output is None:
        print("Warning: No sensor data available")
        return {}
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        print("Warning: Failed to parse sensor data")
        return {}

def detect_gpus():
    gpus = []
    if run_command("nvidia-smi -L", error_msg=None) is not None:
        gpus.append("nvidia")
    if run_command("rocm-smi --showtemp --json", error_msg=None) is not None:
        gpus.append("amd")
    return gpus

def detect_disks():
    cmd = "lsblk -d -o NAME,TYPE | grep disk | awk '{print $1}'"
    output = run_command(cmd, "No physical disks detected")
    if output is None:
        return []
    return output.splitlines()

# -------------------- Discovery --------------------

def discover_components():
    print("🔍 Discovering system components...")
    return {
        "nics": detect_nics(),
        "sensors": detect_sensors(),
        "gpus": detect_gpus(),
        "disks": detect_disks()
    }

# -------------------- Metric Generation Functions --------------------

def is_relevant_stat(stat):
    keywords = ['rx', 'tx', 'drop', 'error', 'packet', 'byte']
    return any(re.search(keyword, stat, re.I) for keyword in keywords)

def generate_nic_metrics(iface):
    base_cmd = f"ethtool -S {iface}"
    base_output = run_command(base_cmd, error_msg=None)
    if base_output is None:
        return []
    stats = [line.split(':')[0].strip() for line in base_output.splitlines() if ':' in line]
    metrics = [
        {"name": f"{iface}_statistics", "tool": "ethtool", "command": base_cmd, "type": "dynamic_multi", "subsection": f"{iface} Network Statistics"}
    ]
    for stat in stats:
        if is_relevant_stat(stat):
            cmd = f"ethtool -S {iface} | grep -w '{stat}' | awk '{{print $2}}'"
            metrics.append({
                "name": f"{iface}_{stat.replace(' ', '_').lower()}",
                "tool": "ethtool",
                "command": cmd,
                "type": "dynamic_single",
                "subsection": f"{iface} {stat}"
            })
    return metrics

def generate_sensor_metrics():
    base_cmd = "sensors -j"
    base_output = run_command(base_cmd, error_msg=None)
    if base_output is None:
        return []
    try:
        sensors_data = json.loads(base_output)
    except json.JSONDecodeError:
        return []
    metrics = [
        {"name": "sensors_full", "tool": "sensors", "command": base_cmd, "type": "dynamic_multi", "subsection": "Environmental Parameters"}
    ]
    for chip, chip_data in sensors_data.items():
        for key, value in chip_data.items():
            if isinstance(value, dict):
                for subkey, subvalue in value.items():
                    if isinstance(subvalue, (int, float)):
                        metric_name = f"{chip}_{key}_{subkey}".replace(" ", "_").lower()
                        cmd = f"sensors -j | jq '.\"{chip}\".\"{key}\".\"{subkey}\"'"
                        metrics.append({
                            "name": metric_name,
                            "tool": "sensors",
                            "command": cmd,
                            "type": "dynamic_single",
                            "subsection": f"{chip} {key} {subkey}"
                        })
    return metrics

def generate_disk_metrics(disk):
    base_cmd = f"iostat -x 1 1 {disk}"
    base_output = run_command(base_cmd, error_msg=None)
    if base_output is None or disk not in base_output:
        return []
    metrics = [
        {"name": f"{disk}_io_stats", "tool": "iostat", "command": base_cmd, "type": "dynamic_multi", "subsection": f"{disk} I/O Statistics"}
    ]
    fields = [
        ("await", 10, "Average wait time"),
        ("r_await", 11, "Read wait time"),
        ("w_await", 12, "Write wait time"),
        ("%util", 14, "Utilization percentage")
    ]
    for field_name, col, description in fields:
        cmd = f"iostat -x 1 1 {disk} | awk '/^{disk}/ {{print ${col}}}'"
        metrics.append({
            "name": f"{disk}_{field_name}",
            "tool": "iostat",
            "command": cmd,
            "type": "dynamic_single",
            "subsection": f"{disk} {description}"
        })
    return metrics

def is_ipmi_supported():
    if shutil.which("ipmitool") is None:
        return False
    cmd = "ipmitool -I open chassis status"
    return run_command(cmd, error_msg=None) is not None

# -------------------- Config Generation --------------------

def generate_config(components):
    all_tools = [
        "lscpu", "top", "lspci", "sensors", "dmidecode", "free", "ip", "ethtool",
        "ipmitool", "lsusb", "numactl", "dpkg-query", "rpm", "ps", "systemctl",
        "service", "ping", "perf", "iostat", "nvidia-smi", "rocm-smi", "lsblk", "jq",
        "dpdk-devbind", "ibv_devinfo"
    ]
    missing_tools = check_required_tools(all_tools)
    comments = ["Generated by system_info.py"]
    if missing_tools:
        comments.append(f"Missing tools: {', '.join(missing_tools)}")
    comments.append("NIC documentation (primary page, specifications, manuals) available via Grok Q&A in upgrade_recommender.py")

    # Load existing custom metrics
    custom_metrics = []
    config_path = locate_file("metrics_config.json")
    if config_path and os.path.exists(config_path):
        try:
            with open(config_path, "r") as f:
                existing_config = json.load(f)
                custom_metrics = existing_config.get("custom_metrics", [])
            print(f"Loaded custom_metrics: {custom_metrics}")
        except Exception as e:
            print(f"Warning: Could not read existing config: {e}")

    # Predefined metrics with adjusted types
    predefined_metrics = {
        "SMBIOS / DMI": [
            {"name": "dmi_table", "tool": "dmidecode", "command": "dmidecode 2>/dev/null", "type": "static", "subsection": "Full DMI Table"}
        ],
        "CPU Info": [
            {"name": "cpu_info", "tool": "lscpu", "command": "lscpu", "type": "static", "subsection": "CPU Info"},
            {"name": "cpu_frequency", "tool": "lscpu", "command": "lscpu | grep -i 'cpu.*mhz' | awk '{print $3}'", "type": "dynamic_single", "subsection": "CPU Frequency"},
            {"name": "cpu_scaling_governor", "tool": "cat", "command": "cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null", "type": "dynamic_multi", "subsection": "CPU Scaling Governor"},
            {"name": "cpu_utilization", "tool": "top", "command": "top -bn1 | head -n3", "type": "dynamic_multi", "subsection": "CPU Utilization Snapshot"},
            {"name": "cpu_mitigations", "tool": "grep", "command": "grep . /sys/devices/system/cpu/vulnerabilities/* 2>/dev/null", "type": "static", "subsection": "CPU Mitigations"},
            {"name": "realtime_kernel", "tool": "uname", "command": "uname -r | grep -qi 'rt' && echo 'Real-time kernel detected' || echo 'Standard kernel'", "type": "static", "subsection": "Real-Time Kernel Check"},
            {"name": "cache_misses", "tool": "perf", "command": "perf stat -e cache-misses sleep 1 2>&1 | awk '/cache-misses/ {print $1}'", "type": "dynamic_single", "subsection": "Cache Misses"}
        ],
        "NIC Model Info": [
            {"name": "nic_model", "tool": "lspci", "command": "lspci | grep -i ethernet", "type": "static", "subsection": "NIC Model"},
            {"name": "nic_details", "tool": "lspci", "command": "lspci -v -s $(lspci | grep -i ethernet | awk '{print $1}') 2>/dev/null", "type": "static", "subsection": "NIC Details"},
            {"name": "nic_sriov", "tool": "lspci", "command": "lspci -v -s $(lspci | grep -i ethernet | awk '{print $1}') | grep -i sriov 2>/dev/null", "type": "static", "subsection": "SR-IOV Support"}
        ],
        "Memory Info": [
            {"name": "memory_hardware", "tool": "dmidecode", "command": "dmidecode -t memory 2>/dev/null", "type": "static", "subsection": "Hardware Memory Info"},
            {"name": "memory_utilization", "tool": "free", "command": "free -h", "type": "dynamic_multi", "subsection": "Memory Utilization"},
            {"name": "memory_details", "tool": "cat", "command": "cat /proc/meminfo", "type": "dynamic_multi", "subsection": "Memory Details"},
            {"name": "hugepages", "tool": "grep", "command": "grep HugePages /proc/meminfo", "type": "dynamic_multi", "subsection": "Hugepages"}
        ],
        "/proc Snapshots": [
            {"name": "proc_cpuinfo", "tool": "cat", "command": "cat /proc/cpuinfo", "type": "static", "subsection": "/proc/cpuinfo"},
            {"name": "proc_net_dev", "tool": "cat", "command": "cat /proc/net/dev", "type": "dynamic_multi", "subsection": "/proc/net_dev"}
        ],
        "NUMA Topology": [
            {"name": "numa_topology", "tool": "numactl", "command": "numactl -H", "type": "static", "subsection": "NUMA Topology"}
        ],
        "PCIe Devices per NUMA Node": [
            {"name": "pcie_numa", "tool": "lspci", "command": "lspci -vvv | awk '/^[0-9a-f]+:/{dev=$0} /NUMA node/{print dev \"\\n\" $0 \"\\n\"}'", "type": "static", "subsection": "PCIe Devices and NUMA Nodes"}
        ],
        "Installed Packages": [
            {"name": "debian_packages", "tool": "dpkg-query", "command": "dpkg-query -W", "type": "static", "subsection": "Debian/Ubuntu Packages"},
            {"name": "rpm_packages", "tool": "rpm", "command": "rpm -qa", "type": "static", "subsection": "RHEL/Fedora Packages"}
        ],
        "Running Processes": [
            {"name": "processes", "tool": "ps", "command": "ps aux", "type": "dynamic_multi", "subsection": "Processes"}
        ],
        "Service Status": [
            {"name": "systemd_services", "tool": "systemctl", "command": "systemctl list-units --type=service --all", "type": "static", "subsection": "systemd Services"},
            {"name": "sysv_services", "tool": "service", "command": "service --status-all 2>/dev/null", "type": "static", "subsection": "SysV Services"}
        ],
        "PCI Devices (Verbose)": [
            {"name": "pci_devices", "tool": "lspci", "command": "lspci -vvv", "type": "static", "subsection": "PCI Devices"}
        ],
        "USB Devices": [
            {"name": "usb_list", "tool": "lsusb", "command": "lsusb", "type": "static", "subsection": "USB Device List"},
            {"name": "usb_verbose", "tool": "lsusb", "command": "lsusb -v 2>/dev/null", "type": "static", "subsection": "USB Devices (Verbose)"}
        ],
        "Network Interface Configuration": [
            {"name": "ping_rtt", "tool": "ping", "command": "ping -c 1 google.com | grep 'time=' | awk '{print $7}' | cut -d'=' -f2", "type": "dynamic_single", "subsection": "Ping RTT"},
            {"name": "ping_loss", "tool": "ping", "command": "ping -c 1 google.com | grep 'packet loss' | awk '{print $6}'", "type": "dynamic_single", "subsection": "Ping Loss"}
        ],
        "NIC Firmware Info": [],
        "Disk Info": [],
        "GPU Info": [],
        "Custom Metrics": custom_metrics
    }

    # Filter predefined metrics
    sections = {}
    for title, metrics in predefined_metrics.items():
        sections[title] = [m for m in metrics if is_valid_metric(m)]

    # Add IPMI metrics if supported
    if is_ipmi_supported():
        ipmi_metrics = [
            {"name": "ipmi_sensors", "tool": "ipmitool", "command": "ipmitool sensor", "type": "dynamic_multi", "subsection": "IPMI Sensor Readings"},
            {"name": "ipmi_sel", "tool": "ipmitool", "command": "ipmitool sel list", "type": "dynamic_multi", "subsection": "IPMI SEL Log"},
            {"name": "ipmi_fru", "tool": "ipmitool", "command": "ipmitool fru", "type": "static", "subsection": "IPMI FRU Info"}
        ]
        sections["IPMI / BMC Data"] = [m for m in ipmi_metrics if is_valid_metric(m)]
    else:
        sections["IPMI / BMC Data"] = []

    # Add NIC model info with DPDK and RDMA if tools are present
    nic_model_metrics = []
    for iface in components["nics"]:
        if shutil.which("dpdk-devbind"):
            nic_model_metrics.append({
                "name": f"{iface}_dpdk_status",
                "tool": "dpdk-devbind",
                "command": f"dpdk-devbind.py --status | grep {iface} || echo 'No DPDK binding for {iface}'",
                "type": "static",
                "subsection": f"{iface} DPDK Status"
            })
        if shutil.which("ibv_devinfo"):
            nic_model_metrics.append({
                "name": f"{iface}_rdma_info",
                "tool": "ibv_devinfo",
                "command": f"ibv_devinfo | grep {iface} || echo 'No RDMA support for {iface}'",
                "type": "static",
                "subsection": f"{iface} RDMA Info"
            })
    sections["NIC Model Info"] += nic_model_metrics

    # Add environmental parameters
    if components["sensors"]:
        sections["Environmental Parameters"] = generate_sensor_metrics()
    else:
        fallback_metrics = [
            {"name": "cpu_temp", "tool": "sensors", "command": "sensors | awk '/Package id 0:/ {print $4}'", "type": "dynamic_single", "subsection": "CPU Temperature"},
            {"name": "cpu_vcore", "tool": "sensors", "command": "sensors | awk '/Vcore:/ {print $2}'", "type": "dynamic_single", "subsection": "CPU Vcore"},
            {"name": "fan1_speed", "tool": "sensors", "command": "sensors | awk '/[Ff]an1:/ {print $2}'", "type": "dynamic_single", "subsection": "Fan1 Speed"}
        ]
        sections["Environmental Parameters"] = [m for m in fallback_metrics if is_valid_metric(m)]

    # Add NIC configuration metrics
    nic_config_metrics = []
    for iface in components["nics"]:
        nic_config_metrics.extend(generate_nic_metrics(iface))
        for cmd_type, subsection in [
            (f"ethtool {iface}", "General Settings"),
            (f"ethtool -k {iface}", "Offload Features"),
            (f"ethtool -c {iface}", "IRQ Coalescing"),
            (f"ethtool -g {iface}", "Ring Buffers"),
            (f"cat /proc/interrupts | grep -E '{iface}|virtio[0-9]'", "Interrupt Distribution"),
        ]:
            cmd = cmd_type
            nic_config_metrics.append({
                "name": f"{iface}_{subsection.lower().replace(' ', '_')}",
                "tool": "ethtool" if "ethtool" in cmd_type else "cat",
                "command": cmd,
                "type": "dynamic_multi",
                "subsection": f"{iface} {subsection}"
            })
        sections["NIC Firmware Info"].append({
            "name": f"{iface}_firmware",
            "tool": "ethtool",
            "command": f"ethtool -i {iface}",
            "type": "static",
            "subsection": f"{iface} NIC Firmware Info"
        })
    sections["Network Interface Configuration"] += nic_config_metrics

    # Add disk metrics
    disk_metrics = []
    for disk in components["disks"]:
        disk_metrics.extend(generate_disk_metrics(disk))
    sections["Disk Info"] = disk_metrics

    # Add GPU metrics
    gpu_metrics = []
    for gpu_type in components["gpus"]:
        if gpu_type == "nvidia":
            gpu_metrics.append({
                "name": "nvidia_gpu_temp", "tool": "nvidia-smi", "command": "nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader", "type": "dynamic_single", "subsection": "NVIDIA GPU Temperature"}
            )
        elif gpu_type == "amd":
            gpu_metrics.append({
                "name": "amd_gpu_temp", "tool": "rocm-smi", "command": "rocm-smi --showtemp --json | jq '.[] | .temperature'", "type": "dynamic_single", "subsection": "AMD GPU Temperature"}
            )
    sections["GPU Info"] = gpu_metrics

    # Generate config
    config = {
        "comments": comments,
        "custom_metrics": custom_metrics,
        "sections": [{"title": title, "metrics": metrics} for title, metrics in sections.items()]
    }
    print(f"Saving config with custom_metrics: {config['custom_metrics']}")
    config_path = locate_file("metrics_config.json", prompt_message="Cannot write to default paths for metrics_config.json.")
    if not config_path:
        print("Error: No valid output path for metrics_config.json.")
        sys.exit(1)
    temp_path = config_path + ".tmp"
    try:
        with open(temp_path, "w") as f:
            json.dump(config, f, indent=2)
        shutil.move(temp_path, config_path)
    except Exception as e:
        print(f"Error: Failed to write metrics_config.json: {e}")
        sys.exit(1)
    print(f"✅ Generated metrics_config.json at {config_path}")
    return config

# -------------------- Collection --------------------

def collect_metrics(config, output_path):
    tools = set(metric["tool"] for section in config["sections"] for metric in section["metrics"])
    missing_tools = [tool for tool in tools if shutil.which(tool) is None]
    if missing_tools:
        print(f"Missing tools: {', '.join(missing_tools)}. Some metrics may not be collected.")

    try:
        open(output_path, "w").close()
    except Exception as e:
        print(f"Error: Cannot write to {output_path}: {e}")
        sys.exit(1)

    with open(output_path, "a") as out:
        out.write(f"System metrics collection started at {time.ctime()}\n")

    summary = []
    for section in tqdm(config["sections"], desc="Collecting sections"):
        with open(output_path, "a") as out:
            out.write(f"\n=== {section['title']} ===\n")
        if not section["metrics"]:
            with open(output_path, "a") as out:
                out.write("No applicable metrics found.\n")
        else:
            for metric in section["metrics"]:
                with open(output_path, "a") as out:
                    out.write(f"\n--- {metric['subsection']} ---\n")
                cmd = metric["command"]
                tool = metric["tool"]
                if shutil.which(tool) is None:
                    with open(output_path, "a") as out:
                        out.write(f"Tool '{tool}' not found.\n")
                    summary.append([section["title"], metric["subsection"], cmd, "Failed (Tool not found)"])
                    continue
                output = run_command(cmd, error_msg=None)
                if output is not None:
                    with open(output_path, "a") as out:
                        out.write(output + "\n")
                    summary.append([section["title"], metric["subsection"], cmd, "Success"])
                else:
                    with open(output_path, "a") as out:
                        out.write("N/A\n")
                    summary.append([section["title"], metric["subsection"], cmd, "Failed (Command failed)"])

    print("\n=== Data Collection Summary ===")
    current_section = None
    for title, subsection, cmd, status in summary:
        if title != current_section:
            if current_section is not None:
                print()
            print(f"**{title}**")
            current_section = title
        icon = "✅" if "Success" in status else "❌"
        reason = status.replace("Failed ", "") if "Failed" in status else ""
        print(f"{subsection}\n  {icon} {cmd}{(' (' + reason + ')') if reason else ''}")
    print(f"\n✅ Data collection complete. Output saved to {output_path}")

# -------------------- Main --------------------

def main():
    parser = argparse.ArgumentParser(description="Discover system components and collect metrics.")
    parser.add_argument("--output", help="Path to output file (default: system_info.txt)")
    args = parser.parse_args()

    output_path = args.output if args.output else locate_file(
        "system_info.txt",
        prompt_message="Could not locate system_info.txt in default paths."
    )
    if not output_path:
        print("Error: No output path specified for system_info.txt.")
        sys.exit(1)

    components = discover_components()
    config = generate_config(components)
    collect_metrics(config, output_path)

if __name__ == "__main__":
    main()