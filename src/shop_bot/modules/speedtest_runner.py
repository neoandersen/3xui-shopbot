"""Speedtest Runner Module for NeoVPN ShopBot.
Provides compatibility re-exports and parsing utilities from shop_bot.data_manager.speedtest_runner.
"""
import json
import re

from shop_bot.data_manager.speedtest_runner import (
    COMMANDS,
    _extract_json_from_text,
    _ssh_exec_json,
    _parse_ookla_json,
    _parse_speedtest_cli_json,
    ssh_speedtest_for_host,
    net_probe_for_host,
    run_and_store_net_probe,
    run_and_store_ssh_speedtest,
    run_both_for_host,
    run_speedtests_for_all_hosts,
    auto_install_speedtest_on_host,
)


def parse_speedtest_output(raw_output: str) -> dict:
    """Robustly parse JSON object from speedtest stdout, returning metrics in Mbps and ms."""
    match = re.search(r'\{.*\}', raw_output, re.DOTALL)
    if not match:
        raise ValueError("No JSON object found in speedtest output")
    
    data = json.loads(match.group(0))
    raw_dl = data.get("download", 0)
    raw_ul = data.get("upload", 0)
    if isinstance(raw_dl, dict):
        raw_dl = raw_dl.get("bandwidth", 0) * 8
    if isinstance(raw_ul, dict):
        raw_ul = raw_ul.get("bandwidth", 0) * 8
    raw_ping = data.get("ping", 0)
    if isinstance(raw_ping, dict):
        raw_ping = raw_ping.get("latency", 0)

    return {
        "download_mbps": round(float(raw_dl) / 1_000_000, 2),
        "upload_mbps": round(float(raw_ul) / 1_000_000, 2),
        "ping_ms": round(float(raw_ping), 2)
    }


__all__ = [
    "COMMANDS",
    "parse_speedtest_output",
    "_extract_json_from_text",
    "_ssh_exec_json",
    "_parse_ookla_json",
    "_parse_speedtest_cli_json",
    "ssh_speedtest_for_host",
    "net_probe_for_host",
    "run_and_store_net_probe",
    "run_and_store_ssh_speedtest",
    "run_both_for_host",
    "run_speedtests_for_all_hosts",
    "auto_install_speedtest_on_host",
]
