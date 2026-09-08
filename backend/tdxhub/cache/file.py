import functools
import os
import pathlib
import pickle
import time
from typing import Any
from typing import Callable
from typing import Optional
from typing import Union

from ..exceptions import FileNeedRefresh
from .compat import ParamSpec
from .compat import TypeAlias

P = ParamSpec('P')

PathLike: TypeAlias = Union[str, pathlib.Path]


def file_cache(filepath: PathLike, refresh_time: Optional[float] = None):
    def decorator(func: Callable[P, Any]):
        @functools.wraps(func)
        def retrieve_cache(*args: P.args, **kwargs: P.kwargs):
            try:
                if refresh_time is not None and os.path.getmtime(filepath) + int(refresh_time) < time.time():
                    raise FileNeedRefresh(f'{filepath} 太旧，需要刷新')
                with open(filepath, 'rb') as fp:
                    cached = pickle.load(fp)
            except (FileNotFoundError, EOFError):
                pathlib.Path(filepath).parent.mkdir(exist_ok=True, parents=True)

                cached = func(*args, **kwargs)
                with open(filepath, 'wb') as fp:
                    pickle.dump(cached, fp)

            return cached

        return retrieve_cache

    return decorator
