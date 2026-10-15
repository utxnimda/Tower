import unittest
from planning_defaults import build_defaults, identify_job


def person(key, job=None, x=5, y=5, value=10):
    return {"id": key, "inMainCity": True, "currentTaskType": job, "x": x, "y": y,
            "rangePixels": 150, "stats": {"rows": [{"group": "work", "jobType": "研究", "value": value}]}}


class DefaultTests(unittest.TestCase):
    def test_saved_job_beats_nearby_guess(self):
        facilities = [{"x": 5, "y": 6, "jobType": "研究"}]
        self.assertEqual(identify_job(person("a", "采矿"), facilities)[:2], ("采矿", "saved"))

    def test_ambiguous_nearby_jobs_stay_unknown(self):
        facilities = [{"x": 5, "y": 6, "jobType": "研究"}, {"x": 6, "y": 5, "jobType": "操练"}]
        self.assertIsNone(identify_job(person("a"), facilities)[0])

    def test_defaults_fill_people_once_and_keep_saved_roles(self):
        snapshot = {"jobTypes": ["研究", "采矿"], "facilities": [], "buildings": [],
                    "facilityGroups": [{"template": 1, "ownerId": 3, "jobType": "研究", "count": 2},
                                       {"template": 71, "ownerId": 4, "jobType": "采矿", "count": 7}],
                    "workers": [person("a", "采矿"), person("b", "研究"), person("c", value=100)],
                    "pedestalLimit": {"maximum": 27}}
        result = build_defaults(snapshot)
        assignments = {a["workerId"]: a["jobType"] for a in result["workerAssignments"]}
        self.assertEqual(assignments, {"a": "采矿", "b": "研究", "c": "研究"})
        self.assertEqual(result["priorityStrategy"], "focus")
        self.assertEqual(result["facilityRequests"][0]["count"], 2)
        self.assertEqual(result["jobRequirements"][1]["minFacilitiesPerWorker"], 1)
        self.assertEqual(len(assignments), len(result["workerAssignments"]))


if __name__ == "__main__":
    unittest.main()
