#!/usr/bin/env python3

import os
import json
import requests
import sys
import time
import subprocess
from tqdm import tqdm
from tabulate import tabulate
import re

# -------------------- Constants --------------------
OUTPUT_FILE = "performance_report.txt"
TUNE_SCRIPT = "tune_system.sh"
CONFIG_FILE = "metrics_config.json"
API_KEY_FILE = "api_key.txt"
SYSTEM_INFO_FILE = "system_info.txt"

# HFT-relevant sections from collect_data.py
HFT_SECTIONS = [
    "Network Interface Configuration",
    "NUMA Topology",
    "CPU Info",
    "Memory Info",
    "Environmental Parameters",
    "Disk Info",
    "Running Processes",
    "Service Status",
    "IPMI / BMC Data",
    "GPU Info"
]
# Non-HFT sections to exclude from balanced profile
NON_HFT_SECTIONS = ["USB Devices", "Installed Packages", "PCI Devices (Verbose)"]

# -------------------- Helpers --------------------
def locate_file(filename, default_paths=None, prompt_message=None):
    """Locate a file in default paths or prompt user for its location."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if default_paths is None:
        default_paths = [
            os.path.join(script_dir, filename),
            os.path.join(script_dir, "LinuxVM", filename),
            os.path.join(script_dir, "Linux VM", filename),
            os.path.expanduser(os.path.join("~", filename)),
            os.path.expanduser(os.path.join("~", "LinuxVM", filename)),
        ]
    for path in default_paths:
        if os.path.exists(path):
            return path
    if prompt_message:
        print(prompt_message)
    else:
        print(f"Could not find '{filename}'. Please provide the full path (or press Enter to skip):")
    while True:
        user_path = input().strip()
        if not user_path:
            return None
        if os.path.exists(user_path):
            return user_path
        print(f"File '{user_path}' not found. Try again (or press Enter to skip):")

def check_config_age(config_path):
    """Warn if metrics_config.json is older than 24 hours."""
    mtime = os.path.getmtime(config_path)
    if time.time() - mtime > 86400:
        print("⚠️ Warning: metrics_config.json is older than 24 hours. Consider running collect_data.py.")

def collect_system_data(config, sections, progress_callback=None):
    """Collect system metrics for specified sections with progress updates."""
    system_data = {}
    if progress_callback:
        progress_callback({"step": "Collecting system metrics...", "percent": 10, "error": None})
    else:
        print("Collecting system metrics...")
    for idx, section in enumerate(config["sections"]):
        if section["title"] not in sections:
            continue
        step = f"Gathering {section['title']} data..."
        progress = 10 + (idx + 1) * (30 // len(config["sections"]))
        if progress_callback:
            progress_callback({"step": step, "percent": min(progress, 40), "error": None})
        for metric in section["metrics"]:
            try:
                output = subprocess.check_output(metric["command"], shell=True, text=True, stderr=subprocess.STDOUT).strip()
                system_data[metric["name"]] = output
            except subprocess.CalledProcessError as e:
                system_data[metric["name"]] = f"Error: {e.output.strip()}"
    if not system_data:
        error = "No system data collected"
        if progress_callback:
            progress_callback({"step": error, "percent": 10, "error": error})
        raise Exception(error)
    return system_data

def collect_validation_metrics(metrics, duration=5, progress_callback=None, percent_start=0, percent_end=100):
    """Collect dynamic metrics for specified duration with progress updates."""
    results = {}
    step = f"Validating pre-tuning metrics..."
    if progress_callback:
        progress_callback({"step": step, "percent": percent_start, "error": None})
    else:
        print(f"Collecting metrics for {duration} seconds...")
    with tqdm(total=duration, desc="Collecting", unit="s") as pbar:
        start_time = time.time()
        while time.time() - start_time < duration:
            for metric in metrics:
                try:
                    output = subprocess.check_output(metric["command"], shell=True, text=True, stderr=subprocess.STDOUT).strip()
                    results[metric["name"]] = output
                except subprocess.CalledProcessError as e:
                    results[metric["name"]] = f"Error: {e.output.strip()}"
            time.sleep(1)
            if progress_callback:
                elapsed = time.time() - start_time
                progress = percent_start + int((elapsed / duration) * (percent_end - percent_start))
                progress_callback({"step": step, "percent": min(progress, percent_end), "error": None})
            pbar.update(1)
    return results

def get_profile_sections(config, profile):
    """Retrieve sections for the selected profile or category."""
    available_sections = [s["title"] for s in config["sections"] if s["metrics"]]
    if not available_sections:
        print("❌ Error: No sections with metrics in metrics_config.json. Run collect_data.py first.")
        sys.exit(1)
    if profile == "balanced":
        return [
            s for s in available_sections
            if s not in NON_HFT_SECTIONS
            and any(m["type"] in ["dynamic_single", "dynamic_multi"]
                    for m in next(sec["metrics"] for sec in config["sections"] if sec["title"] == s))
        ]
    sections = [
        s for s in HFT_SECTIONS if s in available_sections and s in (
            ["Network Interface Configuration", "NUMA Topology", "CPU Info"]
            if profile == "latency" else
            ["Network Interface Configuration", "Memory Info", "CPU Info", "Disk Info"]
        )
    ]
    if not sections:
        print(f"❌ Error: No relevant sections found for {profile}. Run collect_data.py first.")
        sys.exit(1)
    return sections

def get_dynamic_metrics(config, sections):
    """Retrieve dynamic_single and dynamic_multi metrics for validation."""
    return [
        m for s in config["sections"] if s["title"] in sections
        for m in s["metrics"] if m["type"] in ["dynamic_single", "dynamic_multi"]
    ]

# -------------------- API Call Functions --------------------
def analyze_system(api_key, data, system_info, profile, sections, progress_callback=None):
    """Call xAI API to analyze system metrics with streaming Markdown output."""
    url = "https://api.x.ai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    scope = f"{'profile' if profile else 'category'}-specific ({', '.join(sections)})"
    focus = {
        "latency": "IRQ affinity, NUMA binding, CPU utilization",
        "throughput": "NIC ring buffers, memory bandwidth, core scaling, I/O latency",
        "balanced": "equal weight across all dynamic metrics",
        "category": "selected system components"
    }[profile or "category"]
    system_context = system_info if system_info else "No system context available (system_info.txt not found)."

    # Collect system data if not provided
    if not data:
        config_path = locate_file("metrics_config.json")
        if not config_path:
            error = "metrics_config.json is required"
            if progress_callback:
                progress_callback({"step": error, "percent": 10, "error": error})
            raise Exception(error)
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        data = collect_system_data(config, sections, progress_callback)

    # Warn about virtualization limitations
    if "virtio" in json.dumps(data).lower():
        step = "⚠️ Virtualized environment detected."
        if progress_callback:
            progress_callback({"step": step, "percent": 40, "error": None})
        else:
            print(step)

    prompt = (
        "You are a performance optimization expert for Linux systems, specializing in low-latency environments like high-frequency trading (HFT).\n\n"
        f"**System Context**: {system_context}\n\n"
        "Analyze the following system metrics from `metrics_config.json`:\n\n"
        "```json\n"
        f"{json.dumps(data, indent=2)}\n"
        "```\n\n"
        f"**Analysis Instructions**:\n"
        f"- Scope: {scope}. Analyze only the provided metrics and avoid assumptions about missing data (e.g., CPU info if not included).\n"
        f"- Focus on {focus} for HFT optimization.\n"
        "- Identify bottlenecks or misconfigurations impacting latency or throughput.\n"
        "- For NUMA, if metrics include node, CPU, or memory data, construct a detailed table with nodes, CPUs, memory, and PCIe devices. If incomplete, suggest specific commands to collect missing data (e.g., `numactl -H` for PCIe mappings).\n"
        "- Evaluate CPU frequency, scaling governor, and thermal data if provided.\n"
        "- Suggest safe, specific commands tailored to the system configuration and provided metrics.\n"
        "- If data is limited, include fallback recommendations to collect missing information.\n"
        "- Avoid recommendations unrelated to the provided metrics (e.g., no SMBIOS/DMI suggestions if only Memory Info is provided).\n"
        "- Cross-reference NIC-related metrics (e.g., enp0s1_interrupt_distribution) to ensure accurate IRQ identification.\n\n"
        "**Output Requirements**:\n"
        "Return a single JSON object with two keys:\n"
        "1. `analysis`: A Markdown-formatted report structured as:\n"
        "   ```markdown\n"
        "   === Performance Analysis ===\n"
        "   - **System Overview**: Summarize system context (e.g., virtualization, OS, CPU) from system_info.txt, if available.\n"
        "   - **NUMA Topology**: Table of nodes, CPUs, memory, devices (or note if data is missing).\n"
        "   - **Bottlenecks**: CPU, NIC, memory, processes, environmental, based on provided metrics.\n"
        "   === Optimization Recommendations ===\n"
        "   - **Recommendation Title**:\n"
        "     - **Rationale**: Why it improves HFT performance.\n"
        "     - **Impact**: Expected gain.\n"
        "     - **Commands**:\n"
        "       ```bash\n"
        "       # Safe commands\n"
        "       ```\n"
        "   ```\n"
        "2. `recommendations`: Array of objects with:\n"
        "   - `id`: Unique identifier (e.g., 'rec-001').\n"
        "   - `title`: Short title.\n"
        "   - `description`: Brief explanation.\n"
        "   - `impact`: Quantitative or qualitative estimate (e.g., '10-20% latency reduction').\n"
        "   - `commands`: Array of bash commands as strings.\n"
        "- Ensure commands are safe, include checks, and are relevant to the provided metrics.\n"
        "- Return ONLY valid JSON.\n"
    )
    payload = {
        "model": "grok-3-latest",
        "messages": [
            {"role": "system", "content": "You are a performance optimization expert for Linux systems."},
            {"role": "user", "content": prompt}
        ],
        "stream": True
    }
    if progress_callback:
        progress_callback({"step": "Analyzing...", "percent": 50, "error": None})
    else:
        print("Analyzing...")
    try:
        response = requests.post(url, headers=headers, json=payload, stream=True)
        response.raise_for_status()
        partial_content = ""
        current_section = []
        section_buffer = ""
        section_count = 0
        expected_sections = 4  # System Overview, NUMA, Bottlenecks, Recommendations
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
                                partial_content += content
                                section_buffer += content
                                # Check for section boundaries
                                if "\n===" in section_buffer or "\n- **" in section_buffer or section_buffer.strip().endswith("```"):
                                    # Extract complete sections
                                    sections = re.split(r'\n(?=== |\n- \*\*)', section_buffer)
                                    for i, section in enumerate(sections[:-1]):
                                        if section.strip():
                                            current_section.append(section)
                                            section_text = "".join(current_section)
                                            try:
                                                json.loads(partial_content)  # Ensure valid JSON so far
                                                section_count += 1
                                                percent = 50 + (25 * section_count / expected_sections)
                                                if progress_callback:
                                                    progress_callback({
                                                        "step": f"Generating report: {section.strip().split('\n')[0]}",
                                                        "percent": min(percent, 75),
                                                        "error": None,
                                                        "partial_report": section_text
                                                    })
                                                else:
                                                    print(section_text)
                                                current_section = []
                                            except json.JSONDecodeError:
                                                continue
                                    section_buffer = sections[-1]
                    except json.JSONDecodeError:
                        continue
        if partial_content:
            parsed = json.loads(partial_content)
            # Emit any remaining section
            if section_buffer.strip():
                section_count += 1
                percent = 50 + (25 * section_count / expected_sections)
                if progress_callback:
                    progress_callback({
                        "step": "Generating report: Finalizing...",
                        "percent": min(percent, 75),
                        "error": None,
                        "partial_report": section_buffer
                    })
            return parsed["analysis"], parsed["recommendations"]
        else:
            raise Exception("No valid content received from API")
    except requests.RequestException as e:
        payload["stream"] = False
        try:
            response = requests.post(url, headers=headers, json=payload)
            response.raise_for_status()
            parsed = response.json()
            content = parsed["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            if progress_callback:
                progress_callback({"step": "Analysis complete (non-streaming).", "percent": 75, "error": None, "partial_report": parsed["analysis"]})
            else:
                print("Analysis complete (non-streaming).")
            return parsed["analysis"], parsed["recommendations"]
        except requests.RequestException as e:
            raise Exception(f"Non-streaming API request failed: {e}")
        except (KeyError, json.JSONDecodeError) as e:
            raise Exception(f"Invalid non-streaming API response format: {e}")
    except (KeyError, json.JSONDecodeError) as e:
        raise Exception(f"Invalid streaming API response format: {e}")

def get_grok_targets(api_key, data, system_info, profile, sections, progress_callback=None):
    """Get performance targets from xAI API with incremental table output."""
    url = "https://api.x.ai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    scope = f"{'profile' if profile else 'category'}-specific ({', '.join(sections)})"
    system_context = system_info if system_info else "No system context available."
    prompt = (
        "You are a performance optimization expert for Linux systems, specializing in HFT.\n\n"
        f"**System Context**: {system_context}\n\n"
        "Given system metrics:\n\n"
        "```json\n"
        f"{json.dumps(data, indent=2)}\n"
        "```\n\n"
        f"Scope: {scope}\n"
        "Suggest performance targets for dynamic metrics (e.g., enp0s1_rx_queue_0_drops < 10).\n"
        "Return JSON: {\"targets\": [{\"metric\": \"\", \"target\": 0, \"unit\": \"\"}]}\n"
    )
    payload = {
        "model": "grok-3-latest",
        "messages": [
            {"role": "system", "content": "You are a performance optimization expert for Linux systems."},
            {"role": "user", "content": prompt}
        ],
        "stream": True
    }
    if progress_callback:
        progress_callback({"step": "Fetching targets...", "percent": 85, "error": None})
    else:
        print("Fetching targets...")
    try:
        response = requests.post(url, headers=headers, json=payload, stream=True)
        response.raise_for_status()
        partial_content = ""
        current_section = []
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
                                partial_content += content
                                current_section.append(content)
                                section_text = "".join(current_section)
                                if "\n  }" in section_text or section_text.strip().endswith("]"):
                                    try:
                                        targets = json.loads(partial_content)["targets"]
                                        if not progress_callback:
                                            print(tabulate(
                                                [[t["metric"], t["target"], t["unit"]] for t in targets],
                                                headers=["Metric", "Target", "Unit"],
                                                tablefmt="grid"
                                            ))
                                        current_section = []
                                    except json.JSONDecodeError:
                                        continue
                    except json.JSONDecodeError:
                        continue
        if partial_content:
            parsed = json.loads(partial_content)
            if progress_callback:
                progress_callback({"step": "Targets received.", "percent": 90, "error": None})
            return parsed["targets"]
        else:
            raise Exception("No valid content received from API")
    except requests.RequestException as e:
        payload["stream"] = False
        try:
            response = requests.post(url, headers=headers, json=payload)
            response.raise_for_status()
            parsed = response.json()
            content = parsed["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            if progress_callback:
                progress_callback({"step": "Targets received (non-streaming).", "percent": 90, "error": None})
            else:
                print("Targets received (non-streaming).")
            return parsed["targets"]
        except requests.RequestException as e:
            raise Exception(f"Non-streaming API request failed: {e}")
        except (KeyError, json.JSONDecodeError) as e:
            raise Exception(f"Invalid non-streaming API response format: {e}")
    except (KeyError, json.JSONDecodeError) as e:
        raise Exception(f"Invalid streaming API response format: {e}")

def apply_tuning(recommendations, inputs, dynamic_metrics, pre_metrics, progress_callback=None, tune_script_path=None):
    """Apply tuning commands with optional progress callback and script path."""
    tuning_applied = False
    validation = []
    post_metrics = {}

    if tune_script_path:
        step = "Executing tuning script..."
        if progress_callback:
            progress_callback({"step": step, "percent": 70, "error": None})
        else:
            print(step)
        try:
            subprocess.run(['sudo', 'bash', tune_script_path], check=True, timeout=300)
            tuning_applied = True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            error = f"Tuning script failed: {e}"
            if progress_callback:
                progress_callback({"step": error, "percent": 70, "error": error})
            else:
                print(f"⚠️ Warning: {error}")

    for idx, rec in enumerate(recommendations):
        step = f"Applying {rec['title']}..."
        if progress_callback:
            progress_callback({"step": step, "percent": 70, "error": None})
        else:
            print(step)
        for cmd in rec["commands"]:
            cmd = cmd.replace("/path/to/hft_app", inputs.get("appPath", "/path/to/hft_app"))
            if "smp_affinity" in cmd:
                irq_num = cmd.split("/")[3].split("/")[0]
                if not os.path.exists(f"/proc/irq/{irq_num}/smp_affinity"):
                    continue
            try:
                proc = subprocess.run(f"sudo {cmd}", shell=True, capture_output=True, text=True, timeout=30)
                if proc.returncode != 0:
                    raise subprocess.CalledProcessError(proc.returncode, cmd, proc.stderr)
                tuning_applied = True
                success_step = f"Applied: {rec['title']}"
                if progress_callback:
                    progress_callback({"step": success_step, "percent": 70, "error": None})
                else:
                    print(f"✅ {success_step}")
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
                error = f"Failed to apply {rec['title']}: {e}"
                if progress_callback:
                    progress_callback({"step": error, "percent": 70, "error": error})
                else:
                    print(f"⚠️ Warning: {error}")
                return False, []

    if dynamic_metrics and tuning_applied:
        step = "Collecting post-tuning metrics..."
        if progress_callback:
            progress_callback({"step": step, "percent": 85, "error": None})
        post_metrics = collect_validation_metrics(dynamic_metrics, duration=5, progress_callback=progress_callback, percent_start=85, percent_end=95)
        step = "Validating post-tuning metrics..."
        if progress_callback:
            progress_callback({"step": step, "percent": 95, "error": None})
        validation = [{"metric": k, "pre": pre_metrics.get(k, "N/A"), "post": post_metrics.get(k, "N/A")} for k in pre_metrics]

    step = "Tuning complete"
    if progress_callback:
        progress_callback({"step": step, "percent": 100, "error": None})
    return tuning_applied, validation

# -------------------- Main --------------------
def main():
    """Main function for HFT performance optimization."""
    api_key_path = locate_file(
        "api_key.txt",
        prompt_message="Could not find 'api_key.txt'. Enter its path or press Enter to exit."
    )
    if not api_key_path:
        print("✖️ API key file is required. Exiting.")
        sys.exit(1)
    global XAI_API_KEY
    with open(api_key_path, "r", encoding="utf-8-sig") as f:
        XAI_API_KEY = f.read().strip()
    if not XAI_API_KEY:
        print("✖️ API key is empty. Exiting.")
        sys.exit(1)

    config_path = locate_file(
        "metrics_config.json",
        prompt_message="Could not find 'metrics_config.json'. Run collect_data.py or enter its path."
    )
    if not config_path:
        print("✖️ metrics_config.json is required. Exiting.")
        sys.exit(1)
    check_config_age(config_path)
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    system_info_path = locate_file(
        "system_info.txt",
        prompt_message="Could not find 'system_info.txt'. Run ./system_info.sh or enter its path (or press Enter to skip)."
    )
    system_info = None
    if system_info_path:
        try:
            with open(system_info_path, "r", encoding="utf-8", errors="ignore") as f:
                system_info = f.read().strip()
        except Exception as e:
            print(f"⚠️ Could not read system_info.txt: {e}")

    output_dir = os.path.dirname(config_path)
    output_file = os.path.join(output_dir, OUTPUT_FILE)
    tune_script = os.path.join(output_dir, TUNE_SCRIPT)

    while True:
        print("\nOptimization Menu:")
        print("1. Latency Profile (Focusing on IRQ affinity, NUMA binding, CPU utilization)")
        print("2. Throughput Profile (Focusing on NIC ring buffers, memory bandwidth, core scaling)")
        print("3. Balanced Profile (Focusing on all available dynamic metrics)")
        print("4. Select Category (e.g., Network Interface Configuration, CPU Info, Custom Metrics)")
        print("5. Exit")
        choice = input("Enter your choice (1-5): ").strip()

        if choice == "4":
            categories = [s["title"] for s in config["sections"] if s["metrics"]] + ["All"]
            if not categories:
                print("❌ No sections with metrics available. Run collect_data.py first.")
                continue
            while True:
                print("\nAvailable Categories:")
                print("0. Back")
                print("\n".join(f"{i+1}. {c}" for i, c in enumerate(categories)))
                try:
                    category_input = input("Select category (number): ").strip()
                    if category_input == "0":
                        break
                    category_idx = int(category_input) - 1
                    if category_idx < 0 or category_idx >= len(categories):
                        print("❌ Invalid category selection. Try again.")
                        continue
                    category = categories[category_idx]
                    profile, sections = None, [category] if category != "All" else categories[:-1]
                    dynamic_metrics = get_dynamic_metrics(config, sections)
                    if not dynamic_metrics and category != "All":
                        print(f"⚠️ No dynamic metrics in {category} (validation will be skipped).")
                    break
                except ValueError:
                    print("❌ Invalid input. Try again.")
            if category_input == "0":
                continue
        elif choice == "5":
            print("Exiting.")
            return
        else:
            profile_map = {"1": "latency", "2": "throughput", "3": "balanced"}
            if choice not in profile_map:
                print("❌ Invalid choice. Try again.")
                continue
            profile = profile_map[choice]
            sections = get_profile_sections(config, profile)

        system_data = collect_system_data(config, sections)
        if "virtio" in json.dumps(system_data).lower():
            print("⚠️ Virtualized environment detected. Some HFT optimizations may be limited.")

        print("Sending data to xAI API for analysis...")
        analysis, recommendations = analyze_system(XAI_API_KEY, system_data, system_info, profile, sections)
        print("\n=== Performance Report ===\n")
        print(analysis)
        dynamic_metrics = get_dynamic_metrics(config, sections)
        if dynamic_metrics:
            print("\n=== Dynamic Metrics for Validation ===\n")
            print(tabulate(
                [[m["name"], m["command"]] for m in dynamic_metrics],
                headers=["Metric", "Command"],
                tablefmt="grid"
            ))
        print("=== End of Report ===\n")

        if not dynamic_metrics:
            print("⚠️ No dynamic metrics available, validation will be skipped.")
        ans = input("Proceed with system tuning? (y/N): ").strip().lower()
        if ans != "y":
            print("Skipping tuning and validation.")
            continue

        with open(tune_script, "w", encoding="utf-8") as f:
            f.write("#!/bin/bash\n\n")
            f.write("# Performance tuning script for HFT system optimization\n")
            f.write('echo "=== System Tuning ==="\n')
            f.write(f'echo "Performance report available in {output_file}"\n')
            f.write(f'echo "Found {len(recommendations)} optimization recommendations."\n')
            f.write('echo "🔧 Executing optimizations for high-frequency trading (interactive prompts)..."\n\n')
            for rec in recommendations:
                f.write(f'echo "=== {rec["title"]} ==="\n')
                f.write(f'echo "{rec["description"]}"\n')
                if "numactl" in " ".join(rec["commands"]).lower() and "/path/to/hft_app" in " ".join(rec["commands"]):
                    f.write('echo "Please provide the path to your HFT application (e.g., /usr/bin/trading_app):"\n')
                    f.write('read -p "Application path: " APP_PATH\n')
                    f.write('if [ -z "$APP_PATH" ] || [ ! -x "$APP_PATH" ]; then\n')
                    f.write('    echo "⚠️ Invalid or missing application path, skipping."\n')
                    f.write('    continue\n')
                    f.write('fi\n')
                elif "kill" in " ".join(rec["commands"]).lower():
                    f.write('echo "⚠️ This recommendation terminates processes. Ensure critical processes are not affected."\n')
                elif "thermal" in rec["title"].lower() or "temperature" in rec["title"].lower():
                    f.write('echo "⚠️ This recommendation addresses hardware health (physical systems only)."\n')
                f.write('read -p "Apply this recommendation? (y/N): " ans\n')
                f.write('if [[ $ans =~ ^[Yy]$ ]]; then\n')
                for cmd in rec["commands"]:
                    if "free -m -s" in cmd or "vmstat" in cmd:
                        f.write(f'    timeout 10s {cmd}\n')
                    elif "smp_affinity" in cmd:
                        irq_num = cmd.split("/")[3].split("/")[0]
                        f.write(f'if [ -f "/proc/irq/{irq_num}/smp_affinity" ]; then\n')
                        f.write(f'    sudo {cmd}\n')
                        f.write(f'else\n')
                        f.write(f'    echo "IRQ {irq_num} not found, skipping."\n')
                        f.write(f'fi\n')
                    else:
                        if "/path/to/hft_app" in cmd:
                            f.write(f'    {cmd.replace("/path/to/hft_app", "$APP_PATH")}\n')
                        else:
                            f.write(f'    sudo {cmd}\n')
                f.write(f'    echo "✅ Applied: {rec["title"]}"\n')
                f.write('else\n')
                f.write(f'    echo "⏭️ Skipped: {rec["title"]}"\n')
                f.write('fi\n\n')
            f.write('echo "🎉 Tuning complete. Reboot if necessary."\n')
        os.chmod(tune_script, 0o755)
        print(f"⚙️ Generated tuning script: {tune_script}")

        pre_metrics = {}
        if dynamic_metrics:
            pre_metrics = collect_validation_metrics(dynamic_metrics, duration=5)
        tuning_applied, validation = apply_tuning(recommendations, {}, dynamic_metrics, pre_metrics, tune_script_path=tune_script)
        if tuning_applied and dynamic_metrics:
            print("\n=== Validation Results ===\n")
            table = [[v["metric"], v["pre"], v["post"]] for v in validation]
            print(tabulate(table, headers=["Metric", "Pre", "Post"], tablefmt="grid"))
            if not tuning_applied:
                print("Note: No tuning applied, pre/post metrics expected to be similar.")
            print("=== End of Validation Results ===\n")
        else:
            print("Validation skipped: Tuning script failed or interrupted.")

        with open(output_file, "w", encoding="utf-8") as f:
            f.write(analysis + "\n")
            grouped_recs = {}
            for rec in recommendations:
                section = next(
                    (s["title"] for s in config["sections"] for m in s["metrics"]
                     if m["name"] in rec["title"] or m["subsection"] in rec["title"]),
                    "General Optimizations"
                )
                grouped_recs.setdefault(section, []).append(rec)
            for section, recs in grouped_recs.items():
                f.write(f"\n=== {section} ===\n")
                f.write(tabulate(
                    [[r["id"], r["title"], r.get("impact", "N/A")] for r in recs],
                    headers=["ID", "Title", "Impact"],
                    tablefmt="grid"
                ))
            f.write("\n=== Static Metrics Analyzed ===\n")
            static_metrics = [
                m for s in config["sections"] if s["title"] in sections
                for m in s["metrics"] if m["type"] == "static"
            ]
            if static_metrics:
                f.write(tabulate(
                    [[m["name"], m["subsection"]] for m in static_metrics],
                    headers=["Metric", "Subsection"],
                    tablefmt="grid"
                ))
            else:
                f.write("No static metrics analyzed.\n")
            f.write("\n=== Dynamic Metrics for Validation ===\n")
            if dynamic_metrics:
                f.write(tabulate(
                    [[m["name"], m["command"]] for m in dynamic_metrics],
                    headers=["Metric", "Command"],
                    tablefmt="grid"
                ))
            else:
                f.write("No dynamic metrics available for validation.\n")
            f.write("\n=== Validation Targets ===\n")
            if dynamic_metrics:
                targets = get_grok_targets(XAI_API_KEY, system_data, system_info, profile, sections)
                if targets:
                    f.write(tabulate(
                        [[t["metric"], t["target"], t["unit"]] for t in targets],
                        headers=["Metric", "Target", "Unit"],
                        tablefmt="grid"
                    ))
                else:
                    f.write("No validation targets provided.\n")
            f.write("\n=== Validation Results ===\n")
            if tuning_applied and dynamic_metrics:
                table = [[v["metric"], v["pre"], v["post"]] for v in validation]
                f.write(tabulate(table, headers=["Metric", "Pre", "Post"], tablefmt="grid"))
                if not tuning_applied:
                    f.write("\nNote: No tuning applied, pre/post metrics expected to be similar.\n")
            else:
                f.write("No validation results (no dynamic metrics or tuning not applied).\n")
            f.write("\n=== Section Summary ===\n")
            for section in sections:
                metrics = [m for m in next(s["metrics"] for s in config["sections"] if s["title"] == section)]
                has_dynamic = any(m["type"] in ["dynamic_single", "dynamic_multi"] for m in metrics)
                f.write(f"{section}: {'Validated' if has_dynamic else 'Analyzed only (no dynamic metrics)'}\n")
        print(f"📄 Saved analysis to {output_file}")

if __name__ == "__main__":
    main()