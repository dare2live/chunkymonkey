# cython: language_level=3
import struct
from pathlib import Path

from tdxhub.protocol.reader.base_reader import BaseReader
from tdxhub.protocol.reader.base_reader import TdxFileNotFoundException


class TdxExHqDailyBarReader(BaseReader):
    """
    读取通达信数据
    """

    def parse_data_by_file(self, filename):
        """
        按文件名解析内容
        :param filename:
        :return:
        """
        if not Path(filename).is_file():
            raise TdxFileNotFoundException(f"no tdx kline data, please check path {filename}")

        content = Path(filename).read_bytes()
        content = self.unpack_records("<IffffIIf", content)  # noqa

        return content

    def get_df(self, code_or_file, **kwargs):
        """
        转换 records 格式
        :param code_or_file:
        :param kwargs:
        :return:
        """
        return [self._record_convert(row) for row in self.parse_data_by_file(code_or_file)]

    @staticmethod
    def _record_convert(row):
        """

        :param row:
        :return:
        """
        t_date = str(row[0])
        datestr = t_date[:4] + "-" + t_date[4:6] + "-" + t_date[6:]

        (hk_stock_amount,) = struct.unpack("<f", struct.pack("<I", row[5]))
        return {
            "date": datestr,
            "open": row[1],
            "high": row[2],
            "low": row[3],
            "close": row[4],
            "amount": row[5],
            "volume": row[6],
            "jiesuan": row[7],
            "hk_stock_amount": hk_stock_amount,
        }
