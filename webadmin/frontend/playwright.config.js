import { defineConfig } from '@playwright/test';
import { fileURLToPath } from 'node:url';

export default defineConfig({
  testDir: './tests',
  workers: 1,
  outputDir: process.env.UI_TEST_ARTIFACTS_DIR || '/tmp/simtrade-ui-test-results',
  use: {
    baseURL: 'http://127.0.0.1:18765',
    locale: 'zh-CN',
    viewport: { width: 1440, height: 1000 },
    launchOptions: process.env.UI_CHROMIUM_EXECUTABLE ? { executablePath: process.env.UI_CHROMIUM_EXECUTABLE } : {},
  },
  webServer: {
    command: 'uv run python tests/ui_server.py',
    cwd: fileURLToPath(new URL('../../', import.meta.url)),
    url: 'http://127.0.0.1:18765/api/jobs',
    reuseExistingServer: false,
    timeout: 30000,
  },
});
