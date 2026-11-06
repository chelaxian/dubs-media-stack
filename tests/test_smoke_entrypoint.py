"""Offline regression for the integration_smoke entrypoint (NO Docker).

CI41861637228 was falsely green: the old runner called
unittest.main(module=None, argv=[...]), which selected ZERO tests ("Ran 0
tests") and exited 0. These tests prove, with Docker setUp and test bodies
stubbed out (the real smoke never runs here):

  - without DUBS_SMOKE the module exposes no test cases (offline discovery
    stays hermetic);
  - with DUBS_SMOKE the runner selects ALL six IntegrationSmoke methods;
  - the runner refuses success (exit code 2) when zero tests are selected
    or when zero tests actually ran;
  - a fully stubbed execution of the real selection returns success with
    all six methods accounted for.
"""
import contextlib
import importlib.util
import os
import sys
import types
import unittest
from unittest import mock

import _dubs_env  # offline roots; keeps discovery hermetic

SMOKE_PATH = _dubs_env.REPO / 'tests' / 'integration_smoke.py'
FLAGGED = 'dubs-smoke-flagged-copy'


def load_flagged():
    """Fresh import of integration_smoke with DUBS_SMOKE=1 (class defined)."""
    with mock.patch.dict(os.environ, {'DUBS_SMOKE': '1'}):
        spec = importlib.util.spec_from_file_location(FLAGGED, SMOKE_PATH)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[FLAGGED] = mod
        spec.loader.exec_module(mod)
    return mod


class OfflineGating(unittest.TestCase):
    def test_plain_module_exposes_no_tests_offline(self):
        import integration_smoke  # imported WITHOUT DUBS_SMOKE
        self.assertFalse(hasattr(integration_smoke, 'IntegrationSmoke'))
        self.assertEqual(integration_smoke.suite_for(integration_smoke).countTestCases(), 0)
        self.assertEqual(integration_smoke.smoke_main(integration_smoke), 2)

    def test_gated_copy_defines_six_methods(self):
        mod = load_flagged()
        names = mod.expected_names(mod)
        self.assertEqual(len(names), 6)
        self.assertTrue(all(n.startswith('test_0') for n in names))
        suite = mod.suite_for(mod)
        selected = sorted({c.id().rsplit('.', 1)[-1] for c in mod._cases(suite)})
        self.assertEqual(selected, names)  # loader selects every method


class StubbedExecution(unittest.TestCase):
    """Prove the runner works end-to-end without any Docker involvement."""

    def _stubbed(self):
        mod = load_flagged()
        cls = mod.IntegrationSmoke
        contexts = [mock.patch.object(cls, 'setUpClass',
                                      classmethod(lambda c: None)),
                    mock.patch.object(cls, '_teardown',
                                      classmethod(lambda c: None)),
                    mock.patch.object(cls, 'addClassCleanup',
                                      classmethod(lambda c, *a, **k: None))]
        for name in mod.expected_names(mod):
            contexts.append(mock.patch.object(cls, name, mock.Mock(name=name, return_value=None)))
        return mod, contexts

    def test_stubbed_run_selects_and_succeeds(self):
        mod, contexts = self._stubbed()
        with contextlib.ExitStack() as stack:
            for ctx in contexts:
                stack.enter_context(ctx)
            code = mod.smoke_main(mod)
        self.assertEqual(code, 0)  # six stubbed methods ran, no Docker touched

    def test_runner_refuses_zero_executed_tests(self):
        mod = load_flagged()
        result = types.SimpleNamespace(testsRun=0, wasSuccessful=lambda: True)
        with mock.patch.object(unittest.TextTestRunner, 'run', return_value=result):
            self.assertEqual(mod.smoke_main(mod), 2)

    def test_runner_refuses_zero_selected_tests(self):
        mod = load_flagged()
        self.addCleanup(sys.modules.pop, FLAGGED, None)
        empty = types.ModuleType('dubs-empty-smoke-target')  # no TestCase at all
        with mock.patch.object(unittest, 'TextTestRunner',
                               side_effect=AssertionError('must not run')) \
                as runner:
            self.assertEqual(mod.smoke_main(empty), 2)
            runner.assert_not_called()


if __name__ == '__main__':
    unittest.main()
