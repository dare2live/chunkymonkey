import os
import re
import secrets
import shutil
import tempfile
from pathlib import Path
from struct import calcsize
from struct import unpack

from tdxhub.protocol.hq import TdxHq_API

from ..logger import logger
from .base import BaseFinancial
from .columns import columns as financial_columns


_RAW_COLUMN_RE = re.compile(r'^col([1-9]\d*)$')


def _raw_column_index(name):
    match = _RAW_COLUMN_RE.match(name)
    if not match:
        return None
    return int(match.group(1)) - 1


def _field_index_for_column(name):
    raw_index = _raw_column_index(name)
    if raw_index is not None:
        return raw_index
    return financial_columns.index(name) - 1


def _resolved_column_names(report_fields_count):
    column_names = list(financial_columns[: report_fields_count + 1])
    while len(column_names) < report_fields_count + 1:
        column_names.append(f'col{len(column_names)}')
    return column_names


def _normalize_selected_columns(selected_columns):
    if selected_columns is None:
        return None

    if isinstance(selected_columns, str):
        selected_columns = [selected_columns]

    normalized = []
    missing = []
    for name in selected_columns:
        if name == 'code':
            continue
        if name not in financial_columns and _raw_column_index(name) is None:
            missing.append(name)
            continue
        if name not in normalized:
            normalized.append(name)

    if missing:
        raise ValueError(f'Unknown gpcw columns: {missing}')

    return tuple(normalized)


def _projected_column_names(selected_columns):
    return ['report_date'] + [name for name in selected_columns if name != 'report_date']


def _open_gpcw_dat_file(download_file):
    tmpdir = None
    if download_file.name.endswith('.zip'):
        tmpdir_root = tempfile.gettempdir()
        subdir_name = f'tdxhub_{secrets.randbelow(1000000)}'
        tmpdir = Path(tmpdir_root, subdir_name)
        shutil.rmtree(tmpdir, ignore_errors=True)
        Path(tmpdir).mkdir(parents=True)
        shutil.unpack_archive(download_file.name, extract_dir=tmpdir)

        dat_file = None
        for _file in os.listdir(tmpdir):
            if str(_file).endswith('.dat'):
                dat_file = open(Path(tmpdir, str(_file)), 'rb')
                break

        if dat_file is None:
            shutil.rmtree(tmpdir, ignore_errors=True)
            raise Exception('no dat file found in zip archive')
        return dat_file, tmpdir

    if download_file.name.endswith('.dat'):
        return download_file, None

    return None, None


def _close_gpcw_dat_file(download_file, dat_file, tmpdir):
    if tmpdir is not None and dat_file is not None:
        dat_file.close()
        shutil.rmtree(tmpdir, ignore_errors=True)
    download_file.close()


def _read_gpcw_header(dat_file):
    header_pack_format = '<1hI1H3L'
    header_size = calcsize(header_pack_format)
    data_header = dat_file.read(header_size)
    stock_header = unpack(header_pack_format, data_header)
    report_size = stock_header[4]
    return {
        'header_size': header_size,
        'stock_item_size': calcsize('<6s1c1L'),
        'stock_count': stock_header[2],
        'report_date': stock_header[1],
        'report_size': report_size,
        'report_fields_count': int(report_size / 4),
    }


def _validate_selected_columns_for_report(selected_columns, selected_field_indexes, report_fields_count):
    if selected_columns is None or selected_field_indexes is None:
        return

    selected_data_columns = [name for name in selected_columns if name != 'report_date']
    out_of_range = [
        name
        for name, index in zip(selected_data_columns, selected_field_indexes)
        if index >= report_fields_count
    ]
    if out_of_range:
        raise ValueError(
            'Requested gpcw columns outside this report: '
            f'{out_of_range}; report_fields_count={report_fields_count}'
        )


def _connect_tdx_api(api, bestip):
    connected = api.connect(*bestip)
    if not connected:
        raise ConnectionError(f'connect to TDX financial server failed: {bestip}')
    return api


class FinancialReader(object):
    @staticmethod
    def to_data(filename, **kwargs):
        """
        读取历史财务数据文件，并返回 records，类似 `gpcw20171231.zip` 格式，具体字段含义参考

        https://github.com/rainx/pytdx/issues/133

        :param filename: 数据文件地址， 数据文件类型可以为 .zip 文件，也可以为解压后的 .dat, 可以不写扩展名. 程序自动识别
        :return: records 格式的历史财务数据
        """

        crawler = Financial()

        with open(filename, 'rb') as fp:
            data = crawler.parse(download_file=fp, **kwargs)

        return crawler.to_records(data, **kwargs)


class FinancialList(BaseFinancial):
    def content(self, report_hook=None, downdir=None, proxies=None, chunk_size=1024 * 50, *args, **kwargs):
        """
        解析财务文件

        :param report_hook: 钩子回调函数
        :param downdir: 要解析的文件夹
        :param proxies:
        :param chunk_size:
        :param args:
        :param kwargs:
        :return:
        """

        tmp = tempfile.NamedTemporaryFile(delete=True)

        api = TdxHq_API(**kwargs)
        api.need_setup = False

        _connect_tdx_api(api, self.bestip)
        try:
            content = api.get_report_file_by_size('tdxfin/gpcw.txt')
            download_file = open(downdir, 'wb') if downdir else tmp
            download_file.write(content)
            download_file.seek(0)

            return download_file
        finally:
            api.close()

    def parse(self, download_file, *args, **kwargs):
        """
        解析财务文件

        :param download_file:
        :param args:
        :param kwargs:
        :return:
        """

        with download_file:
            content = download_file.read()
            content = content.decode('utf-8')

        def l2d(i):
            return {'filename': i[0], 'hash': i[1], 'filesize': int(i[2])}

        if content:
            content = content.strip().split('\n')
            return [l2d(i) for i in [line.strip().split(',') for line in content]]

        return None


class Financial(BaseFinancial):
    def content(self, report_hook=None, downdir=None, proxies=None, chunk_size=51200, *args, **kwargs):
        """
        解析财务文件

        :param report_hook: 钩子回调函数
        :param downdir: 要解析的文件夹
        :param proxies: 代理配置
        :param chunk_size:
        :param args:
        :param kwargs:
        :return:
        """

        filename = kwargs.get('filename')
        downfile = str(Path(downdir) / filename)
        filesize = kwargs.get('filesize') if kwargs.get('filesize') else 0

        logger.debug(f'{filename}: start download...')

        if not filename:
            raise Exception('Param filename is not set')

        api = TdxHq_API()
        api.need_setup = False

        _connect_tdx_api(api, self.bestip)
        try:
            content = api.get_report_file_by_size(f'tdxfin/{filename}', filesize=filesize, reporthook=report_hook)
            download_file = downfile and open(downfile, 'wb') or tempfile.NamedTemporaryFile(delete=True)
            download_file.write(content)
            download_file.seek(0)

            del content

            logger.debug(f'{filename}: done')

            return download_file
        finally:
            api.close()

    def parse(self, download_file, *args, **kwargs):
        """
        解析财务文件

        :param download_file: 要解析的文件
        :param args:
        :param kwargs:
        :return:
        """

        selected_columns = _normalize_selected_columns(kwargs.get('columns'))
        selected_field_indexes = None
        if selected_columns is not None:
            selected_field_indexes = [
                _field_index_for_column(name)
                for name in selected_columns
                if name != 'report_date'
            ]

        dat_file, tmpdir = _open_gpcw_dat_file(download_file)
        if dat_file is None:
            return None

        try:
            header = _read_gpcw_header(dat_file)
            header_size = header['header_size']
            stock_item_size = header['stock_item_size']
            max_count = header['stock_count']
            report_date = header['report_date']
            report_fields_count = header['report_fields_count']
            _validate_selected_columns_for_report(
                selected_columns,
                selected_field_indexes,
                report_fields_count,
            )
            report_pack_format = '<{}f'.format(report_fields_count)

            results = []

            for stock_idx in range(0, max_count):
                dat_file.seek(header_size + stock_idx * calcsize('<6s1c1L'))
                si = dat_file.read(stock_item_size)
                stock_item = unpack('<6s1c1L', si)
                code = stock_item[0].decode('utf-8')
                dat_file.seek(stock_item[2])

                info_data = dat_file.read(calcsize(report_pack_format))
                cw_info = unpack(report_pack_format, info_data)
                if selected_field_indexes is None:
                    one_record = (code, report_date) + cw_info
                else:
                    one_record = (code, report_date) + tuple(cw_info[index] for index in selected_field_indexes)
                results.append(one_record)
        finally:
            _close_gpcw_dat_file(download_file, dat_file, tmpdir)

        return results

    def inspect(self, download_file, sample_size=0, *args, **kwargs):
        """Inspect a gpcw zip/dat schema without requiring a complete parse."""

        dat_file, tmpdir = _open_gpcw_dat_file(download_file)
        if dat_file is None:
            return None

        try:
            header = _read_gpcw_header(dat_file)
            report_fields_count = header['report_fields_count']
            resolved_columns = _resolved_column_names(report_fields_count)
            data_columns = resolved_columns[1:]
            placeholder_columns = [name for name in data_columns if name.startswith('col')]
            raw_tail_columns = data_columns[max(0, len(financial_columns) - 1):]
            info = {
                'report_date': header['report_date'],
                'stock_count': header['stock_count'],
                'report_size': header['report_size'],
                'report_fields_count': report_fields_count,
                'known_data_columns': min(report_fields_count, len(financial_columns) - 1),
                'raw_tail_columns': raw_tail_columns,
                'raw_tail_count': len(raw_tail_columns),
                'placeholder_columns': placeholder_columns,
                'placeholder_count': len(placeholder_columns),
            }
            if sample_size:
                info['sample'] = self._sample_schema_values(dat_file, header, sample_size)
            return info
        finally:
            _close_gpcw_dat_file(download_file, dat_file, tmpdir)

    @staticmethod
    def _sample_schema_values(dat_file, header, sample_size):
        report_pack_format = '<{}f'.format(header['report_fields_count'])
        sample_count = min(int(sample_size), header['stock_count'])
        rows = []
        for stock_idx in range(sample_count):
            dat_file.seek(header['header_size'] + stock_idx * header['stock_item_size'])
            stock_item = unpack('<6s1c1L', dat_file.read(header['stock_item_size']))
            code = stock_item[0].decode('utf-8')
            dat_file.seek(stock_item[2])
            values = unpack(report_pack_format, dat_file.read(calcsize(report_pack_format)))
            non_zero = sum(1 for value in values if value != 0)
            rows.append({
                'code': code,
                'non_zero_fields': non_zero,
                'zero_fields': len(values) - non_zero,
            })
        return rows

    @staticmethod
    def to_records(data, header='zh', columns=None):
        """
        转换数据为 records 格式

        :param data: 要转换的数据
        :param header: 是否中文表头
        :return: list[dict]
        """

        if not data or len(data[0]) == 0:
            return []

        selected_columns = _normalize_selected_columns(columns)
        records = []

        if selected_columns is not None:
            names = _projected_column_names(selected_columns)
            include_report_date = 'report_date' in selected_columns
            if not include_report_date:
                names = [name for name in names if name != 'report_date']

            for row in data:
                values = row[1:] if include_report_date else row[2:]
                record = {'code': row[0]}
                record.update(dict(zip(names, values)))
                records.append(record)

            logger.debug('records=%s', len(records))
            return records

        if header == 'zh':
            names = _resolved_column_names(len(data[0]) - 2)
        else:
            names = ['report_date']
            for i in range(1, len(data[0]) - 1):
                names.append('col' + str(i))

        for row in data:
            record = {'code': row[0]}
            record.update(dict(zip(names, row[1:])))
            records.append(record)

        logger.debug('records=%s', len(records))

        return records

    @staticmethod
    def to_df(data, header='zh', columns=None):
        """Compatibility shim for callers that still use the old method name."""
        return Financial.to_records(data, header=header, columns=columns)
