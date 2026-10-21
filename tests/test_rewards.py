"""Synthetic reward checks; no real completion, reward purchase, or portal access."""
from datetime import datetime, timedelta
from pathlib import Path
import sys
import tempfile
import unittest
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import AppError, RiverStore
from rewards import chore_owner, init_rewards, reward_mutate, reward_state, task_planned


class RewardBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='river-rewards-test-')
        self.now = datetime(2026, 9, 11, 16, 0, tzinfo=ZoneInfo('America/Indiana/Indianapolis'))
        self.store = RiverStore(self.tmp.name, now=lambda: self.now)
        init_rewards(self.store)

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def mutate(self, action, body, actor='parent'):
        if action == 'redeem' and 'requestRevision' not in body:
            award = next((a for a in self.store.state['rewards']['awards'] if a['id'] == body.get('awardId')), None)
            body = dict(body, requestRevision=award['requestRevision'] if award else 0)
        path = '/api/parent/rewards/' + action if actor == 'parent' else '/api/rewards/' + action
        return self.store.transaction(lambda: reward_mutate(self.store, path, body, actor))

    def plan(self, day, **changes):
        fields = {'subject': 'Synthetic fixture', 'title': 'Planned example', 'instructions': 'Do one fresh example.',
            'planDate': day, 'scope': 'work_block', 'plannedMinutes': 25,
            'requirements': {'understanding': False, 'submission': False, 'parent': False}}
        fields.update(changes)
        task = self.store.create_task(fields)
        original = self.store.task(task['id'])
        # The module's contract also works before the root server is wired.
        original.update(scope=fields['scope'], plannedMinutes=fields['plannedMinutes'])
        task_planned(self.store, original)
        return original

    def finish_day(self, day, expected=()):
        previous_now = self.now
        self.now = datetime.fromisoformat(day + 'T16:00:00').replace(tzinfo=self.now.tzinfo)
        try:
            self.mutate('day', {'date': day, 'expectedSchoolIds': list(expected), 'note': 'Synthetic daily expectations explicitly reviewed.'})
            d = self.store.day(day)
            for t in d['chores'] if d['afterSchool'] else []:
                self.store.mutate('/api/tasks/' + t['id'] + '/report', {'note': 'Synthetic report'}, 'river')
                self.store.mutate('/api/parent/tasks/' + t['id'] + '/confirm', {'note': 'Synthetic observation'}, 'parent')
            self.store.mutate('/api/parent/braces/confirm', {'date': day, 'note': 'Synthetic parent observation of one hour wearing.'}, 'parent')
        finally:
            self.now = previous_now

    def finish_week(self):
        for number in range(7, 12):
            self.finish_day(f'2026-09-{number:02d}')

    def earn(self):
        return self.mutate('award', {'date': '2026-09-11', 'note': 'Synthetic parent award.'})['award']

    def test_award_and_redemption_need_no_typed_confirmation_note(self):
        self.finish_week()
        award = self.mutate('award', {'date':'2026-09-11'})['award']
        self.assertEqual(award['note'], '')
        self.mutate('request', {'awardId':award['id'],'prizeId':'ice-cream'}, 'river')
        redeemed = self.mutate('redeem', {'awardId':award['id']})['award']
        self.assertEqual(redeemed['redemptionNote'], '')
        self.assertEqual(redeemed['redeemedBy'], 'parent')
        self.assertIsNotNone(redeemed['redeemedAt'])

    def test_holiday_credit_preserves_tasks_and_survives_reload_and_undo(self):
        import copy
        task = self.plan('2026-09-07')
        before = copy.deepcopy(self.store.day('2026-09-07'))
        result = self.mutate('holiday', {'date': '2026-09-07', 'enabled': True})['day']
        self.assertEqual(result['status'], 'holiday')
        self.assertTrue(result['complete'])
        self.assertEqual(result['holiday']['actor'], 'parent')
        self.assertEqual(result['expectedSchoolIds'], [task['id']])
        self.assertEqual(self.store.day('2026-09-07'), before)
        self.assertIsNone(task.get('completedAt'))
        self.assertEqual(reward_state(self.store)['week']['completedDays'], 1)
        self.mutate('holiday', {'date': '2026-09-07', 'enabled': True})
        events = self.store.state['rewards']['days']['2026-09-07']['history']
        self.assertEqual(sum(e['action'] == 'holiday_marked' for e in events), 1)
        again = RiverStore(self.tmp.name, now=lambda: self.now)
        try:
            self.assertEqual(reward_state(again)['week']['days'][0]['status'], 'holiday')
        finally:
            again.db.close()
        self.mutate('holiday', {'date': '2026-09-07', 'enabled': False})
        self.assertFalse(reward_state(self.store)['week']['days'][0]['complete'])
        self.assertEqual(events[-1]['action'], 'holiday_removed')
        self.assertEqual(self.store.day('2026-09-07'), before)

    def test_holidays_respect_dates_schedule_and_remaining_days(self):
        self.now = self.now.replace(day=8)
        self.mutate('holiday', {'date': '2026-09-07', 'enabled': True})
        self.mutate('holiday', {'date': '2026-09-09', 'enabled': True})
        self.mutate('holiday', {'date': '2026-09-12', 'enabled': True})
        week = reward_state(self.store)['week']
        self.assertEqual(week['completedDays'], 1)
        self.assertEqual(week['requiredDays'], 5)
        self.assertFalse(week['eligible'])
        self.assertEqual(week['days'][2]['status'], 'future')
        self.now = self.now.replace(day=12)
        week = reward_state(self.store)['week']
        self.assertEqual(week['completedDays'], 2)
        self.assertEqual(week['days'][2]['status'], 'holiday')
        self.assertFalse(week['days'][5]['qualifying'])
        self.assertFalse(week['eligible'])

    def test_holiday_removal_requires_recheck_before_redemption(self):
        self.mutate('holiday', {'date': '2026-09-07', 'enabled': True})
        for number in range(8, 12):
            self.finish_day(f'2026-09-{number:02d}')
        self.assertTrue(reward_state(self.store)['week']['eligible'])
        award = self.earn()
        self.mutate('request', {'awardId': award['id'], 'prizeId': 'ice-cream'}, 'river')
        self.mutate('holiday', {'date': '2026-09-07', 'enabled': False})
        with self.assertRaises(AppError):
            self.mutate('redeem', {'awardId': award['id'], 'note': 'Synthetic review.'})
        self.assertEqual(reward_state(self.store)['history'][0]['evidenceSnapshot'][0]['status'], 'holiday')
        self.mutate('holiday', {'date': '2026-09-07', 'enabled': True})
        self.assertIsNotNone(self.mutate('redeem', {'awardId': award['id'], 'note': 'Synthetic review.'})['award']['redeemedAt'])

    def test_holiday_input_and_actor_validation(self):
        for actor in ('river', 'assistant'):
            with self.assertRaises(AppError):
                reward_mutate(self.store, '/api/parent/rewards/holiday', {'date': '2026-09-07', 'enabled': True}, actor)
        for body in ({'date': '2026-09-99', 'enabled': True}, {'enabled': 'true'}, {'enabled': 1}, {}):
            with self.assertRaises(AppError):
                self.mutate('holiday', body)
        self.assertEqual(reward_state(self.store)['week']['completedDays'], 0)

    def test_custom_routine_chore_can_be_selected_for_moms_following_week(self):
        chore=self.store.transaction(lambda:self.store.mutate('/api/parent/routine-chores',{'title':'Synthetic tidy desk'},'parent'))['chore']
        self.finish_week()
        award=self.earn()
        self.mutate('request',{'awardId':award['id'],'prizeId':'mom-chore','chore':chore['id']},'river')
        redeemed=self.mutate('redeem',{'awardId':award['id'],'note':'Synthetic swap'})['award']
        self.assertEqual(redeemed['swap']['title'],'Synthetic tidy desk')
        self.assertEqual(chore_owner(self.store,'2026-09-14',chore['id']),'mom')
        self.assertEqual(self.store.view('2026-09-14')['tasks'][3]['owner'],'mom')

    def test_defaults_and_empty_history_never_fabricate_verified_days(self):
        state = reward_state(self.store)
        self.assertEqual(state['settings'], {'weekStart': 0, 'qualifyingDays': [0, 1, 2, 3, 4]})
        self.assertEqual(state['week']['requiredDays'], 5)
        self.assertEqual(state['week']['completedDays'], 0)
        self.assertFalse(state['week']['eligible'])
        self.assertEqual(state['week']['days'][0]['status'], 'needs_plan')
        self.assertEqual(len(state['catalog']), 3)
        dash = next(p for p in state['catalog'] if p['id'] == 'doordash')
        ice = next(p for p in state['catalog'] if p['id'] == 'ice-cream')
        self.assertIn('budget agreed', dash['description'])
        self.assertNotIn('$40', dash['description'])
        self.assertNotIn('$40', ice['description'])
        self.assertIn('anything', ice['description'])
        weekend = state['week']['days'][-1]
        self.assertFalse(weekend['qualifying'])
        self.assertEqual([i['kind'] for i in weekend['items']], ['braces'])
        with self.assertRaises(AppError):
            self.earn()

    def test_readiness_and_timer_expiry_are_awaiting_review(self):
        self.finish_week()
        day = self.store.day('2026-09-11')
        chore = day['chores'][0]
        self.store.mutate('/api/parent/tasks/' + chore['id'] + '/revoke', {'field': 'parentConfirmed', 'note': 'Synthetic pending review'}, 'parent')
        self.store.mutate('/api/parent/braces/revoke', {'date': '2026-09-11', 'note': 'Synthetic pending review'}, 'parent')
        day['braces'].update(elapsedSeconds=3600, running=False)
        state = reward_state(self.store)
        friday = state['week']['days'][4]
        self.assertEqual(friday['status'], 'awaiting_review')
        self.assertEqual(next(i for i in friday['items'] if i['id'] == 'braces')['status'], 'awaiting_review')
        self.assertFalse(state['week']['eligible'])
        self.assertEqual(state['week']['completedDays'], 4)
        with self.assertRaises(AppError):
            self.earn()
        self.finish_day('2026-09-11')
        self.assertTrue(reward_state(self.store)['week']['eligible'])

    def test_work_block_evidence_does_not_finish_whole_assignment(self):
        self.finish_week()
        assignment = self.plan('2026-09-11', scope='assignment', title='Entire assignment', requirements={'understanding': True, 'submission': True, 'parent': False})
        block = self.plan('2026-09-11', title='First 25 minutes', requirements={'understanding': True, 'submission': False, 'parent': False})
        block['assignmentId'] = assignment['id']
        self.mutate('day', {'date': '2026-09-11', 'expectedSchoolIds': [block['id']], 'note': 'Today expects only the first practice block.'})
        self.store.mutate('/api/tasks/' + block['id'] + '/report', {'note': 'Synthetic work ready'}, 'river')
        self.assertFalse(reward_state(self.store)['week']['eligible'])
        self.store.mutate('/api/assistant/tasks/' + block['id'] + '/understanding', {'goal': 'One example', 'evidence': 'Synthetic fresh response', 'independent': True, 'method': 'fresh_example'}, 'assistant')
        self.assertFalse(reward_state(self.store)['week']['eligible'])
        self.store.mutate('/api/tasks/' + block['id'] + '/complete', {}, 'river')
        self.assertTrue(reward_state(self.store)['week']['eligible'])
        self.assertIsNone(assignment['completedAt'])
        self.assertIsNone(assignment['submission'])
        friday = reward_state(self.store)['week']['days'][4]
        work = next(i for i in friday['items'] if i['kind'] == 'school')
        self.assertEqual(work['scope'], 'work_block')
        self.assertIn('whole assignment is separate', work['reason'])
        self.store.mutate('/api/assistant/tasks/' + block['id'] + '/revoke', {'field': 'understanding', 'note': 'Fresh example needs review'}, 'assistant')
        self.assertFalse(reward_state(self.store)['week']['eligible'])

    def test_later_school_completion_does_not_certify_an_earlier_day(self):
        self.finish_week()
        task = self.plan('2026-09-07')
        self.store.mutate('/api/tasks/' + task['id'] + '/report', {'note': 'Synthetic work first finished Friday.'}, 'river')
        self.store.mutate('/api/tasks/' + task['id'] + '/complete', {}, 'river')
        state = reward_state(self.store)
        self.assertFalse(state['week']['eligible'])
        monday = state['week']['days'][0]
        item = next(i for i in monday['items'] if i['kind'] == 'school')
        self.assertEqual(item['status'], 'awaiting_review')
        self.assertIn('after this planned day', item['reason'])
        self.mutate('day', {'date': '2026-09-07', 'expectedSchoolIds': [], 'note': 'Parent explicitly rescheduled this expectation to Friday.'})
        self.mutate('day', {'date': '2026-09-11', 'expectedSchoolIds': [task['id']], 'note': 'Parent explicitly expects this task on Friday.'})
        self.assertTrue(reward_state(self.store)['week']['eligible'])
        self.assertIsNotNone(task['completedAt'])

    def test_later_verification_can_support_work_reported_on_the_planned_day(self):
        self.finish_week()
        task = self.plan('2026-09-07', requirements={'understanding': True, 'submission': False, 'parent': False})
        self.now -= timedelta(days=4)
        self.store.mutate('/api/tasks/' + task['id'] + '/report', {'note': 'Synthetic work reported Monday.'}, 'river')
        self.now += timedelta(days=4)
        self.assertFalse(reward_state(self.store)['week']['eligible'])
        self.store.mutate('/api/assistant/tasks/' + task['id'] + '/understanding', {'goal': 'One example', 'evidence': 'Synthetic later fresh response supports Monday’s reported work.', 'independent': True, 'method': 'fresh_example'}, 'assistant')
        self.store.mutate('/api/tasks/' + task['id'] + '/complete', {}, 'river')
        self.assertTrue(reward_state(self.store)['week']['eligible'])

    def test_award_and_redemption_are_parent_only_and_one_per_week(self):
        self.finish_week()
        for actor in ('river', 'assistant'):
            with self.assertRaises(AppError):
                reward_mutate(self.store, '/api/parent/rewards/award', {'date': '2026-09-11', 'note': 'Attempt'}, actor)
        award = self.earn()
        with self.assertRaises(AppError):
            self.earn()
        with self.assertRaises(AppError):
            reward_mutate(self.store, '/api/rewards/request', {'awardId': award['id'], 'prizeId': 'doordash'}, 'assistant')
        self.mutate('request', {'awardId': award['id'], 'prizeId': 'doordash'}, actor='river')
        for actor in ('river', 'assistant'):
            with self.assertRaises(AppError):
                reward_mutate(self.store, '/api/parent/rewards/redeem', {'awardId': award['id'], 'note': 'Attempt'}, actor)
        result = self.mutate('redeem', {'awardId': award['id'], 'note': 'Synthetic review, no order placed.'})['award']
        self.assertEqual(result['selectedPrize']['id'], 'doordash')
        self.assertIsNone(result['swap'])
        with self.assertRaises(AppError):
            self.mutate('redeem', {'awardId': award['id'], 'note': 'Repeated click'})
        with self.assertRaises(AppError):
            self.mutate('request', {'awardId': award['id'], 'prizeId': 'ice-cream'}, actor='river')
        self.assertEqual(len(reward_state(self.store)['history']), 1)
        self.assertEqual(len(result['history']), 3)

    def test_following_calendar_week_changes_owner_without_removing_routine(self):
        self.finish_week()
        award = self.earn()
        for invalid in ('braces', 'school', 'all'):
            with self.assertRaises(AppError):
                self.mutate('request', {'awardId': award['id'], 'prizeId': 'mom-chore', 'chore': invalid}, actor='river')
        request = self.mutate('request', {'awardId': award['id'], 'prizeId': 'mom-chore', 'chore': 'petey'}, actor='river')['award']['requestedPrize']
        self.assertEqual(request['swap']['start'], '2026-09-14')
        self.assertEqual(request['swap']['end'], '2026-09-20')
        self.assertEqual(chore_owner(self.store, '2026-09-14', 'petey'), 'river')
        self.mutate('redeem', {'awardId': award['id'], 'note': 'Synthetic Parent confirmation.'})
        for day, owner in (('2026-09-13', 'river'), ('2026-09-14', 'mom'), ('2026-09-20', 'mom'), ('2026-09-21', 'river')):
            self.assertEqual(chore_owner(self.store, day, 'petey'), owner)
            self.assertEqual(chore_owner(self.store, day, 'dogs'), 'river')
        self.now += timedelta(days=3)
        state = reward_state(self.store)
        petey = next(i for i in state['day']['items'] if i['id'] == 'chore:petey')
        self.assertEqual(petey['owner'], 'mom')
        self.assertEqual(petey['status'], 'complete')
        physical = self.store.day('2026-09-14')['chores'][0]
        self.assertIsNone(physical['completedAt'])
        self.assertIsNone(physical['parentConfirmed'])
        self.assertEqual(len(state['activeSwaps']), 1)

    def test_future_days_configuration_and_overlap_cannot_duplicate_award(self):
        self.finish_week()
        self.now -= timedelta(days=1)
        self.assertFalse(reward_state(self.store)['week']['eligible'])
        self.assertEqual(reward_state(self.store)['week']['days'][4]['status'], 'future')
        self.now += timedelta(days=1)
        self.earn()
        self.mutate('settings', {'weekStart': 6, 'qualifyingDays': [0, 1, 2, 3, 4]})
        week = reward_state(self.store)['week']
        self.assertEqual(week['start'], '2026-09-06')
        self.assertFalse(week['eligible'])
        self.assertIsNotNone(week['award'])
        with self.assertRaises(AppError):
            self.earn()
        self.mutate('settings', {'weekStart': 0, 'qualifyingDays': [0, 1, 2, 3, 4, 5, 6]})
        self.assertEqual(reward_state(self.store)['week']['requiredDays'], 7)
        self.assertEqual(len(reward_state(self.store)['week']['days'][5]['items']), 1)
        self.assertFalse(reward_state(self.store)['week']['days'][5]['planReviewed'])
        with self.assertRaises(AppError):
            self.mutate('settings', {'weekStart': 0, 'qualifyingDays': []})

    def test_expectations_can_be_changed_with_reason_and_keep_real_tasks(self):
        self.finish_week()
        self.store.day('2026-09-11')['braces']['parentConfirmed'] = None
        self.assertFalse(reward_state(self.store)['week']['eligible'])
        with self.assertRaises(AppError):
            self.mutate('day', {'date': '2026-09-11', 'expectedSchoolIds': [], 'excusedItems': {'braces': ''}, 'note': 'Edited'})
        self.mutate('day', {'date': '2026-09-11', 'expectedSchoolIds': [], 'excusedItems': {'braces': 'Parent documented an exception for this prize week.'}, 'note': 'Explicit expectation change.'})
        state = reward_state(self.store)
        self.assertTrue(state['week']['eligible'])
        self.assertEqual(next(i for i in state['day']['items'] if i['id'] == 'braces')['status'], 'excused')
        self.assertIsNone(self.store.day('2026-09-11')['braces']['parentConfirmed'])
        self.assertIn('braces', [i['id'] for i in state['day']['items']])
        self.assertTrue(self.store.state['rewards']['days']['2026-09-11']['history'])
        tasks = [self.plan('2026-09-11', plannedMinutes=50) for _ in range(2)]
        over_budget = reward_state(self.store)['day']
        self.assertEqual(over_budget['status'], 'needs_plan')
        self.assertFalse(over_budget['planReviewed'])
        self.assertIn('exceed 90 minutes', ' '.join(over_budget['planIssues']))
        with self.assertRaises(AppError):
            self.mutate('day', {'date': '2026-09-11', 'expectedSchoolIds': [t['id'] for t in tasks], 'note': '100 minute plan rejected.'})

    def test_correction_before_redemption_requires_review_and_reward_history_persists(self):
        self.finish_week()
        award = self.earn()
        self.mutate('request', {'awardId': award['id'], 'prizeId': 'ice-cream'}, actor='river')
        self.store.mutate('/api/parent/braces/revoke', {'date': '2026-09-11', 'note': 'Synthetic correction before redeeming.'}, 'parent')
        with self.assertRaises(AppError):
            self.mutate('redeem', {'awardId': award['id'], 'note': 'Evidence pending.'})
        self.finish_day('2026-09-11')
        result = self.mutate('redeem', {'awardId': award['id'], 'note': 'Reviewed after correction.'})['award']
        self.assertEqual(result['selectedPrize']['id'], 'ice-cream')
        self.store.save()
        again = RiverStore(self.tmp.name, now=lambda: self.now)
        try:
            history = reward_state(again)['history']
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0]['redeemedAt'], result['redeemedAt'])
            self.assertEqual(history[0]['selectedPrize']['id'], 'ice-cream')
        finally:
            again.db.close()

    def test_expired_swap_is_rejected_but_an_alternative_remains_available(self):
        self.finish_week()
        award = self.earn()
        self.mutate('request', {'awardId': award['id'], 'prizeId': 'mom-chore', 'chore': 'dogs'}, actor='river')
        self.now = datetime(2026, 9, 21, 16, 0, tzinfo=self.now.tzinfo)
        with self.assertRaises(AppError):
            self.mutate('redeem', {'awardId': award['id'], 'note': 'Too late for this fixed swap week.'})
        self.mutate('request', {'awardId': award['id'], 'prizeId': 'ice-cream'}, actor='river')
        result = self.mutate('redeem', {'awardId': award['id'], 'note': 'Alternative selected.'})['award']
        self.assertEqual(result['selectedPrize']['id'], 'ice-cream')
        self.assertEqual(chore_owner(self.store, '2026-09-21', 'dogs'), 'river')

    def test_catalog_is_parent_managed_and_preserves_redemption_copy(self):
        for actor in ('river', 'assistant'):
            with self.assertRaises(AppError):
                reward_mutate(self.store, '/api/parent/rewards/catalog', {'title': 'Attempt', 'description': 'Attempt'}, actor)
        prize = self.mutate('catalog', {'title': 'Choose the movie', 'description': 'Parent-arranged movie choice.', 'active': True})['prize']
        self.assertEqual(prize['kind'], 'parent_review')
        self.finish_week()
        award = self.earn()
        self.mutate('request', {'awardId': award['id'], 'prizeId': prize['id']}, actor='river')
        self.mutate('redeem', {'awardId': award['id'], 'note': 'Synthetic approval.'})
        self.mutate('catalog', {'id': prize['id'], 'title': 'Choose a book', 'description': 'Updated option.', 'active': False})
        self.assertEqual(reward_state(self.store)['history'][0]['selectedPrize']['title'], 'Choose the movie')


if __name__ == '__main__':
    unittest.main()
