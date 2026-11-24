"""Load the packaged, normalized copy of the released StudyChat snapshot."""
import gzip
import json

from .adapters import record_summary
from .core import ROOT, Problem, pseudonym
from .studychat_snapshot import file_hash

DATA = ROOT / "data"
MANIFEST = DATA / "studychat-v1.1.manifest.json"
ARCHIVE = DATA / "studychat-v1.1.snapshot.jsonl.gz"
IDENTITY = "studychat-embedded-v1.1"
TITLE = "StudyChat v1.1 · 内置数据集"
LICENCE = "CC BY 4.0 · StudyChat v1.1 · Hunter McNichols, Fareya Ikram, Andrew Lan"


def manifest():
    if not ARCHIVE.is_file() or not MANIFEST.is_file():
        raise Problem(404, "BUILTIN_SNAPSHOT_MISSING", "未找到内置 StudyChat 包，请使用本地快照导入。")
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def load_builtin(settings, metadata):
    spec = manifest()
    if file_hash(ARCHIVE) != spec["sha256"]:
        raise Problem(422, "BUILTIN_CHECKSUM", "内置 StudyChat 校验未通过，请重新生成数据包。")
    records = []
    local_key = settings.key()
    with gzip.open(ARCHIVE, "rt", encoding="utf-8") as rows:
        for line in rows:
            record = json.loads(line)
            record["participant_key"] = pseudonym(local_key, IDENTITY, record["participant_key"])
            record["external_session_id"] = pseudonym(local_key, IDENTITY + "/session", record["external_session_id"])
            records.append(record)
    if len(records) != spec["preview"]["valid"]:
        raise Problem(422, "BUILTIN_RECORD_COUNT", "内置 StudyChat 会话数量校验未通过。")
    preview = spec["preview"]
    preview.update(source=TITLE, examples=[record_summary(record) for record in records[:5]], builtin=True)
    metadata.update(name=TITLE, source_version=spec["sha256"], demo=False)
    return records, preview
