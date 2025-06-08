#!/usr/bin/env python3

import os
import sys
import json
import time
import requests
from datetime import date

def fetch_thresholds_from_grok(model_name, api_key):
    url = "https://api.x.ai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    prompt = (
        f"You are a hardware optimization expert.\n\n"
        f"Provide safe operational thresholds for the model '{model_name}'.\n\n"
        "Return a valid JSON object with the following structure:\n"
        "- thresholds: an object containing key-value pairs where each key is a threshold type (e.g., 'max_temperature', 'min_fan_speed', 'expected_voltage', etc.) and each value is the corresponding threshold value.\n"
        "- reference: a string that must be a real online URL starting with 'http' pointing to the source of the threshold information.\n\n"
        "Important: The 'reference' field must be a verifiable link to a real source (e.g., datasheet, maintenance guide, or official documentation). Do not invent general text or use placeholders. Only return the JSON object.\n\n"
        "Examples:\n"
        "For a CPU: {{ 'thresholds': {{ 'max_temperature': 95, 'min_fan_speed': 1000, 'expected_voltage': 1.2, 'voltage_tolerance': 0.05 }}, 'reference': 'https://example.com/cpu-datasheet' }}\n"
        "For a GPU: {{ 'thresholds': {{ 'max_temperature': 85, 'max_power_consumption': 250 }}, 'reference': 'https://example.com/gpu-specs' }}"
    )

    payload = {
        "model": "grok-3-latest",
        "messages": [
            {"role": "system", "content": "You are a performance optimization expert for Linux systems."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0
    }

    for attempt in range(1, 4):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=120)
            resp.raise_for_status()
            content = resp.json().get("choices", [])[0].get("message", {}).get("content", "")
            parsed = json.loads(content)
            ref = parsed.get("reference", "")
            if not ref.startswith("http"):
                raise ValueError("Missing or invalid 'reference' URL in Grok response.")
            return parsed
        except Exception as e:
            if attempt == 3:
                raise e
            time.sleep(5)

def main():
    # Locate API key file
    script_dir = os.path.dirname(os.path.abspath(__file__))
    default_api_key_paths = [
        os.path.join(script_dir, 'api_key.txt'),
        os.path.expanduser('~/api_key.txt'),
        os.path.join(script_dir, 'LinuxVM', 'api_key.txt'),
        os.path.expanduser('~/LinuxVM/api_key.txt'),
    ]
    api_key_file = None
    for path in default_api_key_paths:
        if os.path.exists(path):
            api_key_file = path
            break
    if not api_key_file:
        print("Could not find 'api_key.txt' in default locations.")
        api_key_file = input("Enter path to 'api_key.txt' (or press Enter to exit): ").strip()
        if not api_key_file or not os.path.exists(api_key_file):
            print("API key file not found. Exiting.")
            sys.exit(1)

    with open(api_key_file, 'r', encoding='utf-8-sig') as f:
        api_key = f.read().strip()
    if not api_key:
        print("API key is empty. Exiting.")
        sys.exit(1)

    # Get hardware list file path from user
    hardware_list_path = "system_info.txt"

    # Read hardware list
    with open(hardware_list_path, 'r', encoding='utf-8') as f:
        components = [line.strip() for line in f if line.strip()]
    if not components:
        print("Hardware list is empty. Exiting.")
        sys.exit(1)

    all_thresholds = {}
    failed_models = []
    for component in components:
        print(f"Fetching thresholds for '{component}'...")
        try:
            thresholds = fetch_thresholds_from_grok(component, api_key)
            thresholds['last_updated'] = date.today().isoformat()
            all_thresholds[component] = thresholds
        except Exception as e:
            print(f"Failed: {e}")
            failed_models.append(component)

    if not all_thresholds:
        print("No thresholds were fetched successfully. Exiting.")
        sys.exit(1)

    # Ask to save output
    #save_to_file = input("Do you want to save the thresholds to a file? (y/n): ").strip().lower()
    '''if save_to_file == 'y':
        output_path = input("Enter the output file path: ").strip()
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(all_thresholds, f, indent=2)
        print(f"Saved thresholds to '{output_path}'")'''
    
    print("Thresholds fetched:")
    print(json.dumps(all_thresholds, indent=2))

    if failed_models:
        print("\nFailed to fetch thresholds for the following models:")
        for model in failed_models:
            print(f"- {model}")

    return str(all_thresholds)

if __name__ == "__main__":
    main()