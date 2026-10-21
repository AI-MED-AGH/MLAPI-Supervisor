from collections import defaultdict

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cluster.backend import Usage
from app.telemetry.tables import UsageSample
from app.telemetry.tiers import BY_NAME, RANGES, TIERS


def _bucket(ts: float, step: int) -> int:
    return int(ts) // step * step


async def record(session: AsyncSession, model: str, usage: Usage, now: float) -> None:
    """Stores a live measurement in the finest tier; a second one in the same 5 s bucket replaces the first."""
    ts = _bucket(now, BY_NAME["5s"].step)
    existing = (await session.execute(
        select(UsageSample).where(UsageSample.model == model, UsageSample.tier == "5s", UsageSample.ts == ts)
    )).scalar_one_or_none()
    if existing is None:
        session.add(UsageSample(model=model, tier="5s", ts=ts, cpu_m_avg=usage.cpu_m, cpu_m_max=usage.cpu_m,
                                mem_avg=usage.memory_bytes, mem_max=usage.memory_bytes, n=1))
    else:
        existing.cpu_m_avg = existing.cpu_m_max = usage.cpu_m
        existing.mem_avg = existing.mem_max = usage.memory_bytes
    await session.flush()


async def raw_rows(session: AsyncSession, model: str, tier: str) -> list[UsageSample]:
    result = await session.execute(
        select(UsageSample).where(UsageSample.model == model, UsageSample.tier == tier).order_by(UsageSample.ts)
    )
    return list(result.scalars())


async def rollup(session: AsyncSession, now: float) -> None:
    """Builds every coarser tier from the finer one, for each bucket that is completely over."""
    for tier in TIERS:
        if tier.source is None:
            continue
        models = (await session.execute(
            select(UsageSample.model).where(UsageSample.tier == tier.source).distinct()
        )).scalars().all()
        newest = dict((await session.execute(
            select(UsageSample.model, func.max(UsageSample.ts)).where(UsageSample.tier == tier.name).group_by(UsageSample.model)
        )).all())
        for model in models:
            floor = newest[model] + tier.step if model in newest else 0
            source_rows = (await session.execute(
                select(UsageSample).where(UsageSample.model == model, UsageSample.tier == tier.source, UsageSample.ts >= floor)
                .order_by(UsageSample.ts)
            )).scalars().all()
            buckets: dict[int, list[UsageSample]] = defaultdict(list)
            for row in source_rows:
                start = _bucket(row.ts, tier.step)
                if start + tier.step <= now:  # the bucket is over: no more samples can land in it
                    buckets[start].append(row)
            for start, group in sorted(buckets.items()):
                n = sum(r.n for r in group)
                session.add(UsageSample(
                    model=model, tier=tier.name, ts=start, n=n,
                    cpu_m_avg=sum(r.cpu_m_avg * r.n for r in group) / n, cpu_m_max=max(r.cpu_m_max for r in group),
                    mem_avg=sum(r.mem_avg * r.n for r in group) / n, mem_max=max(r.mem_max for r in group),
                ))
        await session.flush()


async def prune(session: AsyncSession, now: float) -> None:
    for tier in TIERS:
        await session.execute(delete(UsageSample).where(UsageSample.tier == tier.name, UsageSample.ts < now - tier.keep))


async def series(session: AsyncSession, model: str, range_: str, now: float) -> list[dict]:
    """The points a chart of `range_` needs, oldest first."""
    if range_ not in RANGES:
        raise ValueError(f"unknown range {range_!r}; use one of {', '.join(RANGES)}")
    tier = BY_NAME[RANGES[range_]]
    rows = (await session.execute(
        select(UsageSample)
        .where(UsageSample.model == model, UsageSample.tier == tier.name, UsageSample.ts >= now - tier.keep)
        .order_by(UsageSample.ts)
    )).scalars().all()
    return [
        {"t": r.ts, "cpu_m": r.cpu_m_avg, "cpu_m_max": r.cpu_m_max, "memory_bytes": r.mem_avg, "memory_bytes_max": r.mem_max}
        for r in rows
    ]


async def latest(session: AsyncSession, model: str, now: float, max_age: float = 30) -> dict | None:
    """The newest live measurement, or None when the model has not been measured recently (asleep, stopped)."""
    row = (await session.execute(
        select(UsageSample).where(UsageSample.model == model, UsageSample.tier == "5s")
        .order_by(UsageSample.ts.desc()).limit(1)
    )).scalar_one_or_none()
    if row is None or now - row.ts > max_age:
        return None
    return {"t": row.ts, "cpu_m": row.cpu_m_avg, "memory_bytes": row.mem_avg}


async def delete_model(session: AsyncSession, model: str) -> None:
    await session.execute(delete(UsageSample).where(UsageSample.model == model))
