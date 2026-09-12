import os
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'packages/shared'), str(ROOT/'workers/media')]
from media_worker.providers import GeminiProvider


class PaidGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_and_invalid_values_never_create_transport(self):
        provider=GeminiProvider.__new__(GeminiProvider)
        provider.key='test-only'
        for value in ('false','', '1', 'yes'):
            with patch.dict(os.environ,{'PAID_LLM_ENABLED':value}):
                with patch('media_worker.providers.httpx.AsyncClient') as transport:
                    with self.assertRaisesRegex(RuntimeError,'Paid LLM calls are disabled'):
                        async with provider._client(None):
                            self.fail('Must never yield a paid client')
                    transport.assert_not_called()

    async def test_missing_switch_defaults_to_locked(self):
        provider=GeminiProvider.__new__(GeminiProvider)
        with patch.dict(os.environ,{},clear=True):
            with patch('media_worker.providers.genai.Client') as sdk:
                with self.assertRaises(RuntimeError):
                    async with provider._client(None):
                        pass
                sdk.assert_not_called()


if __name__=='__main__':unittest.main()
