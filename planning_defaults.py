"""Editable planning suggestions derived from a read-only game snapshot."""
from collections import Counter
import math

RESOURCE_JOBS = frozenset(("伐木", "种植", "采矿"))


def work_value(worker, job):
    row = next((row for row in worker.get("stats", {}).get("rows", [])
                if row.get("group") == "work" and row.get("jobType") == job), {})
    value = row.get("value")
    return float(value) if isinstance(value, (int, float)) and math.isfinite(value) else 0.0


def identify_job(worker, facilities):
    """Saved state is evidence; position/capability are explicitly suggestions."""
    saved = worker.get("currentTaskType")
    if saved:
        return saved, "saved", "存档明确设定的工种", "high"
    target = worker.get("priorityTarget", {})
    tx, ty = target.get("rawX", 0) - 10000, target.get("rawY", 0) - 10000
    target_jobs = {f["jobType"] for f in facilities if f["x"] == tx and f["y"] == ty}
    if len(target_jobs) == 1:
        return target_jobs.pop(), "priority-target", "由存档中的优先工作目标推测", "medium"
    if worker.get("x") is not None and worker.get("rangePixels") is not None:
        distances = [((f["x"] - worker["x"]) ** 2 + (f["y"] - worker["y"]) ** 2,
                      f["jobType"]) for f in facilities]
        near = [(distance, job) for distance, job in distances
                if distance * 10000 <= worker["rangePixels"] ** 2]
        if near:
            nearest = min(distance for distance, _ in near)
            jobs = {job for distance, job in near if distance == nearest}
            if len(jobs) == 1:
                return jobs.pop(), "nearby", "由当前站位附近的最近设施推测，可手动修改", "low"
    return None, "unknown", "存档未保存明确岗位", "unknown"


def build_defaults(snapshot):
    requests = [{"template": group["template"], "ownerId": group["ownerId"],
                 "count": group["count"], "minWorkers": 1}
                for group in snapshot.get("facilityGroups", [])]
    totals = Counter()
    for group in snapshot.get("facilityGroups", []):
        totals[group["jobType"]] += group["count"]
    people = [w for w in snapshot.get("workers", []) if w.get("inMainCity")
              and w.get("rangePixels") is not None]
    inferred = {w["id"]: identify_job(w, snapshot.get("facilities", [])) for w in people}
    existing = Counter(value[0] for value in inferred.values() if value[0])
    requirements = []
    for job in snapshot.get("jobTypes", []):
        total = totals[job]
        if job in RESOURCE_JOBS:
            desired = min(total, existing[job]) if existing[job] else (1 if total else 0)
        else:
            desired = max(total, existing[job]) if total else 0
        requirements.append({"jobType": job, "workerCount": desired,
                             "minFacilitiesPerWorker": 1,
                             "allocation": "exclusive" if job in RESOURCE_JOBS else "station"})
    jobs = {r["jobType"]: r["workerCount"] for r in requirements}
    assigned, suggestions = {}, {}
    # Prefer explicit saved duties before inferred duties, without assigning a person twice.
    for source in ("saved", "priority-target", "nearby"):
        for worker in people:
            job, evidence, reason, confidence = inferred[worker["id"]]
            if evidence == source and jobs.get(job, 0) > 0:
                assigned[worker["id"]] = job
                jobs[job] -= 1
                suggestions[worker["id"]] = {"workerId": worker["id"], "jobType": job,
                                               "source": evidence, "reason": reason,
                                               "confidence": confidence}
    for job, missing in jobs.items():
        available = sorted((w for w in people if w["id"] not in assigned),
                           key=lambda w: (-work_value(w, job), str(w["id"])))
        for worker in available[:missing]:
            assigned[worker["id"]] = job
            suggestions[worker["id"]] = {"workerId": worker["id"], "jobType": job,
                                           "source": "ability", "reason": "按当前工作能力补足的规划建议，可手动修改",
                                           "confidence": "low"}
    for worker in snapshot.get("workers", []):
        if worker["id"] not in suggestions:
            suggestions[worker["id"]] = {"workerId": worker["id"], "jobType": None,
                                           "source": "unassigned", "reason": "未安排规划职责，保持原位",
                                           "confidence": "unknown"}
    limit = snapshot.get("pedestalLimit", {}).get("maximum")
    pads = sum(b.get("template") == 67 for b in snapshot.get("buildings", []))
    return {"mode": "migration", "maxPedestals": int(limit) if limit is not None else pads,
            "seconds": 60, "allowMainBuildingMoves": True, "lockedBuildingIds": [],
            "lockedFacilityIds": [], "lockedWorkerIds": [],
            "priorityStrategy": "focus", "facilityRequests": requests,
            "jobRequirements": requirements,
            "workerAssignments": [{"workerId": key, "jobType": job} for key, job in assigned.items()],
            "suggestions": list(suggestions.values())}
