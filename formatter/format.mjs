import { format } from 'prettier';
let input = '';
for await (const chunk of process.stdin) input += chunk;
try {
  const {before, after, parser} = JSON.parse(input);
  const options = {parser, tabWidth: 2, useTabs: false, printWidth: 60, endOfLine: 'lf'};
  const values = await Promise.all([before, after].map(text => text ? format(text, options) : ''));
  process.stdout.write(JSON.stringify(values));
} catch {
  process.stderr.write('This file could not be formatted.');
  process.exitCode = 1;
}
