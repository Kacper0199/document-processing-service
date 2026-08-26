from dataclasses import dataclass
from pathlib import Path

from papertrail.domain import InputLineage


@dataclass(frozen=True, slots=True)
class DownloadedDocument:
    path: Path
    lineage: InputLineage
