import asyncio
import configparser
import copy
import json
import socket
import time
from functools import partial
from pathlib import Path
from typing import Any
from typing import Optional
from typing import Union

from tdxhub.consts import CONFIG
from tdxhub.consts import EX_HOSTS
from tdxhub.consts import GP_HOSTS
from tdxhub.consts import HQ_HOSTS
from tdxhub.logger import logger
from tdxhub.paths import get_config_path

PathLike = Union[str, Path]
ServerTuple = tuple[str, str, int]
ServerGroups = dict[str, list[ServerTuple]]

DEFAULT_CONNECT_CFG_SECTION_MAP = {
    'HQ': 'HQHOST',
    'EX': 'DSHOST',
}


def _host_tuples_to_proxy_list(server_list: list[ServerTuple]) -> list[dict[str, Any]]:
    return [{'addr': host[1], 'port': host[2], 'time': 0, 'site': host[0]} for host in server_list]


def _load_runtime_config(config_path: Optional[PathLike] = None) -> dict[str, Any]:
    path = Path(config_path or get_config_path('config.json'))
    default = copy.deepcopy(CONFIG)

    try:
        with path.open('r', encoding='utf-8') as handle:
            loaded = json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError):
        return default

    default.update({key: value for key, value in loaded.items() if key not in {'SERVER', 'BESTIP'}})
    default['SERVER'].update(loaded.get('SERVER', {}))
    default['BESTIP'].update(loaded.get('BESTIP', {}))

    return default


def _save_runtime_config(config_data: dict[str, Any], config_path: Optional[PathLike] = None) -> Path:
    path = Path(config_path or get_config_path('config.json'))
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open('w', encoding='utf-8') as handle:
        json.dump(config_data, handle, indent=2, ensure_ascii=False)

    return path


def _read_connect_cfg_text(cfg_path: PathLike) -> str:
    data = Path(cfg_path).read_bytes()

    for encoding in ('gbk', 'utf-8-sig', 'utf-8', 'cp936'):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue

    return data.decode('latin-1')


def parse_connect_cfg(
    cfg_path: PathLike,
    section_map: Optional[dict[str, str]] = None,
) -> ServerGroups:
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    parser.read_string(_read_connect_cfg_text(cfg_path))

    imported: ServerGroups = {}
    active_section_map = section_map or DEFAULT_CONNECT_CFG_SECTION_MAP

    for target_key, section_name in active_section_map.items():
        if not parser.has_section(section_name):
            continue

        host_count = parser.getint(section_name, 'HostNum', fallback=0)
        seen: set[tuple[str, int]] = set()
        section_hosts: list[ServerTuple] = []

        for index in range(1, host_count + 1):
            suffix = f'{index:02d}'
            site = parser.get(section_name, f'HostName{suffix}', fallback='').strip()
            addr = parser.get(section_name, f'IPAddress{suffix}', fallback='').strip()
            port_text = parser.get(section_name, f'Port{suffix}', fallback='').strip()

            if not addr or not port_text:
                continue

            try:
                port = int(port_text)
            except ValueError:
                continue

            key = (addr, port)
            if key in seen:
                continue

            seen.add(key)
            section_hosts.append((site or addr, addr, port))

        if section_hosts:
            imported[target_key] = section_hosts

    return imported


def configure_hosts_from_connect_cfg(
    cfg_path: PathLike,
    config_path: Optional[PathLike] = None,
    section_map: Optional[dict[str, str]] = None,
) -> ServerGroups:
    imported = parse_connect_cfg(cfg_path, section_map=section_map)

    if not imported:
        return imported

    runtime_config = _load_runtime_config(config_path)

    for key, server_list in imported.items():
        hosts[key] = _host_tuples_to_proxy_list(server_list)
        results[key] = []
        runtime_config['SERVER'][key] = server_list

    _save_runtime_config(runtime_config, config_path)
    return imported

# HQ_HOSTS 已在 consts.py 中合并了 tdxpy + mootdx 全部服务器（117 台去重）
# 无需再从 tdxpy.constants 导入 hq_hosts
hosts = {
    'HQ': _host_tuples_to_proxy_list(HQ_HOSTS),
    'EX': _host_tuples_to_proxy_list(EX_HOSTS),
    'GP': _host_tuples_to_proxy_list(GP_HOSTS),
}

results = {k: [] for k in hosts}


def callback(res, key):
    """
    异步回调函数

    :param res:
    :param key:
    """
    result = res.result()

    if result.get('time'):
        results[key].append(result)

    # logger.debug(f"callback: {res.result()}")


def connect(proxy: dict) -> dict:
    """
    连接服务器函数

    :param proxy: 代理IP信息
    :return:
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(.7)

        start = time.perf_counter()

        sock.connect((proxy.get('addr'), int(proxy.get('port'))))
        sock.close()

        proxy['time'] = (time.perf_counter() - start) * 1000

        logger.debug('{addr}:{port} 验证通过，响应时间：{time} ms.'.format(**proxy))
    except socket.timeout:  # noqa
        logger.debug('{addr},{port} time out.'.format(**proxy))
        proxy['time'] = None
    except ConnectionRefusedError:  # noqa
        logger.debug('{addr},{port} 验证失败.'.format(**proxy))
        proxy['time'] = None

    return proxy


async def verify(proxy: dict) -> dict:
    """
    检验代理连通性函数

    :param proxy: 代理IP信息
    :return:
    """
    start = time.perf_counter()
    reader = None
    writer = None

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(proxy.get('addr'), int(proxy.get('port'))),
            timeout=0.7,
        )
        proxy['time'] = (time.perf_counter() - start) * 1000
        logger.debug('{addr}:{port} 验证通过，响应时间：{time} ms.'.format(**proxy))
    except (asyncio.TimeoutError, OSError):
        logger.debug('{addr}:{port} 验证失败.'.format(**proxy))
        proxy['time'] = None
    finally:
        if writer is not None:
            writer.close()
            await writer.wait_closed()

    return proxy


def server(index=None, limit=5, console=False, sync=True):
    _hosts = hosts[index]

    async def async_event():
        tasks = []

        for host in _hosts:
            task = asyncio.create_task(verify(dict(host)))
            task.add_done_callback(partial(callback, key=index))
            tasks.append(task)

        if tasks:
            await asyncio.wait(tasks)

    global results

    if sync:
        results[index] = [connect(proxy) for proxy in _hosts]
        results[index] = [x for x in results[index] if x.get('time')]
    else:
        results[index] = []
        asyncio.run(async_event())

    servers = results[index]

    # 结果按响应时间从小到大排序
    if console:
        from prettytable import PrettyTable

        servers.sort(key=lambda item: item['time'])

        if limit:
            servers = servers[:limit]

        logger.debug('[√] 最优服务器:')

        t = PrettyTable(['Name', 'Addr', 'Port', 'Time'])
        t.align['Name'] = 'l'
        t.align['Addr'] = 'l'
        t.align['Port'] = 'l'
        t.align['Time'] = 'r'
        t.padding_width = 1

        for host in servers:
            t.add_row(
                [
                    host['site'],
                    host['addr'],
                    host['port'],
                    '{:5.2f} ms'.format(host['time']),
                ]
            )

        logger.debug('\n' + str(t))

    return [(item['addr'], int(item['port'])) for item in servers]


def check_server(console=False, limit=5, sync=False) -> None:
    return bestip(console=console, limit=limit, sync=sync)


def bestip(console=False, limit=5, sync=False) -> None:
    config_ = get_config_path('config.json')
    default = _load_runtime_config(config_)

    logger.info('[-] 选择最快的服务器...')
    logger.debug(f'sync => {sync}')

    for index in ['HQ', 'EX', 'GP']:
        try:
            data = server(index=index, limit=limit, console=console, sync=sync)

            if data:
                default['BESTIP'][index] = data[0]
        except RuntimeError:
            logger.error('请手动运行`python -m tdxhub bestip`')
            break

    _save_runtime_config(default, config_)


if __name__ == '__main__':
    bestip(sync=False, limit=5, console=True)
