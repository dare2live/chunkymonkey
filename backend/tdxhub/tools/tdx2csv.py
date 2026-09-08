import asyncio
import csv
import glob
from functools import partial
from pathlib import Path

from tdxhub.logger import logger


def _parse_number(value: str):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return value
    return int(number) if number.is_integer() else number


def txt2csv(infile: str, outfile: str = None) -> list[dict]:
    """通达信导出文件转换为 csv records.

    :param infile: 通达信导出的 txt 文件路径
    :param outfile: 转换后的目标 csv 文件路径
    """

    try:
        names = ['date', 'open', 'high', 'low', 'close', 'volume', 'amount']
        lines = Path(infile).read_text(encoding='gbk').splitlines()
        rows = []
        for raw in lines[3:-1]:
            values = next(csv.reader([raw]))
            if len(values) != len(names):
                continue
            item = dict(zip(names, values))
            for key in names[1:]:
                item[key] = _parse_number(item[key])
            rows.append(item)

        if not rows:
            raise ValueError(f'no rows parsed from {infile}')

        # 传参 outfile 目录存在则写文件
        outfile = outfile if outfile else infile.replace('.txt', '.csv')
        if Path(outfile).parent.is_dir():
            with open(outfile, 'w', newline='', encoding='utf-8') as fp:
                writer = csv.DictWriter(fp, fieldnames=names)
                writer.writeheader()
                writer.writerows(rows)

        return rows
    except FileNotFoundError as ex:
        logger.error(f'输入文件不存在: {infile}')
        return []
    except (ValueError, TypeError) as ex:
        logger.error(f'无法解析输入文件: {infile}')
        return []


async def covert(src, dst):
    return await asyncio.get_event_loop().run_in_executor(None, partial(txt2csv, infile=src, outfile=dst))


def batch(src, dst):
    """批量转换通达信导出文件

    :param src: 来源目录
    :param dst: 目标目录
    """

    tasks = []
    event = asyncio.get_event_loop()

    # 分配任务
    for x in glob.glob1(src, '*.txt'):
        src_ = str(Path(src, x))
        dst_ = src_.replace('.txt', '.csv')

        task = event.create_task(covert(src=src_, dst=dst_))
        tasks.append(task)

    # 执行任务
    event.run_until_complete(asyncio.wait(tasks))


__all__ = ('txt2csv', 'batch')
