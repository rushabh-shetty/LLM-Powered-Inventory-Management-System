# LLM Inventory Management System

## Table of Content

- [Team](#Team) 
- [Project Summary](#project-summary)
- [Repository Organization](#repository-organization)
- [Getting Started](#getting-started)
- [Requirements](#requirements)
- [How to Run](#how-to-run)
- [Troubleshooting](#troubleshooting)
- [Demo Video](#demo-video)
- [Final Report](#final-report)

## Team

**Rushabh Prasad Shetty**, Team Lead

<img src= "imgs/Headshot_Rushabh_Shetty.jpeg" alt="Rushabh Shetty" width="150"/><br>

Hello! I’m Rushabh Prasad Shetty, an Industrial Engineering graduate student at the University of Illinois Urbana-Champaign, on track to complete my degree in Fall 2025. Over the past several years, I’ve built a strong foundation in manufacturing and automobile engineering, complemented by hands-on experience implementing SAP solutions for enterprise-scale clients. I’m passionate about leveraging data analytics and machine learning to drive process improvements. I’m actively seeking internship opportunities this summer and will be available for full-time roles beginning immediately after graduation in Fall 2025.

Email: shetty6@illinois.edu<br>
LinkedIn: https://www.linkedin.com/in/rushabh-shetty/

**Alejandro J. Chavez Ramos**, Data Analyst

<img src="imgs/Alejandro.jpeg" alt="Alejandro" width="150"/> <br>

Hello, my name is Alejandro Chavez Ramos. I am currently in my second semester pursuing an MS Degree in Industrial Engineering at the University of Illinois at Urbana-Champaign, with an expected graduation at the end of Fall 2025. My background includes experience devising, implementing, and controlling continuous improvement projects using Six Sigma and Lean methodologies. I am particularly passionate about optimization, which is why I am enrolled in this MS program. I am open to work opportunities this summer. 

Email: ajc21@illinois.edu <br>
LikedIn: www.linkedin.com/in/ajc21

## Project Summary

**Objective**

Develop an intelligent hardware inventory management system for Linux-based virtual machines (VMs) to automate hardware data collection, monitoring, and optimization. The system will query hardware specifications, monitor operational parameters, suggest performance optimizations, and provide upgrade recommendations, leveraging xAI's Grok 3 for enhanced data processing and decision-making.

**Background**

Modern data centers and enterprise environments rely on diverse hardware configurations, making manual inventory tracking and optimization challenging. Tools like dmidecode, cat /proc/cpuinfo, cat /proc/meminfo, lspci, lsusb, and lsblk provide raw hardware data, but lack integration for automated analysis, monitoring, and optimization. This project addresses the need for a comprehensive system to streamline hardware management, improve performance, and reduce operational risks.

**Impact and Relevance**

This system will enhance operational efficiency by:<br>
- Automating hardware inventory collection, reducing manual effort.<br>
- Providing real-time monitoring and alerts based on critical thresholds (e.g., temperature, clock frequency, voltage).<br>
- Optimizing system performance through reconfiguration recommendations (e.g., adjusting clock frequencies based on RMA capabilities).<br>
- Supporting cost-effective upgrade decisions by analyzing free DIMM slots, compatible memory types, and trade-offs (e.g., performance vs. power consumption). <br>
- Ensuring reliability by tracking environmental limits (e.g., max/min temperature, humidity, altitude, ESD sensitivity).<br>

**Approach**

1. Hardware Data Collection:<br>
    - Develop scripts to query hardware details using Linux tools (dmidecode, lspci, lsusb, lsblk, ethtool, numactl, ipmitool, sensors, perf, etc.).
    - Document data sources for CPUs (sockets, cores, frequency, mitigations), PCIe devices (RAID controllers, GPUs, NICs, NUMA mappings), memory (hardware, hugepages), USB devices, disks, and environmental parameters.
    - Extract NIC-specific details (firmware, model, DPDK/RDMA status, performance metrics) locally; delegate fetching of NIC documentation (primary page, specifications, manuals) to a frontend reader via Grok Q&A.
    - Allow users to define custom commands for inclusion in the analysis.
    - Outputs: system_info.txt holds all command outputs; metrics_config.json logs the commands used. Both files act as inputs for downstream modules.
2. Intelligent Monitoring:
    - Collect real-time system metrics (CPU temperature, fan speed, network packet drops, ping latency) using sensor commands for reliable data capture.
    - Enable or disable metrics the user wants to monitor via a dynamic menu system.
    - Configure thresholds for each metric through manual input or Grok 3–recommended values.
    - Stores metric settings and thresholds in a JSON configuration file (monitor_settings.json) for persistent, user-editable setups.
    - Logs metrics, thresholds, and violations to a JSON file (monitor_log.json) with timestamps and statistics for detailed analysis and debugging.
    - Display metrics in a dynamic table, updated every 5 seconds (or any user-defined interval), showing current value, max, min, mean, and standard deviation for quick performance analysis.
3. Performance Optimization:
    - Leverage Grok 3 to analyze system metrics, recommend safe tuning commands, and set performance targets for dynamic metrics (e.g., enp0s1_rx_queue_0_drops < 10).
    - Optimize latency and throughput via hardware-specific adjustments (e.g., NIC ring buffer tuning, IRQ affinity distribution, enabling GSO where supported).
    - Generate an interactive tuning script (tune_system.sh) with safeguards (e.g., timeout for long-running commands, IRQ existence checks).
    - Validate optimizations by comparing pre- and post-tuning dynamic metrics (e.g., ping_rtt, enp0s1_rx_queue_0_packets) in a tabulated report.
4. Upgrade Recommendations:
    - Analyze system metrics (CPU, memory, NICs, storage, and custom sections) from system_info.txt for high-frequency trading optimization.
    - Generate detailed analyses focusing on general performance or specific hardware categories, considering user-specified budgets.
    - Provide cost-benefit analyses for hardware upgrades, including partial additions (e.g., RAM, SSDs) and full replacements (e.g., CPUs, NICs), with titles, descriptions, and estimated costs.
    - Evaluate performance impacts (e.g., latency reduction, throughput gains) and trade-offs (e.g., cost vs. compatibility, virtualization overhead).
    - Stream recommendations incrementally in a formatted report with a typewriter effect for real-time display.
    - Support interactive Q&A to refine recommendations, streamed conversationally.
    - Cache results for fast repeated runs, with automatic inclusion of new system metrics.
5. Integration with xAI:
    - Deploy the system on xAI’s Grok 3 platform to process complex queries, generate insights, and automate documentation.
6. Frontend and Backend Integration:
    - Develop a Flask-based backend (server.py) to handle API requests for data collection, monitoring, performance optimization, upgrade recommendations, and Grok-powered Q&A interactions.
    - Implement SocketIO for real-time, bidirectional communication, enabling dynamic updates to the frontend (e.g., live monitoring tables, streaming recommendations).
    - Create a modular frontend interface (index.html) with four intuitive tabs (Main, Monitor, Performance, Upgrade) for streamlined user interaction.
    - Enable the Main tab to trigger data collection, display system_info.txt, and support custom metric definitions via a user-friendly form.
    - Design the Monitor tab to present real-time metrics in a dynamic, color-coded table, with controls for enabling/disabling metrics, setting thresholds, and downloading logs.
    - Configure the Performance tab to select optimization profiles, run analyses, display pre/post-tuning reports, and download tune_system.sh scripts.
    - Build the Upgrade tab to input budgets, select analysis types, view recommendations with costs and benefits, and interact via a dedicated Grok Q&A.
    - Ensure cross-tab consistency by integrating custom metrics and settings from metrics_config.json and monitor_settings.json.
    - Provide downloadable reports (e.g., system_info.txt, monitor_log.json) and scripts via API endpoints for user accessibility.
    - Support secure, RESTful API interactions with error handling and validation to ensure robust operation across diverse server environments.

**Innovation**
- Automation: Combines disparate Linux tools into a unified system for seamless hardware querying and monitoring.
- Intelligent Analysis: Uses Grok 3 to interpret raw data, predict optimal configurations, and suggest upgrades based on real-time market and environmental data.
- Dynamic Optimization: Adapts system parameters (e.g., clock frequencies) based on RMA tolerances and environmental conditions, surpassing default conservative settings.
- Scalability: Designed for Linux, ensuring compatibility across diverse enterprise environments.

**Future Work**

- Expand support for additional hardware types (e.g., NVMe drives, TPUs), and operating systems.
- Integrate predictive maintenance models to forecast hardware failures based on sensor trends.
- Enhance upgrade recommendations with lifecycle cost analyses, including energy efficiency and depreciation.

## Repository Organization
<ul>
    <li>group_19_project
        <ul>
            <ul>
                <li>collect_data.py</li>
                <li>index.html</li>
                <li>m_monitor_system_analyzer.py</li>
                <li>m_monitor_system.py</li>
                <li>performance_optimizer.py</li>
                <li>rag_implement_v3.py</li>
                <li>server.py</li>
                <li>upgrade_recommender.py</li>
                <li>upgrade_recommender.py</li>
                <li>imgs</li>
                <li>Group_19_Final Report.pdf</li>
                <li>README.md</li>
            </ul>
        </ul>
    </li>
</ul>


## Getting Started

You can either download the repository from the gitlab website 
Or clone it using command lines via Git:

```
cd existing_repo
git clone https://gitlab.engr.illinois.edu/ie421_high_frequency_trading_spring_2025/ie421_hft_spring_2025_group_19/group_19_project.git
```


## Requirements

Here is a list of all the libraries you would require to run the code
- requests
- tqdm
- flask
- flask-socketio
- langchain
- langchain-community
- langchain-xai
- sentence-transformers
- tabulate
- colorama
- markdown

You can install all these requirements through pip install using the command given below:

```
pip install requests tqdm flask flask-socketio langchain langchain-community langchain-xai sentence-transformers tabulate colorama markdown

```


You would also need a xAI API key to run this software.
It is recommended to store your xAI API key as a .txt file and place it in the Code_Final folder.

## How to Run
- You can run this software by executing server.py and clicking open the endpoint that is generated in the terminal or pasting it in any internet browser of your choice.
- Because of the modularity, each module (collect_data.py, m_monitor_system.py, performance_optimizer.py and upgrade_recommender.py) can be executed as standalone in a terminal.

## Troubleshooting
- Please ensure that you have all the necessary libraries installed.
- Have stored your xAI API key in the Code_Final folder as 'api_key.txt'.
- If it’s the first time running, start with collect_data.py, or interacting with Grok Q&A in the main tab (if using server.py); this will generate the required inputs (system_info.txt and config_info.json) for the other modules. 

***

## Demo Video

A video demonstrating how the project works: [Demo_Video_Group 19](https://uofi.app.box.com/file/1866996404946)

## Final Report

Final Report of our project: [Report](https://gitlab.engr.illinois.edu/ie421_high_frequency_trading_spring_2025/ie421_hft_spring_2025_group_19/group_19_project/-/blob/main/Group_19_Final%20Report.pdf?ref_type=heads)


## License
For open source projects, say how it is licensed.

