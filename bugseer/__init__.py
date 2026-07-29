"""BugSeer - offline bug-risk prediction for code repositories.

Static analysis + git intelligence + a local ML model. No cloud, no API keys.
"""

__version__ = "0.1.0"

from bugseer.models import FileRisk, RuleHit, RepoReport, ImpactResult

__all__ = ["FileRisk", "RuleHit", "RepoReport", "ImpactResult", "__version__"]
