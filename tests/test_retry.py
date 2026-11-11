"""Tests offline cho retry 429 có giới hạn (bonus). Không gọi LLM API."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent import run_agent, MAX_RATE_LIMIT_ATTEMPTS
from bonus.tools import BonusTools
from status_tools import StatusTools
from test_lab import FakeProvider, tool_call, final


def tool_events(result, name):
    return [t for t in result['trace'] if t.get('event') == 'tool' and t.get('name') == name]


class RetryTests(unittest.TestCase):
    def test_429_goes_to_model_with_retry_hint_then_success(self):
        call = lambda i: tool_call(i, 'get_vercel_status', '{}')
        fake = FakeProvider([call('a'), call('b'), final('Vercel ổn')])
        result = run_agent('x', fake, BonusTools(scenario='rate_limit_once'), max_steps=4)
        self.assertEqual(result['status'], 'completed')
        first = json.loads(fake.histories[1][-1]['content'])
        self.assertEqual(first['error'], 'RATE_LIMITED')
        self.assertEqual(first['agent_retry']['outcome'], 'retry_allowed')
        second = json.loads(fake.histories[2][-1]['content'])
        self.assertEqual(second['provider'], 'vercel')
        self.assertEqual(second['agent_retry']['outcome'], 'success_after_retry')

    def test_persistent_429_stops_at_limit_and_blocks_more_calls(self):
        calls = [tool_call(str(i), 'get_vercel_status', '{}') for i in range(5)]
        fake = FakeProvider(calls + [final('Không xác định được Vercel')])
        result = run_agent('x', fake, BonusTools(scenario='rate_limit_always'), max_steps=8)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(len(tool_events(result, 'get_vercel_status')), MAX_RATE_LIMIT_ATTEMPTS)
        self.assertEqual(sum(t['event'] == 'tool_blocked' for t in result['trace']), 2)
        third = json.loads(fake.histories[MAX_RATE_LIMIT_ATTEMPTS][-1]['content'])
        self.assertEqual(third['agent_retry']['outcome'], 'max_attempts')
        self.assertEqual(json.loads(fake.histories[-1][-1]['content'])['error'], 'RETRY_LIMIT_REACHED')

    def test_short_retry_after_is_waited_long_one_is_not(self):
        tools = BonusTools()
        for seconds, outcome, slept in ((2, 'retry_allowed', True), (120, 'retry_after_too_long', False)):
            limited = {'ok': False, 'error': 'RATE_LIMITED', 'http_status': 429, 'retry_after': str(seconds)}
            fake = FakeProvider([tool_call('a', 'get_vercel_status', '{}'), final('x'), final('x')])
            with self.subTest(seconds=seconds), patch.object(tools, 'execute', return_value=limited), \
                    patch('agent.time.sleep') as sleep:
                run_agent('x', fake, tools, max_steps=4)
                self.assertEqual(json.loads(fake.histories[1][-1]['content'])['agent_retry']['outcome'], outcome)
                self.assertEqual(sleep.called, slept)

    def test_answer_with_pending_retry_gets_one_reminder(self):
        fake = FakeProvider([tool_call('a', 'get_vercel_status', '{}'), final('Vercel bị 429'),
                             tool_call('b', 'get_vercel_status', '{}'), final('Vercel ổn')])
        result = run_agent('x', fake, BonusTools(scenario='rate_limit_once'), max_steps=6)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['answer'], 'Vercel ổn')
        self.assertEqual(sum(t['event'] == 'validation_pending_retry' for t in result['trace']), 1)
        self.assertEqual(len(tool_events(result, 'get_vercel_status')), 2)

    def test_pending_retry_reminder_is_not_repeated(self):
        fake = FakeProvider([tool_call('a', 'get_vercel_status', '{}'), final('Chưa rõ'), final('Vẫn chưa rõ Vercel')])
        result = run_agent('x', fake, BonusTools(scenario='rate_limit_always'), max_steps=6)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['answer'], 'Vẫn chưa rõ Vercel')

    def test_bonus_rules_only_added_for_bonus_tools(self):
        bonus, main = FakeProvider([final()]), FakeProvider([final()])
        run_agent('x', bonus, BonusTools())
        run_agent('x', main, StatusTools())
        self.assertIn('Quy tắc bổ sung', bonus.histories[0][0]['content'])
        self.assertNotIn('Quy tắc bổ sung', main.histories[0][0]['content'])

    def test_main_lab_tools_unchanged(self):
        fake = FakeProvider([tool_call('a', 'get_status', '{"provider":"github"}'), final()])
        result = run_agent('x', fake, StatusTools())
        self.assertEqual(len(tool_events(result, 'get_status')), 1)
        self.assertNotIn('agent_retry', json.loads(fake.histories[1][-1]['content']))
        self.assertIn('GitHub và Cloudflare', fake.histories[0][0]['content'])


if __name__ == '__main__':
    unittest.main()
