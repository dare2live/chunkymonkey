from tdxhub import config
from tdxhub.consts import EX_HOSTS
from tdxhub.consts import GP_HOSTS
from tdxhub.consts import HQ_HOSTS
from tdxhub.server import server
from tdxhub.paths import get_config_path

# 延迟导入 capabilities，避免循环依赖但允许 from tdxhub import capabilities
# 注: 用 importlib 而不是 'from tdxhub import capabilities', 后者会触发 __getattr__ 自递归
def __getattr__(name):
    if name == 'capabilities':
        import importlib
        return importlib.import_module('tdxhub.capabilities')
    raise AttributeError(f"module 'tdxhub' has no attribute {name}")

__version__ = '0.12.0'
__author__ = 'bopo.wang <ibopo@126.com>'
