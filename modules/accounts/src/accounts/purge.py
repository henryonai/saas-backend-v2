"""
The purge job: EventBridge Scheduler runs it every hour (template.yaml), and it
removes every workspace whose grace period has ended (AccountService.purge_due).

A scheduled job, as every one in this template: the schedule invokes the
function asynchronously, Lambda retries a failed run twice, and a run that
still fails, or a schedule that couldn't invoke it, goes to the job's
dead-letter queue, which has an alarm. Each run starts from what is stored, so
a failed or unfinished run is simply continued by the next one.
"""
import time

from aws_lambda_powertools import Logger

from accounts.container import get_account_service
from accounts.service import AccountService

logger = Logger()


def run(context, service: AccountService) -> dict:
    purged = service.purge_due(int(time.time()), lambda: context.get_remaining_time_in_millis() / 1000)
    return {"purged": len(purged)}


@logger.inject_lambda_context
def handler(event: dict, context) -> dict:
    return run(context, get_account_service())
