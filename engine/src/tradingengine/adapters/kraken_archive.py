"""Import der herunterladbaren Kraken-OHLCVT-Archive (CSV ohne Kopfzeile:
timestamp, open, high, low, close, volume, trades). Intervalle ohne Trades fehlen im Archiv und
werden als flache Kerzen (Volumen 0) mit eigener Quelle ergänzt, damit die Reihe lückenlos ist."""

from __future__ import annotations

import csv
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from ..core.candles import Candle, timeframe_delta

SOURCE = "kraken-archive"
SOURCE_FILL = "kraken-archive-fill"


def load_csv(path: Path, instrument_id: str, timeframe: str) -> list[Candle]:
    step = timeframe_delta(timeframe)
    out: list[Candle] = []
    with path.open(newline="") as fh:
        for row in csv.reader(fh):
            open_time = datetime.fromtimestamp(int(row[0]), tz=UTC)
            while out and out[-1].open_time + step < open_time:
                prev = out[-1]
                out.append(
                    Candle(
                        instrument_id, timeframe, prev.close_time, prev.close_time + step,
                        prev.close, prev.close, prev.close, prev.close, Decimal(0), 0, SOURCE_FILL,
                    )
                )
            out.append(
                Candle(
                    instrument_id, timeframe, open_time, open_time + step,
                    Decimal(row[1]), Decimal(row[2]), Decimal(row[3]), Decimal(row[4]),
                    Decimal(row[5]), int(row[6]), SOURCE,
                )
            )
    return out
