"""Persistent timer behavior using a fake clock and disposable data."""
import tempfile
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from server import RiverStore, AppError

class SchoolTimerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.now = datetime(2026,9,8,16,tzinfo=ZoneInfo('America/Indiana/Indianapolis'))
        self.store = RiverStore(self.tmp.name, now=lambda:self.now)
    def tearDown(self):
        self.store.db.close(); self.tmp.cleanup()
    def move(self,minutes): self.now += timedelta(minutes=minutes)
    def start(self,kind=None):
        return self.store.transaction(lambda:self.store.mutate('/api/school-clock/start',{} if kind is None else {'kind':kind},'river'))
    def pause(self): return self.store.transaction(lambda:self.store.mutate('/api/school-clock/pause',{},'river'))
    def clock(self): return self.store.view()['schoolClock']

    def test_work_stops_at_thirty_and_waiting_does_not_spend_budget(self):
        self.start('work'); self.move(45)
        c=self.clock()
        self.assertEqual(c['elapsedSeconds'],1800)
        self.assertEqual(c['phase']['remainingSeconds'],0)
        self.assertFalse(c['running']); self.assertTrue(c['phase']['finished'])
        self.start('break'); self.move(20)
        self.assertEqual(self.clock()['elapsedSeconds'],1800)
        self.assertTrue(self.clock()['phase']['finished'])
        self.assertFalse(any(t['completedAt'] for t in self.store.view()['tasks']))

    def test_pause_resume_and_restart_preserve_remaining_time(self):
        ident=self.start('work')['phase']['id']; self.move(12); self.pause(); self.move(7)
        self.assertEqual(self.clock()['phase']['remainingSeconds'],1080)
        self.start(); self.move(5)
        again=RiverStore(self.tmp.name,now=lambda:self.now)
        try:
            c=again.view()['schoolClock']
            self.assertEqual(c['elapsedSeconds'],1020)
            self.assertEqual(c['phase']['id'],ident)
            self.assertEqual(c['phase']['remainingSeconds'],780)
        finally: again.db.close()

    def test_braces_break_pauses_school_and_uses_the_same_daily_timer(self):
        self.start('work'); self.move(15)
        c=self.start('braces')
        self.assertFalse(c['running']); self.assertTrue(c['phase']['running'])
        self.move(20); self.pause(); self.move(10)
        self.assertEqual(self.clock()['elapsedSeconds'],900)
        self.assertEqual(self.store.view()['braces']['elapsedSeconds'],1200)
        self.start(); self.move(40)
        c=self.clock()
        self.assertEqual(c['elapsedSeconds'],900)
        self.assertTrue(c['phase']['finished'])
        self.assertTrue(self.store.view()['braces']['timerElapsed'])
        self.assertIsNone(self.store.view()['braces']['parentConfirmed'])
        self.start('work'); self.move(30)
        self.assertEqual(self.clock()['elapsedSeconds'],1800)

    def test_existing_braces_minutes_are_preserved_and_direct_pause_stays_in_sync(self):
        self.store.mutate('/api/braces/start',{},'river'); self.move(25)
        self.start('braces')
        self.assertEqual(self.clock()['phase']['remainingSeconds'],2100)
        self.store.mutate('/api/braces/pause',{},'river'); self.move(10)
        self.assertFalse(self.clock()['phase']['running'])
        self.assertEqual(self.clock()['phase']['remainingSeconds'],2100)
        with self.assertRaises(AppError): self.store.start_school_clock()
        self.start('work')
        self.assertEqual(self.clock()['phase']['kind'],'work')

    def test_two_automatic_ten_minute_breaks_and_explicit_work_resume(self):
        self.start('work')
        for session in (1, 2):
            self.move(30)
            c = self.clock()
            self.assertEqual(c['elapsedSeconds'], session * 1800)
            self.assertEqual(c['phase']['kind'], 'break')
            self.assertEqual(c['phase']['remainingSeconds'], 600)
            self.assertFalse(c['running'])
            self.assertTrue(c['phase']['running'])
            self.assertFalse(self.store.view()['braces']['running'])
            self.assertEqual(self.store.view()['braces']['elapsedSeconds'], 0)
            self.move(10)
            c = self.clock()
            self.assertTrue(c['phase']['finished'])
            self.assertFalse(c['running'])
            self.move(20)
            self.assertEqual(self.clock()['elapsedSeconds'], session * 1800)
            with self.assertRaisesRegex(AppError, 'Resume work'):
                self.store.start_school_clock()
            self.start('work')
        self.move(30)
        c = self.clock()
        self.assertTrue(c['limitReached'])
        self.assertEqual(c['completedWorkSessions'], 3)
        self.assertEqual(c['restSeconds'], 1200)
        self.assertEqual(c['phase']['kind'], 'work')
        self.assertFalse(c['phase']['running'])
        with self.assertRaises(AppError): self.start('work')

    def test_late_poll_and_restart_use_work_deadline_for_break(self):
        ident = self.start('work')['phase']['id']
        self.move(34)
        again = RiverStore(self.tmp.name, now=lambda: self.now)
        try:
            c = again.view()['schoolClock']
            self.assertEqual(c['elapsedSeconds'], 1800)
            self.assertEqual(c['phase']['remainingSeconds'], 360)
            self.assertEqual(c['phase']['fromWorkId'], ident)
            again.save()
        finally:
            again.db.close()
        self.store.db.close()
        self.store = RiverStore(self.tmp.name, now=lambda: self.now)
        self.move(40)
        self.assertEqual(self.clock()['elapsedSeconds'], 1800)
        self.assertEqual(self.clock()['phase']['remainingSeconds'], 0)
        self.assertFalse(self.clock()['phase']['running'])

    def test_work_pause_requires_explicit_resume_and_break_can_end_early(self):
        self.start('work'); self.move(10); self.pause()
        self.move(25)
        with self.assertRaisesRegex(AppError, 'Resume work'):
            self.store.start_school_clock()
        self.assertEqual(self.clock()['elapsedSeconds'], 600)
        self.start('work'); self.move(20)
        self.assertEqual(self.clock()['phase']['remainingSeconds'], 600)
        self.move(3); self.start('work'); self.move(1)
        self.assertEqual(self.clock()['elapsedSeconds'], 1860)
        self.assertEqual(self.clock()['restSeconds'], 180)

    def test_rest_pause_resume_and_restart_never_spend_work_time(self):
        self.start('work'); self.move(30)
        self.start('break'); self.move(4); self.pause(); self.move(10)
        self.assertEqual(self.clock()['restSeconds'],240)
        self.assertEqual(self.clock()['elapsedSeconds'],1800)
        self.start(); self.move(6)
        c=self.clock()
        self.assertEqual(c['elapsedSeconds'],1800)
        self.assertEqual(c['restSeconds'],600)
        self.assertTrue(c['phase']['finished'])
        again=RiverStore(self.tmp.name,now=lambda:self.now)
        try:
            self.assertEqual(again.view()['schoolClock']['restSeconds'],600)
            self.assertEqual(again.view()['schoolClock']['elapsedSeconds'],1800)
        finally: again.db.close()

    def test_interrupted_work_finishes_the_same_thirty_minute_session(self):
        self.start('work'); self.move(12)
        self.start('break'); self.move(10)
        c=self.start('work')
        self.assertEqual(c['phase']['durationSeconds'],18*60)
        self.assertEqual(c['phase']['sessionNumber'],1)
        self.move(18)
        self.assertEqual(self.clock()['completedWorkSessions'],1)
        self.assertEqual(self.clock()['elapsedSeconds'],1800)

    def test_midnight_caps_previous_day_and_next_day_is_fresh(self):
        self.now=self.now.replace(hour=23,minute=50)
        self.start('work'); self.move(20)
        old=self.store.view('2026-09-08')['schoolClock']
        self.assertEqual(old['elapsedSeconds'],600)
        self.assertFalse(old['phase']['running'])
        self.assertEqual(self.clock()['elapsedSeconds'],0)
        self.assertIsNone(self.clock()['phase'])

    def test_invalid_kind_and_assistant_cannot_control_timer(self):
        with self.assertRaises(AppError): self.start('unknown')
        with self.assertRaises(AppError): self.store.mutate('/api/school-clock/start',{'kind':'braces'},'assistant')
        self.assertEqual(self.clock()['elapsedSeconds'],0)

    def test_legacy_pause_resumes_work_instead_of_forcing_a_break(self):
        c = self.store.day(self.store.today())['schoolClock']
        c.update(elapsedSeconds=63*60, startedAt=self.store.stamp(), running=True)
        self.move(1); self.pause(); self.move(5)
        with self.assertRaisesRegex(AppError, 'Resume work'):
            self.store.start_school_clock()
        resumed = self.start()
        self.assertEqual(resumed['phase']['kind'], 'work')
        self.assertEqual(resumed['phase']['remainingSeconds'], 26*60)
        self.move(2)
        self.assertEqual(self.clock()['elapsedSeconds'], 66*60)

    def correct(self, minutes, **fields):
        return self.store.transaction(lambda:self.store.mutate('/api/parent/school-clock/correct', {'removeMinutes':minutes, **fields}, 'parent'))

    def test_parent_removes_running_time_and_resume_restores_work_to_do(self):
        self.start('work'); self.move(20)
        before_tasks = self.store.view()['tasks']
        c = self.correct(10)
        self.assertEqual(c['elapsedSeconds'],600)
        self.assertEqual(c['phase']['remainingSeconds'],1200)
        self.assertFalse(c['running'])
        self.assertEqual(c['history'][-1]['details']['beforeSeconds'],1200)
        self.assertEqual(c['history'][-1]['details']['afterSeconds'],600)
        self.assertEqual(self.store.view()['tasks'],before_tasks)
        self.move(5)
        self.assertEqual(self.clock()['elapsedSeconds'],600)
        self.start(); self.move(20)
        self.assertEqual(self.clock()['elapsedSeconds'],1800)
        self.assertEqual(self.clock()['completedWorkSessions'],1)

    def test_correction_after_budget_limit_reopens_work_and_persists(self):
        for _ in range(3):
            self.start('work'); self.move(30)
        c = self.correct(15)
        self.assertFalse(c['limitReached'])
        self.assertEqual(c['phase']['sessionNumber'],3)
        self.assertEqual(c['phase']['remainingSeconds'],900)
        again=RiverStore(self.tmp.name,now=lambda:self.now)
        try:
            saved=again.view()['schoolClock']
            self.assertEqual(saved['elapsedSeconds'],4500)
            self.assertFalse(saved['running'])
            self.assertEqual(saved['history'][-1]['action'],'school_time_corrected')
        finally:again.db.close()
        self.start(); self.move(15)
        self.assertTrue(self.clock()['limitReached'])

    def test_correction_preserves_running_rest_and_braces(self):
        self.start('work'); self.move(30)
        self.start('break'); self.move(4)
        p = self.clock()['phase']['id']
        self.correct(5)
        self.assertEqual(self.clock()['phase']['id'],p)
        self.assertTrue(self.clock()['phase']['running'])
        self.move(6)
        self.assertEqual(self.clock()['restSeconds'],600)
        self.assertEqual(self.clock()['elapsedSeconds'],1500)
        self.start('braces'); self.move(20)
        self.correct(5)
        self.move(40)
        self.assertTrue(self.store.view()['braces']['timerElapsed'])
        self.assertIsNone(self.store.view()['braces']['parentConfirmed'])
        self.assertEqual(self.clock()['restSeconds'],600)
        self.assertEqual(self.clock()['elapsedSeconds'],1200)

    def test_correction_authority_validation_and_past_dates(self):
        self.start('work'); self.move(20)
        for actor in ('river','assistant'):
            with self.assertRaises(AppError):self.store.mutate('/api/parent/school-clock/correct',{'removeMinutes':5},actor)
        for minutes in (-1,0,21,91,True,'5',1.5):
            with self.assertRaises(AppError):self.correct(minutes)
        with self.assertRaises(AppError):self.correct(5,date='2026-09-09')
        self.assertEqual(self.clock()['elapsedSeconds'],1200)
        self.pause(); self.move(24*60)
        self.correct(5,date='2026-09-08')
        self.assertEqual(self.store.view('2026-09-08')['schoolClock']['elapsedSeconds'],900)
        self.assertEqual(self.clock()['elapsedSeconds'],0)
