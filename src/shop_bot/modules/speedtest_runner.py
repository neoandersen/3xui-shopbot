"""Speedtest Runner Module for NeoVPN ShopBot.
Provides compatibility re-exports from shop_bot.data_manager.speedtest_runner.
"""
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

__all__ = [
    "COMMANDS",
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
