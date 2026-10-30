"""Step 2: turn raw collected data into tagged news and warnings."""
import logging

from ..config import settings
from ..jobs import Job
from .tokens import HackAlerts, TokenWatch

log = logging.getLogger(__name__)


def all_jobs() -> list[Job]:
    jobs: list[Job] = [HackAlerts(), TokenWatch()]
    if settings.anthropic_api_key:
        from .classifier import Classifier

        jobs.insert(0, Classifier())
    else:
        log.info("classifier skipped: set ANTHROPIC_API_KEY to enable AI tagging")
    return jobs
