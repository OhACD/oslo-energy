"""Non-destructive baseline policy for audited analytical observations."""

from oslo_energy.transformation.cleaning.base import CleaningPolicy
from oslo_energy.transformation.cleaning.contracts import MEASUREMENTS, CleaningContext, CleaningResult, QualityRecord


class PreserveAndFlagPolicy(CleaningPolicy):
    """Retain every source value and independently computed diagnostic finding."""

    policy_id = "preserve_and_flag"
    version = "1"

    def _apply(self, context: CleaningContext) -> CleaningResult:
        observations = context.observations.copy(deep=True)
        for metric in MEASUREMENTS:
            observations[f"{metric}_original"] = observations[metric]
        actions = tuple(
            QualityRecord(
                kind="policy", code="observation_retained", action="retain",
                period_start_utc=row.period_start_utc.isoformat(),
                period_end_utc=row.period_end_utc.isoformat(),
            )
            for row in observations.itertuples(index=False)
        )
        return CleaningResult(observations, context.findings, actions)