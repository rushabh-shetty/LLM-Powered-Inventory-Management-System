#!/usr/bin/env python3

import os
import json
import requests
import sys
import hashlib
import time
import subprocess
import re
from tqdm import tqdm
from datetime import datetime

# -------------------- Constants --------------------
OUTPUT_FILE = "upgrade_report.txt"
JSON_FILE = "upgrade_report.json"
CACHE_FILE = "upgrade_cache.json"
LOG_FILE = "api_call_log.txt"

CATEGORIES = [
    {"name": "SMBIOS / DMI", "desc": "System board and chassis information"},
    {"name": "CPU Info", "desc": "CPU architecture, utilization, and mitigations"},
    {"name": "NIC Model Info", "desc": "Network interface card details and performance"},
    {"name": "Memory Info", "desc": "Memory configuration and utilization"},
    {"name": "Environmental Parameters", "desc": "Thermal and hardware health data"},
    {"name": "NUMA Topology", "desc": "NUMA node configuration and device mappings"},
]

# -------------------- Helpers --------------------
def locate_file(filename, default_paths=None, prompt_message=None):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if default_paths is None:
        default_paths = [
            os.path.join(script_dir, filename),
            os.path.expanduser(os.path.join("~", filename)),
        ]
    for path in default_paths:
        if os.path.exists(path):
            return path
    if prompt_message:
        print(prompt_message)
    while True:
        user_path = input(f"Enter path to '{filename}' (or press Enter to skip): ").strip()
        if not user_path:
            return None
        if os.path.exists(user_path):
            return user_path
        print(f"Error: File '{user_path}' not found. Try again or skip.")

def log_message(msg):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as log:
            log.write(f"{time.ctime()}: {msg}\n")
    except Exception:
        pass

def prompt_analysis_type(chat_bot=False):
    if chat_bot == False:
        print("Choose analysis type:")
        print("(1) General (identify primary upgrade area)")
        print("(2) Specific (select a category)")
        choice = input("Enter 1 or 2: ").strip()
    else:
        choice = "1"
    if choice == "1":
        return "general", None
    elif choice == "2":
        print("Select a category:")
        for i, cat in enumerate(CATEGORIES, 1):
            print(f"({i}) {cat['name']}: {cat['desc']}")
        cat_choice = input("Enter category number: ").strip()
        try:
            idx = int(cat_choice) - 1
            if 0 <= idx < len(CATEGORIES):
                return "specific", CATEGORIES[idx]["name"]
            else:
                raise ValueError("Invalid category number")
        except ValueError:
            print("Invalid input. Exiting.")
            sys.exit(1)
    else:
        print("Invalid choice. Exiting.")
        sys.exit(1)

def display_category_recap(analysis_type, category):
    if analysis_type == "specific" and category:
        for cat in CATEGORIES:
            if cat["name"] == category:
                print(f"You selected {category}. This includes {cat['desc']}.")
                break

def prompt_budget(chat_bot=False, budget=10000):
    if chat_bot == False:
        budget = input("💰 Enter your maximum budget (USD or press Enter for no limit): ").strip()
    try:
        return float(budget) if budget else None
    except ValueError:
        print("Invalid budget. Treating as no limit.")
        return None

def parse_system_info(file_path):
    sections = {}
    current_section = None
    section_content = []
    system_data = ""

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.rstrip()
                system_data += line + "\n"
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
        raise Exception(f"Failed to parse system_info.txt: {e}")

    # Filter out empty sections
    sections = {k: v for k, v in sections.items() if v}
    return sections, system_data

def generate_summary(sections):
    summary = []
    if "CPU Info" in sections:
        cpu_lines = sections["CPU Info"].splitlines()
        for line in cpu_lines:
            if "Model name" in line:
                summary.append(f"CPU: {line.split(':', 1)[1].strip()}")
            elif "CPU(s):" in line:
                summary.append(f"Cores: {line.split(':', 1)[1].strip()}")
    if "Memory Info" in sections:
        mem_lines = sections["Memory Info"].splitlines()
        for line in mem_lines:
            if "MemTotal" in line:
                mem_total = line.split(":", 1)[1].strip()
                summary.append(f"Memory: {mem_total}")
    if "NIC Model Info" in sections:
        nic_lines = sections["NIC Model Info"].splitlines()
        for line in nic_lines:
            if "Ethernet" in line:
                summary.append(f"NIC: {line.strip()}")
    return "\n".join(summary)

def check_cache(cache_file, data_hash, analysis_type, category, budget):
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as cf:
                cache = json.load(cf)
            if data_hash in cache:
                cached_data = cache[data_hash]
                cache_time = os.path.getmtime(cache_file)
                cache_date = datetime.fromtimestamp(cache_time).date()
                current_date = datetime.now().date()
                if (cached_data.get("analysis_type") == analysis_type and
                    cached_data.get("category") == category and
                    cached_data.get("budget") == budget and
                    cache_date == current_date):
                    print(f"Using cached report for {analysis_type} analysis"
                          f"{f' (Category: {category})' if category else ''}.")
                    return cached_data, None
        except Exception as e:
            print(f"⚠️ Could not read cache: {e}")
    return None, None

def save_cache(cache_file, data_hash, analysis_type, category, budget, analysis, recommendations):
    cache_data = {
        "data_hash": data_hash,
        "analysis_type": analysis_type,
        "category": category,
        "budget": budget,
        "analysis": analysis,
        "recommendations": recommendations
    }
    try:
        with open(cache_file, "w", encoding="utf-8") as cf:
            json.dump(cache_data, cf, indent=2)
    except Exception as e:
        print(f"⚠️ Could not write cache: {e}")

def parse_plain_text(text):
    analysis = ""
    recommendations = []
    lines = text.splitlines()
    current_section = None
    current_rec = None
    
    for line in lines:
        line = line.strip()
        if line == "=== Analysis ===":
            current_section = "analysis"
            continue
        elif line == "=== Recommendations ===":
            current_section = "recommendations"
            continue
        elif line.startswith("Recommendation ") and line.endswith(":"):
            if current_rec:
                recommendations.append(current_rec)
            current_rec = {"title": "", "description": "", "cost": ""}
            continue
        
        if current_section == "analysis" and line:
            analysis += line + "\n"
        elif current_section == "recommendations" and current_rec:
            if line.startswith("Title: "):
                current_rec["title"] = line[7:].strip()
            elif line.startswith("Description: "):
                current_rec["description"] = line[13:].strip()
            elif line.startswith("Estimated Cost: "):
                cost = line[16:].strip().replace(" USD", "")
                current_rec["cost"] = float(cost) if cost.replace(".", "").isdigit() else cost
    
    if current_rec and any(current_rec.values()):
        recommendations.append(current_rec)
    
    return analysis.strip(), recommendations

def analyze_system(api_key, data, analysis_type, category, budget, progress_callback=None):
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    prompt = (
        f"Analyze the following system metrics for a high-frequency trading setup:\n\n{data}\n\n"
        f"Provide a detailed analysis focusing on {'general performance' if analysis_type == 'general' else category}. "
        f"Include specific recommendations for hardware upgrades to optimize latency and throughput, "
        f"considering a budget of {budget if budget else 'no limit'} USD. "
        f"For each recommendation, include a title, description, and estimated cost in USD. "
        f"Return the response as plain text in this exact format:\n"
        f"=== Upgrade Report ===\n"
        f"=== Analysis ===\n"
        f"[Detailed analysis text]\n"
        f"=== Recommendations ===\n"
        f"Recommendation 1:\n"
        f"  Title: [Title]\n"
        f"  Description: [Description]\n"
        f"  Estimated Cost: [Cost] USD\n"
        f"Recommendation 2:\n"
        f"  Title: [Title]\n"
        f"  Description: [Description]\n"
        f"  Estimated Cost: [Cost] USD\n"
        f"...\n"
        f"Ensure each section is clearly separated and formatted as shown."
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
        if progress_callback:
            progress_callback({"step": "Calling xAI API for system analysis", "percent": 10})
        print("Calling xAI API for system analysis...", flush=True)
        for chunk in response.iter_lines():
            if chunk:
                chunk_data = chunk.decode("utf-8")
                if chunk_data.startswith("data: "):
                    chunk_json = chunk_data[6:]
                    if chunk_json.strip() == "[DONE]":
                        if progress_callback:
                            progress_callback({"step": "Analysis complete", "percent": 100})
                        break
                    try:
                        chunk_obj = json.loads(chunk_json)
                        if "choices" in chunk_obj and chunk_obj["choices"]:
                            content = chunk_obj["choices"][0].get("delta", {}).get("content", "")
                            if content:
                                full_content += content
                                print(content, end="", flush=True)
                                if progress_callback:
                                    progress_callback({"step": "Receiving analysis data", "percent": 50, "content": content})
                    except json.JSONDecodeError:
                        continue
        print()
        analysis, recommendations = parse_plain_text(full_content)
        return analysis, recommendations
    except requests.RequestException as e:
        if progress_callback:
            progress_callback({"step": "API request failed", "percent": 0, "error": str(e)})
        raise Exception(f"API request failed: {e}")
    except (KeyError, json.JSONDecodeError) as e:
        if progress_callback:
            progress_callback({"step": "Invalid API response format", "percent": 0, "error": str(e)})
        raise Exception(f"Invalid API response format: {e}")

def save_outputs(output_dir, sections, analysis, recommendations, data_hash, analysis_type, category, budget):
    # Save raw output
    os.makedirs(output_dir, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("=== Upgrade Report ===\n")
        f.write("=== Analysis ===\n")
        f.write(str(analysis) + "\n")
        f.write("\n=== Recommendations ===\n")
        for i, rec in enumerate(recommendations, 1):
            f.write(f"\nRecommendation {i}:\n")
            f.write(f"  Title: {rec.get('title', f'Recommendation {i}')}\n")
            f.write(f"  Description: {rec.get('description', 'No description provided')}\n")
            f.write(f"  Estimated Cost: {rec.get('cost', 'Not specified')} USD\n")

    # Update cache
    cache = {}
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                cache = json.load(f)
        except Exception:
            print(f"⚠️ Could not read cache: {sys.exc_info()[1]}")
    cache[data_hash] = {
        "analysis": analysis,
        "recommendations": recommendations,
        "analysis_type": analysis_type,
        "category": category,
        "budget": budget
    }
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)

def display_report(analysis, recommendations, analysis_type, category):
    print("\n=== Upgrade Report ===")
    print("\n=== Analysis ===")
    print(analysis)
    
    print("\n=== Recommendations ===")
    for i, rec in enumerate(recommendations, 1):
        title = rec.get("title", f"Recommendation {i}")
        print(f"\nRecommendation {i}:")
        print(f"  Title: {title}")
        print(f"  Description: {rec.get('description', 'No description provided')}")
        print(f"  Estimated Cost: {rec.get('cost', 'Not specified')} USD")

def post_report_qa(api_key, analysis, recommendations, output_dir, data_hash, analysis_type, category, budget, system_data, question, progress_callback=None):
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    prompt = (
        f"You analyzed a high-frequency trading system with the following metrics:\n\n{system_data}\n\n"
        f"Recommendations provided:\n{json.dumps(recommendations, indent=2)}\n\n"
        f"User question: '{question}'\nAnswer conversationally, focusing on the system and recommendations."
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
        if progress_callback:
            progress_callback({"step": "Processing question", "percent": 10})
        for chunk in response.iter_lines():
            if chunk:
                chunk_data = chunk.decode("utf-8")
                if chunk_data.startswith("data: "):
                    chunk_json = chunk_data[6:]
                    if chunk_json.strip() == "[DONE]":
                        if progress_callback:
                            progress_callback({"step": "Answer complete", "percent": 100, "content": full_content})
                        break
                    try:
                        chunk_obj = json.loads(chunk_json)
                        if "choices" in chunk_obj and chunk_obj["choices"]:
                            content = chunk_obj["choices"][0].get("delta", {}).get("content", "")
                            if content:
                                full_content += content
                                if progress_callback:
                                    progress_callback({"step": "Receiving answer", "percent": 50, "content": content})
                    except json.JSONDecodeError:
                        continue
        return full_content
    except requests.RequestException as e:
        if progress_callback:
            progress_callback({"step": "API request failed", "percent": 0, "error": str(e)})
        raise Exception(f"API request failed: {e}")
    except (KeyError, json.JSONDecodeError) as e:
        if progress_callback:
            progress_callback({"step": "Invalid API response format", "percent": 0, "error": str(e)})
        raise Exception(f"Invalid API response format: {e}")

# -------------------- Main --------------------
def main(chat_bot=False):
    # Load API key
    api_key_path = locate_file(
        "api_key.txt",
        prompt_message="Could not find 'api_key.txt'. Enter its path or press Enter to exit."
    )
    if not api_key_path:
        print("✖️ API key file is required. Exiting.")
        sys.exit(1)
    with open(api_key_path, "r", encoding="utf-8-sig") as f:
        api_key = f.read().strip()
    if not api_key:
        print("✖️ API key is empty. Exiting.")
        sys.exit(1)

    if chat_bot == True:
        system_info_path = "src/system_info.txt"
    else:
        # Locate system_info.txt
        system_info_path = locate_file(
            "system_info.txt",
            default_paths=[
                os.path.join(os.path.dirname(os.path.abspath(__file__)), "system_info.txt"),
                os.path.expanduser(os.path.join("~", "system_info.txt")),
            ],
            prompt_message="Could not find 'system_info.txt'. Run './collect_data.py' or enter its path."
        )
        if not system_info_path:
            print("✖️ system_info.txt is required. Exiting.")
            sys.exit(1)

    # Parse system metrics
    try:
        sections, system_data = parse_system_info(system_info_path)
    except Exception as e:
        print(f"Error reading system_info.txt: {e}. Exiting.")
        sys.exit(1)
    if not sections:
        print("Error: system_info.txt is empty or malformed. Exiting.")
        sys.exit(1)

    # Define paths
    output_dir = os.path.dirname(system_info_path)
    global OUTPUT_FILE, CACHE_FILE, LOG_FILE
    OUTPUT_FILE = os.path.join(output_dir, "upgrade_report.txt")
    CACHE_FILE = os.path.join(output_dir, "upgrade_cache.json")
    LOG_FILE = os.path.join(output_dir, "api_call_log.txt")

    # Prompt for analysis type and budget
    if chat_bot == True:
        analysis_type, category = prompt_analysis_type(chat_bot)
        display_category_recap(analysis_type, category)
        budget = prompt_budget(chat_bot, 10000)
    else:
        analysis_type, category = prompt_analysis_type()
        display_category_recap(analysis_type, category)
        budget = prompt_budget()

    # Compute data hash
    data_hash = hashlib.sha256(system_data.encode("utf-8")).hexdigest()

    # Check cache
    cache, _ = check_cache(CACHE_FILE, data_hash, analysis_type, category, budget)
    if cache:
        analysis = cache["analysis"]
        recommendations = cache["recommendations"]
        display_report(analysis, recommendations, analysis_type, category)
    else:
        # Prepare data for API
        data = generate_summary(sections) if analysis_type == "general" else sections.get(category, "")
        if not data:
            print(f"Error: No data available for {category or 'general analysis'}. Ensure system_info.txt contains relevant metrics. Exiting.")
            sys.exit(1)

        try:
            analysis, recommendations = analyze_system(api_key, data, analysis_type, category, budget)
            if chat_bot == True:
                return analysis + recommendations
        except Exception as e:
            print(f"Error: API call failed: {e}. Verify api_key.txt or network.")
            sys.exit(1)

        # Save outputs
        save_outputs(output_dir, sections, analysis, recommendations, data_hash, analysis_type, category, budget)

    # Post-report Q&A
    if chat_bot == False:
        post_report_qa(api_key, analysis, recommendations, output_dir, data_hash, analysis_type, category, budget, system_data, question="")

if __name__ == "__main__":
    main()