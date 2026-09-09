import { mergeConfig } from 'vite';
import baseline from '../../vite.config.ts';

export default mergeConfig(baseline, {
  server: { watch: { ignored: ['**/.recovery/**', '**/model_studio/**'] } },
});
