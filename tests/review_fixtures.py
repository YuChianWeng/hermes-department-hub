"""Retired v2 implicit provenance helpers.

No helper may infer PASS, a review-lane claim, or a submission SHA from an
outcome. Tests now construct raw board fields explicitly at scenario callsites.
The empty module remains only so the removal is visible in before/after audits.
"""
