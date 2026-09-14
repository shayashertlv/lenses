import {fileURLToPath} from 'node:url';
import {readFile, readdir} from 'node:fs/promises';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {defineConfig, mergeConfig} from 'vite';
import type {Plugin, ViteDevServer, PreviewServer} from 'vite';
import base from '../../vite.config.ts';
import {acceptedReferencePlugin} from '../hair-live-preview/accepted-reference.ts';
import {hairModelAssetsPlugin} from '../hair-live-preview/vite.config.ts';
const entry='/experiments/fps-candidate/live.html';
function fingerprintPlugin():Plugin{
  return {name:'fps-candidate-source-fingerprint',apply:'build',async buildStart(){
    const directory=fileURLToPath(new URL('./',import.meta.url));
    const paths:string[]=[];
    const collect=async(dir:string):Promise<void>=>{
      for(const item of await readdir(dir,{withFileTypes:true})){
        const name=path.join(dir,item.name);
        if(item.isDirectory())await collect(name);else if(item.name.endsWith('.ts')&&!item.name.endsWith('.test.ts'))paths.push(name);
      }
    };
    await collect(path.join(directory,'runtime'));
    paths.push(...['entry.ts','study.ts','live.html','vite.config.ts','qa/g-preservation.json'].map(name=>path.join(directory,name)));
    const files=[];
    for(const filename of paths.sort()){
      const bytes=await readFile(filename);files.push({path:path.relative(directory,filename).replaceAll('\\','/'),sha256:createHash('sha256').update(bytes).digest('hex')});
    }
    const fingerprint=createHash('sha256').update(JSON.stringify(files)).digest('hex');
    this.emitFile({type:'asset',fileName:'fps-build.json',source:JSON.stringify({schema:'fps-candidate-source-build-v1',fingerprint,files},null,2)});
  }};
}
function entryPlugin():Plugin {
  const install=(server:ViteDevServer|PreviewServer):void=>{
    server.middlewares.use((request,response,next)=>{
      const url=new URL(request.url??'/','http://localhost');
      if(url.pathname!=='/' || !['GET','HEAD'].includes(request.method??'')){next();return;}
      response.statusCode=302;response.setHeader('Location',entry+url.search);
      response.setHeader('Cache-Control','no-store');response.end();
    });
  };
  return {name:'isolated-fps-candidate-entry',configureServer:install,configurePreviewServer:install};
}
export default defineConfig(async()=>mergeConfig(base,{
  cacheDir:'node_modules/.vite-fps-candidate',
  plugins:[await acceptedReferencePlugin(),hairModelAssetsPlugin(),entryPlugin(),fingerprintPlugin()],
  server:{host:'127.0.0.1',port:8101,strictPort:true,watch:{ignored:['**/.recovery/**','**/model_studio/**','**/modeling_auto/**']}},
  preview:{host:'127.0.0.1',port:8100,strictPort:true},
  build:{outDir:'experiments/fps-candidate/dist',emptyOutDir:true,
    rollupOptions:{input:{fpsCandidate:fileURLToPath(new URL('./live.html',import.meta.url))}}},
}));
