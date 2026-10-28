"""공통 유틸: 재시도, 로깅, 날짜, JSON 입출력."""
from __future__ import annotations

import functools
import gzip
import json
import logging
import random
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, TypeVar

from .config import KST

T = TypeVar("T")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("nps")


class RetryError(RuntimeError):
    """재시도 후에도 실패."""


def retry(tries: int = 4, base: float = 2.0, max_wait: float = 30.0,
          exceptions: tuple = (Exception,), empty_is_error: bool = False,
          giveup: tuple = ()):
    """지수 백오프 재시도 데코레이터.

    empty_is_error=True 이면 None/빈 DataFrame/빈 리스트 결과도 실패로 보고 재시도한다
    (KRX가 일시 오류 때 빈 표를 돌려주는 경우 대비).
    giveup 에 든 예외(예: 휴장일)는 재시도 없이 바로 올린다.
    """
    def deco(fn: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs) -> T:
            last: Exception | None = None
            for attempt in range(1, tries + 1):
                try:
                    out = fn(*args, **kwargs)
                    if empty_is_error and _is_empty(out):
                        raise ValueError(f"{fn.__name__}: empty result")
                    return out
                except exceptions as e:  # noqa: PERF203
                    if giveup and isinstance(e, giveup):
                        raise
                    last = e
                    if attempt == tries:
                        break
                    wait = min(max_wait, base ** attempt) + random.uniform(0, 1)
                    log.warning("%s 실패(%d/%d): %s → %.1fs 후 재시도",
                                fn.__name__, attempt, tries, e, wait)
                    time.sleep(wait)
            raise RetryError(f"{fn.__name__} failed after {tries} tries: {last}") from last
        return wrapper
    return deco


def _is_empty(x: Any) -> bool:
    if x is None:
        return True
    if hasattr(x, "empty"):
        return bool(x.empty)
    try:
        return len(x) == 0
    except TypeError:
        return False


class RateLimiter:
    """최소 호출 간격 보장."""

    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self._last = 0.0

    def wait(self) -> None:
        now = time.monotonic()
        gap = now - self._last
        if gap < self.min_interval:
            time.sleep(self.min_interval - gap)
        self._last = time.monotonic()


def now_kst() -> datetime:
    return datetime.now(KST)


def ymd(d: date | str) -> str:
    """date/‘YYYY-MM-DD’/‘YYYYMMDD’ → 'YYYYMMDD'."""
    if isinstance(d, (date, datetime)):
        return d.strftime("%Y%m%d")
    return d.replace("-", "")


def iso(d: date | str) -> str:
    """→ 'YYYY-MM-DD'."""
    s = ymd(d)
    return f"{s[:4]}-{s[4:6]}-{s[6:]}"


def to_date(s: str) -> date:
    return datetime.strptime(ymd(s), "%Y%m%d").date()


def write_json(path: Path, obj: Any, compact: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, ensure_ascii=False,
                      separators=(",", ":") if compact else None,
                      indent=None if compact else 2)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_gz(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    tmp.replace(path)


def read_json_gz(path: Path) -> Any:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def eok(won: float | int | None, nd: int = 2) -> float | None:
    """원 → 억원."""
    if won is None:
        return None
    return round(won / 1e8, nd)
