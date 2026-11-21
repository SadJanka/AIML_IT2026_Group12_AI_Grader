from dataclasses import dataclass, field

from .. import models
from ..config import settings


@dataclass
class WeakTopic:
    topic: str
    criteria: list[str] = field(default_factory=list)
    materials: list[models.Material] = field(default_factory=list)


def weak_topics(submission: models.Submission, threshold: float | None = None) -> list[WeakTopic]:
    threshold = settings.weak_threshold if threshold is None else threshold
    course = submission.assignment.course
    materials_by_topic: dict[str, list[models.Material]] = {}
    for m in course.materials:
        materials_by_topic.setdefault(m.topic.strip().lower(), []).append(m)

    topics: dict[str, WeakTopic] = {}
    for score in submission.scores:
        crit = score.criterion
        points = score.final_points if score.final_points is not None else score.ai_points
        if points is None or not crit.max_points:
            continue
        if points / crit.max_points < threshold:
            key = (crit.topic or crit.name).strip()
            wt = topics.setdefault(key.lower(), WeakTopic(key))
            wt.criteria.append(crit.name)
    for key, wt in topics.items():
        wt.materials = materials_by_topic.get(key, [])
    return list(topics.values())
