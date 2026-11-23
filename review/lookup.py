from delivery.results import DeliveryResult, DeliveryStore
from review.provenance import ProvenanceEntry, list_provenance


class ResultUnavailable(LookupError):
    pass


class ReviewLookup:
    def __init__(self, results: DeliveryStore):
        self.results = results

    def review(self, result_id: str) -> DeliveryResult:
        result = self.results.get_result(result_id)
        if result is None:
            raise ResultUnavailable(result_id)
        return result

    def provenance(self, result_id: str) -> tuple[ProvenanceEntry, ...]:
        return list_provenance(self.review(result_id))
