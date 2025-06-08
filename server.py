from flask import Flask, jsonify, request, send_from_directory
from m_monitor_system import fetch_thresholds_from_grok, get_hardware_context
from flask_socketio import SocketIO
import os
import json
import threading
import time
from datetime import datetime
import subprocess
import requests
import hashlib
from m_monitor_system import collect_metrics
from performance_optimizer import locate_file as perf_locate_file, check_config_age, get_profile_sections, get_dynamic_metrics, analyze_system as perf_analyze_system, get_grok_targets, collect_validation_metrics, apply_tuning
from upgrade_recommender import locate_file as upgrade_locate_file, parse_system_info, generate_summary, analyze_system as upgrade_analyze_system, post_report_qa, CATEGORIES, save_outputs
import sys
import markdown
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))
import rag_implement_v3
import shutil


def locate_file(filename, real_time=False):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    possible_paths = [
        os.path.join(script_dir, filename),
        os.path.join(script_dir, "LinuxVM", filename),
        os.path.join(script_dir, "Linux VM", filename),
        os.path.expanduser(os.path.join("~", filename)),
        os.path.expanduser(os.path.join("~", "LinuxVM", filename)),
    ]
    for path in possible_paths:
        try:
            if real_time:
                os.stat(path)  # Force a fresh filesystem check
            if os.path.exists(path):
                print(f"locate_file: Found {filename} at {path} (real_time={real_time})")
                return path
        except (OSError, FileNotFoundError):
            continue
    print(f"locate_file: Could not find {filename} in any path (real_time={real_time})")
    return None

def is_valid_metric(metric):
    output = run_command(metric["command"])
    if output is None:
        return False
    if metric["type"] == "dynamic_single":
        try:
            float(output.strip())
            return True
        except ValueError:
            return False
    return bool(output.strip())


app = Flask(__name__, static_folder='.')
socketio = SocketIO(app, cors_allowed_origins="*")  # Allow all origins for development

# Constants
DEFAULT_SETTINGS_PATH = "monitor_settings.json"
DEFAULT_LOG_PATH = "monitor_log.json"
METRICS_CONFIG_PATH = "metrics_config.json"
TUNE_LOG_PATH = "tune_log.txt"

# Global state
monitoring_active = False
background_monitor_thread = None
metrics_history = []

@app.route('/api/available_metrics', methods=['GET', 'POST'])
def available_metrics():
    metrics_config = load_json(METRICS_CONFIG_PATH, {"sections": [{"metrics": []}]})
    settings = load_json(DEFAULT_SETTINGS_PATH, {"general": {"interval": 5, "max_rows": 5, "ping_hosts": ["google.com"]}, "metrics": []})
    
    if request.method == 'GET':
        try:
            dynamic_single_metrics = [
                m["name"] for section in metrics_config.get("sections", [])
                for m in section.get("metrics", []) if m.get("type") == "dynamic_single" and "name" in m
            ]
            enabled_metrics = {m["name"] for m in settings.get("metrics", []) if m.get("enabled", False) and "name" in m}
            available = []
            for section in metrics_config.get("sections", []):
                for m in section.get("metrics", []):
                    if m.get("type") == "dynamic_single" and "name" in m:
                        thresholds = next((m["thresholds"] for m in settings["metrics"] if m.get("name") == m["name"]), {"alert": True, "max": None, "min": None})
                        available.append({
                            "name": m["name"],
                            "subsection": m.get("subsection", ""),
                            "enabled": m["name"] in enabled_metrics,
                            "thresholds": thresholds
                        })
            settings_metrics = [m["name"] for m in settings.get("metrics", []) if "name" in m]
            diff = len(dynamic_single_metrics) != len(set(settings_metrics))
            settings_exists = os.path.exists(DEFAULT_SETTINGS_PATH) and bool(load_json(DEFAULT_SETTINGS_PATH).get("metrics"))
            return jsonify({
                "success": True,
                "metrics": available,
                "diff": diff,
                "settings_configured": bool(settings.get("metrics")),
                "settings_exists": settings_exists
            })
        except Exception as e:
            print(f"Error processing available metrics: {e}")
            return jsonify({"success": False, "error": str(e)}), 500
    
    elif request.method == 'POST':
        data = request.json
        if data.get("generate") and not settings.get("metrics"):
            dynamic_single_metrics = [
                m["name"] for section in metrics_config.get("sections", [])
                for m in section.get("metrics", []) if m.get("type") == "dynamic_single" and "name" in m
            ]
            settings["metrics"] = [
                {"name": m, "enabled": m == "ping_rtt", "thresholds": {"alert": True, "max": 100.0 if m == "ping_rtt" else None, "min": 0.0 if m == "ping_rtt" else None}}
                for m in dynamic_single_metrics
            ]
            save_json(settings, DEFAULT_SETTINGS_PATH)
            socketio.emit('settings_updated')
            return jsonify({"success": True, "message": "Settings generated"})
        
        if data.get("regenerate"):
            dynamic_single_metrics = [
                m["name"] for section in metrics_config.get("sections", [])
                for m in section.get("metrics", []) if m.get("type") == "dynamic_single" and "name" in m
            ]
            current_metrics = {m["name"]: m for m in settings.get("metrics", []) if "name" in m}
            settings["metrics"] = []
            for metric_name in dynamic_single_metrics:
                current = current_metrics.get(metric_name, {})
                enabled = current.get("enabled", metric_name == "ping_rtt")
                thresholds = current.get("thresholds", {"alert": True, "max": 100.0 if metric_name == "ping_rtt" else None, "min": 0.0 if metric_name == "ping_rtt" else None})
                settings["metrics"].append({
                    "name": metric_name,
                    "enabled": enabled,
                    "thresholds": thresholds
                })
            save_json(settings, DEFAULT_SETTINGS_PATH)
            socketio.emit('settings_updated')
            return jsonify({"success": True, "message": "Settings regenerated"})
        
        metric_name = data.get("name")
        action = data.get("action")
        if metric_name and action in ["enable", "disable"]:
            metric_found = False
            for metric in settings.get("metrics", []):
                if metric.get("name") == metric_name:
                    metric["enabled"] = (action == "enable")
                    if not metric.get("thresholds"):
                        metric["thresholds"] = {"alert": True, "max": None, "min": None}
                    metric_found = True
                    break
            if not metric_found:
                settings["metrics"].append({
                    "name": metric_name,
                    "enabled": (action == "enable"),
                    "thresholds": {"alert": True, "max": None, "min": None}
                })
            save_json(settings, DEFAULT_SETTINGS_PATH)
            socketio.emit('settings_updated')
            return jsonify({"success": True, "message": f"{metric_name} {'enabled' if action == 'enable' else 'disabled'}"})
        return jsonify({"success": False, "message": "Invalid request"}), 400

@app.route('/api/regenerate_monitor_settings', methods=['GET'])
def regenerate_monitor_settings():
    try:
        metrics_config = load_json(METRICS_CONFIG_PATH, {"sections": [{"metrics": []}]})
        settings = load_json(DEFAULT_SETTINGS_PATH, {"general": {"interval": 5, "max_rows": 5, "ping_hosts": ["google.com"]}, "metrics": []})
        
        dynamic_single_metrics = [
            m["name"] for section in metrics_config.get("sections", [])
            for m in section.get("metrics", []) if m.get("type") == "dynamic_single" and "name" in m
        ]
        current_metrics = {m["name"]: m for m in settings.get("metrics", []) if "name" in m}
        general_settings = settings.get("general", {"interval": 5, "max_rows": 5, "ping_hosts": ["google.com"]})
        settings["metrics"] = []
        for metric_name in dynamic_single_metrics:
            current = current_metrics.get(metric_name, {})
            enabled = current.get("enabled", metric_name == "ping_rtt")
            thresholds = current.get("thresholds", {"alert": True, "max": 100.0 if metric_name == "ping_rtt" else None, "min": 0.0 if metric_name == "ping_rtt" else None})
            settings["metrics"].append({
                "name": metric_name,
                "enabled": enabled,
                "thresholds": thresholds
            })
        settings["general"] = general_settings
        save_json(settings, DEFAULT_SETTINGS_PATH)
        socketio.emit('settings_updated')
        return jsonify({"success": True, "message": "monitor_settings.json regenerated"})
    except Exception as e:
        print(f"Error regenerating monitor_settings.json: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/grok_thresholds', methods=['POST'])
def grok_thresholds():
    try:
        data = request.json
        metric_name = data.get('metric_name')
        if not metric_name:
            return jsonify({"success": False, "error": "Metric name is required"}), 400
        
        metrics_config = load_json(METRICS_CONFIG_PATH, {"sections": [{"metrics": []}]})
        dynamic_metrics = [
            m["name"] for section in metrics_config.get("sections", [])
            for m in section.get("metrics", []) if m.get("type") == "dynamic_single"
        ]
        if metric_name not in dynamic_metrics:
            return jsonify({"success": False, "error": f"Invalid metric: {metric_name}"}), 400
        
        hardware = get_hardware_context(metric_name)
        grok_data = fetch_thresholds_from_grok(metric_name, hardware)
        thresholds = grok_data["thresholds"]
        reference = grok_data["reference"]
        
        return jsonify({
            "success": True,
            "thresholds": {"max": thresholds["max"], "min": thresholds["min"]},
            "reference": reference
        })
    except Exception as e:
        print(f"Error fetching Grok thresholds: {e}")
        error_msg = str(e)
        if "Invalid API key" in error_msg:
            error_msg = "API key invalid. Check api_key.txt or contact the administrator."
        return jsonify({"success": False, "error": error_msg}), 500

def load_json(path, default=None):
    if not os.path.exists(path):
        return default if default is not None else {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading {path}: {e}")
        return default if default is not None else {}

def save_json(data, path):
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        raise Exception(f"Error writing {path}: {e}")

def run_command(cmd):
    try:
        env = os.environ.copy()
        env["PATH"] = "/bin:/usr/bin:/usr/local/bin"
        proc = subprocess.run(cmd, capture_output=True, text=True, shell=True, check=False, env=env)
        if proc.returncode != 0:
            print(f"Warning: Command failed - Return code: {proc.returncode}, Error: {proc.stderr.strip()}")
            return ""
        output = proc.stdout.strip()
        if "ping -c 1" in cmd:
            print(f"Debug: Raw output for command '{cmd}': {output}")
        return output
    except Exception as e:
        print(f"Warning: Command failed - {e}")
        return ""

def background_monitor_loop():
    global monitoring_active, metrics_history
    settings = load_json(DEFAULT_SETTINGS_PATH, {"general": {"interval": 5}, "metrics": []})
    metrics_config = load_json(METRICS_CONFIG_PATH, {"sections": [{"metrics": []}]})
    log_data = load_json(DEFAULT_LOG_PATH, [])
    while monitoring_active:
        metrics, violations = collect_metrics(settings, metrics_config)
        if metrics:
            entry = {
                "timestamp": datetime.now().strftime("%H:%M:%S"),
                "metrics": metrics,
                "violations": violations
            }
            metrics_history.append(entry)
            log_data.append(entry)
            save_json(log_data, DEFAULT_LOG_PATH)
            print(f"Emitting metrics_update: {entry}")
            socketio.emit('metrics_update', metrics_history[-10:])
        else:
            print("No metrics collected in this cycle")
        time.sleep(settings["general"].get("interval", 5))

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/collect', methods=['GET'])
def collect():
    try:
        result = subprocess.run(['python3', 'collect_data.py'], capture_output=True, text=True, check=True)
        with open('system_info.txt', 'r') as f:
            data = f.read()
        return jsonify(success=True, data=data)
    except Exception as e:
        return jsonify(success=False, error=str(e)), 500

@app.route('/grok', methods=['POST'])
def grok():
    prompt = request.json.get('prompt', '')
    try:
        answer = markdown.markdown(rag_implement_v3.main(prompt))
        return jsonify(success=True, response=answer)
    except Exception as e:
        return jsonify(success=False, error=str(e)), 500

@app.route('/api/settings', methods=['GET', 'POST'])
def settings():
    if request.method == 'GET':
        settings = load_json(DEFAULT_SETTINGS_PATH, None)
        settings_exists = os.path.exists(DEFAULT_SETTINGS_PATH) and bool(load_json(DEFAULT_SETTINGS_PATH, {}).get("metrics", []))
        if settings is None:
            return jsonify({
                "success": True,
                "settings_configured": False,
                "settings": None,
                "settings_exists": settings_exists
            })
        return jsonify({
            "success": True,
            "settings_configured": bool(settings.get("metrics")),
            "settings": settings,
            "settings_exists": settings_exists
        })
    elif request.method == 'POST':
        try:
            data = request.json
            if not isinstance(data, dict) or 'general' not in data or 'metrics' not in data:
                return jsonify({"success": False, "error": "Invalid settings format"}), 400
            if not isinstance(data['general'].get('ping_hosts'), list):
                return jsonify({"success": False, "error": "ping_hosts must be a list"}), 400
            save_json(data, DEFAULT_SETTINGS_PATH)
            socketio.emit('settings_updated')
            return jsonify({"success": True})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/menu', methods=['GET'])
def menu():
    monitoring_active = globals().get('monitoring_active', False)
    menu = {
        "name": "Main Menu",
        "items": [
            {"name": "Continue monitoring", "action": "continue_monitoring"} if monitoring_active else {"name": "Start monitoring", "action": "start_monitoring"},
            {"name": "Stop monitoring", "action": "stop_monitoring"} if monitoring_active else {
                "name": "Configure settings",
                "items": [
                    {"name": "General settings", "action": "configure_general"},
                    {"name": "Enable/disable metrics", "action": "configure_metrics"},
                    {"name": "Manage thresholds", "action": "configure_thresholds"}
                ]
            },
            {"name": "View logs", "action": "view_logs"},
            {"name": "Exit", "action": "exit"}
        ]
    }
    return jsonify({"success": True, "menu": menu})

@app.route('/api/monitor', methods=['POST'])
def monitor():
    global monitoring_active, background_monitor_thread
    action = request.json.get('action')
    if action == 'start_monitoring' and not monitoring_active:
        monitoring_active = True
        background_monitor_thread = threading.Thread(target=background_monitor_loop)
        background_monitor_thread.start()
        return jsonify({"success": True, "message": "Monitoring started"})
    elif action == 'stop_monitoring' and monitoring_active:
        monitoring_active = False
        if background_monitor_thread:
            background_monitor_thread.join()
        background_monitor_thread = None
        return jsonify({"success": True, "message": "Monitoring stopped"})
    return jsonify({"success": False, "message": "Invalid action or state"})

@app.route('/api/logs', methods=['GET'])
def logs():
    logs = load_json(DEFAULT_LOG_PATH, [])
    return jsonify({"success": True, "logs": logs[-10:]})

@app.route('/api/view_monitor_log', methods=['GET'])
def view_monitor_log():
    try:
        if not os.path.exists(DEFAULT_LOG_PATH):
            return jsonify({"success": False, "error": "monitor_log.json not found"}), 404
        return send_from_directory(
            os.path.dirname(DEFAULT_LOG_PATH),
            os.path.basename(DEFAULT_LOG_PATH),
            as_attachment=True,
            download_name='monitor_log.json',
            mimetype='application/json'
        )
    except Exception as e:
        print(f"Error serving monitor_log.json: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

# Performance Endpoints
@app.route('/api/performance/files', methods=['GET'])
def performance_files():
    try:
        files = {
            "api_key.txt": {"exists": bool(locate_file("api_key.txt", real_time=True))},
            "metrics_config.json": {"exists": bool(locate_file("metrics_config.json", real_time=True))},
            "system_info.txt": {"exists": bool(locate_file("system_info.txt", real_time=True))}
        }
        return jsonify({"success": True, "files": files})
    except Exception as e:
        print(f"Error checking files: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/performance/profiles', methods=['GET'])
def performance_profiles():
    try:
        profiles = [
            {"value": "latency", "label": "Latency Profile"},
            {"value": "throughput", "label": "Throughput Profile"},
            {"value": "balanced", "label": "Balanced Profile"}
        ]
        return jsonify({"success": True, "profiles": profiles})
    except Exception as e:
        print(f"Error fetching profiles: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/performance/categories', methods=['GET'])
def performance_categories():
    try:
        config = load_json(METRICS_CONFIG_PATH, {"sections": []})
        categories = [s["title"] for s in config["sections"] if s.get("metrics")]
        if not categories:
            return jsonify({"success": False, "error": "No categories found in metrics_config.json"}), 400
        return jsonify({"success": True, "categories": categories + ["All"]})
    except Exception as e:
        print(f"Error fetching categories: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/performance/dynamic-metrics', methods=['GET'])
def performance_dynamic_metrics():
    try:
        profile = request.args.get('profile', '')
        category = request.args.get('category', '')
        if not (profile or category):
            return jsonify({"success": False, "error": "Profile or category required"}), 400
        config = load_json(METRICS_CONFIG_PATH, {"sections": []})
        sections = get_profile_sections(config, profile) if profile else [category] if category != "All" else [s["title"] for s in config["sections"]]
        metrics = get_dynamic_metrics(config, sections)
        return jsonify({"success": True, "metrics": [m["name"] for m in metrics]})
    except Exception as e:
        print(f"Error fetching dynamic metrics: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/performance/run', methods=['POST'])
def performance_run():
    try:
        data = request.json
        profile = data.get('profile', '')
        category = data.get('category', '')
        if not (profile or category):
            socketio.emit('progress_update', {"step": "Profile or category required", "percent": 0, "error": "Profile or category required"})
            return jsonify({"success": False, "error": "Profile or category required"}), 400

        api_key_path = locate_file("api_key.txt")
        if not api_key_path:
            socketio.emit('progress_update', {"step": "api_key.txt not found", "percent": 0, "error": "api_key.txt not found"})
            return jsonify({"success": False, "error": "api_key.txt not found"}), 400
        with open(api_key_path, "r", encoding="utf-8-sig") as f:
            api_key = f.read().strip()
        if not api_key:
            socketio.emit('progress_update', {"step": "API key is empty", "percent": 0, "error": "API key is empty"})
            return jsonify({"success": False, "error": "API key is empty"}), 400

        config_path = locate_file("metrics_config.json")
        if not config_path:
            socketio.emit('progress_update', {"step": "metrics_config.json not found", "percent": 0, "error": "metrics_config.json not found"})
            return jsonify({"success": False, "error": "metrics_config.json not found"}), 400
        check_config_age(config_path)
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)

        system_info_path = locate_file("system_info.txt")
        system_info = None
        if system_info_path:
            with open(system_info_path, "r", encoding="utf-8", errors="ignore") as f:
                system_info = f.read().strip()

        sections = get_profile_sections(config, profile) if profile else [category] if category != "All" else [s["title"] for s in config["sections"]]
        sections = [s["title"] if isinstance(s, dict) and "title" in s else str(s) for s in sections]
        dynamic_metrics = get_dynamic_metrics(config, sections)

        # Step 1 & 2: Collect and Analyze
        retries = 3
        for attempt in range(retries):
            try:
                analysis, recommendations = perf_analyze_system(
                    api_key, {}, system_info, profile or None, sections,
                    progress_callback=lambda x: socketio.emit('progress_update', x)
                )
                break
            except Exception as e:
                if attempt == retries - 1:
                    error = f"Analysis failed after {retries} attempts: {e}"
                    socketio.emit('progress_update', {"step": "Analysis failed", "percent": 50, "error": error})
                    return jsonify({"success": False, "error": error}), 500
                time.sleep(1)

        # Step 3: Validate Pre-Tuning
        validation = []
        targets = []
        pre_metrics = {}
        if dynamic_metrics:
            pre_metrics = collect_validation_metrics(
                dynamic_metrics, duration=5,
                progress_callback=lambda x: socketio.emit('progress_update', x),
                percent_start=75, percent_end=85
            )
            if pre_metrics:
                validation = [{"metric": k, "pre": v, "post": "N/A"} for k, v in pre_metrics.items()]
            else:
                socketio.emit('progress_update', {"step": "No pre-tuning metrics collected", "percent": 80, "error": "No pre-tuning metrics collected"})
                print("Warning: No pre-tuning metrics collected")

            targets = get_grok_targets(
                api_key, {}, system_info, profile or None, sections,
                progress_callback=lambda x: socketio.emit('progress_update', x)
            )
            validation = [{"metric": k, "pre": pre_metrics.get(k, "N/A"), "post": "N/A"} for k in pre_metrics]

        socketio.emit('progress_update', {"step": "Analysis complete", "percent": 100, "error": None})
        with open(TUNE_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now()}: Ran analysis for {profile or category} ({', '.join(sections)})\n")

        return jsonify({
            "success": True,
            "analysis": analysis,
            "recommendations": recommendations,
            "dynamic_metrics": [{"name": m["name"], "command": m["command"]} for m in dynamic_metrics],
            "validation": validation,
            "targets": targets,
            "pre_metrics": pre_metrics
        })
    except Exception as e:
        error = f"Analysis failed: {e}"
        socketio.emit('progress_update', {"step": "Analysis failed", "percent": 25, "error": error})
        print(f"Error running analysis: {e}")
        return jsonify({"success": False, "error": error}), 500

@app.route('/api/performance/tune', methods=['POST', 'GET'])
def performance_tune():
    try:
        # For GET requests (download action)
        if request.method == 'GET':
            action = request.args.get('action')
            if action != 'download':
                socketio.emit('progress_update', {"step": "Invalid action", "percent": 0, "error": "Invalid action"})
                return jsonify({"success": False, "error": "Invalid action"}), 400

            tune_script = locate_file("tune_system.sh", real_time=False)
            if not tune_script:
                socketio.emit('progress_update', {"step": "tune_system.sh not found", "percent": 0, "error": "tune_system.sh not found"})
                return jsonify({"success": False, "error": "tune_system.sh not found"}), 404

            with open(TUNE_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(f"{datetime.now()}: Downloaded tune_system.sh\n")
            return send_from_directory(
                os.path.dirname(tune_script),
                os.path.basename(tune_script),
                as_attachment=True,
                download_name='tune_system.sh',
                mimetype='text/x-shellscript'
            )

        # For POST requests (apply action)
        data = request.json
        action = data.get('action')
        if action not in ['apply', 'download']:
            socketio.emit('progress_update', {"step": "Invalid action", "percent": 0, "error": "Invalid action"})
            return jsonify({"success": False, "error": "Invalid action"}), 400

        rec_id = data.get('id')
        recommendations = data.get('recommendations', [])
        print(f"Received recommendations: {recommendations}")
        recommendations = [rec for rec in data.get('recommendations', []) if rec["id"] == rec_id]
        if not recommendations:
            error_msg = f"Recommendation {rec_id} not found in provided recommendations"
            socketio.emit('progress_update', {"step": error_msg, "percent": 0, "error": error_msg})
            return jsonify({"success": False, "error": error_msg}), 404
        rec = recommendations[0]

        blocklist = ['kill', 'rm', 'reboot']
        if any(word in ' '.join(rec["commands"]).lower() for word in blocklist) or 'thermal' in rec["title"].lower():
            socketio.emit('progress_update', {"step": "High-risk command blocked", "percent": 0, "error": "High-risk command blocked. Use script manually."})
            return jsonify({"success": False, "error": "High-risk command blocked. Use script manually."}), 403

        inputs = data.get('inputs', {})
        app_path = inputs.get("appPath", "")
        if "/path/to/hft_app" in ' '.join(rec["commands"]) and not app_path:
            socketio.emit('progress_update', {"step": "Application path required", "percent": 0, "error": "Application path required for numactl"})
            return jsonify({"success": False, "error": "Application path required for numactl"}), 400
        if app_path and not os.access(app_path, os.X_OK):
            socketio.emit('progress_update', {"step": "Invalid application path", "percent": 0, "error": "Invalid or non-executable application path"})
            return jsonify({"success": False, "error": "Invalid or non-executable application path"}), 400

        # Step 4 & 5: Tune and Validate Post-Tuning
        dynamic_metrics = data.get('dynamic_metrics', [])
        pre_metrics = data.get('pre_metrics', {})
        tuning_applied, validation = apply_tuning(
            [rec], inputs, dynamic_metrics, pre_metrics,
            progress_callback=lambda x: socketio.emit('progress_update', x)
        )

        if not tuning_applied:
            error = str(tuning_applied) if isinstance(tuning_applied, str) else "Tuning failed"
            socketio.emit('progress_update', {"step": error, "percent": 70, "error": error})
            return jsonify({"success": False, "error": error}), 500

        with open(TUNE_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now()}: Applied {rec['title']} (ID: {rec_id})\n")
        return jsonify({"success": True, "validation": validation})
    except Exception as e:
        error = f"Tuning failed: {e}"
        socketio.emit('progress_update', {"step": error, "percent": 70, "error": error})
        print(f"Error tuning: {e}")
        return jsonify({"success": False, "error": error}), 500

@app.route('/api/upgrade/files', methods=['GET'])
def upgrade_files():
    try:
        files = {
            "api_key.txt": {"exists": bool(locate_file("api_key.txt", real_time=True))},
            "system_info.txt": {"exists": bool(locate_file("system_info.txt", real_time=True))}
        }
        return jsonify({"success": True, "files": files})
    except Exception as e:
        print(f"Error checking upgrade files: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/upgrade/categories', methods=['GET'])
def upgrade_categories():
    try:
        categories = [c["name"] for c in CATEGORIES]  # Return only the names as a flat list
        return jsonify({"success": True, "categories": categories})
    except Exception as e:
        print(f"Error fetching upgrade categories: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/upgrade/run', methods=['POST'])
def upgrade_run():
    try:
        data = request.json
        analysis_type = data.get('analysis_type', 'general')
        category = data.get('category')
        budget = data.get('budget')
        if analysis_type == 'specific' and not category:
            socketio.emit('upgrade_progress_update', {"step": "Category required for specific analysis", "percent": 0, "error": "Category required for specific analysis"})
            return jsonify({"success": False, "error": "Category required for specific analysis"}), 400

        api_key_path = locate_file("api_key.txt")
        if not api_key_path:
            socketio.emit('upgrade_progress_update', {"step": "api_key.txt not found", "percent": 0, "error": "api_key.txt not found"})
            return jsonify({"success": False, "error": "api_key.txt not found"}), 400
        with open(api_key_path, "r", encoding="utf-8-sig") as f:
            api_key = f.read().strip()
        if not api_key:
            socketio.emit('upgrade_progress_update', {"step": "API key is empty", "percent": 0, "error": "API key is empty"})
            return jsonify({"success": False, "error": "API key is empty"}), 400

        system_info_path = locate_file("system_info.txt")
        if not system_info_path:
            socketio.emit('upgrade_progress_update', {"step": "system_info.txt not found", "percent": 0, "error": "system_info.txt not found"})
            return jsonify({"success": False, "error": "system_info.txt not found"}), 400

        sections, system_data = parse_system_info(system_info_path)
        if not sections:
            socketio.emit('upgrade_progress_update', {"step": "system_info.txt is empty or malformed", "percent": 0, "error": "system_info.txt is empty or malformed"})
            return jsonify({"success": False, "error": "system_info.txt is empty or malformed"}), 400

        data_hash = hashlib.sha256(system_data.encode("utf-8")).hexdigest()
        data = generate_summary(sections) if analysis_type == 'general' else sections.get(category, "")
        if not data:
            socketio.emit('upgrade_progress_update', {"step": f"No data for {category or 'general analysis'}", "percent": 0, "error": f"No data for {category or 'general analysis'}"})
            return jsonify({"success": False, "error": f"No data for {category or 'general analysis'}"}), 400

        analysis, recommendations = upgrade_analyze_system(
            api_key, data, analysis_type, category, budget,
            progress_callback=lambda x: socketio.emit('upgrade_progress_update', x)
        )

        output_dir = os.path.dirname(system_info_path)
        save_outputs(output_dir, sections, analysis, recommendations, data_hash, analysis_type, category, budget)

        return jsonify({
            "success": True,
            "analysis": analysis,
            "recommendations": recommendations,
            "system_summary": generate_summary(sections),
            "system_data": system_data
        })
    except Exception as e:
        socketio.emit('upgrade_progress_update', {"step": "Analysis failed", "percent": 0, "error": str(e)})
        print(f"Error running upgrade analysis: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/upgrade/qa', methods=['POST'])
def upgrade_qa():
    try:
        data = request.json
        question = data.get('question')
        analysis = data.get('analysis')
        recommendations = data.get('recommendations', [])
        system_data = data.get('system_data')
        if not question:
            socketio.emit('upgrade_qa_update', {"step": "Question required", "percent": 0, "error": "Question required"})
            return jsonify({"success": False, "error": "Question required"}), 400

        api_key_path = locate_file("api_key.txt")
        if not api_key_path:
            socketio.emit('upgrade_qa_update', {"step": "api_key.txt not found", "percent": 0, "error": "api_key.txt not found"})
            return jsonify({"success": False, "error": "api_key.txt not found"}), 400
        with open(api_key_path, "r", encoding="utf-8-sig") as f:
            api_key = f.read().strip()
        if not api_key:
            socketio.emit('upgrade_qa_update', {"step": "API key is empty", "percent": 0, "error": "API key is empty"})
            return jsonify({"success": False, "error": "API key is empty"}), 400

        answer = post_report_qa(
            api_key, analysis, recommendations, "", "", "", "", None, system_data, question,
            progress_callback=lambda x: socketio.emit('upgrade_qa_update', x)
        )

        return jsonify({"success": True, "answer": answer})
    except Exception as e:
        socketio.emit('upgrade_qa_update', {"step": "Q&A failed", "percent": 0, "error": str(e)})
        print(f"Error processing Q&A: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/upgrade/report', methods=['GET'])
def upgrade_report():
    try:
        report_path = locate_file("upgrade_report.txt", real_time=False)
        if not report_path:
            return jsonify({"success": False, "error": "upgrade_report.txt not found"}), 404
        return send_from_directory(
            os.path.dirname(report_path),
            os.path.basename(report_path),
            as_attachment=True,
            download_name='upgrade_report.txt',
            mimetype='text/plain'
        )
    except Exception as e:
        print(f"Error serving upgrade_report.txt: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/custom_metrics', methods=['GET'])
def get_custom_metrics():
    try:
        config = load_json(METRICS_CONFIG_PATH, {"custom_metrics": []})
        return jsonify({"success": True, "metrics": config.get("custom_metrics", [])})
    except Exception as e:
        print(f"Error fetching custom metrics: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/custom_metrics', methods=['POST'])
def add_custom_metric():
    try:
        data = request.json
        required_fields = ["name", "tool", "command", "type"]
        if not all(field in data for field in required_fields):
            return jsonify({"success": False, "error": "Missing required fields"}), 400
        if data["type"] not in ["static", "dynamic_single", "dynamic_multi"]:
            return jsonify({"success": False, "error": "Invalid metric type"}), 400
        if not shutil.which(data["tool"]):
            return jsonify({"success": False, "error": f"Tool '{data['tool']}' not found"}), 400
        
        metric = {
            "name": data["name"],
            "tool": data["tool"],
            "command": data["command"],
            "type": data["type"],
            "subsection": data.get("subsection", data["name"])
        }
        if not is_valid_metric(metric):
            return jsonify({"success": False, "error": "Invalid command output"}), 400
        
        config = load_json(METRICS_CONFIG_PATH, {"custom_metrics": [], "sections": []})
        if any(m["name"] == metric["name"] for m in config.get("custom_metrics", [])):
            return jsonify({"success": False, "error": "Metric name already exists"}), 400
        
        config["custom_metrics"].append(metric)
        for section in config["sections"]:
            if section["title"] == "Custom Metrics":
                section["metrics"].append(metric)
                break
        else:
            config["sections"].append({"title": "Custom Metrics", "metrics": [metric]})
        
        save_json(config, METRICS_CONFIG_PATH)
        socketio.emit('custom_metrics_updated')
        return jsonify({"success": True, "message": "Metric added"})
    except Exception as e:
        print(f"Error adding custom metric: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/custom_metrics/<name>', methods=['DELETE'])
def delete_custom_metric(name):
    try:
        config = load_json(METRICS_CONFIG_PATH, {"custom_metrics": [], "sections": []})
        config["custom_metrics"] = [m for m in config["custom_metrics"] if m["name"] != name]
        for section in config["sections"]:
            if section["title"] == "Custom Metrics":
                section["metrics"] = [m for m in section["metrics"] if m["name"] != name]
                break
        
        save_json(config, METRICS_CONFIG_PATH)
        socketio.emit('custom_metrics_updated')
        return jsonify({"success": True, "message": "Metric deleted"})
    except Exception as e:
        print(f"Error deleting custom metric: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5001, debug=True)