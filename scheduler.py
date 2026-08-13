"""
ErnestOS — when the unattended jobs run.

This module owns *scheduling*, not sending. The four job bodies stay in
`app.py`, beside the renderers they use, because a report is a rendered bot
message and separating the two would mean either a circular import or passing
four renderers around as arguments — more indirection than the split is worth.

What is genuinely separate, and lives here, is the policy: how often each job
wakes up, how late it may run and still be worth running, and how many copies
of it may be in flight. That policy was previously thirty lines inside
`lifespan`, wedged between starting the bot and yielding to the server, where
it was neither readable nor reachable from a test.

Two guarantees the caller must not undo
---------------------------------------

**Nothing runs twice.** Two protections, and both matter. `max_instances=1`
stops one process from overlapping a job with itself when a run takes longer
than the tick. `svc.JobLock` — a PostgreSQL advisory lock, taken inside each
job — stops *several processes* from doing the same work, which is the case
that actually shows up in production the day a second instance is started.
Under both of those sits the outbox claim in `svc.claim_report`, so even a
scheduler that misbehaved could not send one user two morning reports.

**A missed tick is not a missed day.** `misfire_grace_time` is generous on
purpose: a deploy that takes four minutes must not mean nobody gets a report,
and the once-a-day guarantee comes from the claim rather than from the
schedule, so running late is always better than not running.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

import config
import services as svc

log = logging.getLogger("ernestos")

#: How long after its moment a job may still start and be worth starting.
#: Reports: ten minutes, because a late report is still the day's report.
#: Reminders: two, because a reminder for something that already happened is
#: noise. Statistics: an hour, since nobody is waiting on the exact minute.
REPORT_GRACE = 600
REMINDER_GRACE = 120
STATS_GRACE = 3600


def build(bot, *, send_reports, send_reminders, send_platform_stats
          ) -> AsyncIOScheduler:
    """Wire the jobs onto a scheduler and return it, **not** started.

    Wiring and starting are separate because starting needs a running event
    loop and wiring does not — which is what lets a test assert on the real
    triggers instead of grepping this file for a string. `start` is the pair
    of this for the application.

    The job callables are passed in rather than imported, which is what keeps
    this module free of a cycle back into `app.py`.

    The scheduler's own clock is `svc.TZ`, the project clock — so the daily
    statistics post at `STATS_POST_HOUR` means that hour in Tashkent no matter
    which region the process is deployed to. Per-user report times are not
    scheduled here at all: the report jobs tick frequently and each one asks
    every user whether their chosen moment, in their own zone, has arrived.
    """
    scheduler = AsyncIOScheduler(timezone=svc.TZ)

    # Report times are a per-user setting, so the jobs run on a short cycle and
    # each one decides, per user, whether their moment has come. Delivering
    # exactly once a day is the outbox claim's job, which is why a frequent
    # tick cannot produce a duplicate.
    for report_type in ("morning", "evening"):
        scheduler.add_job(send_reports, "cron",
                          minute=f"*/{config.REPORT_TICK_MINUTES}",
                          args=[bot, report_type], id=report_type,
                          max_instances=1, misfire_grace_time=REPORT_GRACE)

    scheduler.add_job(send_reminders, "cron",
                      minute=f"*/{svc.REMINDER_JOB_MINUTES}",
                      args=[bot], id="reminders",
                      max_instances=1, misfire_grace_time=REMINDER_GRACE)

    # The statistics post ticks like the reports do, and decides for itself
    # whether today's is owed. It used to be `cron(hour=STATS_POST_HOUR)`,
    # which is the obvious way to say "once a day at ten" and does not
    # survive contact with a restart: this scheduler's jobstore is in memory,
    # so at every boot cron computes the next fire *after now*, and a deploy
    # at 11:00 moved the post to 10:00 tomorrow. Redeploy most days and it
    # never fires at all. "Once a day" is now guaranteed by the claim in
    # `job_runs`, which a restart cannot forget.
    scheduler.add_job(send_platform_stats, "cron",
                      minute=f"*/{config.REPORT_TICK_MINUTES}",
                      args=[bot], id="stats",
                      max_instances=1, misfire_grace_time=STATS_GRACE)

    return scheduler


def start(bot, **jobs) -> AsyncIOScheduler:
    """Wire the jobs and start ticking. Needs a running event loop."""
    scheduler = build(bot, **jobs)
    scheduler.start()
    log.info("scheduler started — reports every %s min, reminders every %s min, "
             "statistics at %02d:00 %s",
             config.REPORT_TICK_MINUTES, svc.REMINDER_JOB_MINUTES,
             config.STATS_POST_HOUR, svc.TZ)
    return scheduler
