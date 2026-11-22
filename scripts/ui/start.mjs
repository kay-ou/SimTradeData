import { spawnSync, spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../../', import.meta.url));
const frontend = join(root, 'webadmin/frontend');
const npmCli = process.env.npm_execpath;
function run(command, args) {
  const result = spawnSync(command, args, { cwd: root, stdio: 'inherit' });
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status || 1);
}
function hashFiles(paths) {
  const hash = createHash('sha256');
  for (const path of paths.sort()) hash.update(path).update(readFileSync(path));
  return hash.digest('hex');
}
function sourceFiles(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
    const path = join(directory, entry.name);
    return entry.isDirectory() ? sourceFiles(path) : [path];
  });
}
function matches(path, hash) { return existsSync(path) && readFileSync(path, 'utf8') === hash; }
try {
  if (!npmCli) throw new Error('Start with npm run ui');
  // uv run synchronizes Python dependencies. Frontend setup happens only when needed.
  const dependencies = hashFiles([join(frontend, 'package.json'), join(frontend, 'package-lock.json')]);
  const dependencyStamp = join(frontend, 'node_modules/.simtrade-dependencies');
  if (!existsSync(join(frontend, 'node_modules/.bin/vite')) || !matches(dependencyStamp, dependencies)) {
    console.log('Installing UI dependencies / 安装界面依赖 / UI-Abhängigkeiten installieren…');
    run(process.execPath, [npmCli, 'ci', '--prefix', frontend]);
    writeFileSync(dependencyStamp, dependencies);
  }
  const buildHash = hashFiles([...sourceFiles(join(frontend, 'src')), join(frontend, 'index.html'), join(frontend, 'vite.config.js'), join(frontend, 'package-lock.json')]);
  const buildStamp = join(frontend, 'node_modules/.simtrade-build');
  if (!existsSync(join(root, 'simtradedata/web/static/index.html')) || !matches(buildStamp, buildHash)) {
    console.log('Building UI / 构建界面 / Oberfläche erstellen…');
    run(process.execPath, [npmCli, '--prefix', frontend, 'run', 'build']);
    writeFileSync(buildStamp, buildHash);
  }
  const child = spawn('uv', ['run', 'python', '-m', 'simtradedata.web', ...process.argv.slice(2)], { cwd: root, stdio: 'inherit' });
  child.on('error', error => { console.error(error.message); process.exitCode = 1; });
  child.on('exit', code => { process.exitCode = code ?? 0; });
  for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => child.kill(signal));
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
