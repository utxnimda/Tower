import copy
import unittest

import test_validation as validation_fixtures
from validation import validate_layout, validate_result, worker_coverage


class WorkerValidationTests(unittest.TestCase):
    def setUp(self):
        fixture = validation_fixtures.IndependentValidationTests()
        fixture.setUp()
        self.snapshot, self.options, self.layout = fixture.snapshot, fixture.options, fixture.layout
        self.snapshot['workerConstraintVersion'] = 1
        self.snapshot['workers'] = [{'id': 'w1', 'name': '甲', 'x': 4, 'y': 1,
                                     'inMainCity': True, 'selectable': True, 'rangePixels': 150}]
        self.snapshot['facilities'][0]['jobType'] = '种植'
        self.snapshot['facilityGroups'][0]['jobType'] = '种植'
        self.layout['facilities'][0]['jobType'] = '种植'
        self.options['workerAssignments'] = [{'workerId': 'w1', 'jobType': '种植'}]
        self.layout['workers'] = [{'workerId': 'w1', 'jobType': '种植', 'x': 4, 'y': 1,
                                   'rangePixels': 150, 'fixed': False}]

    def test_diagonal_is_covered_and_result_is_stamped(self):
        result = {'strict': self.layout, 'balanced': None}
        validate_result(self.snapshot, self.options, result)
        report = result['strict']['workerCoverage']
        self.assertTrue(report['validated'])
        self.assertEqual(report['coveredFacilities'], 1)
        self.assertEqual(report['facilityCoverage'][0]['workerIds'], ['w1'])

    def test_two_grid_distance_requires_the_real_larger_radius(self):
        self.layout['workers'][0].update(x=5, y=0)
        with self.assertRaisesRegex(ValueError, '真实工作范围'):
            validate_layout(self.snapshot, self.options, self.layout)
        self.snapshot['workers'][0]['rangePixels'] = 200
        self.layout['workers'][0]['rangePixels'] = 200
        self.assertTrue(validate_layout(self.snapshot, self.options, self.layout)['validated'])

    def test_distant_four_plots_cannot_use_one_worker(self):
        self.snapshot['buildings'][0].update(facilityCapacity=4, category='资源')
        self.options['facilityRequests'] = [{'template': 1, 'ownerId': 7, 'count': 4}]
        self.layout['facilities'] = [dict(self.layout['facilities'][0], id=f'f{n}', x=x, y=y)
                                     for n, (x, y) in enumerate([(3, 0), (4, 0), (5, 2), (7, 3)], 1)]
        self.layout['S'] = 6
        with self.assertRaisesRegex(ValueError, '真实工作范围'):
            validate_layout(self.snapshot, self.options, self.layout)

    def test_forged_radius_does_not_extend_a_worker(self):
        self.layout['workers'][0].update(x=5, y=0, rangePixels=999)
        with self.assertRaisesRegex(ValueError, '工作范围与存档'):
            validate_layout(self.snapshot, self.options, self.layout)

    def test_wrong_job_and_forged_facility_type_are_rejected(self):
        self.options['workerAssignments'][0]['jobType'] = '采矿'
        self.layout['workers'][0]['jobType'] = '采矿'
        self.layout['facilities'][0]['jobType'] = '采矿'
        with self.assertRaisesRegex(ValueError, '设施工种'):
            validate_layout(self.snapshot, self.options, self.layout)

    def test_workers_cannot_stand_on_facilities_or_each_other(self):
        self.layout['workers'][0].update(x=3, y=0)
        with self.assertRaisesRegex(ValueError, '重叠'):
            validate_layout(self.snapshot, self.options, self.layout)
        self.layout['workers'][0].update(x=4, y=1)
        self.snapshot['workers'].append(dict(self.snapshot['workers'][0], id='w2', name='乙'))
        self.options['workerAssignments'].append({'workerId': 'w2', 'jobType': '种植'})
        self.layout['workers'].append(dict(self.layout['workers'][0], workerId='w2'))
        with self.assertRaisesRegex(ValueError, '重叠'):
            validate_layout(self.snapshot, self.options, self.layout)

    def test_unselected_worker_is_a_fixed_obstacle(self):
        self.snapshot['workers'].append(dict(self.snapshot['workers'][0], id='w2', x=0, y=0))
        self.layout['workers'].append({'workerId': 'w2', 'x': 0, 'y': 0, 'fixed': True, 'jobType': None})
        with self.assertRaisesRegex(ValueError, '重叠'):
            validate_layout(self.snapshot, self.options, self.layout)

    def test_cannot_use_same_person_twice_or_city_exterior_person(self):
        self.options['workerAssignments'] *= 2
        with self.assertRaisesRegex(ValueError, '人物重复'):
            validate_layout(self.snapshot, self.options, self.layout)
        self.options['workerAssignments'] = self.options['workerAssignments'][:1]
        self.snapshot['workers'][0]['inMainCity'] = False
        with self.assertRaisesRegex(ValueError, '主城人员'):
            validate_layout(self.snapshot, self.options, self.layout)

    def test_automatic_baseline_does_not_claim_a_known_job(self):
        current = copy.deepcopy(self.layout)
        current['workers'][0].update(jobType=None, fixed=True)
        report = worker_coverage(self.snapshot, current, baseline=True)
        self.assertFalse(report['validated'])
        self.assertEqual(report['coveredFacilities'], 0)
        self.assertTrue(report['warnings'])

    def test_coverage_is_shared_across_main_building_owners(self):
        snapshot = copy.deepcopy(self.snapshot)
        snapshot['facilityGroups'].append({'template': 2, 'ownerId': 8, 'jobType': '种植'})
        layout = copy.deepcopy(self.layout)
        layout['facilities'].append({'id': 'f2', 'template': 2, 'ownerId': 8, 'x': 5, 'y': 1})
        self.assertEqual(worker_coverage(snapshot, layout)['coveredFacilities'], 2)


if __name__ == '__main__':
    unittest.main()
