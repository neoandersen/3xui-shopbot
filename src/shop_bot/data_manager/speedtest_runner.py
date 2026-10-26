import os
import asyncio
import json
import logging
import re
from urllib.parse import urlparse

import aiohttp
import paramiko

from shop_bot.data_manager import database

logger = logging.getLogger(__name__)


def _load_paramiko_pkey(ssh_key_path: str | None, passphrase: str | None = None):
    """Load RSA, Ed25519, or ECDSA key from file with optional passphrase."""
    if not ssh_key_path or not os.path.isfile(ssh_key_path):
        return None
    key_classes = [paramiko.RSAKey, paramiko.Ed25519Key]
    if hasattr(paramiko, 'ECDSAKey'):
        key_classes.append(paramiko.ECDSAKey)
    for KeyClass in key_classes:
        try:
            return KeyClass.from_private_key_file(ssh_key_path, password=passphrase)
        except Exception:
            pass
        if passphrase:
            try:
                return KeyClass.from_private_key_file(ssh_key_path)
            except Exception:
                pass
    return None


def _parse_host_port_from_url(url: str) -> tuple[str | None, int | None, bool]:
    try:
        u = urlparse(url)
        host = u.hostname
        port = u.port
        is_https = (u.scheme == 'https')
        if port is None:
            port = 443 if is_https else 80
        return host, port, is_https
    except Exception:
        return None, None, False


async def net_probe_for_host(host_row: dict) -> dict:
    """Lightweight network probe from panel to host_url: TCP connect + HTTP GET / (HEAD).
    Returns dict with ok, ping_ms (TCP connect time), http_ms, error (if any).
    """
    url = (host_row.get('host_url') or '').strip()
    target_host, target_port, _ = _parse_host_port_from_url(url)
    result = {
        'ok': False,
        'method': 'net',
        'ping_ms': None,
        'jitter_ms': None,
        'download_mbps': None,
        'upload_mbps': None,
        'server_name': None,
        'server_id': None,
        'http_ms': None,
        'error': None,
    }
    if not target_host or not target_port:
        result['error'] = f'Invalid host_url: {url}'
        return result

    # TCP connect timing
    try:
        loop = asyncio.get_event_loop()
        start = loop.time()
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(target_host, target_port), timeout=10.0
        )
        tcp_ms = (loop.time() - start) * 1000.0
        result['ping_ms'] = round(tcp_ms, 2)
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass
    except Exception as e:
        result['error'] = f'TCP connect failed: {e}'
        return result

    # HTTP HEAD/GET timing
    try:
        async with aiohttp.ClientSession() as session:
            start = asyncio.get_event_loop().time()
            async with session.head(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                _ = resp.status
            http_ms = (asyncio.get_event_loop().time() - start) * 1000.0
            result['http_ms'] = round(http_ms, 2)
        result['ok'] = True
    except Exception:
        # fallback to GET if HEAD not supported
        try:
            async with aiohttp.ClientSession() as session:
                start = asyncio.get_event_loop().time()
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    _ = await resp.text()
                http_ms = (asyncio.get_event_loop().time() - start) * 1000.0
                result['http_ms'] = round(http_ms, 2)
            result['ok'] = True
        except Exception as e:
            result['error'] = f'HTTP failed: {e}'
    return result


COMMANDS = [
    "speedtest --json",
    "speedtest-cli --json",
    "/usr/local/bin/speedtest --json",
    "speedtest --accept-license --accept-gdpr -f json",
    "speedtest -f json"
]


def _extract_json_from_text(text: str) -> dict | None:
    """Robustly parse JSON object from text, ignoring surrounding noise/stderr logs."""
    if not text:
        return None
    clean = text.strip()
    try:
        data = json.loads(clean)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    
    # Extract bracketed JSON slice
    first_brace = clean.find("{")
    last_brace = clean.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        candidate = clean[first_brace:last_brace + 1]
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
            
    m = re.search(r"\{.*\}", clean, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return None


def _ssh_exec_json(ssh: paramiko.SSHClient, commands: list[str]) -> tuple[dict | None, str | None]:
    """Try commands sequentially; expect JSON on stdout. Returns (json_obj, error)."""
    for cmd in commands:
        try:
            stdin, stdout, stderr = ssh.exec_command(cmd, timeout=120)
            out = stdout.read().decode('utf-8', errors='ignore')
            err = stderr.read().decode('utf-8', errors='ignore')
            data = _extract_json_from_text(out)
            if data:
                return data, None
            if err:
                logger.debug(f"SSH cmd error ({cmd}): {err}")
        except Exception as e:
            logger.debug(f"SSH exec failed for '{cmd}': {e}")
            continue
    return None, 'No JSON output from speedtest commands'


def _parse_ookla_json(data: dict) -> dict:
    # Ookla CLI JSON format (-f json)
    try:
        ping_val = float(data.get('ping', {}).get('latency')) if data.get('ping') else None
        jitter_val = float(data.get('ping', {}).get('jitter')) if data.get('ping') else None
        down_bps = float(data.get('download', {}).get('bandwidth', 0)) * 8.0  # bytes/s -> bits/s
        up_bps = float(data.get('upload', {}).get('bandwidth', 0)) * 8.0
        server = data.get('server', {})
        dl_mbps = round(down_bps / 1_000_000.0, 2) if down_bps else None
        ul_mbps = round(up_bps / 1_000_000.0, 2) if up_bps else None
        p_ms = round(ping_val, 2) if ping_val is not None else None
        j_ms = round(jitter_val, 2) if jitter_val is not None else None
        return {
            'ping_ms': p_ms,
            'ping': p_ms,
            'jitter_ms': j_ms,
            'download_mbps': dl_mbps,
            'download_speed': dl_mbps,
            'upload_mbps': ul_mbps,
            'upload_speed': ul_mbps,
            'server_name': server.get('name'),
            'server_id': str(server.get('id')) if server.get('id') is not None else None,
        }
    except Exception:
        return {}


def _parse_speedtest_cli_json(data: dict) -> dict:
    # speedtest-cli (sivel 2.1.3+) JSON
    try:
        ping_val = float(data.get('ping')) if data.get('ping') is not None else None
        jitter_val = None
        down_bps = float(data.get('download', 0))  # bits per second
        up_bps = float(data.get('upload', 0))
        srv = data.get('server', {})
        dl_mbps = round(down_bps / 1_000_000.0, 2) if down_bps else None
        ul_mbps = round(up_bps / 1_000_000.0, 2) if up_bps else None
        p_ms = round(ping_val, 2) if ping_val is not None else None
        return {
            'ping_ms': p_ms,
            'ping': p_ms,
            'jitter_ms': jitter_val,
            'download_mbps': dl_mbps,
            'download_speed': dl_mbps,
            'upload_mbps': ul_mbps,
            'upload_speed': ul_mbps,
            'server_name': srv.get('name'),
            'server_id': str(srv.get('id')) if srv.get('id') is not None else None,
        }
    except Exception:
        return {}


async def ssh_speedtest_for_host(host_row: dict) -> dict:
    """Run speedtest on remote host via SSH using compatibility command list.
    Returns dict with ok, metrics, error.
    """
    result = {
        'ok': False,
        'method': 'ssh',
        'ping_ms': None,
        'ping': None,
        'jitter_ms': None,
        'download_mbps': None,
        'download_speed': None,
        'upload_mbps': None,
        'upload_speed': None,
        'server_name': None,
        'server_id': None,
        'error': None,
    }
    ssh_host = (host_row.get('ssh_host') or '').strip()
    ssh_port = int(host_row.get('ssh_port') or 22)
    ssh_user = (host_row.get('ssh_user') or '').strip()
    ssh_password = host_row.get('ssh_password')
    ssh_key_path = (host_row.get('ssh_key_path') or '').strip() or None

    if not ssh_host or not ssh_user:
        result['error'] = 'SSH settings are not configured for host'
        return result

    def _run_ssh() -> dict:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        pkey = _load_paramiko_pkey(ssh_key_path, ssh_password) if ssh_key_path else None
        ssh.connect(ssh_host, port=ssh_port, username=ssh_user, password=ssh_password, pkey=pkey, timeout=20)

        # Run commands list prioritizing speedtest-cli 2.1.3 --json compatibility
        data, err = _ssh_exec_json(ssh, COMMANDS)
        ssh.close()
        if data:
            if isinstance(data.get('download'), dict) and 'bandwidth' in data['download']:
                parsed = _parse_ookla_json(data)
            elif 'download' in data:
                parsed = _parse_speedtest_cli_json(data)
            else:
                parsed = _parse_ookla_json(data) or _parse_speedtest_cli_json(data)
            return {'ok': True, **parsed}
        return {'ok': False, 'error': err or 'unknown'}

    try:
        loop = asyncio.get_event_loop()
        out = await loop.run_in_executor(None, _run_ssh)
        result.update(out)
    except Exception as e:
        result['error'] = str(e)
        result['ok'] = False
    return result


async def run_and_store_net_probe(host_name: str) -> dict:
    host = database.get_host(host_name)
    if not host:
        return {'ok': False, 'error': 'host not found'}
    res = await net_probe_for_host(host)
    database.insert_host_speedtest(
        host_name=host_name,
        method='net',
        ping_ms=res.get('ping_ms'),
        jitter_ms=res.get('jitter_ms'),
        download_mbps=res.get('download_mbps'),
        upload_mbps=res.get('upload_mbps'),
        server_name=res.get('server_name'),
        server_id=res.get('server_id'),
        ok=bool(res.get('ok')),
        error=res.get('error'),
    )
    return res


async def run_and_store_ssh_speedtest(host_name: str) -> dict:
    host = database.get_host(host_name)
    if not host:
        return {'ok': False, 'error': 'host not found'}
    res = await ssh_speedtest_for_host(host)
    database.insert_host_speedtest(
        host_name=host_name,
        method='ssh',
        ping_ms=res.get('ping_ms'),
        jitter_ms=res.get('jitter_ms'),
        download_mbps=res.get('download_mbps'),
        upload_mbps=res.get('upload_mbps'),
        server_name=res.get('server_name'),
        server_id=res.get('server_id'),
        ok=bool(res.get('ok')),
        error=res.get('error'),
    )
    return res


async def run_both_for_host(host_name: str) -> dict:
    ok = True
    errors: list[str] = []
    out = {'ssh': None, 'net': None}
    try:
        out['ssh'] = await run_and_store_ssh_speedtest(host_name)
        if not out['ssh'].get('ok'):
            ok = False
            if out['ssh'].get('error'):
                errors.append(f"ssh: {out['ssh'].get('error')}")
    except Exception as e:
        ok = False
        errors.append(f'ssh exception: {e}')
    try:
        out['net'] = await run_and_store_net_probe(host_name)
        if not out['net'].get('ok'):
            ok = False
            if out['net'].get('error'):
                errors.append(f"net: {out['net'].get('error')}")
    except Exception as e:
        ok = False
        errors.append(f'net exception: {e}')
    return {'ok': ok, 'details': out, 'error': '; '.join(errors) if errors else None}


def _ssh_connect(host_row: dict) -> paramiko.SSHClient:
    ssh_host = (host_row.get('ssh_host') or '').strip()
    ssh_port = int(host_row.get('ssh_port') or 22)
    ssh_user = (host_row.get('ssh_user') or '').strip()
    ssh_password = host_row.get('ssh_password')
    ssh_key_path = (host_row.get('ssh_key_path') or '').strip() or None
    if not ssh_host or not ssh_user:
        raise RuntimeError('SSH settings are not configured for host')
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    pkey = _load_paramiko_pkey(ssh_key_path, ssh_password) if ssh_key_path else None
    ssh.connect(ssh_host, port=ssh_port, username=ssh_user, password=ssh_password, pkey=pkey, timeout=20)
    return ssh


def _ssh_exec(ssh: paramiko.SSHClient, cmd: str, timeout: int = 180) -> tuple[int, str, str]:
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode('utf-8', errors='ignore')
    err = stderr.read().decode('utf-8', errors='ignore')
    rc = stdout.channel.recv_exit_status() if hasattr(stdout, 'channel') else 0
    return rc, out, err


async def auto_install_speedtest_on_host(host_name: str) -> dict:
    """Attempt to auto-install Ookla speedtest or speedtest-cli on remote host via SSH.
    Tries package manager scripts, falls back to pip speedtest-cli. Returns {'ok', 'log'}.
    """
    host = database.get_host(host_name)
    if not host:
        return {'ok': False, 'log': 'host not found'}

    def _install() -> dict:
        log_lines: list[str] = []
        try:
            ssh = _ssh_connect(host)
        except Exception as e:
            return {'ok': False, 'log': f'SSH connect failed: {e}'}
        try:
            # If already installed
            rc, out, err = _ssh_exec(ssh, 'command -v speedtest || command -v speedtest-cli || echo "NO"')
            if 'speedtest' in out or 'speedtest-cli' in out:
                log_lines.append('Found existing speedtest binary: ' + out.strip())
                # Проверим версию и примем лицензию
                rc2, o2, e2 = _ssh_exec(ssh, 'speedtest --accept-license --accept-gdpr --version || true')
                ver_text = (o2 + e2).strip()
                if ver_text:
                    log_lines.append(('$ speedtest --accept-license --accept-gdpr --version\n' + ver_text).strip())
                # Если версия не 1.2.0 — выполним переустановку через tarball ниже
                need_reinstall = True
                try:
                    if '1.2.0' in ver_text:
                        need_reinstall = False
                except Exception:
                    need_reinstall = True
                if not need_reinstall:
                    return {'ok': True, 'log': '\n'.join(log_lines)}
                else:
                    log_lines.append('Different Ookla speedtest version detected; reinstalling 1.2.0 via tarball.')

            # Detect OS info
            rc, out, _ = _ssh_exec(ssh, 'cat /etc/os-release || uname -a')
            os_release = out.lower()
            log_lines.append('OS detection: ' + out.strip())

            # Сначала: УСТАНОВКА ЧЕРЕЗ TARBALL СТРОГОЙ ВЕРСИИ 1.2.0 (предпочтительно)
            # Detect arch
            rc, arch_out, _ = _ssh_exec(ssh, 'uname -m || echo unknown')
            arch = (arch_out or '').strip()
            # Map to Ookla naming
            if arch in ('x86_64', 'amd64'):
                arch_tag = 'linux-x86_64'
            elif arch in ('aarch64', 'arm64'):
                arch_tag = 'linux-aarch64'
            elif arch in ('armv7l',):
                arch_tag = 'linux-armhf'
            else:
                arch_tag = 'linux-x86_64'
            tar_url = f'https://install.speedtest.net/app/cli/ookla-speedtest-1.2.0-{arch_tag}.tgz'
            cmds_tar = [
                f'curl -fsSL {tar_url} -o /tmp/ookla-speedtest.tgz || wget -O /tmp/ookla-speedtest.tgz {tar_url}',
                'mkdir -p /tmp/ookla-speedtest && tar -xf /tmp/ookla-speedtest.tgz -C /tmp/ookla-speedtest',
                'install -m 0755 /tmp/ookla-speedtest/speedtest /usr/local/bin/speedtest || (cp /tmp/ookla-speedtest/speedtest /usr/local/bin/speedtest && chmod +x /usr/local/bin/speedtest)',
                # Принятие лицензии сразу после установки бинаря (идемпотентно)
                'speedtest --accept-license --accept-gdpr --version || true',
                'rm -rf /tmp/ookla-speedtest /tmp/ookla-speedtest.tgz'
            ]
            for c in cmds_tar:
                rc, o, e = _ssh_exec(ssh, c)
                log_lines.append(f'$ {c}\n{o}{e}'.strip())

            # Verify version is exactly 1.2.0
            rc, out, err = _ssh_exec(ssh, 'command -v speedtest || echo "NO"')
            if 'NO' not in out:
                rcv, ov, ev = _ssh_exec(ssh, 'speedtest --version 2>&1 || true')
                ver_info = (ov + ev).strip()
                if '1.2.0' in ver_info:
                    log_lines.append('Installed Ookla speedtest via tarball (1.2.0): ' + out.strip())
                    return {'ok': True, 'log': '\n'.join(log_lines)}
                else:
                    log_lines.append('Tarball install finished but version check did not return 1.2.0; continuing fallbacks.')

            # Если по какой-то причине tarball не сработал — пробуем официальный репозиторий (м.б. недоступен на noble)
            cmds_deb = [
                'which sudo || true',
                'export DEBIAN_FRONTEND=noninteractive',
                'curl -fsSL https://packagecloud.io/install/repositories/ookla/speedtest-cli/script.deb.sh | bash',
                'apt-get update -y || true',
                'apt-get install -y speedtest || true'
            ]
            cmds_rpm = [
                'curl -fsSL https://packagecloud.io/install/repositories/ookla/speedtest-cli/script.rpm.sh | bash',
                'yum -y install speedtest || dnf -y install speedtest || true'
            ]
            tried_ookla = False
            if 'debian' in os_release or 'ubuntu' in os_release:
                tried_ookla = True
                for c in cmds_deb:
                    rc, o, e = _ssh_exec(ssh, c)
                    log_lines.append(f'$ {c}\n{o}{e}'.strip())
            elif any(x in os_release for x in ['centos', 'rhel', 'fedora', 'almalinux', 'rocky']):
                tried_ookla = True
                for c in cmds_rpm:
                    rc, o, e = _ssh_exec(ssh, c)
                    log_lines.append(f'$ {c}\n{o}{e}'.strip())

            # Check again
            rc, out, err = _ssh_exec(ssh, 'command -v speedtest || command -v speedtest-cli || echo "NO"')
            if 'speedtest' in out or 'speedtest-cli' in out:
                log_lines.append('Installed speedtest successfully: ' + out.strip())
                # Автопринятие лицензии для Ookla CLI, если присутствует
                rc2, o2, e2 = _ssh_exec(ssh, 'speedtest --accept-license --accept-gdpr --version || true')
                if o2 or e2:
                    log_lines.append(('$ speedtest --accept-license --accept-gdpr --version\n' + (o2 + e2)).strip())
                return {'ok': True, 'log': '\n'.join(log_lines)}

            # Fallback: try install speedtest-cli via pip
            pip_try = [
                'command -v python3 || command -v python || echo NO',
                'command -v pip3 || command -v pip || (apt-get update -y && apt-get install -y python3-pip) || (yum -y install python3-pip || dnf -y install python3-pip) || true',
                'pip3 install --upgrade pip || true',
                'pip3 install speedtest-cli || pip install speedtest-cli || true',
                # create symlink if needed
                'command -v speedtest-cli || (which python3 && python3 -m pip show speedtest-cli && ln -sf $(python3 -c "import shutil,sys; import os; print(shutil.which(\"speedtest-cli\") or \"/usr/local/bin/speedtest-cli\")") /usr/local/bin/speedtest-cli) || true'
            ]
            for c in pip_try:
                rc, o, e = _ssh_exec(ssh, c)
                log_lines.append(f'$ {c}\n{o}{e}'.strip())

            # Final check
            rc, out, err = _ssh_exec(ssh, 'command -v speedtest || command -v speedtest-cli || echo "NO"')
            if 'NO' not in out:
                log_lines.append('Installed speedtest-cli via pip: ' + out.strip())
                return {'ok': True, 'log': '\n'.join(log_lines)}

            # Последний фоллбек: ставим python-пакет speedtest-cli (не Ookla), если всё остальное не сработало
            # (этот шаг оставлен выше по коду — уже выполнен; если не сработал — выдаём ошибку ниже)

            return {'ok': False, 'log': 'Failed to install speedtest using available methods.\n' + '\n'.join(log_lines)}
        finally:
            try:
                ssh.close()
            except Exception:
                pass

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _install)
