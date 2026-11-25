// Compile a generic isolated Vue fixture; no enterprise application or API calls.
import { createRequire } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, resolve } from 'node:path';
import { mkdir, writeFile, readFile } from 'node:fs/promises';
import { spawnSync } from 'node:child_process';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const frontend = resolve(root, 'frontend');
const require = createRequire(resolve(frontend, 'package.json'));
const fixture = resolve(frontend, '.m0', 'probe');
const output = resolve(frontend, '.m0', 'dist');
const load = async (name) => import(pathToFileURL(require.resolve(name)).href);

await mkdir(fixture, { recursive: true });
await mkdir(resolve(root, '.m0'), { recursive: true });
let result;
try {
  const vite = await load('vite');
  const pluginVue = (await load('@vitejs/plugin-vue')).default;
  const ts = (await load('typescript')).default;
  const compiled = ts.transpileModule('const value: number = 1;', {
    compilerOptions: { target: ts.ScriptTarget.ES2022 },
    reportDiagnostics: true,
  });
  await writeFile(resolve(fixture, 'index.html'), '<div id="app"></div><script type="module" src="/main.ts"></script>');
  await writeFile(resolve(fixture, 'main.ts'), `import { createApp } from 'vue';
import { createPinia } from 'pinia';
import { createRouter, createMemoryHistory } from 'vue-router';
import axios from 'axios';
import App from './App.vue';
const client = axios.create({baseURL:'/api/v1'});
if (!client.defaults.baseURL) throw new Error('Client configuration missing');
createApp(App).use(createPinia()).use(createRouter({history:createMemoryHistory(),routes:[{path:'/',component:App}]})).mount('#app');`);
  await writeFile(resolve(fixture, 'App.vue'), `<script setup lang="ts">
import { ref } from 'vue';
import { ElButton } from 'element-plus';
const count = ref<number>(0);
</script><template><ElButton @click="count++">M0 {{ count }}</ElButton></template>`);
  await writeFile(resolve(frontend, '.m0', 'tsconfig.json'), JSON.stringify({
    compilerOptions: {
      target: 'ES2022', module: 'ESNext', moduleResolution: 'Bundler', strict: true,
      skipLibCheck: true, noEmit: true, lib: ['ES2022', 'DOM'], types: ['node'],
    },
    include: ['probe/**/*.ts', 'probe/**/*.vue'],
  }, null, 2));
  await vite.build({
    root: fixture, configFile: false, plugins: [pluginVue()], logLevel: 'warn',
    build: { outDir: output, emptyOutDir: false },
  });
  const types = spawnSync(process.execPath, [
    require.resolve('vue-tsc/bin/vue-tsc.js'), '--noEmit', '--pretty', 'false',
    '-p', resolve(frontend, '.m0', 'tsconfig.json'),
  ], { cwd: frontend, encoding: 'utf8', timeout: 45000 });
  if (types.status !== 0) throw new Error(types.stderr || types.stdout || 'Vue typecheck failed');
  const manifest = JSON.parse(await readFile(resolve(frontend, 'package.json'), 'utf8'));
  result = {
    phase: 'frontend', passed: compiled.diagnostics.length === 0,
    checks: { typescript_transpile: compiled.diagnostics.length === 0, vue_vite_build: true, vue_typecheck: true },
    node: process.version,
    dependencies: manifest.dependencies,
    devDependencies: manifest.devDependencies,
    model_api_requests: 0,
  };
} catch (error) {
  result = { phase: 'frontend', passed: false, error_type: error.name, error: error.message };
}
await writeFile(resolve(root, '.m0', 'frontend.json'), JSON.stringify(result, null, 2));
console.log(JSON.stringify(result, null, 2));
process.exitCode = result.passed ? 0 : 1;
