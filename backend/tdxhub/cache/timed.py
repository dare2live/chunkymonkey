import functools
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from typing import Any
from typing import Callable
from typing import cast
from typing import Optional
from typing import Protocol

from .compat import ParamSpec
from .compat import TypeAlias

P = ParamSpec('P')

CacheFunc: TypeAlias = Callable[P, Any]


class LRUCacheWrapper(Protocol[P]):
    lifetime: timedelta
    expiration: datetime

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> Any:
        pass

    def clear(self):
        pass


def lru_cache(seconds: Optional[int] = None, maxsize: Optional[int] = None, typed: bool = True):
    """
    装饰器工厂，生成一个带过期时间的内存缓存装饰器.

    :param seconds: 保留缓存的秒数
    :param maxsize: 要存储在缓存中的最大项目数
    :param typed: 不同类型的参数是否将单独缓存
    :return: 在内存中缓存调用结果的装饰器（含过期时间）.
    """

    def decorator(func: CacheFunc) -> CacheFunc:
        lru_func = cast(LRUCacheWrapper, functools.lru_cache(maxsize=maxsize, typed=typed)(func))

        if not seconds:
            return lru_func

        lru_func.lifetime = timedelta(seconds=seconds)
        lru_func.expiration = datetime.now(timezone.utc) + lru_func.lifetime

        @functools.wraps(func)
        def retrieve_cache(*args: P.args, **kwargs: P.kwargs) -> Any:
            if datetime.now(timezone.utc) >= lru_func.expiration:
                lru_func.clear()
                lru_func.expiration = datetime.now(timezone.utc) + lru_func.lifetime

            return lru_func(*args, **kwargs)

        return cast(CacheFunc, retrieve_cache)

    return decorator
