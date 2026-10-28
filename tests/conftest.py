"""테스트는 저장소의 data/ 를 건드리지 않도록 임시 폴더를 쓴다(모듈 import 전에 설정)."""
import os
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="nps-test-"))
os.environ["NPS_DATA_DIR"] = str(_TMP / "data")
os.environ["NPS_DB_PATH"] = str(_TMP / "nps.sqlite")
os.environ.setdefault("KRX_SLEEP", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TMP = _TMP
