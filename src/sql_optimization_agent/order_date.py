"""One validated calendar day under orders.created_at's naive timestamp semantics."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import re


ORDER_DATE_SQL = """SELECT customer_id, created_at, status, amount
FROM public.orders
WHERE created_at::date = %s::date"""


@dataclass(frozen=True)
class OrderDateContext:
    target: date
    start: datetime
    end: datetime

    @classmethod
    def parse(cls, value: str):
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            raise ValueError("Invalid date: expected YYYY-MM-DD with a representable next day")
        try:
            target = date.fromisoformat(value)
            start = datetime.combine(target, time.min)
            end = datetime.combine(target + timedelta(days=1), time.min)
        except (ValueError, OverflowError):
            raise ValueError("Invalid date: expected YYYY-MM-DD with a representable next day") from None
        return cls(target, start, end)

    @property
    def original_params(self):
        return (self.target,)

    def as_dict(self):
        return {"target_date": self.target.isoformat(), "start": self.start.isoformat(sep=" "),
                "end": self.end.isoformat(sep=" "), "timestamp_type": "timestamp without time zone"}
