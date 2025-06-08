#!/usr/bin/env python3

import os
import sys
import json
import time
import signal
import re
from datetime import datetime
from collections import deque
from statistics import mean, stdev
import subprocess
import threading
import requests
from colorama import init, Fore, Style

# Server integration
def build_menu_tree():
    settings = load_json(DEFAULT_SETTINGS_PATH, {"general": {}, "metrics": []})
    monitoring_active = globals().get('monitoring_active', False)
    menu = {
        "name": "Main Menu",
        "items": []
    }
    if monitoring_active:
        menu["items"] = [
            {"name": "Continue monitoring", "action": "continue_monitoring"},
            {"name": "Stop monitoring", "action": "stop_monitoring"},
            {"name": "Exit", "action": "exit"}
        ]
    else:
        menu["items"] = [
            {"name": "Start monitoring", "action": "start_monitoring"},
            {
                "name": "Configure settings",
                "items": [
                    {"name": "General settings", "action": "configure_general"},
                    {"name": "Enable/disable metrics", "action": "configure_metrics"},
                    {"name": "Manage thresholds", "action": "configure_thresholds"},
                    {"name": "Reset configuration", "action": "reset_config"}
                ]
            },
            {"name": "List available metrics", "action": "list_metrics"},
            {"name": "View logs", "action": "view_logs"},
            {"name": "Check status", "action": "check_status"},
            {"name": "Exit", "action": "exit"}
        ]
    return menu

def handle_monitor_action(action):
    global running, monitoring_active, background_monitor_thread, metrics_history
    settings = load_json(DEFAULT_SETTINGS_PATH, {"general": {}, "metrics": []})
    metrics_config = load_json(METRICS_CONFIG_PATH, {"sections": [{"metrics": []}]})

    if action == "start_monitoring":
        if check_existing_instance():
            return {"success": False, "output": "Another instance is running."}
        if not [m["name"] for m in settings.get("metrics", []) if m.get("enabled")]:
            return {"success": False, "output": "No metrics enabled."}
        running = True
        monitoring_active = True
        with open(DEFAULT_PID_PATH, 'w') as f:
            f.write(str(os.getpid()))
        background_monitor_thread = threading.Thread(target=background_monitor_loop, args=(metrics_config, metrics_history))
        background_monitor_thread.start()
        return {"success": True, "output": "Monitoring started."}

    elif action == "stop_monitoring":
        running = False
        monitoring_active = False
        if background_monitor_thread and background_monitor_thread.is_alive():
            background_monitor_thread.join()
        background_monitor_thread = None
        if os.path.exists(DEFAULT_PID_PATH):
            os.remove(DEFAULT_PID_PATH)
        return {"success": True, "output": "Monitoring stopped."}

    elif action == "configure_general":
        return {
            "success": True,
            "output": {
                "form": [
                    {"name": "interval", "type": "number", "value": settings["general"].get("interval", 5)},
                    {"name": "max_rows", "type": "number", "value": settings["general"].get("max_rows", 5)},
                    {"name": "ping_hosts", "type": "text", "value": ",".join(settings["general"].get("ping_hosts", ["google.com"]))}
                ]
            }
        }

    elif action.startswith("update_general"):
        data = json.loads(action.split(":", 1)[1]) if ":" in action else {}
        settings["general"].update(data)
        save_json(settings, DEFAULT_SETTINGS_PATH)
        return {"success": True, "output": "General settings updated."}

    elif action == "configure_metrics":
        dynamic_metrics = [
            m["name"] for section in metrics_config.get("sections", [])
            for m in section.get("metrics", []) if m.get("type") == "dynamic_single"
        ]
        form = []
        for metric in dynamic_metrics:
            enabled = any(m["name"] == metric and m["enabled"] for m in settings["metrics"])
            form.append({"name": "metric", "type": "checkbox", "value": enabled})
        return {"success": True, "output": {"form": form}}

    elif action.startswith("update_metrics"):
        data = json.loads(action.split(":", 1)[1]) if ":" in action else {}
        dynamic_metrics = [
            m["name"] for section in metrics_config.get("sections", [])
            for m in section.get("metrics", []) if m.get("type") == "dynamic_single"
        ]
        for metric in dynamic_metrics:
            for m in settings["metrics"]:
                if m["name"] == metric:
                    m["enabled"] = data.get(metric, False)
        save_json(settings, DEFAULT_SETTINGS_PATH)
        return {"success": True, "output": "Metrics updated."}

    return {"success": False, "output": f"Unknown action: {action}"}

def get_metrics_history():
    return list(metrics_history)

# Initialize colorama for colored output
init()

# Constants
DEFAULT_SETTINGS_PATH = "monitor_settings.json"
DEFAULT_LOG_PATH = "monitor_log.json"
DEFAULT_PID_PATH = "m_monitor_system.pid"
METRICS_CONFIG_PATH = "metrics_config.json"
INTERVAL = 5      # Default seconds between updates
MAX_ROWS = 5      # Default number of rows in table
INITIAL_DELAY = 1  # Seconds for initial table fill
API_KEY_PATH = "api_key.txt"

# Global variables
running = True
background_monitor_thread = None
monitoring_active = False
metrics_history = deque(maxlen=100)
warned_metrics = set()

# Signal handler
def signal_handler(sig, frame):
    global running, background_monitor_thread, monitoring_active
    running = False
    monitoring_active = False
    if background_monitor_thread:
        background_monitor_thread.join()
    if os.path.exists(DEFAULT_PID_PATH):
        os.remove(DEFAULT_PID_PATH)
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# Helpers
def check_existing_instance():
    if os.path.exists(DEFAULT_PID_PATH):
        try:
            with open(DEFAULT_PID_PATH, 'r') as f:
                pid = int(f.read().strip())
            if pid != os.getpid():  # Exclude current process
                os.kill(pid, 0)  # Check if process is running
                return pid
            return pid
        except (ValueError, OSError):
            # Invalid PID or process not running, remove stale PID file
            os.remove(DEFAULT_PID_PATH)
    return None

def locate_file(filename, default_paths=None):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if default_paths is None:
        default_paths = [os.path.join(script_dir, filename)]
    for path in default_paths:
        if os.path.exists(path):
            return path
    return None

def parse_system_info(file_path):
    sections = {}
    current_section = None
    section_content = []
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.rstrip()
                if line.startswith("===") and line.endswith("==="):
                    if current_section and section_content:
                        sections[current_section] = "\n".join(section_content).strip()
                    current_section = line.strip("=").strip()
                    section_content = []
                elif current_section:
                    section_content.append(line)
            if current_section and section_content:
                sections[current_section] = "\n".join(section_content).strip()
    except Exception as e:
        print(f"Error parsing system_info.txt: {e}")
        return {}
    return sections

def load_json(path, default=None):
    if not path or not os.path.exists(path):
        return default if default is not None else {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading {path}: {e}")
        return default if default is not None else {}

def save_json(data, path):
    try:
        temp_path = path + ".tmp"
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        os.replace(temp_path, path)
    except Exception as e:
        print(f"Error writing {path}: {e}")

def run_command(cmd, error_msg="Command failed"):
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            shell=True  # Enable shell to support pipelines
        )
        if proc.returncode != 0:
            print(f"Warning: {error_msg} - Return code: {proc.returncode}, Error: {proc.stderr.strip()}")
            return ""
        return proc.stdout.strip()
    except subprocess.SubprocessError as e:
        print(f"Warning: {error_msg} - {e}")
        return ""

def load_api_key():
    api_key_path = locate_file(API_KEY_PATH)
    if api_key_path:
        try:
            with open(api_key_path, 'r') as f:
                return f.read().strip()
        except Exception as e:
            print(f"Error reading API key: {e}")
    print("API key not found. Please provide your xAI Grok API key.")
    api_key = input("Enter API key: ").strip()
    if api_key:
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            api_key_path = os.path.join(script_dir, API_KEY_PATH)
            with open(api_key_path, 'w') as f:
                f.write(api_key)
            print(f"API key saved to {api_key_path}")
            return api_key
        except Exception as e:
            print(f"Error saving API key: {e}")
    return None

# Metric Parsing
def parse_metric_output(name, output):
    if name == "ping_rtt":
        if not output:
            return None
        match = re.search(r"time=([\d\.]+)\s*ms", output)
        return float(match.group(1)) if match else None
    if not output:
        return None
    parsers = {
        "cpu_temp": lambda out: int(re.search(r'Package id 0:\s+\+?([0-9]+)°C', out).group(1)) if re.search(r'Package id 0:\s+\+?([0-9]+)°C', out) else None,
        "fan1_speed": lambda out: int(re.search(r'fan1:\s+(\d+)', out).group(1)) if re.search(r'fan1:\s+(\d+)', out) else None,
        "enp0s1_drops": lambda out: int(out.strip()) if out.strip().isdigit() else None,
        "enp0s1_rx_queue_0_packets": lambda out: int(out.strip()) if out.strip().isdigit() else None,
        "enp0s1_rx_queue_0_bytes": lambda out: int(out.strip()) if out.strip().isdigit() else None,
        "enp0s1_rx_queue_0_drops": lambda out: int(out.strip()) if out.strip().isdigit() else None,
        "enp0s1_rx_queue_0_kicks": lambda out: int(out.strip()) if out.strip().isdigit() else None
    }
    parser = parsers.get(name, lambda out: (float(out.strip()) if out.strip().replace('.', '', 1).replace('-', '', 1).isdigit() else int(out.strip()) if out.strip().isdigit() else None))
    value = parser(output)
    if value is None and output:
        print(f"Debug: Failed to parse {name}. Raw output: '{output}'")
    return value

# Metric Collection
def collect_metrics(settings, metrics_config):
    metrics = {}
    violations = []
    enabled_metrics = [m["name"] for m in settings.get("metrics", []) if m.get("enabled")]
    if not enabled_metrics:
        return {}, []
    for section in metrics_config.get("sections", [{}]):
        for metric in section.get("metrics", []):
            if metric.get("type") == "dynamic_single" and metric["name"] in enabled_metrics:
                name = metric["name"]
                if name == "ping_rtt":
                    host = settings["general"].get("ping_hosts", ["google.com"])[0]
                    cmd = f"ping -c 1 {host}"
                    output = run_command(cmd, f"Failed to collect {name}")
                else:
                    cmd = metric.get("command")
                    if not cmd:
                        print(f"Error: No command defined for metric {name}")
                        continue
                    output = run_command(cmd, f"Failed to collect {name}")
                if output:
                    if name == "ping_rtt" and ("unreachable" in output.lower() or "100% packet loss" in output.lower()):
                        print(f"Warning: Ping host {host} unreachable.")
                        value = None
                    else:
                        value = parse_metric_output(name, output)
                    if value is not None:
                        metrics[name] = value
                        thresholds = next((m["thresholds"] for m in settings["metrics"] if m["name"] == name), {})
                        if thresholds.get("max") is not None and value > thresholds["max"]:
                            violations.append({"metric": name, "value": value, "threshold": f"max={thresholds['max']}"})
                        if thresholds.get("min") is not None and value < thresholds["min"]:
                            violations.append({"metric": name, "value": value, "threshold": f"min={thresholds['min']}"})
                else:
                    print(f"Warning: No output for metric {name}")
    return metrics, violations

# Display
def print_table(metrics_history, enabled_metrics, settings, terminal_history):
    os.system('clear')
    
    for line in terminal_history:
        print(line)
    
    metrics_config = load_json(METRICS_CONFIG_PATH, {"sections": [{"metrics": []}]})
    units = {}
    for section in metrics_config.get("sections", []):
        for metric in section.get("metrics", []):
            if metric["name"] in enabled_metrics:
                units[metric["name"]] = metric.get("unit", "")
    
    col_widths = [len("Timestamp")]
    for name in enabled_metrics:
        header = f"{name}" if not units.get(name) else f"{name} ({units[name]})"
        values = [str(entry["metrics"].get(name, "N/A")) for entry in metrics_history]
        formatted_values = []
        for entry in metrics_history:
            value = entry["metrics"].get(name)
            if value is None:
                formatted_values.append("N/A")
            else:
                violation = next((v for v in entry.get("violations", []) if v["metric"] == name), None)
                if violation:
                    formatted_values.append(f"{value:.2f} [{violation['threshold']}]")
                else:
                    formatted_values.append(f"{value:.2f}")
        max_len = max(
            len(header),
            max([len(v) for v in formatted_values] + [len("N/A")], default=len("N/A"))
        )
        col_widths.append(max_len)
    
    if not enabled_metrics:
        print("\nMonitor (Press Space to exit)\n")
        print("No metrics enabled. Configure settings to enable metrics.")
        return
    
    if not metrics_history and monitoring_active:
        print("\nMonitor (Press Space to exit)\n")
        print("Initializing data collection... Please wait.")
        return
    
    print("\nMonitor (Press Space to exit)\n")
    
    headers = ["Timestamp"] + [
        f"{name}" if not units.get(name) else f"{name} ({units[name]})"
        for name in enabled_metrics
    ]
    print(" ".join(f"{h:<{w}}" for h, w in zip(headers, col_widths)))
    print("-" * sum(w + 1 for w in col_widths))
    
    max_rows = settings.get("general", {}).get("max_rows", MAX_ROWS)
    data_rows = list(metrics_history)[-max_rows:]
    for i in range(max_rows):
        if i < len(data_rows):
            entry = data_rows[i]
            row = [entry["timestamp"]]
            for name in enabled_metrics:
                value = entry["metrics"].get(name)
                if value is None:
                    row.append("N/A")
                else:
                    violation = next((v for v in entry.get("violations", []) if v["metric"] == name), None)
                    if violation:
                        row.append(f"{Fore.RED}{value:.2f} [{violation['threshold']}]{Style.RESET_ALL}")
                    else:
                        row.append(f"{value:.2f}")
        else:
            row = [""] * (len(enabled_metrics) + 1)
        print(" ".join(f"{v:<{w}}" for v, w in zip(row, col_widths)))
    
    stats = {"Mean": {}, "Std": {}, "Max": {}, "Min": {}}
    for name in enabled_metrics:
        values = [e["metrics"].get(name) for e in metrics_history if e["metrics"].get(name) is not None]
        if values:
            stats["Mean"][name] = mean(values)
            stats["Std"][name] = stdev(values) if len(values) > 1 else 0.0
        else:
            stats["Mean"][name] = "N/A"
            stats["Std"][name] = "N/A"
        thresholds = next((m["thresholds"] for m in settings["metrics"] if m["name"] == name), {})
        stats["Max"][name] = thresholds.get("max", "-")
        stats["Min"][name] = thresholds.get("min", "-")
    
    for stat in ["Mean", "Std", "Max", "Min"]:
        row = [stat + ":"]
        for name in enabled_metrics:
            value = stats[stat].get(name)
            row.append(f"{value:.2f}" if isinstance(value, (int, float)) else str(value))
        print(" ".join(f"{v:<{w}}" for v, w in zip(row, col_widths)))

# Logging
def log_metrics(metrics, violations, settings, metrics_history):
    log_entry = {
        "timestamp": datetime.now().strftime("%H:%M:%S"),
        "metrics": metrics,
        "thresholds": {m["name"]: m["thresholds"] for m in settings.get("metrics", [])},
        "violations": violations,
        "mean": {},
        "std": {}
    }
    for name in metrics:
        values = [e["metrics"].get(name) for e in list(metrics_history)[-5:] if e["metrics"].get(name) is not None]
        if values:
            log_entry["mean"][name] = mean(values)
            log_entry["std"][name] = stdev(values) if len(values) > 1 else 0.0
    log_data = load_json(DEFAULT_LOG_PATH, [])
    log_data.append(log_entry)
    save_json(log_data, DEFAULT_LOG_PATH)

# Monitoring Loops
def background_monitor_loop(metrics_config, metrics_history):
    global running
    while running:
        settings = load_json(DEFAULT_SETTINGS_PATH, {"general": {}, "metrics": []})
        metrics, violations = collect_metrics(settings, metrics_config)
        metrics_history.append({
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "metrics": metrics,
            "violations": violations
        })
        log_metrics(metrics, violations, settings, metrics_history)
        time.sleep(settings.get("general", {}).get("interval", INTERVAL))

def display_monitor_loop(settings, metrics_config, metrics_history, terminal_history):
    enabled_metrics = [m["name"] for m in settings.get("metrics", []) if m.get("enabled")]
    
    time.sleep(INITIAL_DELAY)
    
    while True:
        print_table(metrics_history, enabled_metrics, settings, terminal_history)
        
        start_time = time.time()
        input_check_interval = 0.5
        full_interval = settings.get("general", {}).get("interval", INTERVAL)
        elapsed = 0
        
        while elapsed < full_interval:
            remaining = min(input_check_interval, full_interval - elapsed)
            try:
                import msvcrt
                if msvcrt.kbhit() and msvcrt.getch() == b' ':
                    return
            except ImportError:
                import termios, tty, select
                old_settings = termios.tcgetattr(sys.stdin)
                tty.setcbreak(sys.stdin.fileno())
                r, _, _ = select.select([sys.stdin], [], [], remaining)
                if r and sys.stdin.read(1) == ' ':
                    termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
                    return
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
            
            time.sleep(remaining)
            elapsed = time.time() - start_time

# Configuration Wizard
def run_config_wizard(metrics_config):
    settings = {
        "general": {},
        "metrics": []
    }
    settings["general"]["interval"] = float(input("Monitoring interval (seconds, default: 5): ") or 5)
    settings["general"]["max_rows"] = int(input("Max table rows (default: 5): ") or 5)
    settings["general"]["ping_hosts"] = input("Ping hosts (comma-separated, default: google.com): ").split(",") or ["google.com"]
    
    # Use metrics_config.json to populate metrics
    dynamic_metrics = [
        m["name"] for section in metrics_config.get("sections", [])
        for m in section.get("metrics", []) if m.get("type") == "dynamic_single"
    ]
    print("Available metrics:", ", ".join(dynamic_metrics))
    
    for metric in dynamic_metrics:
        thresholds = {"alert": True, "max": 100.0 if metric == "ping_rtt" else None, "min": 0.0 if metric == "ping_rtt" else None}
        settings["metrics"].append({
            "name": metric,
            "enabled": metric == "ping_rtt",  # Enable ping_rtt by default
            "thresholds": thresholds
        })
    
    save_json(settings, DEFAULT_SETTINGS_PATH)
    return settings

def regenerate_monitor_settings():
    metrics_config = load_json(METRICS_CONFIG_PATH)
    settings = load_json(DEFAULT_SETTINGS_PATH, {"general": {"interval": 5, "max_rows": 5, "ping_hosts": ["google.com"]}, "metrics": []})
    general_settings = settings.get("general", {"interval": 5, "max_rows": 5, "ping_hosts": ["google.com"]})
    settings = {
        "general": general_settings,
        "metrics": []
    }
    dynamic_metrics = [
        m["name"] for section in metrics_config.get("sections", [])
        for m in section.get("metrics", []) if m.get("type") == "dynamic_single"
    ]
    for metric in dynamic_metrics:
        thresholds = {"alert": True, "max": 100.0 if metric == "ping_rtt" else None, "min": 0.0 if metric == "ping_rtt" else None}
        settings["metrics"].append({
            "name": metric,
            "enabled": metric == "ping_rtt",
            "thresholds": thresholds
        })
    save_json(settings, DEFAULT_SETTINGS_PATH)
    return {"success": True, "message": "monitor_settings.json regenerated."}

# Grok Integration
def get_hardware_context(metric_name):
    # First, try to read from system_info.txt
    system_info_path = locate_file("system_info.txt", default_paths=[
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "system_info.txt"),
        os.path.expanduser(os.path.join("~", "system_info.txt"))
    ])
    context = []
    
    if system_info_path:
        sections = parse_system_info(system_info_path)
        # Extract CPU details
        if "CPU Info" in sections:
            cpu_lines = sections["CPU Info"].splitlines()
            for line in cpu_lines:
                if "Model name" in line:
                    cpu_model = line.split(":", 1)[1].strip()
                    context.append(f"CPU: {cpu_model}")
                elif "CPU(s):" in line:
                    cores = line.split(":", 1)[1].strip()
                    context.append(f"Cores: {cores}")
        # Extract NIC details if metric is NIC-related
        if "enp0s1" in metric_name and "NIC Model Info" in sections:
            nic_lines = sections["NIC Model Info"].splitlines()
            for line in nic_lines:
                if "Ethernet" in line:
                    nic_info = line.strip()
                    context.append(f"NIC: {nic_info}")
                elif "Speed" in line:
                    nic_speed = line.split(":", 1)[1].strip()
                    context.append(f"NIC Speed: {nic_speed}")
    
    # Fallback to system commands if system_info.txt is unavailable
    if not context:
        try:
            cpu_info = run_command("lscpu | grep 'Model name'", "Failed to get CPU info")
            cpu_model = cpu_info.split(":")[-1].strip() if cpu_info else "Unknown CPU"
            context.append(f"CPU: {cpu_model}")
            if "enp0s1" in metric_name:
                nic_info = run_command("ethtool enp0s1 | grep 'Speed'", "Failed to get NIC info")
                nic_speed = nic_info.split(":")[-1].strip() if nic_info else "Unknown NIC"
                context.append(f"NIC: enp0s1 ({nic_speed})")
        except Exception as e:
            context.append(f"Unknown hardware (error: {e})")
    
    return ", ".join(context) if context else "Unknown hardware"

def fetch_thresholds_from_grok(metric_name, hardware):
    api_key_path = locate_file("api_key.txt")
    if api_key_path:
        try:
            with open(api_key_path, 'r') as f:
                api_key = f.read().strip()
        except Exception as e:
            print(f"Error reading API key: {e}")
            raise Exception("Failed to read API key from api_key.txt")
    else:
        raise Exception("API key file not found. Ensure api_key.txt exists.")
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    prompt = (
        f"Provide recommended thresholds for monitoring the metric '{metric_name}' on the following hardware: {hardware}. "
        f"Return ONLY a JSON object with 'max' and 'min' fields (as floats) and a 'reference' field (as a valid URL, e.g., https://example.com) "
        f"pointing to a relevant system-specific source (e.g., hardware vendor documentation, NIC performance standards, or industry benchmarks). "
        f"Example: {{\"max\": 400000.0, \"min\": 100000.0, \"reference\": \"https://www.intel.com/content/www/us/en/support/articles/000058835.html\"}}."
    )
    payload = {
        "model": "grok-3",
        "messages": [{"role": "user", "content": prompt}],
        "stream": True
    }
    
    try:
        response = requests.post("https://api.x.ai/v1/chat/completions", headers=headers, json=payload, stream=True)
        response.raise_for_status()
        full_content = ""
        for chunk in response.iter_lines():
            if chunk:
                chunk_data = chunk.decode("utf-8")
                if chunk_data.startswith("data: "):
                    chunk_json = chunk_data[6:]
                    if chunk_json.strip() == "[DONE]":
                        break
                    try:
                        chunk_obj = json.loads(chunk_json)
                        if "choices" in chunk_obj and chunk_obj["choices"]:
                            content = chunk_obj["choices"][0].get("delta", {}).get("content", "")
                            if content:
                                full_content += content
                    except json.JSONDecodeError:
                        continue
        threshold_data = json.loads(full_content)
        if "max" in threshold_data and "min" in threshold_data and "reference" in threshold_data:
            reference = threshold_data["reference"]
            if not (reference.startswith("http://") or reference.startswith("https://")):
                print(f"Warning: Reference '{reference}' is not a valid URL.")
            return {
                "thresholds": {
                    "max": float(threshold_data["max"]),
                    "min": float(threshold_data["min"])
                },
                "reference": reference
            }
        else:
            raise ValueError("Invalid threshold format from Grok. Missing 'max', 'min', or 'reference'.")
    
    except requests.RequestException as e:
        if "401" in str(e):
            raise Exception("Invalid API key. Please verify your key in api_key.txt or regenerate it from the xAI console.")
        raise Exception(f"API request failed: {e}")
    except (json.JSONDecodeError, ValueError, KeyError) as e:
        raise Exception(f"Failed to parse Grok response: {e}")

# Menu System
def threshold_menu(settings, metrics_config, metric_name=None):
    target_metrics = [m for m in settings.get("metrics", []) if m.get("enabled") and (metric_name is None or m["name"] == metric_name)]
    while True:
        print("\nManage Thresholds:")
        print("1. View thresholds")
        for i, metric in enumerate(target_metrics, 2):
            print(f"{i}. Edit threshold for {metric['name']}")
        print(f"{len(target_metrics) + 2}. Back")
        print(f"{len(target_metrics) + 3}. Back to Main Menu")
        choice = input("Choice: ").strip()
        
        if choice == "1":
            print("\nCurrent thresholds:")
            for metric in settings.get("metrics", []):
                if metric in target_metrics:
                    print(f"{metric['name']}: {metric['thresholds']}")
        elif choice.isdigit() and 2 <= int(choice) < len(target_metrics) + 2:
            metric = target_metrics[int(choice) - 2]
            while True:
                print(f"\nEdit threshold for {metric['name']}:")
                print("1. Manually edit\n2. Use Grok\n3. Back\n4. Back to Main Menu")
                sub_choice = input("Choice: ").strip()
                if sub_choice == "1":
                    thresholds = metric["thresholds"]
                    max_val = input(f"Enter max (default: {thresholds.get('max', '-')}): ").strip()
                    min_val = input(f"Enter min (default: {thresholds.get('min', '-')}): ").strip()
                    thresholds = {
                        "max": float(max_val) if max_val else thresholds.get("max"),
                        "min": float(min_val) if min_val else thresholds.get("min"),
                        "alert": True
                    }
                    for m in settings["metrics"]:
                        if m["name"] == metric["name"]:
                            m["thresholds"] = thresholds
                    save_json(settings, DEFAULT_SETTINGS_PATH)
                    print(f"Threshold for {metric['name']} updated.")
                elif sub_choice == "2":
                    try:
                        hardware = get_hardware_context(metric["name"])
                        grok_data = fetch_thresholds_from_grok(metric["name"], hardware)
                        if grok_data:
                            thresholds = grok_data["thresholds"]
                            reference = grok_data["reference"]
                            thresholds["alert"] = True
                            print(f"Grok suggested thresholds: {thresholds}")
                            print(f"Reference: {reference}")
                            print(f"Please validate these thresholds using the reference provided above.")
                            if input("Save these thresholds? (y/n): ").strip().lower() == 'y':
                                for m in settings["metrics"]:
                                    if m["name"] == metric["name"]:
                                        m["thresholds"] = thresholds
                                save_json(settings, DEFAULT_SETTINGS_PATH)
                                print(f"Threshold for {metric['name']} updated.")
                        else:
                            print("Error: Grok could not provide thresholds. Please edit manually.")
                    except Exception as e:
                        print(f"API unavailable—please enter threshold manually. (Error: {e})")
                elif sub_choice == "3":
                    break
                elif sub_choice == "4":
                    return "main_menu"  # Signal to jump to main menu
                else:
                    print("Invalid choice.")
        elif choice == str(len(target_metrics) + 2):
            return "parent"  # Signal to return to parent menu
        elif choice == str(len(target_metrics) + 3):
            return "main_menu"  # Signal to jump to main menu
        else:
            print("Invalid choice.")
    return "parent"  # Default return to parent menu

def configure_menu(settings, metrics_config):
    while True:
        print("\nConfigure Settings:")
        print("1. General settings\n2. Enable/disable metrics\n3. Manage thresholds\n4. Reset configuration")
        print("5. Back")
        choice = input("Choice: ").strip()
        
        if choice == "1":
            settings["general"]["interval"] = float(input("Monitoring interval (seconds, default: 5): ") or 5)
            settings["general"]["max_rows"] = int(input("Max table rows (default: 5): ") or 5)
            settings["general"]["ping_hosts"] = input("Ping hosts (comma-separated, default: google.com): ").split(",") or ["google.com"]
            save_json(settings, DEFAULT_SETTINGS_PATH)
            print("General settings updated.")
        elif choice == "2":
            dynamic_metrics = [m["name"] for section in metrics_config.get("sections", []) for m in section.get("metrics", []) if m.get("type") == "dynamic_single"]
            while True:
                print("\nEnable/Disable Metrics:")
                for i, metric in enumerate(dynamic_metrics, 1):
                    enabled = any(m["name"] == metric and m["enabled"] for m in settings["metrics"])
                    print(f"{i}. {metric} [{'x' if enabled else ' '}]")
                print(f"{len(dynamic_metrics) + 1}. Back")
                print(f"{len(dynamic_metrics) + 2}. Back to Main Menu")
                choice = input("Select metric to toggle (1-{}): ".format(len(dynamic_metrics) + 2)).strip()
        
                if choice.isdigit() and 1 <= int(choice) <= len(dynamic_metrics):
                    metric = dynamic_metrics[int(choice) - 1]
                    for m in settings["metrics"]:
                        if m["name"] == metric:
                            m["enabled"] = not m["enabled"]
                            print(f"{metric} {'enabled' if m['enabled'] else 'disabled'}.")
                    save_json(settings, DEFAULT_SETTINGS_PATH)
                elif choice == str(len(dynamic_metrics) + 1):
                    print("Metrics updated.")
                    break
                elif choice == str(len(dynamic_metrics) + 2):
                    return "main_menu"
                else:
                    print("Invalid choice.")
        elif choice == "3":
            result = threshold_menu(settings, metrics_config)
            if result == "main_menu":
                return "main_menu"
        elif choice == "4":
            if input("Reset configuration? (y/n): ").strip().lower() == 'y':
                settings = run_config_wizard(metrics_config)
                print("Configuration reset.")
        elif choice == "5":
            return "parent"
        else:
            print("Invalid choice.")
    return "parent"

def main_menu(settings, metrics_config):
    global running, background_monitor_thread, monitoring_active, metrics_history
    terminal_history = []
    
    def add_to_history(text):
        if isinstance(text, list):
            terminal_history.extend(text)
        else:
            terminal_history.append(text)
    
    while True:
        if monitoring_active:
            menu_text = [
                "\nMonitor System:",
                "1. Continue monitoring",
                "2. Stop monitoring",
                "3. Exit",
                "Choice: "
            ]
        else:
            menu_text = [
                "\nMonitor System:",
                "1. Start monitoring",
                "2. Configure settings",
                "3. List available metrics",
                "4. View logs",
                "5. Check status",
                "6. Exit",
                "Choice: "
            ]
        
        for line in menu_text[:-1]:
            print(line)
        add_to_history(menu_text[:-1])
        
        choice = input("Choice: ").strip()
        add_to_history(f"Choice: {choice}")
        
        if monitoring_active:
            if choice == "1":
                msg = "Monitoring display started. Press Space to return to menu."
                print(msg)
                add_to_history(msg)
                display_monitor_thread = threading.Thread(target=display_monitor_loop, args=(settings, metrics_config, metrics_history, terminal_history))
                display_monitor_thread.start()
                display_monitor_thread.join()
            elif choice == "2":
                running = False
                monitoring_active = False
                if background_monitor_thread and background_monitor_thread.is_alive():
                    background_monitor_thread.join()
                background_monitor_thread = None
                if os.path.exists(DEFAULT_PID_PATH):
                    os.remove(DEFAULT_PID_PATH)
                msg = f"\n{Fore.RED}🔴 Monitoring stopped{Style.RESET_ALL}"
                print(msg)
                add_to_history(msg)
            elif choice == "3":
                running = False
                monitoring_active = False
                if background_monitor_thread and background_monitor_thread.is_alive():
                    background_monitor_thread.join()
                background_monitor_thread = None
                if os.path.exists(DEFAULT_PID_PATH):
                    os.remove(DEFAULT_PID_PATH)
                msg = "Exiting..."
                print(msg)
                add_to_history(msg)
                break
            else:
                msg = "Invalid choice."
                print(msg)
                add_to_history(msg)
        else:
            if choice == "1":
                existing_pid = check_existing_instance()
                if existing_pid:
                    msg = f"Another instance is running (PID: {existing_pid}). Please stop it first."
                    print(msg)
                    add_to_history(msg)
                    continue
                enabled_metrics = [m["name"] for m in settings.get("metrics", []) if m.get("enabled")]
                if not enabled_metrics:
                    msg = "No metrics enabled. Configure settings to enable metrics."
                    print(msg)
                    add_to_history(msg)
                    continue
                running = True
                monitoring_active = True
                if not os.path.exists(DEFAULT_PID_PATH):
                    with open(DEFAULT_PID_PATH, 'w') as f:
                        f.write(str(os.getpid()))
                background_monitor_thread = threading.Thread(target=background_monitor_loop, args=(metrics_config, metrics_history))
                background_monitor_thread.start()
                msg = "Monitoring display started. Press Space to return to menu."
                print(msg)
                add_to_history(msg)
                display_monitor_thread = threading.Thread(target=display_monitor_loop, args=(settings, metrics_config, metrics_history, terminal_history))
                display_monitor_thread.start()
                display_monitor_thread.join()
            elif choice == "2":
                result = configure_menu(settings, metrics_config)
                if result == "main_menu":
                    continue
            elif choice == "3":
                dynamic_metrics = [m["name"] for section in metrics_config.get("sections", []) for m in section.get("metrics", []) if m.get("type") == "dynamic_single"]
                msg = ["\nAvailable metrics:"]
                for metric in dynamic_metrics:
                    enabled = any(m["name"] == metric and m["enabled"] for m in settings["metrics"])
                    msg.append(f"[{'x' if enabled else ' '}] {metric}")
                for line in msg:
                    print(line)
                add_to_history(msg)
            elif choice == "4":
                logs = load_json(DEFAULT_LOG_PATH, [])
                msg = ["\nLast 10 log entries:"]
                for entry in logs[-10:]:
                    msg.append(json.dumps(entry, indent=2))
                for line in msg:
                    print(line)
                add_to_history(msg)
            elif choice == "5":
                if monitoring_active and check_existing_instance():
                    msg = f"\n{Fore.GREEN}✅ Monitoring active (PID: {os.getpid()}){Style.RESET_ALL}"
                else:
                    msg = f"\n{Fore.RED}🔴 Monitoring not active{Style.RESET_ALL}"
                print(msg)
                add_to_history(msg)
            elif choice == "6":
                running = False
                monitoring_active = False
                if background_monitor_thread and background_monitor_thread.is_alive():
                    background_monitor_thread.join()
                background_monitor_thread = None
                if os.path.exists(DEFAULT_PID_PATH):
                    os.remove(DEFAULT_PID_PATH)
                msg = "Exiting..."
                print(msg)
                add_to_history(msg)
                break
            else:
                msg = "Invalid choice."
                print(msg)
                add_to_history(msg)

# Main Function
def main():
    global running, background_monitor_thread, monitoring_active, metrics_history, warned_metrics
    
    existing_pid = check_existing_instance()
    if existing_pid and existing_pid != os.getpid():
        print(f"Error: Another instance is running (PID: {existing_pid}). Please stop it using option 2 or kill the process.")
        sys.exit(1)
    
    metrics_config_path = locate_file(METRICS_CONFIG_PATH)
    metrics_config = load_json(metrics_config_path, {"sections": [{"metrics": []}]})
    
    settings_path = locate_file(DEFAULT_SETTINGS_PATH)
    settings = load_json(settings_path)
    if not settings or not settings.get("metrics"):
        print("Settings not found or invalid. Please initialize monitor_settings.json via the web interface or configure settings manually.")
        settings = {"general": {"interval": 5, "max_rows": 5, "ping_hosts": ["google.com"]}, "metrics": []}  # Provide default to avoid errors
    else:
        dynamic_metrics = [
            m["name"] for section in metrics_config.get("sections", [])
            for m in section.get("metrics", []) if m.get("type") == "dynamic_single"
        ]
        settings_metrics = settings.get("metrics", [])
        missing_metrics = [
            metric["name"] for metric in settings_metrics
            if metric["name"] not in dynamic_metrics            
        ]
        if missing_metrics:
            print(f"Warning: The following metrics are not found in metrics_config.json: {', '.join(missing_metrics)}. Update thresholds in Configure Settings or regenerate settings via the web interface.")
            settings["metrics"] = [
                metric for metric in settings_metrics
                if metric["name"] in dynamic_metrics
            ]
            print(f"Removed invalid metrics from {DEFAULT_SETTINGS_PATH}.")
            save_json(settings, DEFAULT_SETTINGS_PATH)
    warned_metrics = set()
    
    try:
        main_menu(settings, metrics_config)
    finally:
        running = False
        monitoring_active = False
        if background_monitor_thread and background_monitor_thread.is_alive():
            background_monitor_thread.join()
        if os.path.exists(DEFAULT_PID_PATH) and not check_existing_instance():
            os.remove(DEFAULT_PID_PATH)

if __name__ == "__main__":
    main()